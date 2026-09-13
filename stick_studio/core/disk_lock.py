"""
Windows Native Raw Disk and Volume Lock Manager.
Provides sector-aligned direct physical drive I/O and volume locking/dismounting
to bypass Windows Vista/7/8/10/11 volume protection mechanisms.
"""

import os
import re
import sys
import time
from typing import List, Optional

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS = 0x00560000
    FSCTL_LOCK_VOLUME = 0x00090018
    FSCTL_DISMOUNT_VOLUME = 0x00090020
    FSCTL_UNLOCK_VOLUME = 0x0009001C
    IOCTL_DISK_UPDATE_PROPERTIES = 0x00070140

    class DISK_EXTENT(ctypes.Structure):
        _fields_ = [
            ("DiskNumber", wintypes.DWORD),
            ("StartingOffset", ctypes.c_int64),
            ("ExtentLength", ctypes.c_int64),
        ]

    class VOLUME_DISK_EXTENTS(ctypes.Structure):
        _fields_ = [
            ("NumberOfDiskExtents", wintypes.DWORD),
            ("Extents", DISK_EXTENT * 8),
        ]

    kernel32.FindFirstVolumeW.argtypes = [wintypes.LPWSTR, wintypes.DWORD]
    kernel32.FindFirstVolumeW.restype = wintypes.HANDLE
    kernel32.FindNextVolumeW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD]
    kernel32.FindNextVolumeW.restype = wintypes.BOOL
    kernel32.FindVolumeClose.argtypes = [wintypes.HANDLE]
    kernel32.FindVolumeClose.restype = wintypes.BOOL
    kernel32.SetFilePointerEx.argtypes = [wintypes.HANDLE, ctypes.c_int64, ctypes.POINTER(ctypes.c_int64), wintypes.DWORD]
    kernel32.SetFilePointerEx.restype = wintypes.BOOL


class VolumeLockManager:
    """
    Context manager that discovers and locks/dismounts ALL volumes on a physical disk.
    Holds handles open during the entire block so Windows allows direct raw sector
    writes to the physical drive without throwing ERROR_INVALID_HANDLE / [Errno 9] Bad file descriptor.
    """

    def __init__(self, disk_number_or_path):
        self.disk_number: Optional[int] = None
        if isinstance(disk_number_or_path, int):
            self.disk_number = disk_number_or_path
        elif isinstance(disk_number_or_path, str):
            m = re.search(r"physicaldrive(\d+)", disk_number_or_path, re.IGNORECASE)
            if m:
                self.disk_number = int(m.group(1))

        self.locked_handles: List[int] = []

    def __enter__(self):
        if os.name != "nt" or self.disk_number is None:
            return self

        vols = self._find_disk_volumes(self.disk_number)
        for vol_path in vols:
            h = self._lock_and_dismount(vol_path)
            if h is not None:
                self.locked_handles.append(h)
        return self

    def _find_disk_volumes(self, disk_number: int) -> List[str]:
        import string

        volumes = []
        # 1. Drive letters (A: - Z:)
        for letter in string.ascii_uppercase:
            drive_path = f"\\\\.\\{letter}:"
            h = kernel32.CreateFileW(
                drive_path,
                0,
                0x00000001 | 0x00000002,
                None,
                3,  # OPEN_EXISTING
                0,
                None,
            )
            if h != -1 and h != 0:
                exts = VOLUME_DISK_EXTENTS()
                ret_bytes = wintypes.DWORD(0)
                ok = kernel32.DeviceIoControl(
                    h,
                    IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS,
                    None,
                    0,
                    ctypes.byref(exts),
                    ctypes.sizeof(exts),
                    ctypes.byref(ret_bytes),
                    None,
                )
                if ok:
                    for i in range(exts.NumberOfDiskExtents):
                        if exts.Extents[i].DiskNumber == disk_number:
                            volumes.append(drive_path)
                kernel32.CloseHandle(h)

        # 2. GUID Volumes (volumes without a drive letter)
        try:
            buf = ctypes.create_unicode_buffer(260)
            h_find = kernel32.FindFirstVolumeW(buf, len(buf))
            if h_find != wintypes.HANDLE(-1).value and h_find != 0:
                while True:
                    vol_name = buf.value.rstrip("\\")
                    h = kernel32.CreateFileW(
                        vol_name,
                        0,
                        0x00000001 | 0x00000002,
                        None,
                        3,
                        0,
                        None,
                    )
                    if h != -1 and h != 0:
                        exts = VOLUME_DISK_EXTENTS()
                        ret_bytes = wintypes.DWORD(0)
                        ok = kernel32.DeviceIoControl(
                            h,
                            IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS,
                            None,
                            0,
                            ctypes.byref(exts),
                            ctypes.sizeof(exts),
                            ctypes.byref(ret_bytes),
                            None,
                        )
                        if ok:
                            for i in range(exts.NumberOfDiskExtents):
                                if exts.Extents[i].DiskNumber == disk_number:
                                    if vol_name not in volumes:
                                        volumes.append(vol_name)
                        kernel32.CloseHandle(h)
                    if not kernel32.FindNextVolumeW(h_find, buf, len(buf)):
                        break
                kernel32.FindVolumeClose(h_find)
        except Exception:
            pass

        return volumes

    def _lock_and_dismount(self, vol_path: str) -> Optional[int]:
        h = kernel32.CreateFileW(
            vol_path,
            0xC0000000,  # GENERIC_READ | GENERIC_WRITE
            0x00000001 | 0x00000002,  # FILE_SHARE_READ | FILE_SHARE_WRITE
            None,
            3,  # OPEN_EXISTING
            0,
            None,
        )
        if h == wintypes.HANDLE(-1).value or h == 0:
            return None

        bytes_ret = wintypes.DWORD(0)
        # Dismount first to invalidate open handles from other processes
        kernel32.DeviceIoControl(h, FSCTL_DISMOUNT_VOLUME, None, 0, None, 0, ctypes.byref(bytes_ret), None)

        # Retry locking up to 10 times (1 sec total) if Explorer holds a temporary lock
        for _ in range(10):
            if kernel32.DeviceIoControl(h, FSCTL_LOCK_VOLUME, None, 0, None, 0, ctypes.byref(bytes_ret), None):
                break
            time.sleep(0.1)

        return h

    def __exit__(self, exc_type, exc_val, exc_tb):
        if os.name != "nt":
            return

        bytes_ret = wintypes.DWORD(0)
        for h in self.locked_handles:
            try:
                kernel32.DeviceIoControl(h, FSCTL_UNLOCK_VOLUME, None, 0, None, 0, ctypes.byref(bytes_ret), None)
            except Exception:
                pass
            try:
                kernel32.CloseHandle(h)
            except Exception:
                pass
        self.locked_handles.clear()


class Win32RawDisk:
    """
    Direct Win32 sector I/O for physical disks and image files.
    Avoids Python CRT _commit() / os.fsync() and _write() quirks on raw devices.
    Provides seek, read, write, flush with exact Win32 error reporting.
    """

    def __init__(self, target_path: str, write_mode: bool = True):
        self.target_path = target_path
        self.write_mode = write_mode
        self.handle = None
        self._is_py_file = False
        self._py_f = None

    def __enter__(self):
        if os.name == "nt":
            access = 0xC0000000 if self.write_mode else 0x80000000  # GENERIC_READ | GENERIC_WRITE
            share = 0x00000001 | 0x00000002  # FILE_SHARE_READ | FILE_SHARE_WRITE
            self.handle = kernel32.CreateFileW(
                self.target_path,
                access,
                share,
                None,
                3,  # OPEN_EXISTING
                0,
                None,
            )
            if self.handle == wintypes.HANDLE(-1).value or self.handle == 0:
                err = ctypes.get_last_error()
                raise OSError(f"CreateFileW failed for '{self.target_path}' (Win32 error {err})")
        else:
            self._is_py_file = True
            mode = "r+b" if self.write_mode else "rb"
            self._py_f = open(self.target_path, mode, buffering=0)

        return self

    def seek(self, offset: int):
        if self._is_py_file:
            self._py_f.seek(offset)
            return

        new_pos = ctypes.c_int64(0)
        ok = kernel32.SetFilePointerEx(self.handle, ctypes.c_int64(offset), ctypes.byref(new_pos), 0)  # FILE_BEGIN
        if not ok:
            err = ctypes.get_last_error()
            raise OSError(f"SetFilePointerEx failed at offset {offset} (Win32 error {err})")

    def read(self, size: int) -> bytes:
        if self._is_py_file:
            return self._py_f.read(size)

        buf = ctypes.create_string_buffer(size)
        bytes_read = wintypes.DWORD(0)
        ok = kernel32.ReadFile(self.handle, buf, size, ctypes.byref(bytes_read), None)
        if not ok:
            err = ctypes.get_last_error()
            raise OSError(f"ReadFile failed (Win32 error {err})")
        return buf.raw[: bytes_read.value]

    def write(self, data):
        if self._is_py_file:
            self._py_f.write(data)
            return

        if isinstance(data, (bytearray, memoryview)):
            data = bytes(data)
        elif not isinstance(data, bytes):
            data = bytes(data)

        bytes_written = wintypes.DWORD(0)
        ok = kernel32.WriteFile(self.handle, data, len(data), ctypes.byref(bytes_written), None)
        if not ok:
            err = ctypes.get_last_error()
            raise OSError(f"WriteFile failed: written {bytes_written.value}/{len(data)} (Win32 error {err})")

    def flush(self):
        if self._is_py_file:
            self._py_f.flush()
            return

        if self.handle and self.handle != wintypes.HANDLE(-1).value:
            kernel32.FlushFileBuffers(self.handle)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._is_py_file:
            if self._py_f:
                try:
                    self._py_f.flush()
                except Exception:
                    pass
                self._py_f.close()
                self._py_f = None
        else:
            if self.handle and self.handle != wintypes.HANDLE(-1).value:
                try:
                    kernel32.FlushFileBuffers(self.handle)
                except Exception:
                    pass
                kernel32.CloseHandle(self.handle)
                self.handle = None
