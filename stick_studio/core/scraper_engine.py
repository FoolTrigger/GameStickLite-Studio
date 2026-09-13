"""
Scraper & Asset Optimization Engine for Game Stick Lite.
Downscales covers to 256x256 PNG to prevent memory overflow on low-RAM consoles.
"""

import os
import urllib.parse
from typing import Optional
import requests
from PIL import Image

LIBRETRO_SYSTEM_MAP = {
    "fc": "Nintendo_-_Nintendo_Entertainment_System",
    "sfc": "Nintendo_-_Super_Nintendo_Entertainment_System",
    "md": "Sega_-_Mega_Drive_-_Genesis",
    "gba": "Nintendo_-_Game_Boy_Advance",
    "gbc": "Nintendo_-_Game_Boy_Color",
    "gb": "Nintendo_-_Game_Boy",
    "ps1": "Sony_-_PlayStation",
    "atari": "Atari_-_2600",
}

LIBRETRO_CDN_BASE = "https://raw.githubusercontent.com/libretro-thumbnails/{system}/master/Named_Boxarts/{title}.png"


class CoverArtOptimizer:
    TARGET_SIZE = (256, 256)

    @staticmethod
    def process_and_save(source_image_path: str, target_png_path: str) -> bool:
        try:
            with Image.open(source_image_path) as img:
                img = img.convert("RGBA")
                img.thumbnail(CoverArtOptimizer.TARGET_SIZE, Image.Resampling.LANCZOS)
                final_img = Image.new("RGBA", CoverArtOptimizer.TARGET_SIZE, (0, 0, 0, 0))
                offset_x = (CoverArtOptimizer.TARGET_SIZE[0] - img.width) // 2
                offset_y = (CoverArtOptimizer.TARGET_SIZE[1] - img.height) // 2
                final_img.paste(img, (offset_x, offset_y), img)
                final_img.save(target_png_path, "PNG", optimize=True)
                return True
        except Exception:
            return False


class CoverScraper:
    def __init__(self, timeout: int = 10):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "GameStickLiteStudio/1.0"})

    def download_boxart(self, platform_key: str, game_title: str, target_png_path: str) -> bool:
        system = LIBRETRO_SYSTEM_MAP.get(platform_key)
        if not system:
            return False

        clean_title = game_title.replace("&", "_").replace(":", "_").replace("/", "_").replace("\\", "_")
        quoted_title = urllib.parse.quote(clean_title)
        url = LIBRETRO_CDN_BASE.format(system=system, title=quoted_title)

        try:
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code == 200 and len(resp.content) > 100:
                temp_file = target_png_path + ".tmp"
                with open(temp_file, "wb") as f:
                    f.write(resp.content)
                ok = CoverArtOptimizer.process_and_save(temp_file, target_png_path)
                try:
                    os.remove(temp_file)
                except Exception:
                    pass
                return ok
        except Exception:
            pass
        return False
