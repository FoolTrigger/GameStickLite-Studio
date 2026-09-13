"""
Disk Detection & Partition Information Engine for Game Stick Lite.
Safely detects removable SD cards and drives, with strict system drive protection.
"""

import os
import sys
import subprocess
import json
from typing import List, Dict, Optional, Tuple

SECTOR_SIZE = 512
USERDATA_START_LBA = 212768


class DiskInfo:
    def __init__(self, device_id: str, name: str, size_bytes: int, is_removable: bool, is_system: bool, mountpoints: List[str]):
        self.device_id = device_id
        self.name = name
        self.size_bytes = size_bytes
        self.size_gib = size_bytes / (1024 ** 3)
        self.is_removable = is_removable
        self.is_system = is_system
        self.mountpoints = mountpoints

    def display_str(self) -> str:
        tag = "[СИСТЕМНЫЙ - ЗАПРЕЩЕН]" if self.is_system else ("[СЪЕМНЫЙ SD/USB]" if self.is_removable else "[ФИКСИРОВАННЫЙ]")
        mounts = f" ({', '.join(self.mountpoints)})" if self.mountpoints else ""
        return f"{self.device_id} - {self.name} [{self.size_gib:.1f} GiB] {tag}{mounts}"


class DiskDetector:
    @staticmethod
    def get_available_disks() -> List[DiskInfo]:
        if sys.platform == "win32":
            return DiskDetector._get_windows_disks()
        else:
            return DiskDetector._get_linux_disks()

    @staticmethod
    def _get_windows_disks() -> List[DiskInfo]:
        disks = []
        ps_cmd = (
            'Get-CimInstance Win32_DiskDrive | ForEach-Object { '
            '$disk = $_; '
            '$partitions = Get-CimInstance -Query "ASSOCIATORS OF {Win32_DiskDrive.DeviceID=\'$($disk.DeviceID)\'} WHERE AssocClass = Win32_DiskDriveToDiskPartition"; '
            '$drives = $partitions | ForEach-Object { Get-CimInstance -Query "ASSOCIATORS OF {Win32_DiskPartition.DeviceID=\'$($_.DeviceID)\'} WHERE AssocClass = Win32_LogicalDiskToPartition" }; '
            '[PSCustomObject]@{ '
            'DeviceID = $disk.DeviceID; '
            'Model = $disk.Model; '
            'Size = [int64]$disk.Size; '
            'InterfaceType = $disk.InterfaceType; '
            'MediaType = $disk.MediaType; '
            'Letters = ($drives | ForEach-Object { $_.DeviceID }) '
            '} } | ConvertTo-Json -Compress'
        )

        try:
            p = subprocess.Popen(["powershell", "-NoProfile", "-Command", ps_cmd], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            stdout, _ = p.communicate(timeout=8)
            if not stdout.strip():
                return disks

            data = json.loads(stdout)
            if isinstance(data, dict):
                data = [data]

            system_drive = os.environ.get("SystemDrive", "C:").upper()

            for item in data:
                dev_id = item.get("DeviceID", "")
                model = item.get("Model", "Generic Storage")
                size = int(item.get("Size") or 0)
                if size <= 0:
                    continue

                letters = item.get("Letters") or []
                if isinstance(letters, str):
                    letters = [letters]

                is_system = any(l.upper().startswith(system_drive) for l in letters)
                iface = (item.get("InterfaceType") or "").upper()
                media = (item.get("MediaType") or "").upper()
                is_removable = ("USB" in iface) or ("REMOVABLE" in media) or ("SD" in model.upper())

                disks.append(DiskInfo(
                    device_id=dev_id,
                    name=model,
                    size_bytes=size,
                    is_removable=is_removable,
                    is_system=is_system,
                    mountpoints=letters
                ))
        except Exception:
            pass

        return disks

    @staticmethod
    def _get_linux_disks() -> List[DiskInfo]:
        disks = []
        try:
            p = subprocess.Popen(["lsblk", "-J", "-b", "-o", "NAME,PATH,RM,RO,TYPE,SIZE,MODEL,MOUNTPOINT"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            stdout, _ = p.communicate(timeout=5)
            data = json.loads(stdout)

            for dev in data.get("blockdevices", []):
                if dev.get("type") != "disk":
                    continue
                path = dev.get("path", "")
                size = int(dev.get("size") or 0)
                model = (dev.get("model") or "Generic Drive").strip()
                removable = bool(dev.get("rm", False))

                mounts = []
                is_system = False
                if dev.get("mountpoint") in ["/", "/boot", "/home"]:
                    is_system = True

                for part in dev.get("children", []):
                    mp = part.get("mountpoint")
                    if mp:
                        mounts.append(mp)
                        if mp in ["/", "/boot", "/home"]:
                            is_system = True

                disks.append(DiskInfo(
                    device_id=path,
                    name=model,
                    size_bytes=size,
                    is_removable=removable,
                    is_system=is_system,
                    mountpoints=mounts
                ))
        except Exception:
            pass

        return disks
