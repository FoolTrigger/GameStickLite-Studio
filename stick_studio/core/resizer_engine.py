"""
In-Place Partition Resizer Engine for Game Stick Lite.
Expands userdata (exFAT) partition to fill the entire physical SD card capacity
without formatting and without data loss.
"""

import os
import math
import struct
import zlib
from typing import Tuple

from .disk_lock import VolumeLockManager, Win32RawDisk

SECTOR_SIZE = 512
USERDATA_PART_INDEX = 5
USERDATA_START_LBA = 212768
USERDATA_OFFSET_BYTES = USERDATA_START_LBA * SECTOR_SIZE


def calculate_exfat_vbr_checksum(vbr_11_sectors: bytes) -> int:
    """
    Вычисляет 32-битную контрольную сумму первых 11 секторов VBR exFAT.
    Байты VolumeFlags (106, 107) и PercentInUse (112) пропускаются.
    """
    checksum = 0
    for idx, b in enumerate(vbr_11_sectors):
        if idx in (106, 107, 112):
            continue
        checksum = (((checksum & 1) << 31) | (checksum >> 1)) + b
        checksum &= 0xFFFFFFFF
    return checksum


class PartitionResizer:
    @staticmethod
    def expand_device(target_device_or_file: str, total_device_size_bytes: int) -> Tuple[bool, str]:
        """
        Выполняет In-Place расширение 5-го раздела (userdata, exFAT)
        на весь доступный объём физического накопителя или образа.
        """
        total_sectors = total_device_size_bytes // SECTOR_SIZE
        if total_sectors < USERDATA_START_LBA + 65536:
            return False, f"Целевой накопитель слишком мал: {total_device_size_bytes / (1024*1024):.1f} MiB"

        new_backup_lba = total_sectors - 1
        new_last_usable_lba = total_sectors - 34
        new_backup_entries_lba = total_sectors - 33

        if new_last_usable_lba <= USERDATA_START_LBA:
            return False, "Недостаточно пространства для расширения раздела."

        try:
            with VolumeLockManager(target_device_or_file):
                with Win32RawDisk(target_device_or_file, write_mode=True) as f:
                    # 1. ОБНОВЛЕНИЕ ТАБЛИЦЫ GPT
                    f.seek(SECTOR_SIZE)
                    gpt_hdr = bytearray(f.read(SECTOR_SIZE))
                    if gpt_hdr[:8] != b"EFI PART":
                        return False, "Заголовок GPT не обнаружен"

                    hdr_size = struct.unpack("<I", gpt_hdr[12:16])[0]
                    num_parts = struct.unpack("<I", gpt_hdr[80:84])[0]
                    part_entry_size = struct.unpack("<I", gpt_hdr[84:88])[0]
                    entries_bytes_total = num_parts * part_entry_size

                    f.seek(2 * SECTOR_SIZE)
                    entries_data = bytearray(f.read(entries_bytes_total))

                    p5_offset = (USERDATA_PART_INDEX - 1) * part_entry_size
                    p5_entry = entries_data[p5_offset:p5_offset + part_entry_size]

                    p5_start_lba, p5_old_end_lba = struct.unpack("<QQ", p5_entry[32:48])
                    if p5_start_lba != USERDATA_START_LBA:
                        return False, f"Неожиданный Start LBA раздела: {p5_start_lba}"

                    # Обновляем End LBA
                    struct.pack_into("<Q", entries_data, p5_offset + 40, new_last_usable_lba)
                    new_part_sectors = new_last_usable_lba - USERDATA_START_LBA + 1

                    new_entries_crc = zlib.crc32(entries_data)

                    # Primary Header
                    struct.pack_into("<Q", gpt_hdr, 32, new_backup_lba)
                    struct.pack_into("<Q", gpt_hdr, 48, new_last_usable_lba)
                    struct.pack_into("<I", gpt_hdr, 88, new_entries_crc)
                    struct.pack_into("<I", gpt_hdr, 16, 0)
                    new_hdr_crc = zlib.crc32(gpt_hdr[:hdr_size])
                    struct.pack_into("<I", gpt_hdr, 16, new_hdr_crc)

                    f.seek(SECTOR_SIZE)
                    f.write(gpt_hdr)
                    f.seek(2 * SECTOR_SIZE)
                    f.write(entries_data)

                    # Backup GPT
                    f.seek(new_backup_entries_lba * SECTOR_SIZE)
                    f.write(entries_data)

                    backup_hdr = bytearray(gpt_hdr)
                    struct.pack_into("<Q", backup_hdr, 24, new_backup_lba)
                    struct.pack_into("<Q", backup_hdr, 32, 1)
                    struct.pack_into("<Q", backup_hdr, 72, new_backup_entries_lba)
                    struct.pack_into("<I", backup_hdr, 16, 0)
                    new_backup_crc = zlib.crc32(backup_hdr[:hdr_size])
                    struct.pack_into("<I", backup_hdr, 16, new_backup_crc)

                    f.seek(new_backup_lba * SECTOR_SIZE)
                    f.write(backup_hdr)

                    # 2. РАСШИРЕНИЕ EXFAT ФАЙЛОВОЙ СИСТЕМЫ
                    f.seek(USERDATA_OFFSET_BYTES)
                    vbr_raw = bytearray(f.read(12 * SECTOR_SIZE))
                    if vbr_raw[3:11] != b"EXFAT   ":
                        return False, "Сигнатура EXFAT не найдена в начале раздела"

                    fat_offset_sec = struct.unpack("<I", vbr_raw[80:84])[0]
                    old_fat_len_sec = struct.unpack("<I", vbr_raw[84:88])[0]
                    cluster_heap_offset_sec = struct.unpack("<I", vbr_raw[88:92])[0]
                    sec_per_cluster_shift = vbr_raw[109]
                    sec_per_cluster = 1 << sec_per_cluster_shift
                    root_clus = struct.unpack("<I", vbr_raw[96:100])[0]

                    new_volume_length = new_part_sectors
                    new_cluster_count = (new_volume_length - cluster_heap_offset_sec) // sec_per_cluster
                    max_fat_sec = cluster_heap_offset_sec - fat_offset_sec
                    new_fat_len_sec = math.ceil((new_cluster_count + 2) * 4 / SECTOR_SIZE)
                    if new_fat_len_sec > max_fat_sec:
                        new_fat_len_sec = max_fat_sec
                        new_cluster_count = (new_fat_len_sec * SECTOR_SIZE // 4) - 2
                        new_volume_length = cluster_heap_offset_sec + new_cluster_count * sec_per_cluster

                    struct.pack_into("<Q", vbr_raw, 72, new_volume_length)
                    struct.pack_into("<I", vbr_raw, 84, new_fat_len_sec)
                    struct.pack_into("<I", vbr_raw, 92, new_cluster_count)

                    new_vbr_checksum = calculate_exfat_vbr_checksum(bytes(vbr_raw[:11 * SECTOR_SIZE]))
                    checksum_sector = struct.pack("<I", new_vbr_checksum) * 128
                    vbr_raw[11 * SECTOR_SIZE:12 * SECTOR_SIZE] = checksum_sector

                    # 3. ОБНУЛЕНИЕ НОВЫХ СЕКТОРОВ ТАБЛИЦЫ FAT
                    if new_fat_len_sec > old_fat_len_sec:
                        zero_fat_offset = USERDATA_OFFSET_BYTES + (fat_offset_sec + old_fat_len_sec) * SECTOR_SIZE
                        zero_fat_bytes = (new_fat_len_sec - old_fat_len_sec) * SECTOR_SIZE
                        f.seek(zero_fat_offset)
                        f.write(b"\x00" * zero_fat_bytes)

                    # 4. ОБНОВЛЕНИЕ ДЛИНЫ ALLOCATION BITMAP В ROOT DIRECTORY И ОБНУЛЕНИЕ НОВЫХ БИТОВ
                    root_offset = USERDATA_OFFSET_BYTES + (cluster_heap_offset_sec + (root_clus - 2) * sec_per_cluster) * SECTOR_SIZE
                    f.seek(root_offset)
                    root_buf = bytearray(f.read(sec_per_cluster * SECTOR_SIZE))
                    new_bitmap_len = math.ceil(new_cluster_count / 8)

                    for i in range(0, min(len(root_buf), 4096), 32):
                        if root_buf[i] == 0x81:  # Allocation Bitmap Entry
                            bitmap_cluster = struct.unpack("<I", root_buf[i + 20:i + 24])[0]
                            old_bitmap_len = struct.unpack("<Q", root_buf[i + 24:i + 32])[0]
                            struct.pack_into("<Q", root_buf, i + 24, new_bitmap_len)
                            f.seek(root_offset)
                            f.write(root_buf)

                            # Обнуляем новые байты битмапа, чтобы все добавленные кластеры были свободными
                            if new_bitmap_len > old_bitmap_len:
                                bmp_offset = USERDATA_OFFSET_BYTES + (cluster_heap_offset_sec + (bitmap_cluster - 2) * sec_per_cluster) * SECTOR_SIZE
                                f.seek(bmp_offset + old_bitmap_len)
                                f.write(b"\x00" * (new_bitmap_len - old_bitmap_len))
                            break

                    # 5. ЗАПИСЬ ОБНОВЛЕННЫХ VBR (PRIMARY И BACKUP)
                    f.seek(USERDATA_OFFSET_BYTES)
                    f.write(vbr_raw)
                    f.seek(USERDATA_OFFSET_BYTES + 12 * SECTOR_SIZE)
                    f.write(vbr_raw)

                    f.flush()

            # 5. УВЕДОМЛЕНИЕ WINDOWS И АВТОМАТИЧЕСКОЕ НАЗНАЧЕНИЕ БУКВЫ ДИСКА
            assigned_letter = ""
            if target_device_or_file.lower().startswith("\\\\.\\physicaldrive"):
                try:
                    import subprocess
                    import re
                    m = re.search(r"physicaldrive(\d+)", target_device_or_file, re.IGNORECASE)
                    if m:
                        d_num = m.group(1)
                        ps_script = (
                            f"Update-Disk -Number {d_num} -ErrorAction SilentlyContinue; "
                            f"$part = Get-Partition -DiskNumber {d_num} -ErrorAction SilentlyContinue | "
                            f"Where-Object {{ $_.PartitionNumber -eq 5 -or $_.GptType -eq '{{ebd0a0a2-b9e5-4433-87c0-68b6b72699c7}}' }}; "
                            f"if ($part -and -not $part.DriveLetter) {{ "
                            f"  $used = (Get-Volume).DriveLetter; "
                            f"  $free = [char[]](69..90) | Where-Object {{ $_ -notin $used }} | Select-Object -First 1; "
                            f"  if ($free) {{ "
                            f"    Set-Partition -DiskNumber {d_num} -PartitionNumber $part.PartitionNumber -NewDriveLetter $free; "
                            f"    Write-Output $free "
                            f"  }} "
                            f"}}"
                        )
                        res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_script], capture_output=True, text=True, timeout=10)
                        out = res.stdout.strip()
                        if out and len(out) == 1 and out.isalpha():
                            assigned_letter = f" (назначен диск {out.upper()}:)"
                except Exception:
                    pass

            new_size_gib = (new_part_sectors * SECTOR_SIZE) / (1024 ** 3)
            return True, f"Раздел успешно расширен до {new_size_gib:.2f} GiB ({new_cluster_count:,} кластеров)!{assigned_letter}"

        except PermissionError:
            return False, "Отказано в доступе! Запустите программу от имени Администратора."
        except Exception as e:
            return False, f"Ошибка расширения раздела: {e}"
