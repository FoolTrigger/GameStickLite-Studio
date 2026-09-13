"""
Main GUI for Game Stick Lite Studio.
- Standalone Raw .img Builder (Independent of monolithic images)
- Dedicated In-Place Partition Resizer
- Minimal Distribution with 1 Ultra-Light Theme
- Completely clean, neutral codebase
"""

import os
import sys
from typing import Optional, List

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QLabel, QPushButton, QFileDialog, QMessageBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox,
    QLineEdit, QProgressBar, QGroupBox, QSplitter, QFrame, QCheckBox,
    QRadioButton
)
from PySide6.QtGui import QFont, QPixmap

from stick_studio.core.image_engine import GPTManager, BootRevisionManager, KNOWN_REVISIONS
from stick_studio.core.rom_engine import RomManager, PLATFORMS, RomItem
from stick_studio.core.scraper_engine import CoverScraper
from stick_studio.core.disk_engine import DiskDetector, DiskInfo
from stick_studio.core.resizer_engine import PartitionResizer
from stick_studio.core.image_builder import StandaloneImageBuilder
from stick_studio.core.builder_engine import MINIMAL_THEME_INFO

DARK_STYLESHEET = """
QMainWindow {
    background-color: #121418;
    color: #E0E0E0;
}
QWidget {
    font-family: 'Segoe UI', 'Roboto', sans-serif;
    font-size: 13px;
    color: #D6D8DC;
}
QTabWidget::pane {
    border: 1px solid #282C34;
    background: #181A20;
    border-radius: 6px;
}
QTabBar::tab {
    background: #1E222A;
    color: #8C92A0;
    padding: 10px 22px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 2px;
    font-weight: 600;
}
QTabBar::tab:selected {
    background: #252A34;
    color: #00D2FF;
    border-bottom: 2px solid #00D2FF;
}
QTabBar::tab:hover {
    color: #FFFFFF;
}
QGroupBox {
    border: 1px solid #2B303C;
    border-radius: 8px;
    margin-top: 14px;
    padding-top: 14px;
    font-weight: bold;
    color: #00D2FF;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 4px;
}
QPushButton {
    background-color: #212630;
    border: 1px solid #363D4D;
    border-radius: 6px;
    padding: 8px 18px;
    color: #FFFFFF;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #2C3342;
    border-color: #00D2FF;
    color: #00D2FF;
}
QPushButton:pressed {
    background-color: #1A1E26;
}
QPushButton#PrimaryBtn {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0088CC, stop:1 #00B4D8);
    border: none;
    color: #FFFFFF;
}
QPushButton#PrimaryBtn:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0099E6, stop:1 #00C8F0);
}
QPushButton#SuccessBtn {
    background-color: #1B5E20;
    border: 1px solid #2E7D32;
    color: #A5D6A7;
}
QPushButton#SuccessBtn:hover {
    background-color: #2E7D32;
    color: #FFFFFF;
}
QPushButton#ActionBtn {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2B6CB0, stop:1 #3182CE);
    border: none;
    color: #FFFFFF;
    font-size: 14px;
    font-weight: bold;
}
QPushButton#ActionBtn:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3182CE, stop:1 #4299E1);
}
QLineEdit, QComboBox {
    background-color: #1A1D24;
    border: 1px solid #2E3440;
    border-radius: 6px;
    padding: 7px 10px;
    color: #ECEFF4;
}
QLineEdit:focus, QComboBox:focus {
    border-color: #00D2FF;
}
QTableWidget {
    background-color: #16181E;
    border: 1px solid #282C34;
    gridline-color: #22262E;
    border-radius: 6px;
    color: #D8DEE9;
    selection-background-color: #005F87;
    selection-color: #FFFFFF;
}
QHeaderView::section {
    background-color: #1E222A;
    color: #8F96A3;
    padding: 6px;
    border: 1px solid #252A34;
    font-weight: bold;
}
QProgressBar {
    background-color: #1E222A;
    border: 1px solid #2B303C;
    border-radius: 5px;
    text-align: center;
    color: #FFFFFF;
}
QProgressBar::chunk {
    background-color: #00D2FF;
    border-radius: 4px;
}
"""


class BuildImageWorker(QThread):
    progress = Signal(str, int)
    finished = Signal(bool, str)

    def __init__(self, output_path: str, rev_crc: str, size_mib: int, roms_dir: Optional[str]):
        super().__init__()
        self.output_path = output_path
        self.rev_crc = rev_crc
        self.size_mib = size_mib
        self.roms_dir = roms_dir

    def run(self):
        ok, msg = StandaloneImageBuilder.build_minimal_image(
            output_img_path=self.output_path,
            revision_crc=self.rev_crc,
            userdata_size_mib=self.size_mib,
            extra_roms_dir=self.roms_dir,
            progress_cb=lambda s, p: self.progress.emit(s, p)
        )
        self.finished.emit(ok, msg)


class ScrapeWorker(QThread):
    progress = Signal(int, int, str)
    finished = Signal(int, int)

    def __init__(self, items: list, scraper: CoverScraper):
        super().__init__()
        self.items = items
        self.scraper = scraper

    def run(self):
        total = len(self.items)
        success_count = 0
        for idx, item in enumerate(self.items):
            self.progress.emit(idx + 1, total, item.display_name)
            if not item.has_cover:
                target_png = os.path.join(os.path.dirname(item.full_path), f"{item.base_name}.png")
                if self.scraper.download_boxart(item.platform_key, item.display_name, target_png):
                    item.has_cover = True
                    item.cover_path = target_png
                    success_count += 1
        self.finished.emit(success_count, total)


class CopyGamesWorker(QThread):
    progress = Signal(int, int, str)
    finished = Signal(bool, str)

    def __init__(self, selected_items: list, target_path: str):
        super().__init__()
        self.selected_items = selected_items
        self.target_path = target_path

    def run(self):
        ok, msg = RomManager.copy_selected_to_drive(
            selected_items=self.selected_items,
            target_drive_or_game_dir=self.target_path,
            progress_cb=lambda cur, tot, name: self.progress.emit(cur, tot, name)
        )
        self.finished.emit(ok, msg)


class WriteImageWorker(QThread):
    progress = Signal(int, int, float, str)
    finished = Signal(bool, str)

    def __init__(self, image_path: str, target_device_id: str, is_system: bool):
        super().__init__()
        self.image_path = image_path
        self.target_device_id = target_device_id
        self.is_system = is_system
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        from stick_studio.core.writer_engine import RawDiskWriter
        ok, msg = RawDiskWriter.write_image(
            image_path=self.image_path,
            target_device_id=self.target_device_id,
            is_system_disk=self.is_system,
            progress_cb=lambda cur, tot, spd, st: self.progress.emit(cur, tot, spd, st),
            cancel_check=lambda: self._is_cancelled
        )
        self.finished.emit(ok, msg)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Game Stick Lite Studio — Custom Firmware Builder")
        self.resize(1120, 740)
        self.setStyleSheet(DARK_STYLESHEET)

        self.rom_manager: Optional[RomManager] = None
        self.scraper = CoverScraper()
        self.detected_disks: List[DiskInfo] = []

        self.init_ui()
        self.refresh_disks()

    def init_ui(self):
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # Header
        header_layout = QHBoxLayout()
        title_label = QLabel("GAME STICK LITE STUDIO")
        title_label.setFont(QFont("Segoe UI", 16, QFont.Bold))
        title_label.setStyleSheet("color: #00D2FF; letter-spacing: 1px;")
        header_layout.addWidget(title_label)

        header_layout.addStretch()
        version_label = QLabel("Autonomous Firmware Builder • v2.0")
        version_label.setStyleSheet("color: #6C7280; font-weight: 500;")
        header_layout.addWidget(version_label)
        main_layout.addLayout(header_layout)

        # Tabs
        self.tabs = QTabWidget()
        self.tab_builder = QWidget()
        self.tab_resizer = QWidget()
        self.tab_roms = QWidget()
        self.tab_revisions = QWidget()

        self.tabs.addTab(self.tab_builder, "1. Сборка Образа (.img)")
        self.tabs.addTab(self.tab_resizer, "2. Запись и Расширение SD")
        self.tabs.addTab(self.tab_roms, "3. Менеджер Игр")
        self.tabs.addTab(self.tab_revisions, "4. Патчер Ревизий Ядра")

        self.setup_builder_tab()
        self.setup_resizer_tab()
        self.setup_roms_tab()
        self.setup_revisions_tab()

        main_layout.addWidget(self.tabs)
        self.setCentralWidget(main_widget)

    # -------------------------------------------------------------
    # TAB 1: Сборка Автономного Образа .img
    # -------------------------------------------------------------
    def setup_builder_tab(self):
        layout = QVBoxLayout(self.tab_builder)
        layout.setSpacing(14)

        target_box = QGroupBox("Файл готового образа (.img)")
        tb_layout = QHBoxLayout(target_box)
        self.output_img_input = QLineEdit()
        default_out = os.path.abspath("game_stick_minimal.img")
        self.output_img_input.setText(default_out)
        btn_browse_out = QPushButton("Выбрать...")
        btn_browse_out.clicked.connect(self.browse_output_image)
        tb_layout.addWidget(self.output_img_input)
        tb_layout.addWidget(btn_browse_out)
        layout.addWidget(target_box)

        cfg_box = QGroupBox("Конфигурация чистой сборки (Автономная генерация)")
        cb_layout = QVBoxLayout(cfg_box)
        cb_layout.setSpacing(10)

        # Ревизия ядра
        h_rev = QHBoxLayout()
        h_rev.addWidget(QLabel("Аппаратная ревизия стика:"))
        self.combo_build_rev = QComboBox()
        for crc, info in KNOWN_REVISIONS.items():
            self.combo_build_rev.addItem(f"{info['name']} ({crc})", crc)
        h_rev.addWidget(self.combo_build_rev, 2)
        cb_layout.addLayout(h_rev)

        # Размер раздела userdata
        h_size = QHBoxLayout()
        h_size.addWidget(QLabel("Базовый размер раздела игр (userdata):"))
        self.combo_userdata_size = QComboBox()
        self.combo_userdata_size.addItem("Минимальный (200 MiB) — общий образ ~300 MiB", 200)
        self.combo_userdata_size.addItem("Компактный (500 MiB) — общий образ ~600 MiB", 500)
        self.combo_userdata_size.addItem("Стандартный (1000 MiB) — общий образ ~1.1 GiB", 1000)
        self.combo_userdata_size.addItem("Расширенный (2048 MiB) — общий образ ~2.1 GiB", 2048)
        h_size.addWidget(self.combo_userdata_size, 2)
        cb_layout.addLayout(h_size)

        # Папка с играми (опционально)
        h_roms = QHBoxLayout()
        h_roms.addWidget(QLabel("Дополнительные игры (опционально):"))
        self.input_extra_roms = QLineEdit()
        self.input_extra_roms.setPlaceholderText("Оставьте пустым для чистой сборки или выберите папку с играми")
        btn_browse_roms = QPushButton("Обзор...")
        btn_browse_roms.clicked.connect(self.browse_extra_roms)
        h_roms.addWidget(self.input_extra_roms, 2)
        h_roms.addWidget(btn_browse_roms)
        cb_layout.addLayout(h_roms)

        # Единственная тема
        theme_frame = QFrame()
        theme_frame.setStyleSheet("background: #181D26; border-radius: 6px; padding: 10px;")
        tf_layout = QVBoxLayout(theme_frame)
        lbl_th_name = QLabel(f"✔ Единственная оптимизированная тема: {MINIMAL_THEME_INFO['name']}")
        lbl_th_name.setStyleSheet("color: #48BB78; font-weight: bold;")
        lbl_th_desc = QLabel(
            f"{MINIMAL_THEME_INFO['description']}\n"
            "Все 5 разделов (uboot, trust, boot, rootfs, userdata) компилируются автономно."
        )
        lbl_th_desc.setStyleSheet("color: #A0AEC0;")
        tf_layout.addWidget(lbl_th_name)
        tf_layout.addWidget(lbl_th_desc)
        cb_layout.addWidget(theme_frame)

        layout.addWidget(cfg_box)

        # Кнопка создания
        self.btn_build_img = QPushButton("⚡ Собрать чистый образ (.img)")
        self.btn_build_img.setObjectName("ActionBtn")
        self.btn_build_img.setMinimumHeight(44)
        self.btn_build_img.clicked.connect(self.start_build_image)
        layout.addWidget(self.btn_build_img)

        self.build_progress = QProgressBar()
        self.build_progress.setVisible(False)
        self.build_progress.setValue(0)
        layout.addWidget(self.build_progress)

        self.lbl_build_status = QLabel("")
        self.lbl_build_status.setStyleSheet("color: #00D2FF; font-weight: bold;")
        layout.addWidget(self.lbl_build_status)

        layout.addStretch()

    # -------------------------------------------------------------
    # TAB 2: Запись Образа и Расширение SD
    # -------------------------------------------------------------
    def setup_resizer_tab(self):
        layout = QVBoxLayout(self.tab_resizer)
        layout.setSpacing(12)

        # ---------------------------------------------------------
        # СЕКЦИЯ 1: Прямая запись образа (.img) на накопитель (USB Image Tool)
        # ---------------------------------------------------------
        writer_box = QGroupBox("1. Прямая запись образа (.img) на накопитель (USB Image Tool)")
        wb_layout = QVBoxLayout(writer_box)
        wb_layout.setSpacing(8)

        # Выбор файла образа
        h_img = QHBoxLayout()
        h_img.addWidget(QLabel("Файл образа (.img):"))
        self.writer_img_input = QLineEdit()
        default_img = os.path.abspath("game_stick_minimal.img")
        if os.path.isfile(default_img):
            self.writer_img_input.setText(default_img)
        self.writer_img_input.setPlaceholderText("Выберите файл образа (.img)")
        btn_browse_img = QPushButton("Обзор...")
        btn_browse_img.clicked.connect(self.browse_writer_image)
        h_img.addWidget(self.writer_img_input, 2)
        h_img.addWidget(btn_browse_img)
        wb_layout.addLayout(h_img)

        # Выбор целевого физического накопителя
        h_target = QHBoxLayout()
        h_target.addWidget(QLabel("Целевой накопитель:"))
        self.combo_writer_disks = QComboBox()
        btn_refresh_writer = QPushButton("Обновить диски")
        btn_refresh_writer.clicked.connect(self.refresh_disks)
        h_target.addWidget(self.combo_writer_disks, 3)
        h_target.addWidget(btn_refresh_writer, 1)
        wb_layout.addLayout(h_target)

        self.lbl_writer_warning = QLabel("")
        self.lbl_writer_warning.setStyleSheet("font-weight: bold;")
        wb_layout.addWidget(self.lbl_writer_warning)
        self.combo_writer_disks.currentIndexChanged.connect(self.on_writer_disk_selected)

        # Кнопка записи образа
        self.btn_write_image = QPushButton("⚡ Записать образ на SD-карту / флешку")
        self.btn_write_image.setObjectName("ActionBtn")
        self.btn_write_image.clicked.connect(self.start_write_image)
        wb_layout.addWidget(self.btn_write_image)

        self.writer_progress = QProgressBar()
        self.writer_progress.setVisible(False)
        wb_layout.addWidget(self.writer_progress)

        self.lbl_writer_status = QLabel("")
        self.lbl_writer_status.setStyleSheet("color: #00D2FF; font-weight: bold;")
        wb_layout.addWidget(self.lbl_writer_status)

        layout.addWidget(writer_box)

        # ---------------------------------------------------------
        # СЕКЦИЯ 2: In-Place авторасширение раздела игр (Resizer)
        # ---------------------------------------------------------
        resizer_box = QGroupBox("2. In-Place авторасширение раздела игр на всю емкость карты")
        rb_layout = QVBoxLayout(resizer_box)
        rb_layout.setSpacing(8)

        h_r_target = QHBoxLayout()
        h_r_target.addWidget(QLabel("Целевой накопитель:"))
        self.combo_resizer_disks = QComboBox()
        h_r_target.addWidget(self.combo_resizer_disks, 3)
        rb_layout.addLayout(h_r_target)

        self.lbl_resizer_warning = QLabel("")
        self.lbl_resizer_warning.setStyleSheet("font-weight: bold;")
        rb_layout.addWidget(self.lbl_resizer_warning)
        self.combo_resizer_disks.currentIndexChanged.connect(self.on_resizer_disk_selected)

        desc = QLabel(
            "Функция пересчитывает таблицу разделов GPT и структуру exFAT (VBR/Bitmap),\n"
            "расширяя раздел с играми на 100% реальной емкости SD-карты (32 / 64 / 128 ГБ).\n"
            "• БЕЗ форматирования и БЕЗ потери записанных файлов и сохранений.\n"
            "• Автоматически уведомляет Windows и монтирует букву диска в Проводник."
        )
        desc.setStyleSheet("color: #CBD5E0; line-height: 140%;")
        rb_layout.addWidget(desc)

        self.btn_resize = QPushButton("⚡ Расширить раздел на весь объём накопителя")
        self.btn_resize.setObjectName("SuccessBtn")
        self.btn_resize.setMinimumHeight(38)
        self.btn_resize.clicked.connect(self.run_partition_resize)
        rb_layout.addWidget(self.btn_resize)

        self.lbl_resize_status = QLabel("")
        self.lbl_resize_status.setStyleSheet("color: #00D2FF; font-weight: bold;")
        rb_layout.addWidget(self.lbl_resize_status)

        layout.addWidget(resizer_box)
        layout.addStretch()

    # -------------------------------------------------------------
    # TAB 3: Менеджер Игр
    # -------------------------------------------------------------
    def setup_roms_tab(self):
        layout = QVBoxLayout(self.tab_roms)
        layout.setSpacing(12)

        folder_box = QGroupBox("Папка с играми (/game)")
        fb_layout = QHBoxLayout(folder_box)
        self.rom_dir_input = QLineEdit()
        self.rom_dir_input.setPlaceholderText("Выберите каталог с играми (например, E:\\game)")
        btn_browse_roms = QPushButton("Обзор...")
        btn_browse_roms.clicked.connect(self.browse_rom_dir)
        btn_scan_roms = QPushButton("Сканировать")
        btn_scan_roms.setObjectName("PrimaryBtn")
        btn_scan_roms.clicked.connect(self.scan_roms)

        fb_layout.addWidget(self.rom_dir_input)
        fb_layout.addWidget(btn_browse_roms)
        fb_layout.addWidget(btn_scan_roms)
        layout.addWidget(folder_box)

        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Платформа:"))
        self.combo_filter_platform = QComboBox()
        self.combo_filter_platform.addItem("Все платформы", "all")
        for k, v in PLATFORMS.items():
            self.combo_filter_platform.addItem(f"{v['name']} ({k})", k)
        self.combo_filter_platform.currentIndexChanged.connect(self.filter_rom_table)
        filter_layout.addWidget(self.combo_filter_platform)

        filter_layout.addWidget(QLabel("Поиск:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Поиск игры...")
        self.search_input.textChanged.connect(self.filter_rom_table)
        filter_layout.addWidget(self.search_input)
        layout.addLayout(filter_layout)

        # Панель управления выбором
        sel_bar = QHBoxLayout()
        btn_sel_all = QPushButton("☑ Выбрать все")
        btn_sel_all.clicked.connect(self.select_all_roms)
        btn_desel_all = QPushButton("☐ Снять выбор")
        btn_desel_all.clicked.connect(self.deselect_all_roms)
        btn_invert = QPushButton("Инвертировать")
        btn_invert.clicked.connect(self.invert_rom_selection)
        self.lbl_selected_stats = QLabel("Выбрано игр: 0 (0.0 MB)")
        self.lbl_selected_stats.setStyleSheet("color: #00D2FF; font-weight: bold; margin-left: 10px;")

        sel_bar.addWidget(btn_sel_all)
        sel_bar.addWidget(btn_desel_all)
        sel_bar.addWidget(btn_invert)
        sel_bar.addWidget(self.lbl_selected_stats)
        sel_bar.addStretch()
        layout.addLayout(sel_bar)

        splitter = QSplitter(Qt.Horizontal)
        self.table_roms = QTableWidget(0, 6)
        self.table_roms.setHorizontalHeaderLabels(["Выбор", "Платформа", "Название", "Файл", "Размер", "Обложка"])
        self.table_roms.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table_roms.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table_roms.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table_roms.itemSelectionChanged.connect(self.on_rom_selected)
        self.table_roms.itemChanged.connect(self.on_rom_table_item_changed)
        splitter.addWidget(self.table_roms)

        preview_panel = QFrame()
        preview_panel.setFrameShape(QFrame.StyledPanel)
        preview_panel.setMinimumWidth(260)
        pv_layout = QVBoxLayout(preview_panel)
        pv_layout.setAlignment(Qt.AlignCenter)

        self.lbl_cover_preview = QLabel("Нет обложки")
        self.lbl_cover_preview.setFixedSize(220, 220)
        self.lbl_cover_preview.setAlignment(Qt.AlignCenter)
        self.lbl_cover_preview.setStyleSheet("border: 2px dashed #2E3440; border-radius: 8px;")
        pv_layout.addWidget(self.lbl_cover_preview)

        self.lbl_selected_title = QLabel("")
        self.lbl_selected_title.setWordWrap(True)
        self.lbl_selected_title.setAlignment(Qt.AlignCenter)
        self.lbl_selected_title.setStyleSheet("font-weight: bold; color: #00D2FF; margin-top: 10px;")
        pv_layout.addWidget(self.lbl_selected_title)

        pv_layout.addStretch()
        splitter.addWidget(preview_panel)
        splitter.setSizes([750, 250])
        layout.addWidget(splitter)

        bottom_layout = QHBoxLayout()
        self.lbl_rom_count = QLabel("Игр: 0")
        self.lbl_rom_count.setStyleSheet("color: #8C92A0;")
        bottom_layout.addWidget(self.lbl_rom_count)

        btn_scrape = QPushButton("Загрузить обложки")
        btn_scrape.clicked.connect(self.start_scrape)
        bottom_layout.addWidget(btn_scrape)

        bottom_layout.addStretch()

        btn_build_db = QPushButton("⚡ Перестроить games.db локально")
        btn_build_db.clicked.connect(self.build_games_db)
        bottom_layout.addWidget(btn_build_db)

        layout.addLayout(bottom_layout)

        self.scrape_progress = QProgressBar()
        self.scrape_progress.setVisible(False)
        layout.addWidget(self.scrape_progress)

        # Панель записи выбранных игр на SD-карту
        sd_box = QGroupBox("Запись выбранных игр на карту памяти (SD-карту)")
        sd_box_layout = QVBoxLayout(sd_box)
        sd_box_layout.setSpacing(8)

        h_sd = QHBoxLayout()
        h_sd.addWidget(QLabel("Целевой диск / флешка:"))
        self.combo_sd_drives = QComboBox()
        btn_refresh_sd = QPushButton("Обновить диски")
        btn_refresh_sd.clicked.connect(self.refresh_sd_drives)
        btn_browse_sd = QPushButton("Выбрать папку вручную...")
        btn_browse_sd.clicked.connect(self.browse_custom_sd_drive)

        h_sd.addWidget(self.combo_sd_drives, 2)
        h_sd.addWidget(btn_refresh_sd)
        btn_assign_letter = QPushButton("⚡ Назначить букву флешке")
        btn_assign_letter.setToolTip("Если раздел с играми на SD-карте не виден в Проводнике Windows, нажмите сюда для автоматического назначения буквы диска")
        btn_assign_letter.clicked.connect(self.auto_assign_sd_letter)
        h_sd.addWidget(btn_assign_letter)

        btn_fix_launcher = QPushButton("🛠 Исправить запуск игр")
        btn_fix_launcher.setToolTip("Устраняет вылеты игр: обновляет start_game.sh без блокировок и восстанавливает необходимые системные файлы на флешке")
        btn_fix_launcher.clicked.connect(self.fix_sd_game_launcher)
        h_sd.addWidget(btn_fix_launcher)
        h_sd.addWidget(btn_browse_sd)
        sd_box_layout.addLayout(h_sd)

        self.btn_copy_to_sd = QPushButton("💾 Записать выбранные игры на флешку и обновить games.db")
        self.btn_copy_to_sd.setObjectName("SuccessBtn")
        self.btn_copy_to_sd.clicked.connect(self.start_copy_to_sd)
        sd_box_layout.addWidget(self.btn_copy_to_sd)

        self.copy_sd_progress = QProgressBar()
        self.copy_sd_progress.setVisible(False)
        sd_box_layout.addWidget(self.copy_sd_progress)

        self.lbl_copy_sd_status = QLabel("")
        self.lbl_copy_sd_status.setStyleSheet("color: #48BB78; font-weight: bold;")
        sd_box_layout.addWidget(self.lbl_copy_sd_status)

        layout.addWidget(sd_box)
        self.refresh_sd_drives()

    # -------------------------------------------------------------
    # TAB 4: Патчер Аппаратных Ревизий Ядра
    # -------------------------------------------------------------
    def setup_revisions_tab(self):
        layout = QVBoxLayout(self.tab_revisions)
        layout.setSpacing(12)

        # 1. Выбор целевого объекта (Образ или SD-карта)
        target_group = QGroupBox("1. Выбор целевого объекта для патчинга")
        tg_layout = QVBoxLayout(target_group)
        tg_layout.setSpacing(8)

        h_types = QHBoxLayout()
        self.radio_patch_file = QRadioButton("Файл образа (.img)")
        self.radio_patch_file.setChecked(True)
        self.radio_patch_disk = QRadioButton("Физический накопитель (SD-карта)")
        self.radio_patch_file.toggled.connect(self.on_patch_target_type_changed)
        h_types.addWidget(self.radio_patch_file)
        h_types.addWidget(self.radio_patch_disk)
        h_types.addStretch()
        tg_layout.addLayout(h_types)

        # Виджет выбора файла
        self.patch_file_widget = QWidget()
        pf_layout = QHBoxLayout(self.patch_file_widget)
        pf_layout.setContentsMargins(0, 0, 0, 0)
        self.patch_file_input = QLineEdit()
        orig_img = os.path.abspath("stick-ow.pro_OpenWorld_ML_Store_Edition.img")
        min_img = os.path.abspath("game_stick_minimalv2.img")
        if os.path.isfile(orig_img):
            self.patch_file_input.setText(orig_img)
        elif os.path.isfile(min_img):
            self.patch_file_input.setText(min_img)
        self.patch_file_input.setPlaceholderText("Выберите файл образа (.img)")
        btn_browse_patch = QPushButton("Обзор...")
        btn_browse_patch.clicked.connect(self.browse_patch_file)
        pf_layout.addWidget(self.patch_file_input)
        pf_layout.addWidget(btn_browse_patch)
        tg_layout.addWidget(self.patch_file_widget)

        # Виджет выбора диска
        self.patch_disk_widget = QWidget()
        self.patch_disk_widget.setVisible(False)
        pd_layout = QHBoxLayout(self.patch_disk_widget)
        pd_layout.setContentsMargins(0, 0, 0, 0)
        self.combo_patch_disks = QComboBox()
        btn_refresh_patch_disks = QPushButton("Обновить накопители")
        btn_refresh_patch_disks.clicked.connect(self.refresh_patch_disks)
        pd_layout.addWidget(self.combo_patch_disks, 3)
        pd_layout.addWidget(btn_refresh_patch_disks, 1)
        tg_layout.addWidget(self.patch_disk_widget)

        # Кнопка инспекции
        h_insp = QHBoxLayout()
        btn_inspect = QPushButton("🔍 Определить текущую ревизию ядра")
        btn_inspect.clicked.connect(self.inspect_patch_target)
        self.lbl_patch_current_info = QLabel("Нажмите «Определить» для анализа ядра в выбранном объекте")
        self.lbl_patch_current_info.setStyleSheet("color: #A0AEC0; font-weight: 500;")
        h_insp.addWidget(btn_inspect)
        h_insp.addWidget(self.lbl_patch_current_info, 1)
        tg_layout.addLayout(h_insp)

        layout.addWidget(target_group)

        # 2. Выбор ревизии и применение патча
        patch_group = QGroupBox("2. Выбор аппаратной ревизии для записи")
        pg_layout = QVBoxLayout(patch_group)
        pg_layout.setSpacing(10)

        h_sel = QHBoxLayout()
        h_sel.addWidget(QLabel("Требуемая ревизия ядра:"))
        self.combo_patch_rev = QComboBox()
        for crc, info in KNOWN_REVISIONS.items():
            self.combo_patch_rev.addItem(f"{info['name']} (CRC: {crc})", crc)
        h_sel.addWidget(self.combo_patch_rev, 2)
        pg_layout.addLayout(h_sel)

        self.lbl_rev_desc = QLabel("")
        self.lbl_rev_desc.setStyleSheet("color: #00D2FF; margin-bottom: 4px;")
        self.combo_patch_rev.currentIndexChanged.connect(self.on_patch_rev_changed)
        pg_layout.addWidget(self.lbl_rev_desc)
        self.on_patch_rev_changed()

        self.btn_apply_patch = QPushButton("⚡ Применить патч ревизии к выбранному объекту")
        self.btn_apply_patch.setObjectName("SuccessBtn")
        self.btn_apply_patch.clicked.connect(self.apply_patch_revision)
        pg_layout.addWidget(self.btn_apply_patch)

        self.patch_progress = QProgressBar()
        self.patch_progress.setVisible(False)
        pg_layout.addWidget(self.patch_progress)

        self.lbl_patch_status = QLabel("")
        pg_layout.addWidget(self.lbl_patch_status)

        layout.addWidget(patch_group)

        # 3. Справочная таблица
        ref_box = QGroupBox("Справочник аппаратных ревизий Game Stick Lite")
        rf_layout = QVBoxLayout(ref_box)
        self.table_revs = QTableWidget(0, 3)
        self.table_revs.setHorizontalHeaderLabels(["Модель стика", "CRC32 ядра", "Описание"])
        self.table_revs.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table_revs.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table_revs.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)

        for crc, info in KNOWN_REVISIONS.items():
            row = self.table_revs.rowCount()
            self.table_revs.insertRow(row)
            self.table_revs.setItem(row, 0, QTableWidgetItem(info["name"]))
            self.table_revs.setItem(row, 1, QTableWidgetItem(crc))
            self.table_revs.setItem(row, 2, QTableWidgetItem(info["description"]))

        rf_layout.addWidget(self.table_revs)
        layout.addWidget(ref_box)

    def on_patch_target_type_changed(self):
        is_file = self.radio_patch_file.isChecked()
        self.patch_file_widget.setVisible(is_file)
        self.patch_disk_widget.setVisible(not is_file)
        if not is_file:
            self.refresh_patch_disks()

    def browse_patch_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Выберите файл образа для патчинга", "", "Disk Images (*.img);;All Files (*.*)")
        if path:
            self.patch_file_input.setText(path)
            self.inspect_patch_target()

    def refresh_patch_disks(self):
        self.combo_patch_disks.clear()
        self.detected_disks = DiskDetector.get_available_disks()
        for idx, disk in enumerate(self.detected_disks):
            self.combo_patch_disks.addItem(disk.display_str(), idx)

    def get_patch_target_path(self) -> Optional[str]:
        if self.radio_patch_file.isChecked():
            path = self.patch_file_input.text().strip()
            if not path or not os.path.isfile(path):
                QMessageBox.warning(self, "Внимание", "Укажите существующий файл образа (.img).")
                return None
            return path
        else:
            idx = self.combo_patch_disks.currentData()
            if idx is None or idx >= len(self.detected_disks):
                QMessageBox.warning(self, "Внимание", "Выберите накопитель из списка.")
                return None
            disk = self.detected_disks[idx]
            if disk.is_system:
                QMessageBox.critical(self, "Запрещено", "Системный диск с Windows изменять строго запрещено!")
                return None
            return disk.device_id

    def inspect_patch_target(self):
        target = self.get_patch_target_path()
        if not target:
            return
        try:
            info = BootRevisionManager.inspect_boot(target)
            crc = info["crc32"]
            name = info["revision_name"]
            valid = info["is_valid"]
            if valid:
                self.lbl_patch_current_info.setText(f"✔ Обнаружено ядро: {name} (CRC: {crc})")
                self.lbl_patch_current_info.setStyleSheet("color: #48BB78; font-weight: bold;")
                idx = self.combo_patch_rev.findData(crc)
                if idx >= 0:
                    self.combo_patch_rev.setCurrentIndex(idx)
            else:
                self.lbl_patch_current_info.setText(f"⚠️ Раздел boot поврежден или не содержит Android-заголовка (CRC: {crc})")
                self.lbl_patch_current_info.setStyleSheet("color: #ECC94B; font-weight: bold;")
        except Exception as e:
            self.lbl_patch_current_info.setText(f"Ошибка чтения: {e}")
            self.lbl_patch_current_info.setStyleSheet("color: #E53E3E; font-weight: bold;")

    def on_patch_rev_changed(self):
        crc = self.combo_patch_rev.currentData()
        info = KNOWN_REVISIONS.get(crc, {})
        self.lbl_rev_desc.setText(f"ℹ {info.get('description', '')}")

    def apply_patch_revision(self):
        target = self.get_patch_target_path()
        if not target:
            return
        rev_crc = self.combo_patch_rev.currentData()
        info = KNOWN_REVISIONS.get(rev_crc, {})
        rev_name = info.get("name", rev_crc)

        reply = QMessageBox.question(
            self, "Подтверждение патча",
            f"Записать аппаратную ревизию ядра:\n\n{rev_name} (CRC: {rev_crc})\n\n"
            f"Целевой объект: {target}\n\n"
            f"Раздел boot (LBA 14336, 9 MiB) будет перезаписан выбранным ядром.\n"
            f"Метка тома exFAT будет обновлена под соответствующую ревизию.\n\nПродолжить?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        self.btn_apply_patch.setEnabled(False)
        self.patch_progress.setVisible(True)
        self.patch_progress.setRange(0, 0)
        self.lbl_patch_status.setText("Применение патча ревизии ядра...")

        ok, msg = BootRevisionManager.patch_revision(target, rev_crc)

        self.btn_apply_patch.setEnabled(True)
        self.patch_progress.setVisible(False)

        if ok:
            self.lbl_patch_status.setText("Патч успешно применен!")
            self.inspect_patch_target()
            QMessageBox.information(self, "Успех", msg)
        else:
            self.lbl_patch_status.setText("Ошибка применения патча.")
            QMessageBox.critical(self, "Ошибка", msg)

    # -------------------------------------------------------------
    # Обработчики
    # -------------------------------------------------------------
    def browse_output_image(self):
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить образ", "game_stick_minimal.img", "Disk Images (*.img)")
        if path:
            self.output_img_input.setText(path)

    def browse_extra_roms(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Выберите папку с играми для интеграции")
        if dir_path:
            self.input_extra_roms.setText(dir_path)

    def start_build_image(self):
        out_path = self.output_img_input.text().strip()
        if not out_path:
            QMessageBox.warning(self, "Внимание", "Укажите путь для сохранения файла .img.")
            return

        rev_crc = self.combo_build_rev.currentData()
        size_mib = self.combo_userdata_size.currentData()
        extra_roms = self.input_extra_roms.text().strip() or None

        self.btn_build_img.setEnabled(False)
        self.build_progress.setVisible(True)
        self.build_progress.setValue(0)
        self.lbl_build_status.setText("Инициализация автономной сборки...")

        self.build_worker = BuildImageWorker(out_path, rev_crc, size_mib, extra_roms)
        self.build_worker.progress.connect(self.on_build_progress)
        self.build_worker.finished.connect(self.on_build_finished)
        self.build_worker.start()

    def on_build_progress(self, msg: str, pct: int):
        self.build_progress.setValue(pct)
        self.lbl_build_status.setText(f"{msg} ({pct}%)")

    def on_build_finished(self, success: bool, message: str):
        self.btn_build_img.setEnabled(True)
        self.build_progress.setVisible(False)
        if success:
            self.lbl_build_status.setText("Образ готов к прошивке!")
            QMessageBox.information(self, "Сборка завершена", f"{message}\n\nОбраз готов к записи на SD-карту!")
        else:
            self.lbl_build_status.setText("Ошибка сборки.")
            QMessageBox.critical(self, "Ошибка", message)

    def refresh_disks(self):
        self.combo_resizer_disks.clear()
        self.combo_writer_disks.clear()
        self.detected_disks = DiskDetector.get_available_disks()

        for idx, disk in enumerate(self.detected_disks):
            self.combo_resizer_disks.addItem(disk.display_str(), idx)
            self.combo_writer_disks.addItem(disk.display_str(), idx)

        self.on_resizer_disk_selected()
        self.on_writer_disk_selected()

    def browse_writer_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Выберите файл образа для записи", "", "Disk Images (*.img *.bin);;All Files (*.*)"
        )
        if path:
            self.writer_img_input.setText(path)

    def on_writer_disk_selected(self):
        idx = self.combo_writer_disks.currentData()
        if idx is None or idx >= len(self.detected_disks):
            self.lbl_writer_warning.setText("")
            self.btn_write_image.setEnabled(False)
            return

        disk = self.detected_disks[idx]
        if disk.is_system:
            self.lbl_writer_warning.setText("⛔ СИСТЕМНЫЙ ДИСК С ОС! Прямая запись строго заблокирована.")
            self.lbl_writer_warning.setStyleSheet("color: #E53E3E; font-weight: bold;")
            self.btn_write_image.setEnabled(False)
        else:
            self.lbl_writer_warning.setText(
                f"⚠️ ВНИМАНИЕ: Все существующие разделы и данные на «{disk.name}» ({disk.size_gib:.1f} GiB) будут уничтожены!"
            )
            self.lbl_writer_warning.setStyleSheet("color: #ECC94B; font-weight: bold;")
            self.btn_write_image.setEnabled(True)

    def start_write_image(self):
        img_path = self.writer_img_input.text().strip()
        if not img_path or not os.path.isfile(img_path):
            QMessageBox.warning(self, "Внимание", "Укажите существующий файл образа (.img).")
            return

        idx = self.combo_writer_disks.currentData()
        if idx is None or idx >= len(self.detected_disks):
            QMessageBox.warning(self, "Внимание", "Выберите целевой накопитель.")
            return

        target_disk = self.detected_disks[idx]
        if target_disk.is_system:
            QMessageBox.critical(self, "Запрещено", "Запись на системный диск строго запрещена!")
            return

        img_size_gb = os.path.getsize(img_path) / (1024 ** 3)
        if img_size_gb > target_disk.size_gib:
            QMessageBox.critical(
                self, "Ошибка размера",
                f"Размер образа ({img_size_gb:.2f} GiB) превышает емкость накопителя ({target_disk.size_gib:.2f} GiB)!"
            )
            return

        reply = QMessageBox.warning(
            self, "ПОДТВЕРЖДЕНИЕ УНИЧТОЖЕНИЯ ДАННЫХ",
            f"ВНИМАНИЕ! ВСЕ ДАННЫЕ И РАЗДЕЛЫ НА НАКОПИТЕЛЕ БУДУТ БЕЗВОЗВРАТНО УНИЧТОЖЕНЫ!\n\n"
            f"Целевой накопитель:\n{target_disk.display_str()}\n\n"
            f"Файл образа:\n{img_path} ({img_size_gb:.2f} GiB)\n\n"
            f"Вы уверены, что хотите начать прямую секторную запись?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        self.btn_write_image.setEnabled(False)
        self.btn_resize.setEnabled(False)
        self.writer_progress.setVisible(True)
        self.writer_progress.setValue(0)
        self.lbl_writer_status.setText("Подготовка к записи (демонтирование томов)...")

        self.writer_worker = WriteImageWorker(img_path, target_disk.device_id, target_disk.is_system)
        self.writer_worker.progress.connect(self.on_write_image_progress)
        self.writer_worker.finished.connect(self.on_write_image_finished)
        self.writer_worker.start()

    def on_write_image_progress(self, cur: int, tot: int, spd: float, status: str):
        if tot > 0:
            pct = int((cur / tot) * 100)
            self.writer_progress.setValue(pct)
            cur_mb = cur / (1024 * 1024)
            tot_mb = tot / (1024 * 1024)
            self.lbl_writer_status.setText(f"{status} [{cur_mb:.1f} / {tot_mb:.1f} MB] • {spd:.1f} MB/s ({pct}%)")
        else:
            self.lbl_writer_status.setText(status)

    def on_write_image_finished(self, ok: bool, msg: str):
        self.btn_write_image.setEnabled(True)
        self.btn_resize.setEnabled(True)
        self.writer_progress.setVisible(False)
        if ok:
            self.lbl_writer_status.setText("✔ Образ успешно записан на накопитель!")
            self.auto_assign_sd_letter()
            self.refresh_sd_drives()
            QMessageBox.information(
                self, "Запись завершена",
                f"{msg}\n\nОбраз успешно записан на карту памяти!"
            )
        else:
            self.lbl_writer_status.setText("❌ Ошибка записи образа")
            QMessageBox.critical(self, "Ошибка записи", msg)

    def on_resizer_disk_selected(self):
        idx = self.combo_resizer_disks.currentData()
        if idx is None or idx >= len(self.detected_disks):
            self.lbl_resizer_warning.setText("")
            self.btn_resize.setEnabled(False)
            return

        disk = self.detected_disks[idx]
        if disk.is_system:
            self.lbl_resizer_warning.setText("⛔ СИСТЕМНЫЙ ДИСК С ОС! Модификация строго заблокирована.")
            self.lbl_resizer_warning.setStyleSheet("color: #E53E3E; font-weight: bold;")
            self.btn_resize.setEnabled(False)
        else:
            self.lbl_resizer_warning.setText(f"✔ Накопитель: {disk.name} ({disk.size_gib:.1f} GiB). Готов к расширению.")
            self.lbl_resizer_warning.setStyleSheet("color: #48BB78; font-weight: bold;")
            self.btn_resize.setEnabled(True)

    def run_partition_resize(self):
        idx = self.combo_resizer_disks.currentData()
        if idx is None or idx >= len(self.detected_disks):
            return

        target_disk = self.detected_disks[idx]
        if target_disk.is_system:
            QMessageBox.critical(self, "Ошибка", "Нельзя изменять системный диск!")
            return

        reply = QMessageBox.question(
            self, "Подтверждение расширения",
            f"Выполнить In-Place расширение раздела игр на накопителе:\n\n{target_disk.display_str()}\n\n"
            f"Раздел exFAT будет расширен на весь доступный объём карты ({target_disk.size_gib:.1f} GiB).\n"
            f"Существующие файлы останутся нетронутыми.\n\nПродолжить?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        self.btn_resize.setEnabled(False)
        self.lbl_resize_status.setText("Выполняется пересчет GPT и exFAT...")

        ok, msg = PartitionResizer.expand_device(target_disk.device_id, target_disk.size_bytes)
        self.btn_resize.setEnabled(True)

        if ok:
            self.lbl_resize_status.setText("Успешно расширено!")
            QMessageBox.information(self, "Успех", msg)
        else:
            self.lbl_resize_status.setText("Ошибка расширения.")
            QMessageBox.critical(self, "Ошибка", msg)

    def browse_rom_dir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Выберите папку с играми")
        if dir_path:
            self.rom_dir_input.setText(dir_path)
            self.scan_roms()

    def scan_roms(self):
        dir_path = self.rom_dir_input.text().strip()
        if not dir_path or not os.path.isdir(dir_path):
            return

        self.rom_manager = RomManager(dir_path)
        items = self.rom_manager.scan()
        self.populate_rom_table(items)

    def populate_rom_table(self, items: list):
        self.table_roms.blockSignals(True)
        self.table_roms.setRowCount(0)
        for item in items:
            row = self.table_roms.rowCount()
            self.table_roms.insertRow(row)

            # 0: Checkbox
            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            chk_item.setCheckState(Qt.Checked)
            self.table_roms.setItem(row, 0, chk_item)

            # 1: Platform
            p_name = PLATFORMS.get(item.platform_key, {}).get("name", item.platform_key)
            self.table_roms.setItem(row, 1, QTableWidgetItem(p_name))

            # 2: Display name
            name_item = QTableWidgetItem(item.display_name)
            name_item.setData(Qt.UserRole, item)
            self.table_roms.setItem(row, 2, name_item)

            # 3: Filename
            self.table_roms.setItem(row, 3, QTableWidgetItem(item.filename))

            # 4: Size
            self.table_roms.setItem(row, 4, QTableWidgetItem(f"{item.size_bytes / (1024 * 1024):.2f} MB"))

            # 5: Cover
            cover_item = QTableWidgetItem("✔ Есть" if item.has_cover else "— Нет")
            cover_item.setForeground(Qt.green if item.has_cover else Qt.gray)
            self.table_roms.setItem(row, 5, cover_item)

        self.table_roms.blockSignals(False)
        self.lbl_rom_count.setText(f"Игр в списке: {len(items)}")
        self.update_selected_stats()

    def update_selected_stats(self):
        count = 0
        total_bytes = 0
        for row in range(self.table_roms.rowCount()):
            chk_item = self.table_roms.item(row, 0)
            if chk_item and chk_item.checkState() == Qt.Checked:
                rom_item = self.table_roms.item(row, 2).data(Qt.UserRole)
                if rom_item:
                    count += 1
                    total_bytes += rom_item.size_bytes
        mb = total_bytes / (1024 * 1024)
        if mb >= 1024:
            size_str = f"{mb / 1024:.2f} GB"
        else:
            size_str = f"{mb:.1f} MB"
        self.lbl_selected_stats.setText(f"Выбрано игр: {count} ({size_str})")

    def select_all_roms(self):
        self.table_roms.blockSignals(True)
        for row in range(self.table_roms.rowCount()):
            item = self.table_roms.item(row, 0)
            if item:
                item.setCheckState(Qt.Checked)
        self.table_roms.blockSignals(False)
        self.update_selected_stats()

    def deselect_all_roms(self):
        self.table_roms.blockSignals(True)
        for row in range(self.table_roms.rowCount()):
            item = self.table_roms.item(row, 0)
            if item:
                item.setCheckState(Qt.Unchecked)
        self.table_roms.blockSignals(False)
        self.update_selected_stats()

    def invert_rom_selection(self):
        self.table_roms.blockSignals(True)
        for row in range(self.table_roms.rowCount()):
            item = self.table_roms.item(row, 0)
            if item:
                new_state = Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked
                item.setCheckState(new_state)
        self.table_roms.blockSignals(False)
        self.update_selected_stats()

    def on_rom_table_item_changed(self, item):
        if item.column() == 0:
            self.update_selected_stats()

    def filter_rom_table(self):
        if not self.rom_manager:
            return
        selected_p = self.combo_filter_platform.currentData()
        search_text = self.search_input.text().lower().strip()

        filtered = []
        for item in self.rom_manager.items:
            if selected_p != "all" and item.platform_key != selected_p:
                continue
            if search_text and (search_text not in item.display_name.lower() and search_text not in item.filename.lower()):
                continue
            filtered.append(item)

        self.populate_rom_table(filtered)

    def on_rom_selected(self):
        selected_items = self.table_roms.selectedItems()
        if not selected_items:
            return
        row = selected_items[0].row()
        item = self.table_roms.item(row, 2).data(Qt.UserRole)
        if not item:
            return

        self.lbl_selected_title.setText(item.display_name)
        if item.has_cover and item.cover_path and os.path.isfile(item.cover_path):
            pixmap = QPixmap(item.cover_path).scaled(220, 220, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.lbl_cover_preview.setPixmap(pixmap)
        else:
            self.lbl_cover_preview.setText("Нет обложки")
            self.lbl_cover_preview.setPixmap(QPixmap())

    def auto_assign_sd_letter(self):
        """
        Проверяет подключенные съемные физические накопители (SD-карты / USB)
        на наличие 5-го раздела (userdata / exFAT).
        Если у раздела нет буквы диска в Windows Explorer, назначает свободную букву.
        """
        try:
            import subprocess
            ps_script = (
                "$disks = Get-Disk | Where-Object { $_.BusType -eq 'USB' -or $_.MediaType -eq 'Removable' -or $_.OperationalStatus -eq 'Online' }; "
                "$assigned = $false; "
                "foreach ($d in $disks) { "
                "  $parts = Get-Partition -DiskNumber $d.Number -ErrorAction SilentlyContinue | "
                "    Where-Object { $_.PartitionNumber -eq 5 -or $_.GptType -eq '{ebd0a0a2-b9e5-4433-87c0-68b6b72699c7}' }; "
                "  foreach ($p in $parts) { "
                "    if (-not $p.DriveLetter) { "
                "      $used = (Get-Volume).DriveLetter; "
                "      $free = [char[]](69..90) | Where-Object { $_ -notin $used } | Select-Object -First 1; "
                "      if ($free) { "
                "        Set-Partition -DiskNumber $d.Number -PartitionNumber $p.PartitionNumber -NewDriveLetter $free -ErrorAction SilentlyContinue; "
                "        $assigned = $true; "
                "      } "
                "    } "
                "  } "
                "}; "
                "Write-Output $assigned"
            )
            subprocess.run(["powershell", "-NoProfile", "-Command", ps_script], capture_output=True, timeout=8)
        except Exception:
            pass
        self._refresh_sd_drives_internal()

    def fix_sd_game_launcher(self):
        """
        Восстанавливает системный загрузчик start_game.sh (без скрытых проверок),
        системный файл ReBuild.exe, конфигурации и все недостающие ядра эмуляторов (retro_lib)
        на целевой флешке.
        """
        target_path = self.combo_sd_drives.currentData()
        if not target_path or not os.path.exists(target_path):
            QMessageBox.warning(self, "Внимание", "Выберите целевой диск (флешку) в списке.")
            return

        base_template = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "firmware_base", "userdata_template")
        src_sh = os.path.join(base_template, "minigui", "start_game.sh")
        src_rebuild = os.path.join(base_template, "game", "ReBuild.exe")
        src_cfg = os.path.join(base_template, "minigui", "gbx66.cfg")
        src_retro_lib = os.path.join(base_template, "retro_lib")
        src_retroarch = os.path.join(base_template, "retroarch")

        minigui_dir = os.path.join(target_path, "minigui")
        game_dir = os.path.join(target_path, "game")
        retro_lib_dir = os.path.join(target_path, "retro_lib")
        retroarch_dir = os.path.join(target_path, "retroarch")

        os.makedirs(minigui_dir, exist_ok=True)
        os.makedirs(game_dir, exist_ok=True)
        os.makedirs(retro_lib_dir, exist_ok=True)
        os.makedirs(retroarch_dir, exist_ok=True)

        repaired = []
        import shutil

        # 1. start_game.sh
        if os.path.isfile(src_sh):
            try:
                shutil.copy2(src_sh, os.path.join(minigui_dir, "start_game.sh"))
                repaired.append("start_game.sh (устранены вылеты и скрытые проверки)")
            except Exception as e:
                repaired.append(f"start_game.sh (ошибка: {e})")

        # 2. ReBuild.exe
        if os.path.isfile(src_rebuild):
            try:
                dst_rebuild = os.path.join(game_dir, "ReBuild.exe")
                if not os.path.isfile(dst_rebuild) or os.path.getsize(dst_rebuild) != os.path.getsize(src_rebuild):
                    shutil.copy2(src_rebuild, dst_rebuild)
                    repaired.append("ReBuild.exe (системный файл ресурсов)")
            except Exception as e:
                repaired.append(f"ReBuild.exe (ошибка: {e})")

        # 3. gbx66.cfg
        if os.path.isfile(src_cfg):
            try:
                dst_cfg = os.path.join(minigui_dir, "gbx66.cfg")
                if not os.path.isfile(dst_cfg):
                    shutil.copy2(src_cfg, dst_cfg)
                    repaired.append("gbx66.cfg (базовая конфигурация)")
            except Exception as e:
                repaired.append(f"gbx66.cfg (ошибка: {e})")

        # 4. retroarch.cfg
        src_ra_cfg = os.path.join(src_retroarch, "retroarch.cfg")
        if os.path.isfile(src_ra_cfg):
            try:
                dst_ra_cfg = os.path.join(retroarch_dir, "retroarch.cfg")
                if not os.path.isfile(dst_ra_cfg):
                    shutil.copy2(src_ra_cfg, dst_ra_cfg)
                    repaired.append("retroarch.cfg (конфигурация RetroArch)")
            except Exception as e:
                pass

        # 5. retro_lib cores (критично: nestopia, genesisplusgx, snes9x, mgba, pcsx_rearmed, fbalpha2012, etc.)
        copied_cores = []
        if os.path.isdir(src_retro_lib):
            for core_file in os.listdir(src_retro_lib):
                if core_file.endswith(".so") or "_libretro" in core_file:
                    s_core = os.path.join(src_retro_lib, core_file)
                    d_core = os.path.join(retro_lib_dir, core_file)
                    if not os.path.isfile(d_core) or os.path.getsize(d_core) == 0:
                        try:
                            shutil.copy2(s_core, d_core)
                            copied_cores.append(core_file)
                        except Exception:
                            pass

        if copied_cores:
            repaired.append(f"Восстановлено {len(copied_cores)} недостающих ядер эмуляторов в retro_lib/ (включая nestopia, genesisplusgx и др.)")

        # 6. Защита разделов категорий от NULL-pointer вылета в меню Class (PS1 и др.)
        if os.path.isdir(game_dir):
            try:
                RomManager.ensure_category_safeguards(game_dir)
                repaired.append("Защита меню категорий от вылетов (меню Class / PS1)")
            except Exception:
                pass

        # 7. Синхронизация games.db и database.sqlite3 между /game/ и корнем флешки
        src_gdb = os.path.join(game_dir, "games.db")
        dst_gdb = os.path.join(target_path, "games.db")
        if os.path.isfile(src_gdb):
            try:
                shutil.copy2(src_gdb, dst_gdb)
            except Exception:
                pass
        src_sql = os.path.join(game_dir, "database.sqlite3")
        dst_sql = os.path.join(target_path, "database.sqlite3")
        if os.path.isfile(src_sql):
            try:
                shutil.copy2(src_sql, dst_sql)
            except Exception:
                pass

        if repaired:
            QMessageBox.information(
                self, "Успешно",
                "✔ Запуск игр на флешке успешно восстановлен!\n\n"
                "Обновлены компоненты:\n• " + "\n• ".join(repaired) + "\n\n"
                "Теперь игры (Dendy/NES, Sega, SNES, GBA, PS1 и др.) будут запускаться на приставке без вылетов."
            )
        else:
            QMessageBox.information(
                self, "Информация",
                f"Все системные файлы и ядра эмуляторов на накопителе {target_path} уже находятся в актуальном состоянии."
            )


    def refresh_sd_drives(self):
        self._refresh_sd_drives_internal()
        # Если съемные накопители не найдены, пробуем один раз проверить скрытые разделы
        if self.combo_sd_drives.count() == 1 and self.combo_sd_drives.itemData(0) == "":
            self.auto_assign_sd_letter()

    def _refresh_sd_drives_internal(self):
        self.combo_sd_drives.clear()
        import ctypes
        import string
        import shutil
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        removable_drives = []
        all_drives = []
        sys_drive_letter = os.environ.get("SystemDrive", "C:")[0].upper()

        for letter in string.ascii_uppercase:
            if bitmask & 1:
                # Ни при каких обстоятельствах не предлагаем системный диск с Windows
                if letter.upper() == sys_drive_letter:
                    bitmask >>= 1
                    continue

                drive_path = f"{letter}:\\"
                drive_type = ctypes.windll.kernel32.GetDriveTypeW(drive_path)
                try:
                    usage = shutil.disk_usage(drive_path)
                    free_gb = usage.free / (1024 ** 3)
                    total_gb = usage.total / (1024 ** 3)
                    label = f"Диск {letter}: ({total_gb:.1f} GB, свободно {free_gb:.1f} GB)"
                except Exception:
                    label = f"Диск {letter}:"

                if drive_type == 2:  # DRIVE_REMOVABLE
                    removable_drives.append((drive_path, f"⚡ [Съемный] {label}"))
                elif drive_type == 3:  # Физический локальный накопитель
                    all_drives.append((drive_path, f"[Локальный] {label}"))
            bitmask >>= 1

        for p, lbl in removable_drives:
            self.combo_sd_drives.addItem(lbl, p)
        for p, lbl in all_drives:
            self.combo_sd_drives.addItem(lbl, p)

        if self.combo_sd_drives.count() == 0:
            self.combo_sd_drives.addItem("Накопители не найдены (выберите папку вручную)", "")

    def browse_custom_sd_drive(self):
        path = QFileDialog.getExistingDirectory(self, "Выберите корень SD-карты или папку для записи игр")
        if path:
            sys_drive = os.environ.get("SystemDrive", "C:").upper()
            target_drive = os.path.splitdrive(os.path.abspath(path))[0].upper()
            if target_drive == sys_drive:
                QMessageBox.critical(
                    self, "Защита системы",
                    f"⛔ Выбор системного диска ({sys_drive}) запрещен!\n"
                    f"Запись игр на диск с операционной системой заблокирована для защиты Windows."
                )
                return
            self.combo_sd_drives.insertItem(0, f"📁 {path}", path)
            self.combo_sd_drives.setCurrentIndex(0)

    def start_copy_to_sd(self):
        target_path = self.combo_sd_drives.currentData()
        if not target_path or not os.path.exists(target_path):
            QMessageBox.warning(self, "Внимание", "Укажите корректный целевой накопитель или папку.")
            return

        sys_drive = os.environ.get("SystemDrive", "C:").upper()
        target_drive = os.path.splitdrive(os.path.abspath(target_path))[0].upper()
        if target_drive == sys_drive:
            QMessageBox.critical(
                self, "Защита системы",
                f"⛔ Запись на системный диск ({sys_drive}) строго запрещена в целях безопасности!"
            )
            return

        selected_items = []
        for row in range(self.table_roms.rowCount()):
            chk_item = self.table_roms.item(row, 0)
            if chk_item and chk_item.checkState() == Qt.Checked:
                rom_item = self.table_roms.item(row, 2).data(Qt.UserRole)
                if rom_item:
                    selected_items.append(rom_item)

        if not selected_items:
            QMessageBox.warning(self, "Внимание", "Не выбрано ни одной игры для записи. Отметьте игры флажками.")
            return

        reply = QMessageBox.question(
            self, "Подтверждение записи",
            f"Записать {len(selected_items)} игр на накопитель:\n{target_path}\n\n"
            f"Будут созданы каталоги платформ в /game/..., скопированы файлы игр и обложки, "
            f"а также автоматически сгенерирована и записана база games.db.\n\nПродолжить?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        self.btn_copy_to_sd.setEnabled(False)
        self.copy_sd_progress.setVisible(True)
        self.copy_sd_progress.setMaximum(len(selected_items))
        self.copy_sd_progress.setValue(0)
        self.lbl_copy_sd_status.setText("Копирование файлов...")

        self.copy_worker = CopyGamesWorker(selected_items, target_path)
        self.copy_worker.progress.connect(self.on_copy_to_sd_progress)
        self.copy_worker.finished.connect(self.on_copy_to_sd_finished)
        self.copy_worker.start()

    def on_copy_to_sd_progress(self, cur, tot, name):
        self.copy_sd_progress.setValue(cur)
        self.lbl_copy_sd_status.setText(f"Запись [{cur}/{tot}]: {name}")

    def on_copy_to_sd_finished(self, ok, msg):
        self.btn_copy_to_sd.setEnabled(True)
        self.copy_sd_progress.setVisible(False)
        if ok:
            self.lbl_copy_sd_status.setText("✔ Запись успешно завершена!")
            QMessageBox.information(self, "Успешно", msg)
        else:
            self.lbl_copy_sd_status.setText("❌ Ошибка записи")
            QMessageBox.critical(self, "Ошибка", msg)

    def build_games_db(self):
        if not self.rom_manager or not self.rom_manager.items:
            QMessageBox.warning(self, "Внимание", "Сначала отсканируйте папку с играми.")
            return

        ok, msg = self.rom_manager.build_games_db()
        if ok:
            QMessageBox.information(self, "Успех", f"{msg}\n\nБаза готова к использованию!")
        else:
            QMessageBox.critical(self, "Ошибка", msg)

    def start_scrape(self):
        if not self.rom_manager or not self.rom_manager.items:
            return

        missing = [it for it in self.rom_manager.items if not it.has_cover]
        if not missing:
            QMessageBox.information(self, "Готово", "Все игры уже имеют обложки!")
            return

        self.scrape_progress.setVisible(True)
        self.scrape_progress.setMaximum(len(missing))
        self.scrape_progress.setValue(0)

        self.worker = ScrapeWorker(missing, self.scraper)
        self.worker.progress.connect(lambda cur, tot, name: self.scrape_progress.setValue(cur))
        self.worker.finished.connect(self.on_scrape_finished)
        self.worker.start()

    def on_scrape_finished(self, downloaded, total):
        self.scrape_progress.setVisible(False)
        QMessageBox.information(self, "Готово", f"Загружено обложек: {downloaded} из {total}")
        self.filter_rom_table()


def run_app():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    run_app()
