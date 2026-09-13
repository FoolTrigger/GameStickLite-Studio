"""
ROM & Metadata Engine for Game Stick Lite.
Scans ROM folders, filters CUE tracks, and generates native games.db SQLite indexes.
"""

import os
import re
import zlib
import sqlite3
from typing import Dict, List, Set, Optional, Any, Tuple

# Основные платформы Game Stick Lite
PLATFORMS: Dict[str, Dict[str, Any]] = {
    "fc": {
        "name": "Dendy / NES",
        "folder": "fc",
        "class_type": 1,
        "default_game_type": 30,
        "timer": "/sdcard/game/fc",
        "extensions": [".nes", ".unf", ".fds"],
    },
    "sfc": {
        "name": "Super Nintendo / SFC",
        "folder": "sfc",
        "class_type": 6,
        "default_game_type": 6,
        "timer": "/sdcard/game/sfc",
        "extensions": [".smc", ".sfc", ".fig"],
    },
    "md": {
        "name": "Sega Mega Drive / Genesis",
        "folder": "md",
        "class_type": 5,
        "default_game_type": 5,
        "timer": "/sdcard/game/md",
        "extensions": [".bin", ".gen", ".md", ".smd", ".32x", ".gg"],
    },
    "gba": {
        "name": "Game Boy Advance",
        "folder": "gba",
        "class_type": 3,
        "default_game_type": 32,
        "timer": "/sdcard/game/gba",
        "extensions": [".gba"],
    },
    "gbc": {
        "name": "Game Boy Color",
        "folder": "gbc",
        "class_type": 4,
        "default_game_type": 7,
        "timer": "/sdcard/game/gbc",
        "extensions": [".gbc"],
    },
    "gb": {
        "name": "Game Boy",
        "folder": "gb",
        "class_type": 2,
        "default_game_type": 7,
        "timer": "/sdcard/game/gb",
        "extensions": [".gb"],
    },
    "ps1": {
        "name": "Sony PlayStation 1",
        "folder": "ps1",
        "class_type": 7,
        "default_game_type": 9,
        "timer": "/sdcard/game/ps1",
        "extensions": [".bin", ".img", ".iso", ".pbp", ".chd", ".cue"],
    },
    "cps": {
        "name": "Arcade (MAME / CPS / FBNeo)",
        "folder": "cps",
        "class_type": 0,
        "default_game_type": 4,
        "timer": "/sdcard/game/cps",
        "extensions": [".zip"],
    },
    "atari": {
        "name": "Atari 2600/7800",
        "folder": "atari",
        "class_type": 8,
        "default_game_type": 16,
        "timer": "/sdcard/game/atari",
        "extensions": [".a26", ".a52", ".a78", ".atr", ".lnx"],
    }
}

EXT_TO_PLATFORM = {}
for p_key, p_val in PLATFORMS.items():
    for ext in p_val["extensions"]:
        if ext not in EXT_TO_PLATFORM:
            EXT_TO_PLATFORM[ext] = p_key


class CueParser:
    """Парсер .cue для извлечения основного трека данных и вспомогательных аудиотреков."""

    @staticmethod
    def extract_referenced_files(cue_path: str) -> List[str]:
        referenced: List[str] = []
        if not os.path.isfile(cue_path):
            return referenced
        try:
            with open(cue_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    match = re.search(r'FILE\s+["\']?([^"\']+)["\']?\s+BINARY', line, re.IGNORECASE)
                    if match:
                        f_name = match.group(1).strip()
                        if f_name not in referenced:
                            referenced.append(f_name)
        except Exception:
            pass
        return referenced


class RomItem:
    def __init__(self, full_path: str, platform_key: str, display_name: Optional[str] = None):
        self.full_path = full_path
        self.platform_key = platform_key
        self.filename = os.path.basename(full_path)
        self.base_name, self.ext = os.path.splitext(self.filename)
        self.size_bytes = os.path.getsize(full_path)
        self.display_name = display_name or self.base_name
        self.has_cover = False
        self.cover_path: Optional[str] = None
        self.aux_files: List[str] = []

        # Поиск обложки
        png_file = os.path.join(os.path.dirname(full_path), f"{self.base_name}.png")
        if not os.path.isfile(png_file) and display_name:
            alt_png = os.path.join(os.path.dirname(full_path), f"{display_name}.png")
            if os.path.isfile(alt_png):
                png_file = alt_png

        if os.path.isfile(png_file):
            self.has_cover = True
            self.cover_path = png_file

    def get_clean_match_title(self) -> str:
        clean = re.sub(r'[^a-zA-Z0-9\u0400-\u04FF]', '', self.display_name).lower()
        return clean[:50]


class RomManager:
    def __init__(self, game_dir: str):
        self.game_dir = os.path.abspath(game_dir)
        self.items: List[RomItem] = []

    @staticmethod
    def ensure_category_safeguards(target_game_dir: str):
        """
        Гарантирует, что ни одна папка категории эмулятора не является пустой.
        В стоковой прошивке /usr/bin/game при открытии меню Class обращается к category_node->items
        без проверки на NULL, что вызывает мгновенный SIGSEGV (вылет и перезагрузку в вечный Loading),
        если в папке нет ни одного файла с поддерживаемым расширением.
        """
        for p_key, p_info in PLATFORMS.items():
            p_dir = os.path.join(target_game_dir, p_key)
            os.makedirs(p_dir, exist_ok=True)

            valid_exts = set(p_info["extensions"])
            has_real_game = False
            placeholder_file = os.path.join(p_dir, f".{p_key}_safeguard.bin")

            for f in os.listdir(p_dir):
                if f.startswith("."):
                    continue
                if os.path.splitext(f)[1].lower() in valid_exts:
                    has_real_game = True
                    break

            if has_real_game:
                if os.path.isfile(placeholder_file):
                    try:
                        os.remove(placeholder_file)
                    except Exception:
                        pass
            else:
                if not os.path.isfile(placeholder_file):
                    try:
                        with open(placeholder_file, "wb") as pf:
                            pf.write(b"\x00" * 64)
                    except Exception:
                        pass

    def scan(self) -> List[RomItem]:
        self.items = []
        if not os.path.isdir(self.game_dir):
            return self.items

        # Находим все CUE файлы и связываем с их BIN файлами
        ignore_files = set()
        cue_map = {}  # main_bin_path_lower -> (cue_full_path, [aux_files], clean_title)

        for root, _, files in os.walk(self.game_dir):
            for f in files:
                if f.lower().endswith(".cue"):
                    cue_full = os.path.join(root, f)
                    ref_files = CueParser.extract_referenced_files(cue_full)
                    cue_base, _ = os.path.splitext(f)
                    if ref_files:
                        # Первый файл - основной трек данных
                        main_bin = os.path.normpath(os.path.join(root, ref_files[0]))
                        aux = [os.path.normpath(os.path.join(root, rf)) for rf in ref_files[1:]]
                        # Игнорируем сам .cue и вторичные аудиотреки при общем перечислении
                        ignore_files.add(os.path.normpath(cue_full).lower())
                        for a in aux:
                            ignore_files.add(a.lower())
                        cue_map[main_bin.lower()] = (cue_full, aux, cue_base)
                    else:
                        ignore_files.add(os.path.normpath(cue_full).lower())

        for root, _, files in os.walk(self.game_dir):
            for f in files:
                # Игнорируем скрытые и системные файлы
                if f.startswith("."):
                    continue

                full_path = os.path.join(root, f)
                norm_full = os.path.normpath(full_path).lower()
                if norm_full in ignore_files:
                    continue

                _, ext = os.path.splitext(f)
                ext = ext.lower()
                if not ext:
                    continue

                rel_dir = os.path.relpath(root, self.game_dir).replace("\\", "/").split("/")[0].lower()
                platform_key = None
                if rel_dir in PLATFORMS and ext in PLATFORMS[rel_dir]["extensions"]:
                    platform_key = rel_dir
                elif ext in EXT_TO_PLATFORM:
                    platform_key = EXT_TO_PLATFORM[ext]

                if not platform_key:
                    continue

                if norm_full in cue_map:
                    cue_full, aux, clean_title = cue_map[norm_full]
                    item = RomItem(full_path, platform_key, display_name=clean_title)
                    item.aux_files = [cue_full] + aux
                else:
                    item = RomItem(full_path, platform_key)

                self.items.append(item)

        self.items.sort(key=lambda x: x.display_name.lower())
        return self.items

    def build_games_db(self, output_dir: Optional[str] = None) -> Tuple[bool, str]:
        target_dir = output_dir or self.game_dir
        db_path = os.path.join(target_dir, "games.db")

        if os.path.exists(db_path):
            try:
                os.remove(db_path)
            except Exception as e:
                return False, f"Не удалось удалить старую базу: {e}"

        try:
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()

            cur.execute("CREATE TABLE tbl_total(ID INTEGER PRIMARY KEY, total INTEGER);")
            cur.execute('CREATE TABLE "tbl_path"("path_id" TEXT, "path" TEXT);')
            cur.execute('CREATE TABLE "tbl_video"("video_id" TEXT, "video" TEXT, "path_id" TEXT);')
            cur.execute("CREATE TABLE tbl_en(en_id INTEGER, en_title CHAR(50));")
            cur.execute("CREATE TABLE tbl_zh(zh_id INTEGER, zh_title CHAR(50));")
            cur.execute("CREATE TABLE tbl_ko(ko_id, ko_title);")
            cur.execute("CREATE TABLE tbl_tw(en_id INTEGER, en_title CHAR(50));")
            cur.execute("CREATE TABLE tbl_match(ID INTEGER, zh_match CHAR(50));")
            cur.execute(
                "CREATE TABLE tbl_game(gameid INTEGER, game CHAR(50), suffix CHAR(5), "
                "zh_id INTEGER, en_id INTEGER, ko_id INTEGER, video_id INTEGER, "
                "class_type INTEGER, game_type INTEGER, hard INTEGER, timer CHAR(50));"
            )

            cur.execute("INSERT INTO tbl_path VALUES ('1', '/sdcard/game/');")
            total = len(self.items)
            cur.execute("INSERT INTO tbl_total VALUES (?, ?);", (total, total))

            for idx, item in enumerate(self.items, start=1):
                p_info = PLATFORMS[item.platform_key]
                title = item.display_name[:50]
                match_str = item.get_clean_match_title()

                cur.execute("INSERT INTO tbl_en VALUES (?, ?);", (idx, title))
                cur.execute("INSERT INTO tbl_zh VALUES (?, ?);", (idx, title))
                cur.execute("INSERT INTO tbl_ko VALUES (?, ?);", (idx, title))
                cur.execute("INSERT INTO tbl_tw VALUES (?, ?);", (idx, title))
                cur.execute("INSERT INTO tbl_match VALUES (?, ?);", (idx, match_str))
                cur.execute("INSERT INTO tbl_video VALUES (?, NULL, '1');", (str(idx),))

                cur.execute(
                    "INSERT INTO tbl_game VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?);",
                    (idx, item.base_name[:50], item.ext.lower()[:5], idx, idx, idx, idx, p_info["class_type"], p_info["default_game_type"], p_info["timer"])
                )

            conn.commit()
            conn.close()

            # database.sqlite3
            sqlite3_path = os.path.join(target_dir, "database.sqlite3")
            if not os.path.exists(sqlite3_path):
                sconn = sqlite3.connect(sqlite3_path)
                scur = sconn.cursor()
                scur.execute("CREATE TABLE GameInfo(ID INTEGER PRIMARY KEY, GameID INTEGER, STATUS INTEGER);")
                scur.execute("CREATE TABLE History(ID INTEGER PRIMARY KEY, GameID INTEGER, STATUS INTEGER);")
                sconn.commit()
                sconn.close()

            return True, f"База игр успешно создана ({total} игр)"
        except Exception as e:
            return False, f"Ошибка сборки базы: {e}"

    @staticmethod
    def copy_selected_to_drive(
        selected_items: List[RomItem],
        target_drive_or_game_dir: str,
        progress_cb: Optional[Any] = None
    ) -> Tuple[bool, str]:
        """
        Записывает выбранные игры на подключенную карту памяти (SD-карту):
        - Создает структуру /game/<platform>/ на флешке
        - Копирует файл игры (и связанные файлы .bin треков для .cue)
        - Копирует обложку .png при наличии
        - Пересобирает базу games.db на флешке (в /game/games.db и /games.db)
        """
        import shutil

        # Защита от записи на системный диск Windows
        sys_drive = os.environ.get("SystemDrive", "C:").upper()
        target_abs = os.path.abspath(target_drive_or_game_dir)
        target_drive = os.path.splitdrive(target_abs)[0].upper()
        if target_drive == sys_drive:
            return False, f"Запись на системный диск Windows ({sys_drive}) строго запрещена!"

        target_path = target_abs
        if os.path.basename(target_path).lower() == "game":
            target_game_dir = target_path
            target_root = os.path.dirname(target_game_dir)
        else:
            target_game_dir = os.path.join(target_path, "game")
            target_root = target_path

        os.makedirs(target_game_dir, exist_ok=True)

        total = len(selected_items)
        if total == 0:
            return False, "Не выбрано ни одной игры для записи."

        copied = 0
        try:
            for idx, item in enumerate(selected_items):
                if progress_cb:
                    progress_cb(idx + 1, total, item.display_name)

                p_dir = os.path.join(target_game_dir, item.platform_key)
                os.makedirs(p_dir, exist_ok=True)

                dest_file = os.path.join(p_dir, item.filename)
                shutil.copy2(item.full_path, dest_file)

                # Копирование вспомогательных файлов (.cue и вторичных треков)
                if hasattr(item, "aux_files") and item.aux_files:
                    for af in item.aux_files:
                        if os.path.isfile(af):
                            try:
                                shutil.copy2(af, os.path.join(p_dir, os.path.basename(af)))
                            except Exception:
                                pass
                elif item.ext.lower() == ".cue":
                    ref_files = CueParser.extract_referenced_files(item.full_path)
                    src_folder = os.path.dirname(item.full_path)
                    for rf in ref_files:
                        src_bin = os.path.join(src_folder, rf)
                        if os.path.isfile(src_bin):
                            try:
                                shutil.copy2(src_bin, os.path.join(p_dir, rf))
                            except Exception:
                                pass

                # Копирование обложки
                if item.has_cover and item.cover_path and os.path.isfile(item.cover_path):
                    shutil.copy2(item.cover_path, os.path.join(p_dir, f"{item.base_name}.png"))

                copied += 1

            # Защита категорий от NULL-pointer краша в меню Class при пустых папках
            RomManager.ensure_category_safeguards(target_game_dir)

            # Сканируем целевой каталог /game/ и пересобираем games.db
            drive_mgr = RomManager(target_game_dir)
            drive_mgr.scan()
            ok_db, msg_db = drive_mgr.build_games_db(output_dir=target_game_dir)
            if not ok_db:
                return False, f"Игры скопированы ({copied}), но произошла ошибка пересборки базы: {msg_db}"

            # Дублируем базу games.db и database.sqlite3 в корень накопителя (для совместимости)
            built_db = os.path.join(target_game_dir, "games.db")
            if os.path.isfile(built_db):
                try:
                    shutil.copy2(built_db, os.path.join(target_root, "games.db"))
                except Exception:
                    pass
            built_sqlite = os.path.join(target_game_dir, "database.sqlite3")
            if os.path.isfile(built_sqlite):
                try:
                    shutil.copy2(built_sqlite, os.path.join(target_root, "database.sqlite3"))
                except Exception:
                    pass

            # Автоматическое восстановление start_game.sh, ReBuild.exe и недостающих ядер эмуляторов (retro_lib)
            base_template = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "firmware_base", "userdata_template")
            src_sh = os.path.join(base_template, "minigui", "start_game.sh")
            dst_sh = os.path.join(target_root, "minigui", "start_game.sh")
            if os.path.isfile(src_sh):
                try:
                    os.makedirs(os.path.join(target_root, "minigui"), exist_ok=True)
                    shutil.copy2(src_sh, dst_sh)
                except Exception:
                    pass

            src_rebuild = os.path.join(base_template, "game", "ReBuild.exe")
            dst_rebuild = os.path.join(target_game_dir, "ReBuild.exe")
            if os.path.isfile(src_rebuild) and (not os.path.isfile(dst_rebuild) or os.path.getsize(dst_rebuild) != os.path.getsize(src_rebuild)):
                try:
                    shutil.copy2(src_rebuild, dst_rebuild)
                except Exception:
                    pass

            # Синхронизация недостающих ядер в retro_lib
            src_cores = os.path.join(base_template, "retro_lib")
            dst_cores = os.path.join(target_root, "retro_lib")
            if os.path.isdir(src_cores):
                try:
                    os.makedirs(dst_cores, exist_ok=True)
                    for c_file in os.listdir(src_cores):
                        if c_file.endswith(".so") or "_libretro" in c_file:
                            sc = os.path.join(src_cores, c_file)
                            dc = os.path.join(dst_cores, c_file)
                            if not os.path.isfile(dc) or os.path.getsize(dc) == 0:
                                shutil.copy2(sc, dc)
                except Exception:
                    pass

            return True, f"Успешно записано {copied} игр на накопитель {target_root}!\nБаза games.db обновлена (всего на носителе: {len(drive_mgr.items)} игр).\nСистемные файлы и ядра эмуляторов синхронизированы."
        except Exception as e:
            return False, f"Ошибка при копировании игр: {e}"
