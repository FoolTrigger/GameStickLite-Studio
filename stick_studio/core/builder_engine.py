"""
Minimal Firmware Build Engine for Game Stick Lite.
Assembles a lightweight, clean, bloatware-free distribution:
- 1 single ultra-light theme (minimum VRAM, instant loading)
- Clean retroarch.cfg and core mappings
- Clean games database index
- Hardware revision selection
"""

import os
import shutil
import sqlite3
from typing import Optional, Tuple, Dict, Any

from stick_studio.core.image_engine import BootRevisionManager, KNOWN_REVISIONS
from stick_studio.core.rom_engine import RomManager, PLATFORMS

# Минималистичная легковесная тема (Single Minimalist Theme)
MINIMAL_THEME_INFO = {
    "name": "Ultra-Light Dark Minimal",
    "description": "Единая оптимизированная тема оформления. Минимальное потребление VRAM и RAM, мгновенный отклик интерфейса.",
    "theme_file": "segasatan.zip"  # Самый легковесный чистый скин (1.6 MB)
}


class MinimalBuildCreator:
    """Генератор и сборщик минимального дистрибутива Game Stick Lite."""

    @staticmethod
    def create_minimal_sd_layout(target_sd_path: str, revision_crc: str = "06D64343") -> Tuple[bool, str]:
        """
        Создает чистую минимальную структуру каталогов на SD-карте или в папке экспорта.
        """
        if not os.path.isdir(target_sd_path):
            try:
                os.makedirs(target_sd_path, exist_ok=True)
            except Exception as e:
                return False, f"Не удалось создать директорию: {e}"

        try:
            # 1. Структура каталогов для игр
            game_dir = os.path.join(target_sd_path, "game")
            os.makedirs(game_dir, exist_ok=True)

            for p_key in PLATFORMS.keys():
                os.makedirs(os.path.join(game_dir, p_key), exist_ok=True)

            # 2. Создание чистой базы games.db и database.sqlite3
            rom_mgr = RomManager(game_dir)
            rom_mgr.scan()
            rom_mgr.build_games_db(output_dir=game_dir)

            # 3. Минимальная системная конфигурация
            minigui_dir = os.path.join(target_sd_path, "minigui")
            res_dir = os.path.join(minigui_dir, "res")
            themes_dir = os.path.join(res_dir, "themes")
            os.makedirs(themes_dir, exist_ok=True)

            retroarch_dir = os.path.join(target_sd_path, "retroarch")
            os.makedirs(os.path.join(retroarch_dir, "system"), exist_ok=True)
            os.makedirs(os.path.join(retroarch_dir, "autoconfig"), exist_ok=True)

            # 4. Копирование единственной минимальной темы
            src_themes_dir = os.path.join(".", "minigui", "res", "themes")
            minimal_zip = MINIMAL_THEME_INFO["theme_file"]
            src_zip = os.path.join(src_themes_dir, minimal_zip)
            dst_zip = os.path.join(themes_dir, minimal_zip)

            if os.path.isfile(src_zip) and not os.path.isfile(dst_zip):
                shutil.copy2(src_zip, dst_zip)

            rev_name = KNOWN_REVISIONS.get(revision_crc, {}).get("name", revision_crc)
            return True, f"Минимальная сборка успешно создана для {rev_name} в {target_sd_path}."

        except Exception as e:
            return False, f"Ошибка при сборке дистрибутива: {e}"
