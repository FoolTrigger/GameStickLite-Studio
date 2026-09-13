"""
Image Engine: GPT Parser and Kernel Revision Manager for Game Stick Lite.
No external dependencies, pure Python binary structures.
"""

import os
import struct
import uuid
import zlib
from typing import Dict, List, Optional, Any, Tuple

SECTOR_SIZE = 512
GPT_HEADER_OFFSET = 512
BOOT_LBA_OFFSET = 14336
BOOT_PARTITION_OFFSET = BOOT_LBA_OFFSET * SECTOR_SIZE  # 7,340,032 bytes
BOOT_PARTITION_SIZE = 18432 * SECTOR_SIZE             # 9,437,184 bytes (9 MiB)

# Аппаратные ревизии консолей Game Stick Lite
KNOWN_REVISIONS: Dict[str, Dict[str, str]] = {
    "E03DD26C": {
        "name": "Game Stick v7.2 / v7.0 / v20 / M15",
        "description": "Ядро для плат v7.2, v7.0, v20 и M15 (основной рабочий вариант).",
        "file": "E03DD26C.bin",
        "label_suffix": "v7"
    },
    "C3C4FF9D": {
        "name": "Game Stick v7.1 (вариант 1)",
        "description": "Специфическое ядро для системных плат v7.1.",
        "file": "C3C4FF9D.bin",
        "label_suffix": "v7.1"
    },
    "8EA2F36A": {
        "name": "Game Stick v4 (SEGAM-M8-V4.0)",
        "description": "Ревизия под системные платы SEGAM-M8-V4.0.",
        "file": "8EA2F36A.bin",
        "label_suffix": "v4"
    },
    "FD822C5B": {
        "name": "Game Stick v5 (базовая ревизия)",
        "description": "Базовое ядро для плат v5.0.",
        "file": "FD822C5B.bin",
        "label_suffix": "v5"
    },
    "C576C39A": {
        "name": "Game Stick v5 (2-й вариант геймпадов)",
        "description": "Альтернативный чип беспроводного контроллера для v5.0.",
        "file": "C576C39A.bin",
        "label_suffix": "v5_2"
    },
    "06D64343": {
        "name": "Game Stick v20 (альтернативное ядро)",
        "description": "Альтернативное ядро с расширенной поддержкой USB HID.",
        "file": "06D64343.bin",
        "label_suffix": "v20"
    }
}


class GPTManager:
    """Парсер и менеджер разметки GPT."""

    @staticmethod
    def read_partitions(image_path: str) -> List[Dict[str, Any]]:
        if not os.path.isfile(image_path):
            raise FileNotFoundError(f"Файл не найден: {image_path}")

        with open(image_path, "rb") as f:
            f.seek(0)
            mbr = f.read(SECTOR_SIZE)
            if struct.unpack("<H", mbr[510:512])[0] != 0xAA55:
                raise ValueError("Некорректная сигнатура MBR")

            f.seek(GPT_HEADER_OFFSET)
            hdr = f.read(SECTOR_SIZE)
            if hdr[:8] != b"EFI PART":
                raise ValueError("Не найден заголовок GPT")

            _, _, _, _, current_lba, _, _, _, disk_guid, part_lba, num_parts, part_size = (
                struct.unpack("<IIIIQQQQ16sQII", hdr[8:88])
            )

            f.seek(part_lba * SECTOR_SIZE)
            partitions = []
            for i in range(num_parts):
                entry = f.read(part_size)
                type_guid = uuid.UUID(bytes_le=entry[:16])
                if str(type_guid) == "00000000-0000-0000-0000-000000000000":
                    continue

                part_guid = uuid.UUID(bytes_le=entry[16:32])
                start_lba, end_lba, flags = struct.unpack("<QQQ", entry[32:56])
                name = entry[56:128].decode("utf-16le", errors="ignore").strip("\x00")
                sectors = end_lba - start_lba + 1

                partitions.append({
                    "index": i + 1,
                    "name": name,
                    "type_guid": str(type_guid),
                    "part_guid": str(part_guid),
                    "start_lba": start_lba,
                    "end_lba": end_lba,
                    "offset_bytes": start_lba * SECTOR_SIZE,
                    "size_bytes": sectors * SECTOR_SIZE,
                    "size_mib": (sectors * SECTOR_SIZE) / (1024 * 1024),
                })
            return partitions


class BootRevisionManager:
    """Управление подменой ядра под аппаратную ревизию."""

    @staticmethod
    def inspect_boot(image_or_dev_path: str) -> Dict[str, Any]:
        with open(image_or_dev_path, "rb") as f:
            f.seek(BOOT_PARTITION_OFFSET)
            boot_data = f.read(BOOT_PARTITION_SIZE)
            if len(boot_data) != BOOT_PARTITION_SIZE:
                raise ValueError("Не удалось прочитать раздел boot")

            crc32_val = f"{zlib.crc32(boot_data):08X}"
            magic = boot_data[:8]
            is_valid = (magic == b"ANDROID!")

            k_sz = 0
            if is_valid:
                k_sz = struct.unpack("<I", boot_data[8:12])[0]

            rev_info = KNOWN_REVISIONS.get(crc32_val, {
                "name": f"Пользовательское ядро ({crc32_val})",
                "description": "Нестандартная модификация ядра",
                "file": None
            })

            return {
                "crc32": crc32_val,
                "is_valid": is_valid,
                "kernel_size": k_sz,
                "revision_name": rev_info["name"],
                "description": rev_info["description"],
                "matched": crc32_val in KNOWN_REVISIONS
            }

    @staticmethod
    def switch_revision(target_path: str, boot_bin_path: str) -> Tuple[bool, str]:
        if not os.path.exists(boot_bin_path):
            return False, f"Файл {boot_bin_path} не найден"

        if os.path.getsize(boot_bin_path) != BOOT_PARTITION_SIZE:
            return False, "Некорректный размер файла ядра"

        with open(boot_bin_path, "rb") as bf:
            new_data = bf.read()

        new_crc = f"{zlib.crc32(new_data):08X}"

        try:
            with open(target_path, "r+b") as dst_f:
                dst_f.seek(BOOT_PARTITION_OFFSET)
                dst_f.write(new_data)
                dst_f.flush()
            return True, f"Ревизия успешно установлена (CRC32: {new_crc})"
        except Exception as e:
            return False, f"Ошибка записи ядра: {e}"

    @staticmethod
    def patch_revision(target_path: str, revision_crc: str) -> Tuple[bool, str]:
        """
        Патчит образ или физический накопитель указанной аппаратной ревизией ядра.
        Полный функционал standalone-патчеров:
        1. Записывает 9 MiB ядро в LBA 14336 (смещение 0x700000).
        2. Обновляет метку тома exFAT (OW090plus-v*), если она присутствует по смещению 0x6B24000.
        """
        base_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "firmware_base")
        candidate_paths = [
            os.path.join(base_dir, "boots", f"{revision_crc}.bin"),
            os.path.join("boots", f"{revision_crc}.bin")
        ]
        boot_path = None
        for cp in candidate_paths:
            if os.path.isfile(cp):
                boot_path = cp
                break
        if not boot_path:
            return False, f"Файл ядра для CRC {revision_crc} не найден ни в firmware_base/boots/, ни в boots/."

        rev_info = KNOWN_REVISIONS.get(revision_crc, {})
        rev_name = rev_info.get("name", revision_crc)
        rev_label_suffix = rev_info.get("label_suffix", "v7")

        with open(boot_path, "rb") as bf:
            boot_data = bf.read()

        if len(boot_data) != BOOT_PARTITION_SIZE:
            return False, f"Некорректный размер ядра ({len(boot_data)} байт, ожидается {BOOT_PARTITION_SIZE})."

        try:
            with open(target_path, "r+b") as f:
                # 1. Запись раздела boot (LBA 14336, смещение 0x700000)
                f.seek(BOOT_PARTITION_OFFSET)
                f.write(boot_data)

                # 2. Обновление Volume Label в exFAT при наличии
                f.seek(0, 2)
                f_size = f.tell()
                if f_size >= 0x6B24020:
                    f.seek(0x6B24000)
                    tag = f.read(1)
                    if tag == b'\x83':
                        label_str = f"OW090plus-{rev_label_suffix}"
                        label_utf16 = label_str.encode('utf-16le')[:22]
                        entry_vol = bytearray(32)
                        entry_vol[0] = 0x83
                        entry_vol[1] = len(label_utf16) // 2
                        entry_vol[2:2+len(label_utf16)] = label_utf16
                        f.seek(0x6B24000)
                        f.write(entry_vol)

                # 3. Обновление строки версии прошивки в rootfs (LBA 32768)
                l1 = " Game Stick Lite\n"
                l2 = " Custom Firmware v2.0\n"
                name_clean = rev_name.split("(")[0].strip()
                prefix = " HW: "
                suffix = f" [{revision_crc}]\n"
                avail = 90 - len(l1) - len(l2) - len(prefix) - len(suffix)
                if len(name_clean) > avail:
                    name_clean = name_clean[:avail - 1] + "~"
                pad = avail - len(name_clean)
                l3 = prefix + name_clean + (" " * pad) + suffix
                new_ver_bytes = (l1 + l2 + l3).encode("utf-8")[:90]

                f.seek(32768 * 512)
                rootfs_head = bytearray(f.read(4 * 1024 * 1024))
                for mark in [
                    b' Game Stick Lite\n OpenWorld\nv0.90plus ML_Store Edition by ruDronga (https://stick-ow.pro)\n',
                    b' Game Stick Lite\n Custom Firmware v2.0\n'
                ]:
                    v_idx = rootfs_head.find(mark)
                    if v_idx != -1:
                        rootfs_head[v_idx:v_idx+90] = new_ver_bytes
                        f.seek(32768 * 512)
                        f.write(rootfs_head)
                        break

                f.flush()
            return True, f"Патч успешно применен! Установлена ревизия: {rev_name} (CRC: {revision_crc})"
        except Exception as e:
            return False, f"Ошибка записи: {e}"
