"""
Pure Python exFAT Volume Generator for Game Stick Lite.
Creates self-contained, valid exFAT partition binaries with directory trees.
"""

import os
import math
import struct
from typing import Tuple

SECTOR_SIZE = 512
CLUSTER_SIZE = 128 * 1024  # 128 KiB clusters (256 sectors)
SECTORS_PER_CLUSTER = CLUSTER_SIZE // SECTOR_SIZE
BYTES_PER_SECTOR_SHIFT = 9
SECTORS_PER_CLUSTER_SHIFT = 8


def exfat_checksum(data: bytes) -> int:
    checksum = 0
    for i, b in enumerate(data):
        if i in (106, 107, 112):
            continue
        checksum = (((checksum & 1) << 31) | (checksum >> 1)) + b
        checksum &= 0xFFFFFFFF
    return checksum


class PurePythonExfatBuilder:
    def __init__(self, volume_size_bytes: int):
        self.volume_size = volume_size_bytes
        self.total_sectors = volume_size_bytes // SECTOR_SIZE

        self.fat_offset_sectors = 2048
        self.cluster_heap_offset_sectors = 65536  # 32 MiB gap allows FAT table to expand up to 1 TB
        self.total_clusters = (self.total_sectors - self.cluster_heap_offset_sectors) // SECTORS_PER_CLUSTER
        self.fat_length_sectors = math.ceil((self.total_clusters + 2) * 4 / SECTOR_SIZE)

        self.current_free_cluster = 5
        self.clusters = {}
        self.allocations = []

        base_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "firmware_base")
        upcase_path = os.path.join(base_dir, "upcase.bin")
        with open(upcase_path, 'rb') as f:
            self.upcase_data = f.read()

        c3 = bytearray(CLUSTER_SIZE)
        c3[:len(self.upcase_data)] = self.upcase_data
        self.clusters[3] = c3

        self.clusters[4] = bytearray(CLUSTER_SIZE)
        self.root_entries_raw = bytearray()

        # Volume Label Entry (0x83)
        label_str = "GAMESTICK"
        label_utf16 = label_str.encode('utf-16le')[:22]
        entry_vol = bytearray(32)
        entry_vol[0] = 0x83
        entry_vol[1] = len(label_utf16) // 2
        entry_vol[2:2+len(label_utf16)] = label_utf16
        self.root_entries_raw += entry_vol

        bitmap_bytes_len = math.ceil(self.total_clusters / 8)
        self.bitmap_bytes_len = bitmap_bytes_len
        self.bitmap_cluster = 2

        entry_bitmap = struct.pack('<BB18xIQ', 0x81, 0, self.bitmap_cluster, bitmap_bytes_len)
        self.root_entries_raw += entry_bitmap

        # Up-case Table Entry: строго 32 байта (1 + 3 + 4 + 12 + 4 + 8 = 32)
        upcase_checksum = 0xE619D30D
        entry_upcase = struct.pack('<B3xI12xIQ', 0x82, upcase_checksum, 3, len(self.upcase_data))
        self.root_entries_raw += entry_upcase

    def allocate_clusters(self, data_bytes: bytes) -> Tuple[int, int]:
        if not data_bytes:
            return 0, 0
        needed_clusters = math.ceil(len(data_bytes) / CLUSTER_SIZE)
        start_cluster = self.current_free_cluster
        self.current_free_cluster += needed_clusters
        self.allocations.append((start_cluster, needed_clusters))

        for i in range(needed_clusters):
            c_num = start_cluster + i
            chunk = data_bytes[i*CLUSTER_SIZE : (i+1)*CLUSTER_SIZE]
            c_data = bytearray(CLUSTER_SIZE)
            c_data[:len(chunk)] = chunk
            self.clusters[c_num] = c_data

        return start_cluster, needed_clusters

    @staticmethod
    def make_file_entries(name: str, is_dir: bool, start_cluster: int, data_len: int) -> bytes:
        name_utf16 = name.encode('utf-16le')
        num_name_chars = len(name_utf16) // 2
        name_entries_count = math.ceil(num_name_chars / 15)
        secondary_count = 1 + name_entries_count

        attrib = 0x10 if is_dir else 0x20
        entry_85 = bytearray(32)
        entry_85[0] = 0x85
        entry_85[1] = secondary_count
        struct.pack_into('<H', entry_85, 4, attrib)
        default_ts = 0x5C216000  # 2026-01-01 12:00:00
        struct.pack_into('<III', entry_85, 8, default_ts, default_ts, default_ts)

        # Вычисление NameHash согласно спецификации exFAT
        name_hash = 0
        for ch in name.upper():
            c = ord(ch)
            name_hash = (((name_hash & 1) << 15) | (name_hash >> 1)) + (c & 0xFF)
            name_hash &= 0xFFFF
            name_hash = (((name_hash & 1) << 15) | (name_hash >> 1)) + (c >> 8)
            name_hash &= 0xFFFF

        entry_c0 = bytearray(32)
        entry_c0[0] = 0xC0
        # Флаги: 0x01 (AllocationPossible) | 0x02 (NoFatChain) = 0x03
        entry_c0[1] = 0x03 if data_len > 0 else 0x00
        entry_c0[3] = num_name_chars
        struct.pack_into('<H', entry_c0, 4, name_hash)
        struct.pack_into('<Q', entry_c0, 8, data_len)
        struct.pack_into('<I', entry_c0, 20, start_cluster if data_len > 0 else 0)
        struct.pack_into('<Q', entry_c0, 24, data_len)

        name_entries = bytearray()
        for i in range(name_entries_count):
            entry_c1 = bytearray(32)
            entry_c1[0] = 0xC1
            entry_c1[1] = 0x00
            chunk = name_utf16[i*30 : (i+1)*30]
            entry_c1[2:2+len(chunk)] = chunk
            name_entries += entry_c1

        # 16-битный циклический сдвиг вправо: Checksum = ((Checksum & 1) << 15) | (Checksum >> 1) + Byte
        all_entries = bytearray(entry_85 + entry_c0 + name_entries)
        csum = 0
        for idx, b in enumerate(all_entries):
            if idx in (2, 3):
                continue
            csum = (((csum & 1) << 15) | (csum >> 1)) + b
            csum &= 0xFFFF
        struct.pack_into('<H', all_entries, 2, csum)
        return bytes(all_entries)

    def add_directory_tree(self, local_src_dir: str):
        def process_dir(dir_path: str) -> bytes:
            dir_entries = bytearray()
            items = sorted(os.listdir(dir_path))
            for item_name in items:
                full_p = os.path.join(dir_path, item_name)
                if os.path.isdir(full_p):
                    sub_entries = process_dir(full_p)
                    if not sub_entries:
                        sub_entries = bytes(CLUSTER_SIZE)
                    c_start, n_clus = self.allocate_clusters(sub_entries)
                    # Размер директории в exFAT всегда кратен размеру кластера
                    dir_entries += self.make_file_entries(item_name, True, c_start, n_clus * CLUSTER_SIZE)
                else:
                    with open(full_p, 'rb') as f:
                        f_data = f.read()
                    if len(f_data) == 0:
                        c_start = 0
                    else:
                        c_start, _ = self.allocate_clusters(f_data)
                    dir_entries += self.make_file_entries(item_name, False, c_start, len(f_data))
            return bytes(dir_entries)

        root_items = sorted(os.listdir(local_src_dir))
        for r_name in root_items:
            full_p = os.path.join(local_src_dir, r_name)
            if os.path.isdir(full_p):
                sub_bytes = process_dir(full_p)
                if not sub_bytes:
                    sub_bytes = bytes(CLUSTER_SIZE)
                c_start, n_clus = self.allocate_clusters(sub_bytes)
                self.root_entries_raw += self.make_file_entries(r_name, True, c_start, n_clus * CLUSTER_SIZE)
            else:
                with open(full_p, 'rb') as f:
                    f_data = f.read()
                if len(f_data) == 0:
                    c_start = 0
                else:
                    c_start, _ = self.allocate_clusters(f_data)
                self.root_entries_raw += self.make_file_entries(r_name, False, c_start, len(f_data))

        self.clusters[4][:len(self.root_entries_raw)] = self.root_entries_raw

    def write_image(self, out_file_path: str):
        bitmap_bytes = bytearray(self.bitmap_bytes_len)
        for cluster_idx in range(2, self.current_free_cluster):
            bit_pos = cluster_idx - 2
            byte_idx = bit_pos // 8
            bit_idx = bit_pos % 8
            if byte_idx < len(bitmap_bytes):
                bitmap_bytes[byte_idx] |= (1 << bit_idx)
        c2 = bytearray(CLUSTER_SIZE)
        c2[:len(bitmap_bytes)] = bitmap_bytes
        self.clusters[2] = c2

        vbr = bytearray(512 * 12)
        vbr[0:3] = b'\xeb\x76\x90'
        vbr[3:11] = b'EXFAT   '
        struct.pack_into('<Q', vbr, 64, 212768)  # LBA userdata
        struct.pack_into('<Q', vbr, 72, self.total_sectors)
        struct.pack_into('<I', vbr, 80, self.fat_offset_sectors)
        struct.pack_into('<I', vbr, 84, self.fat_length_sectors)
        struct.pack_into('<I', vbr, 88, self.cluster_heap_offset_sectors)
        struct.pack_into('<I', vbr, 92, self.total_clusters)
        struct.pack_into('<I', vbr, 96, 4)       # FirstClusterOfRootDir = 4
        struct.pack_into('<I', vbr, 100, 0x5A11C001)
        struct.pack_into('<H', vbr, 104, 0x0100)
        struct.pack_into('<H', vbr, 106, 0)
        vbr[108] = BYTES_PER_SECTOR_SHIFT
        vbr[109] = SECTORS_PER_CLUSTER_SHIFT
        vbr[110] = 1
        vbr[111] = 0x80
        vbr[112] = 1
        vbr[510:512] = b'\x55\xaa'

        csum = exfat_checksum(bytes(vbr[:512 * 11]))
        vbr[512 * 11 : 512 * 12] = struct.pack('<I', csum) * 128
        backup_vbr = bytes(vbr)

        with open(out_file_path, 'wb') as f:
            f.write(vbr)
            f.write(backup_vbr)

            # Запись полной таблицы FAT со всеми цепочками кластеров
            fat_bytes = bytearray(self.fat_length_sectors * SECTOR_SIZE)
            def set_fat(cluster_idx: int, val: int):
                if cluster_idx * 4 + 4 <= len(fat_bytes):
                    struct.pack_into('<I', fat_bytes, cluster_idx * 4, val)

            set_fat(0, 0xFFFFFFF8)
            set_fat(1, 0xFFFFFFFF)
            set_fat(2, 0xFFFFFFFF)  # Allocation Bitmap
            set_fat(3, 0xFFFFFFFF)  # Up-case Table
            set_fat(4, 0xFFFFFFFF)  # Root Directory

            for start_c, count_c in self.allocations:
                for ci in range(count_c - 1):
                    set_fat(start_c + ci, start_c + ci + 1)
                set_fat(start_c + count_c - 1, 0xFFFFFFFF)

            f.seek(self.fat_offset_sectors * SECTOR_SIZE)
            f.write(fat_bytes)

            # Запись кластеров
            heap_base = self.cluster_heap_offset_sectors * SECTOR_SIZE
            for c_num, c_data in self.clusters.items():
                f.seek(heap_base + (c_num - 2) * CLUSTER_SIZE)
                f.write(c_data)

            f.seek(self.volume_size - 1)
            f.write(b'\x00')
