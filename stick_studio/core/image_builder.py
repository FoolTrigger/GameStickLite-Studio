"""
Autonomous Raw Image Builder for Game Stick Lite.
Synthesizes a complete, bootable .img with all 5 partitions:
- uboot (Rockchip IDB Loader)
- trust (OP-TEE / TOS)
- boot (Kernel zImage + DTB for selected revision)
- rootfs (SquashFS 4.0 Linux runtime)
- userdata (Minimal exFAT with 1 single minimal theme and clean games.db)

Completely standalone and independent of any original monolithic image.
"""

import os
import math
import uuid
import struct
import zlib
from typing import Optional, Callable, Tuple, Dict, Any

from stick_studio.core.image_engine import KNOWN_REVISIONS
from stick_studio.core.rom_engine import RomManager, PLATFORMS

SECTOR_SIZE = 512
CLUSTER_SIZE = 128 * 1024  # 128 KiB clusters
SECTORS_PER_CLUSTER = CLUSTER_SIZE // SECTOR_SIZE  # 256
BYTES_PER_SECTOR_SHIFT = 9
SECTORS_PER_CLUSTER_SHIFT = 8

# LBA разметка Game Stick Lite
LBA_IDBLOADER_START = 64    # 32 KiB (Rockchip BootROM IDBlock & DDR Miniloader)

LBA_UBOOT_START = 8192      # 4 MiB
LBA_UBOOT_END = 10239       # 1 MiB

LBA_TRUST_START = 10240     # 5 MiB
LBA_TRUST_END = 14335       # 2 MiB

LBA_BOOT_START = 14336      # 7 MiB
LBA_BOOT_END = 32767        # 9 MiB

LBA_ROOTFS_START = 32768    # 16 MiB
LBA_ROOTFS_END = 212767     # 87.89 MiB (180,000 sectors)

LBA_USERDATA_START = 212768 # 103.89 MiB

# Точные аппаратные GUID типов разделов Rockchip
GUID_RK_UBOOT = uuid.UUID("59F4FFFF-0000-4C7E-8000-015E00004DB7")
GUID_RK_TRUST = uuid.UUID("2B91FFFF-0000-457F-8000-220D000030DB")
GUID_RK_BOOT = uuid.UUID("B0B3FFFF-0000-4049-8000-36C40000603B")
GUID_RK_ROOTFS = uuid.UUID("7F96FFFF-0000-4568-8000-5DEA000057BF")
GUID_BASIC_DATA = uuid.UUID("EBD0A0A2-B9E5-4433-87C0-68B6B72699C7")

# Аппаратные PARTUUID, требуемые ядром Linux (cmdline root=PARTUUID=614e...)
PARTUUID_UBOOT = uuid.UUID("12390000-0000-4B64-8000-700300004538")
PARTUUID_TRUST = uuid.UUID("9A010000-0000-484B-8000-3E4900003A7D")
PARTUUID_BOOT = uuid.UUID("223E0000-0000-4B12-8000-58CA00007B72")
PARTUUID_ROOTFS = uuid.UUID("614E0000-0000-4B53-8000-1D28000054A9")
PARTUUID_USERDATA = uuid.UUID("D7440000-0000-4354-8000-0E1E0000240C")

# Заводской Disk GUID
DISK_GUID = uuid.UUID("23000000-0000-4C4A-8000-699000005ABB")


def exfat_vbr_checksum(data: bytes) -> int:
    checksum = 0
    for i, b in enumerate(data):
        if i in (106, 107, 112):
            continue
        checksum = (((checksum << 31) | (checksum >> 1)) + b) & 0xFFFFFFFF
    return checksum


class StandaloneImageBuilder:
    """Генератор автономного готового к прошивке образа .img."""

    @staticmethod
    def build_minimal_image(
        output_img_path: str,
        revision_crc: str = "06D64343",
        userdata_size_mib: int = 300,
        extra_roms_dir: Optional[str] = None,
        progress_cb: Optional[Callable[[str, int], None]] = None
    ) -> Tuple[bool, str]:
        """
        Собирает чистый, минимальный, готовый к записи на SD-карту образ .img.
        """
        base_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "firmware_base")
        idbloader_bin = os.path.join(base_dir, "idbloader.bin")
        uboot_bin = os.path.join(base_dir, "uboot.img")
        trust_bin = os.path.join(base_dir, "trust.img")
        rootfs_bin = os.path.join(base_dir, "rootfs.img")
        upcase_bin = os.path.join(base_dir, "upcase.bin")
        template_dir = os.path.join(base_dir, "userdata_template")

        # Проверка наличия базовых компонентов
        for req_f in (idbloader_bin, uboot_bin, trust_bin, rootfs_bin, upcase_bin):
            if not os.path.isfile(req_f):
                return False, f"Отсутствует системный компонент сборщика: {os.path.basename(req_f)}"

        rev_info = KNOWN_REVISIONS.get(revision_crc)
        if not rev_info or not rev_info.get("file"):
            return False, f"Неизвестная ревизия ядра: {revision_crc}"

        boot_bin = os.path.join(base_dir, "boots", rev_info["file"])
        if not os.path.isfile(boot_bin):
            return False, f"Файл ядра ревизии не найден: {rev_info['file']}"

        if progress_cb:
            progress_cb("Подготовка структуры разделов...", 10)

        # Создание временного каталога для userdata
        temp_userdata_dir = os.path.join(os.path.dirname(output_img_path), "_temp_build_userdata")
        if os.path.exists(temp_userdata_dir):
            import shutil
            shutil.rmtree(temp_userdata_dir, ignore_errors=True)
        os.makedirs(temp_userdata_dir, exist_ok=True)

        try:
            # Копируем системные шаблоны
            import shutil
            for item in os.listdir(template_dir):
                s = os.path.join(template_dir, item)
                d = os.path.join(temp_userdata_dir, item)
                if os.path.isdir(s):
                    shutil.copytree(s, d)
                else:
                    shutil.copy2(s, d)

            # Создаем структуру /game/
            game_dir = os.path.join(temp_userdata_dir, "game")
            os.makedirs(game_dir, exist_ok=True)
            for p_key in PLATFORMS.keys():
                os.makedirs(os.path.join(game_dir, p_key), exist_ok=True)

            # Если указана папка с играми, копируем их
            if extra_roms_dir and os.path.isdir(extra_roms_dir):
                if progress_cb:
                    progress_cb("Интеграция игр пользователя...", 20)
                for root, _, files in os.walk(extra_roms_dir):
                    rel = os.path.relpath(root, extra_roms_dir)
                    target_p = os.path.join(game_dir, rel)
                    os.makedirs(target_p, exist_ok=True)
                    for f in files:
                        shutil.copy2(os.path.join(root, f), os.path.join(target_p, f))

            # Защита всех категорий от краша в меню Class при пустых папках
            RomManager.ensure_category_safeguards(game_dir)

            # Компилируем чистую базу games.db
            if progress_cb:
                progress_cb("Компиляция базы данных игр...", 35)
            rom_mgr = RomManager(game_dir)
            rom_mgr.scan()
            rom_mgr.build_games_db(output_dir=game_dir)

            # Дублируем базу games.db и database.sqlite3 в корень userdata для гарантированной совместимости
            built_db = os.path.join(game_dir, "games.db")
            if os.path.isfile(built_db):
                shutil.copy2(built_db, os.path.join(temp_userdata_dir, "games.db"))
            built_sqlite = os.path.join(game_dir, "database.sqlite3")
            if os.path.isfile(built_sqlite):
                shutil.copy2(built_sqlite, os.path.join(temp_userdata_dir, "database.sqlite3"))

            # Расчет точной геометрии GPT с учетом размера контента и запаса кластеров
            total_content_bytes = 0
            file_count = 0
            for root, _, files in os.walk(temp_userdata_dir):
                for f in files:
                    file_count += 1
                    total_content_bytes += os.path.getsize(os.path.join(root, f))

            min_needed_mib = math.ceil((total_content_bytes + file_count * 128 * 1024 + 64 * 1024 * 1024) / (1024 * 1024))
            actual_userdata_mib = max(userdata_size_mib, min_needed_mib, 500)
            userdata_sectors = (actual_userdata_mib * 1024 * 1024) // SECTOR_SIZE
            lba_userdata_end = LBA_USERDATA_START + userdata_sectors - 1

            total_sectors = lba_userdata_end + 34
            lba_last_usable = lba_userdata_end
            lba_backup_entries = total_sectors - 33
            lba_backup_header = total_sectors - 1
            total_img_size = total_sectors * SECTOR_SIZE

            # 3. Синтез exFAT раздела
            if progress_cb:
                progress_cb("Сборка файловой системы exFAT...", 50)

            userdata_bin = os.path.join(os.path.dirname(output_img_path), "_temp_userdata.bin")
            from stick_studio.core.exfat_engine import PurePythonExfatBuilder
            exfat_builder = PurePythonExfatBuilder(volume_size_bytes=userdata_sectors * SECTOR_SIZE)
            exfat_builder.add_directory_tree(temp_userdata_dir)
            exfat_builder.write_image(userdata_bin)

            # 4. Сборка полного .img файла
            if progress_cb:
                progress_cb("Запись разделов в итоговый образ...", 70)

            # Подготовка MBR и GPT
            # MBR
            mbr = bytearray(512)
            mbr[510:512] = b'\x55\xaa'
            # Partition 1: 0xEE (GPT Protective)
            struct.pack_into('<B3sB3sII', mbr, 446, 0, b'\x00\x02\x00', 0xEE, b'\xff\xff\xff', 1, min(total_sectors - 1, 0xFFFFFFFF))

            # GPT Entries (128 записей по 128 байт = 16,384 байт)
            entries_raw = bytearray(128 * 128)

            def make_gpt_entry(index: int, name: str, start_lba: int, end_lba: int, type_guid: uuid.UUID, part_guid: uuid.UUID):
                off = (index - 1) * 128
                entries_raw[off:off+16] = type_guid.bytes_le
                entries_raw[off+16:off+32] = part_guid.bytes_le
                struct.pack_into('<QQQ', entries_raw, off+32, start_lba, end_lba, 0)
                name_bytes = name.encode('utf-16le')
                entries_raw[off+56 : off+56+len(name_bytes)] = name_bytes

            make_gpt_entry(1, "uboot", LBA_UBOOT_START, LBA_UBOOT_END, GUID_RK_UBOOT, PARTUUID_UBOOT)
            make_gpt_entry(2, "trust", LBA_TRUST_START, LBA_TRUST_END, GUID_RK_TRUST, PARTUUID_TRUST)
            make_gpt_entry(3, "boot", LBA_BOOT_START, LBA_BOOT_END, GUID_RK_BOOT, PARTUUID_BOOT)
            make_gpt_entry(4, "rootfs", LBA_ROOTFS_START, LBA_ROOTFS_END, GUID_RK_ROOTFS, PARTUUID_ROOTFS)
            make_gpt_entry(5, "userdata", LBA_USERDATA_START, lba_userdata_end, GUID_BASIC_DATA, PARTUUID_USERDATA)

            entries_crc = zlib.crc32(entries_raw)
            disk_guid = DISK_GUID

            # Primary GPT Header (LBA 1)
            primary_hdr = bytearray(512)
            primary_hdr[0:8] = b'EFI PART'
            struct.pack_into('<IIIIQQQQ16sQII', primary_hdr, 8,
                0x00010000, 92, 0, 0,
                1, lba_backup_header,
                34, lba_last_usable,
                disk_guid.bytes_le,
                2, 128, 128
            )
            struct.pack_into('<I', primary_hdr, 88, entries_crc)
            primary_crc = zlib.crc32(primary_hdr[:92])
            struct.pack_into('<I', primary_hdr, 16, primary_crc)

            # Backup GPT Header (LBA N-1)
            backup_hdr = bytearray(primary_hdr)
            struct.pack_into('<Q', backup_hdr, 24, lba_backup_header)
            struct.pack_into('<Q', backup_hdr, 32, 1)
            struct.pack_into('<Q', backup_hdr, 72, lba_backup_entries)
            struct.pack_into('<I', backup_hdr, 16, 0)
            backup_crc = zlib.crc32(backup_hdr[:92])
            struct.pack_into('<I', backup_hdr, 16, backup_crc)

            # Потоковая запись в файл .img
            with open(output_img_path, 'wb') as img_f:
                # 0. MBR
                img_f.write(mbr)
                # 1. Primary GPT Header
                img_f.write(primary_hdr)
                # 2..33. Primary Entries
                img_f.write(entries_raw)

                # Запись разделов по их точным LBA
                def write_file_at_lba(file_path: str, start_lba: int):
                    img_f.seek(start_lba * SECTOR_SIZE)
                    with open(file_path, 'rb') as f_in:
                        while chunk := f_in.read(1024 * 1024):
                            img_f.write(chunk)

                # Rockchip BootROM Miniloader (LBA 64..277)
                write_file_at_lba(idbloader_bin, LBA_IDBLOADER_START)

                # Подготовка версии прошивки с информацией о ревизии стика
                l1 = " Game Stick Lite\n"
                l2 = " Custom Firmware v2.0\n"
                name_clean = rev_info.get("name", revision_crc).split("(")[0].strip()
                prefix = " HW: "
                suffix = f" [{revision_crc}]\n"
                avail = 90 - len(l1) - len(l2) - len(prefix) - len(suffix)
                if len(name_clean) > avail:
                    name_clean = name_clean[:avail - 1] + "~"
                pad = avail - len(name_clean)
                l3 = prefix + name_clean + (" " * pad) + suffix
                ver_bytes = (l1 + l2 + l3).encode("utf-8")[:90]

                # Системные разделы
                write_file_at_lba(uboot_bin, LBA_UBOOT_START)
                write_file_at_lba(trust_bin, LBA_TRUST_START)
                write_file_at_lba(boot_bin, LBA_BOOT_START)

                # Запись rootfs с обновленной версией под ревизию стика
                img_f.seek(LBA_ROOTFS_START * SECTOR_SIZE)
                with open(rootfs_bin, 'rb') as rf:
                    rf_data = bytearray(rf.read())
                    old_mark = b' Game Stick Lite\n OpenWorld\nv0.90plus ML_Store Edition by ruDronga (https://stick-ow.pro)\n'
                    idx = rf_data.find(old_mark)
                    if idx != -1:
                        rf_data[idx:idx+90] = ver_bytes
                    else:
                        idx2 = rf_data.find(b' Game Stick Lite\n Custom Firmware v2.0\n')
                        if idx2 != -1:
                            rf_data[idx2:idx2+90] = ver_bytes
                    img_f.write(rf_data)

                write_file_at_lba(userdata_bin, LBA_USERDATA_START)

                # Backup Entries
                img_f.seek(lba_backup_entries * SECTOR_SIZE)
                img_f.write(entries_raw)

                # Backup Header
                img_f.seek(lba_backup_header * SECTOR_SIZE)
                img_f.write(backup_hdr)

            if progress_cb:
                progress_cb("Готово!", 100)

            final_size_mb = os.path.getsize(output_img_path) / (1024 * 1024)
            return True, f"Автономный образ успешно создан: {output_img_path} ({final_size_mb:.1f} МБ)"

        except Exception as e:
            return False, f"Ошибка при сборке образа: {e}"

        finally:
            # Очистка временных файлов
            import shutil
            shutil.rmtree(temp_userdata_dir, ignore_errors=True)
            userdata_tmp = os.path.join(os.path.dirname(output_img_path), "_temp_userdata.bin")
            if os.path.exists(userdata_tmp):
                try:
                    os.remove(userdata_tmp)
                except Exception:
                    pass
