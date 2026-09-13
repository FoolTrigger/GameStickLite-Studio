"""
Raw Disk Image Writer Engine for Game Stick Lite.
Writes raw sector disk images (.img) directly to physical USB / SD card media.
Includes volume locking/dismounting and strict system disk protection.
"""

import os
import re
import time
import subprocess
from typing import Optional, Callable, Tuple

from .disk_lock import VolumeLockManager, Win32RawDisk

SECTOR_SIZE = 512
CHUNK_SIZE = 2 * 1024 * 1024  # 2 MiB buffer (sector-aligned)


class RawDiskWriter:
    @staticmethod
    def dismount_disk_volumes(disk_number: int):
        """Размонтирует все смонтированные тома на целевом диске для прямого посекторного доступа."""
        ps_cmd = (
            f"$parts = Get-Partition -DiskNumber {disk_number} -ErrorAction SilentlyContinue; "
            f"foreach ($p in $parts) {{ "
            f"  if ($p.DriveLetter) {{ "
            f"    $vol = Get-Volume -DriveLetter $p.DriveLetter -ErrorAction SilentlyContinue; "
            f"    if ($vol) {{ "
            f"      Dismount-Volume -DriveLetter $p.DriveLetter -Confirm:$false -ErrorAction SilentlyContinue "
            f"    }} "
            f"  }} "
            f"}}"
        )
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, timeout=8)
        except Exception:
            pass

    @staticmethod
    def write_image(
        image_path: str,
        target_device_id: str,
        is_system_disk: bool,
        progress_cb: Optional[Callable[[int, int, float, str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None
    ) -> Tuple[bool, str]:
        """
        Записывает raw-образ (.img) на физический накопитель (\\.\\PhysicalDriveX).
        Использует VolumeLockManager для блокировки всех томов и Win32RawDisk для секторной записи.
        """
        # 1. Строгая защита от записи на системные накопители
        if is_system_disk:
            return False, "ЗАПРЕЩЕНО: Попытка записи на системный диск Windows!"

        if not os.path.isfile(image_path):
            return False, f"Файл образа не найден: {image_path}"

        img_size = os.path.getsize(image_path)
        if img_size < 10 * 1024 * 1024:
            return False, f"Файл образа поврежден или слишком мал ({img_size} байт)."

        # 2. Определение номера физического диска
        m = re.search(r"physicaldrive(\d+)", target_device_id, re.IGNORECASE)
        disk_number = int(m.group(1)) if m else None

        if disk_number is not None:
            if progress_cb:
                progress_cb(0, img_size, 0.0, "Размонтирование и блокировка томов на накопителе...")
            RawDiskWriter.dismount_disk_volumes(disk_number)

        if progress_cb:
            progress_cb(0, img_size, 0.0, "Начало посекторной записи образа...")

        # 3. Потоковая запись с вычислением скорости и прогресса
        try:
            with VolumeLockManager(disk_number):
                with open(image_path, "rb") as src_f, Win32RawDisk(target_device_id, write_mode=True) as dst_f:
                    # Предварительно стираем первые 1 МБ (MBR и GPT), чтобы сбросить старую разметку в Windows
                    try:
                        dst_f.seek(0)
                        dst_f.write(b"\x00" * (1024 * 1024))
                        dst_f.seek(0)
                    except Exception:
                        pass

                    written_bytes = 0
                    start_time = time.time()
                    last_update_time = start_time

                    while True:
                        if cancel_check and cancel_check():
                            return False, "Запись отменена пользователем."

                        chunk = src_f.read(CHUNK_SIZE)
                        if not chunk:
                            break

                        # Выравнивание последнего чанка по границе сектора (512 байт)
                        if len(chunk) % SECTOR_SIZE != 0:
                            pad = SECTOR_SIZE - (len(chunk) % SECTOR_SIZE)
                            chunk = chunk + (b"\x00" * pad)

                        dst_f.write(chunk)
                        written_bytes += len(chunk)

                        now = time.time()
                        if now - last_update_time >= 0.25:
                            elapsed = max(now - start_time, 0.001)
                            speed_mb_s = (written_bytes / (1024 * 1024)) / elapsed
                            pct = min(100.0, (written_bytes / img_size) * 100.0)
                            rem_sec = max(0, int((img_size - written_bytes) / (speed_mb_s * 1024 * 1024 + 1)))
                            status = f"Запись: {pct:.1f}% ({written_bytes/(1024*1024):.1f}/{img_size/(1024*1024):.1f} МБ, {speed_mb_s:.1f} МБ/с, ост. ~{rem_sec}с)"
                            if progress_cb:
                                progress_cb(written_bytes, img_size, speed_mb_s, status)
                            last_update_time = now

                    dst_f.flush()

            # 4. Обновление таблицы разделов в Windows и авто-назначение буквы
            if disk_number is not None:
                if progress_cb:
                    progress_cb(img_size, img_size, 0.0, "Обновление таблицы разделов Windows...")
                try:
                    ps_update = (
                        f"Update-Disk -Number {disk_number} -ErrorAction SilentlyContinue; "
                        f"$part = Get-Partition -DiskNumber {disk_number} -ErrorAction SilentlyContinue | "
                        f"Where-Object {{ $_.PartitionNumber -eq 5 -or $_.GptType -eq '{{ebd0a0a2-b9e5-4433-87c0-68b6b72699c7}}' }}; "
                        f"if ($part -and -not $part.DriveLetter) {{ "
                        f"  $used = (Get-Volume).DriveLetter; "
                        f"  $free = [char[]](69..90) | Where-Object {{ $_ -notin $used }} | Select-Object -First 1; "
                        f"  if ($free) {{ "
                        f"    Set-Partition -DiskNumber {disk_number} -PartitionNumber $part.PartitionNumber -NewDriveLetter $free -ErrorAction SilentlyContinue; "
                        f"  }} "
                        f"}}"
                    )
                    subprocess.run(["powershell", "-NoProfile", "-Command", ps_update], capture_output=True, timeout=8)
                except Exception:
                    pass

            return True, f"Образ успешно записан на накопитель ({img_size / (1024*1024):.1f} МБ)!"

        except PermissionError:
            return False, "Отказано в доступе! Запустите программу от имени Администратора для записи на физический накопитель."
        except Exception as e:
            return False, f"Ошибка при записи образа на диск: {e}"
