import sys
import json
import os
import sqlite3
import shutil
import tempfile
import winreg
import subprocess
import time
import re
from datetime import datetime
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QScrollArea, QGroupBox,
                               QDialog, QCheckBox, QGridLayout, QFormLayout, QCalendarWidget,
                               QMessageBox, QListWidget, QListWidgetItem, QAbstractItemView,
                               QComboBox)
from PySide6.QtCore import Qt, Signal, QDate
from PySide6.QtGui import QTextCharFormat, QColor, QIcon, QStandardItemModel, QStandardItem

try:
    import gspread
    HAS_GSPREAD = True
except ImportError:
    HAS_GSPREAD = False

# ==========================================
# 0. 专业的路径处理逻辑
# ==========================================
def get_app_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def get_bundle_dir():
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))

CONFIG_FILE = os.path.join(get_app_dir(), "settings.json")
DB_FILE = os.path.join(get_app_dir(), "local_history.db")
VIDEO_BROWSER_DIR = os.path.join(get_app_dir(), "VideoBrowserData")

def get_chrome_path():
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as key:
            return winreg.QueryValue(key, None)
    except (OSError, FileNotFoundError, PermissionError, AttributeError):
        # winreg may not exist on non-Windows; registry key may be missing
        pass
    paths = [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe")
    ]
    for p in paths:
        if os.path.exists(p):
            return p
    return None

def init_db():
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS history (
                date TEXT, name TEXT, topic TEXT, value TEXT, note TEXT, UNIQUE(date, name, topic))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS youtube_videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                title TEXT, url TEXT, visit_time INTEGER, browser_source TEXT,
                UNIQUE(title, visit_time))''')
                
        cursor.execute("PRAGMA table_info(youtube_videos)")
        columns = [col[1] for col in cursor.fetchall()]
        if "browser_source" not in columns:
            cursor.execute("DROP TABLE youtube_videos")
            cursor.execute('''CREATE TABLE youtube_videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                title TEXT, url TEXT, visit_time INTEGER, browser_source TEXT,
                UNIQUE(title, visit_time))''')
                
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"初始化本地数据库失败: {e}")

def load_config():
    default_config = {
        "json_key_file": "credentials.json", 
        "sheet_url": "", 
        "sheet_name": "Sheet1", 
        "date_row": "1", 
        "topic_row": "2", 
        "name_col": "A",
        "last_name": "",  
        "browser_type": "内置专属浏览器", 
        "browser_profile": "Default",
        "synced_browsers": ["内置专属浏览器"], 
        "fields_config": [{"topic": f"题目 {i+1}", "show_value": True, "show_note": True, "show_newline": True, "bind_browser": "随全局（混合抓取）"} for i in range(20)]
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                loaded_config = json.load(f)
                for k, v in default_config.items():
                    if k not in loaded_config: 
                        loaded_config[k] = v
                return loaded_config
        except Exception as e:
            print(f"读取配置文件失败: {e}")
    return default_config

def save_config(config):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"保存配置文件失败: {e}")

# ==========================================
# 2. 自定义UI组件
# ==========================================
class DoubleClickLineEdit(QLineEdit):
    doubleClicked = Signal()
    def mouseDoubleClickEvent(self, event):
        self.doubleClicked.emit()
        super().mouseDoubleClickEvent(event)

class FocusLineEdit(QLineEdit):
    focused = Signal(QWidget)
    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.focused.emit(self)

class CleanCalendarWidget(QCalendarWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        self.setFirstDayOfWeek(Qt.Monday) 
        self.setGridVisible(False) 
        default_font = self.font()
        if default_font.pointSize() <= 0: 
            default_font.setPointSize(10)
        self.setFont(default_font)
        header_format = self.headerTextFormat()
        header_format.setBackground(QColor("#FFFFFF"))
        self.setHeaderTextFormat(header_format)
        color_coral = QColor("#D95E46")
        color_grey = QColor("#8E8076")   
        fmt_weekday = QTextCharFormat()
        fmt_weekday.setForeground(color_coral)
        for day in [Qt.Monday, Qt.Tuesday, Qt.Wednesday, Qt.Thursday, Qt.Friday]:
            self.setWeekdayTextFormat(day, fmt_weekday)
        fmt_weekend = QTextCharFormat()
        fmt_weekend.setForeground(color_grey)
        for day in [Qt.Saturday, Qt.Sunday]:
            self.setWeekdayTextFormat(day, fmt_weekend)
        fmt_today = QTextCharFormat()
        fmt_today.setBackground(color_coral)
        fmt_today.setForeground(QColor("white"))
        self.setDateTextFormat(QDate.currentDate(), fmt_today)

class CalendarDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择日期")
        self.resize(320, 310)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        self.calendar = CleanCalendarWidget()
        self.calendar.setStyleSheet("""
            QCalendarWidget { background-color: #FFFFFF; }
            QWidget#qt_calendar_navigationbar { background-color: #FFFFFF; padding: 10px; border-bottom: 1px solid #F0F0F0; }
            QToolButton { color: #D95E46; font-size: 12pt; font-weight: bold; background-color: transparent; border: none; }
            QToolButton::menu-indicator { image: none; }
            QToolButton#qt_calendar_prevmonth, QToolButton#qt_calendar_nextmonth { background-color: #F5F5F5; border-radius: 4px; width: 28px; height: 28px; }
            QToolButton#qt_calendar_prevmonth:hover, QToolButton#qt_calendar_nextmonth:hover { background-color: #EAEAEA; }
            QTableView QHeaderView::section { background-color: #FFFFFF; padding: 8px 0px; border: 1px solid #E0E0E0; border-right: none; font-weight: bold; font-size: 10pt; }
            QTableView QHeaderView::section:last { border-right: 1px solid #E0E0E0; }
            QTableView { selection-background-color: #FCECE9; selection-color: #D95E46; border: none; background-color: #FFFFFF; }
        """)
        self.calendar.clicked.connect(self.accept)
        layout.addWidget(self.calendar)
        
        btn = QPushButton("确定 (也可直接点选日期)")
        btn.setMinimumHeight(38)
        btn.setStyleSheet("QPushButton { background-color: #D95E46; color: white; border: none; border-radius: 4px; font-weight: bold; font-size: 11pt; } QPushButton:hover { background-color: #E26A53; }")
        btn.clicked.connect(self.accept)
        layout.addWidget(btn)

    def get_selected_date(self): 
        return self.calendar.selectedDate().toString("MM-dd")

class VideoSelectDialog(QDialog):
    def __init__(self, target_browser_source, existing_text="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择已观看的 YouTube 视频")
        self.resize(580, 550)
        self.target_browser_source = target_browser_source
        
        self.already_selected = set([t.strip() for t in existing_text.split("┃") if t.strip()])
        
        self.setStyleSheet("""
            QDialog { background-color: #F3F4F6; }
            QListWidget { background-color: #FFFFFF; border: 1px solid #CCCCCC; border-radius: 4px; font-size: 10pt; padding: 5px; }
            QListWidget::item { padding: 8px; border-bottom: 1px solid #EEEEEE; }
            QListWidget::item:selected { background-color: #FCECE9; color: #D95E46; font-weight: bold; }
            QPushButton { background-color: #D95E46; color: white; border: none; padding: 8px 20px; border-radius: 4px; font-weight: bold; }
            QPushButton:hover { background-color: #E26A53; }
        """)
        
        layout = QVBoxLayout(self)
        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.MultiSelection)
        
        if self.target_browser_source == "随全局（混合抓取）":
            layout.addWidget(QLabel("当前模式：【随全局混合抓取】(上方为24小时最新，下方按浏览器分组展示)"))
        else:
            layout.addWidget(QLabel(f"当前模式：【专项隔离提取】(直接呈现该渠道全量历史池: {self.target_browser_source})"))
            
        layout.addWidget(self.list_widget)
        self.load_videos_from_db()
        
        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("✔ 确定填入")
        ok_btn.clicked.connect(self.accept)
        btn_layout.addStretch()
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)

    def load_videos_from_db(self):
        try:
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            one_day_ago = int(time.time()) - 86400

            if self.target_browser_source == "随全局（混合抓取）":
                cursor.execute("SELECT title, browser_source, visit_time FROM youtube_videos WHERE visit_time >= ? ORDER BY visit_time DESC", (one_day_ago,))
                recent_rows = cursor.fetchall()
                if recent_rows:
                    self.add_group_header("【🔥 最近 24 小时内观看 (最新)】")
                    for row in recent_rows:
                        self.add_video_item(row)
                
                cursor.execute("SELECT DISTINCT browser_source FROM youtube_videos")
                sources = [r[0] for r in cursor.fetchall() if r[0]]
                
                for source in sources:
                    cursor.execute("SELECT title, browser_source, visit_time FROM youtube_videos WHERE browser_source = ? AND visit_time < ? ORDER BY visit_time DESC LIMIT 100", (source, one_day_ago))
                    source_rows = cursor.fetchall()
                    if source_rows:
                        self.add_group_header(f"【📁 {source} - 往期记录】")
                        for row in source_rows:
                            self.add_video_item(row)
                            
                if self.list_widget.count() == 0:
                    self.list_widget.addItem(QListWidgetItem("暂无匹配记录，请先在主界面点击【抓取 YouTube】"))
            else:
                cursor.execute("SELECT title, browser_source, visit_time FROM youtube_videos WHERE browser_source = ? ORDER BY visit_time DESC", (self.target_browser_source,))
                rows = cursor.fetchall()
                if rows:
                    self.add_group_header(f"【🎯 专项隔离池：{self.target_browser_source}】")
                    for row in rows:
                        self.add_video_item(row)
                else:
                    self.list_widget.addItem(QListWidgetItem("暂无匹配记录，请先在主界面点击【抓取 YouTube】"))
            conn.close()
        except Exception as e:
            print(f"载入视频列表失败: {e}")

    def add_group_header(self, text):
        item = QListWidgetItem(text)
        item.setFlags(Qt.ItemIsEnabled) 
        item.setBackground(QColor("#EAEAEA"))
        item.setForeground(QColor("#333333"))
        font = item.font()
        font.setBold(True)
        item.setFont(font)
        self.list_widget.addItem(item)

    def add_video_item(self, row):
        title = row[0].replace(" - YouTube", "").strip()
        source = row[1]
        time_str = datetime.fromtimestamp(row[2]).strftime("%m-%d %H:%M")
        display_text = f"{title}   [{source}] ({time_str})"
        
        item = QListWidgetItem(display_text)
        
        if title in self.already_selected:
            item.setBackground(QColor("#E8F5E9"))
            item.setForeground(QColor("#2E7D32"))
            font = item.font()
            font.setBold(True)
            item.setFont(font)
            item.setText(f"✅ (已选) {display_text}")
            
        self.list_widget.addItem(item)

    def get_selected_titles(self):
        titles = []
        for item in self.list_widget.selectedItems():
            text = item.text()
            if "最近 24 小时" in text or "往期记录" in text or "专项隔离池" in text or "暂无匹配记录" in text:
                continue
            
            if text.startswith("✅ (已选) "):
                text = text.replace("✅ (已选) ", "", 1)
                
            clean_title = text.split("   [")[0].strip()
            titles.append(clean_title)
        return titles

# ==========================================
# 黑科技组件：带分组和多选功能的下拉选单
# ==========================================
class CheckableComboBox(QComboBox):
    selectionChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.view().pressed.connect(self.handle_item_pressed)
        self.setModel(QStandardItemModel(self))
        self._changed = False

    def handle_item_pressed(self, index):
        item = self.model().itemFromIndex(index)
        if item.flags() & Qt.ItemIsUserCheckable:
            if item.checkState() == Qt.Checked:
                item.setCheckState(Qt.Unchecked)
            else:
                item.setCheckState(Qt.Checked)
            self._changed = True
            self.selectionChanged.emit()

    def hidePopup(self):
        if not self._changed:
            super().hidePopup()
        self._changed = False

    def set_title(self, title):
        self.clear()
        item = QStandardItem(title)
        item.setFlags(Qt.ItemIsEnabled)
        self.model().appendRow(item)
        self.setCurrentIndex(0)

    def add_group(self, text):
        item = QStandardItem(text)
        item.setFlags(Qt.ItemIsEnabled)
        item.setBackground(QColor("#EAEAEA")) 
        self.model().appendRow(item)

    def add_checkable_item(self, text, checked=False):
        item = QStandardItem(text)
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
        item.setData(Qt.Checked if checked else Qt.Unchecked, Qt.CheckStateRole)
        self.model().appendRow(item)

    def get_checked_items(self):
        checked = []
        for i in range(self.count()):
            item = self.model().item(i)
            if item and item.flags() & Qt.ItemIsUserCheckable:
                if item.checkState() == Qt.Checked:
                    checked.append(item.text())
        return checked

# ==========================================
# 3. 设置面板
# ==========================================
class SettingsDialog(QDialog):
    def __init__(self, current_config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("系统设置")
        self.resize(680, 780) 
        self.current_config = current_config
        self.setup_ui()
        self.apply_white_theme()

    def apply_white_theme(self):
        self.setStyleSheet("""
            QDialog { background-color: #F3F4F6; color: #333333; } QLabel { color: #333333; }
            QLineEdit, QComboBox, QListWidget { border: 1px solid #CCCCCC; padding: 5px; border-radius: 3px; background-color: #FFFFFF; }
            QPushButton { background-color: #FFFFFF; border: 1px solid #CCCCCC; padding: 6px; border-radius: 3px; color: #333333; }
            QPushButton:hover { background-color: #E8E8E8; }
            QGroupBox { border: 1px solid #CCCCCC; border-radius: 4px; margin-top: 10px; font-weight: bold; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 3px 0 3px; }
            QScrollArea { border: none; background-color: transparent; }
        """)

    def setup_ui(self):
        layout = QVBoxLayout(self)
        
        self.field_settings = []
        self.row_combos = []

        api_group = QGroupBox("表格连接设置 (API配置)")
        api_layout = QFormLayout()
        self.json_file_input = QLineEdit(self.current_config.get("json_key_file", "credentials.json"))
        self.sheet_url_input = QLineEdit(self.current_config.get("sheet_url", ""))
        self.sheet_name_input = QLineEdit(self.current_config.get("sheet_name", "Sheet1"))
        api_layout.addRow("JSON密钥文件:", self.json_file_input)
        api_layout.addRow("表格链接(URL):", self.sheet_url_input)
        api_layout.addRow("工作表名称:", self.sheet_name_input)
        
        browser_hbox = QHBoxLayout()
        self.browser_type_combo = QComboBox()
        self.browser_type_combo.addItems(["内置专属浏览器", "谷歌 Chrome", "微软 Edge", "所有浏览器 (可多选)", "关闭外部同步"])
        self.browser_type_combo.setCurrentText(self.current_config.get("browser_type", "内置专属浏览器"))
        
        self.browser_profile_combo = QComboBox()
        self.browser_multi_combo = CheckableComboBox()
        
        browser_hbox.addWidget(self.browser_type_combo, stretch=1)
        browser_hbox.addWidget(self.browser_profile_combo, stretch=1)
        browser_hbox.addWidget(self.browser_multi_combo, stretch=1)
        
        api_layout.addRow("浏览器同步源:", browser_hbox)
        api_group.setLayout(api_layout)
        layout.addWidget(api_group)

        coord_group = QGroupBox("表格坐标设置")
        coord_layout = QFormLayout()
        self.date_row_input = QLineEdit(self.current_config.get("date_row", "1"))
        self.topic_row_input = QLineEdit(self.current_config.get("topic_row", "2"))
        self.name_col_input = QLineEdit(self.current_config.get("name_col", "A"))
        coord_layout.addRow("日期所在行号:", self.date_row_input)
        coord_layout.addRow("题目所在行号:", self.topic_row_input)
        coord_layout.addRow("姓名所在列标:", self.name_col_input)
        coord_group.setLayout(coord_layout)
        layout.addWidget(coord_group)

        fields_group = QGroupBox("题目与输入框配置 (最多支持 20 组)")
        fields_group_layout = QVBoxLayout(fields_group)
        scroll_vars = QScrollArea()
        scroll_vars.setWidgetResizable(True)
        scroll_vars_widget = QWidget()
        fields_layout = QGridLayout(scroll_vars_widget)
        
        fields_layout.addWidget(QLabel("组别"), 0, 0)
        fields_layout.addWidget(QLabel("设定题目名称 (不填隐藏)"), 0, 1)
        fields_layout.addWidget(QLabel("内容框"), 0, 2)
        fields_layout.addWidget(QLabel("备注框"), 0, 3)
        fields_layout.addWidget(QLabel("换行"), 0, 4)
        fields_layout.addWidget(QLabel("专项绑定浏览器"), 0, 5)

        for i in range(20):
            fields_layout.addWidget(QLabel(f"第 {i+1:02d} 组:"), i+1, 0)
            cfg_len = len(self.current_config["fields_config"])
            old_cfg = self.current_config["fields_config"][i] if i < cfg_len else {"topic": "", "show_value": True, "show_note": True, "show_newline": True, "bind_browser": "随全局（混合抓取）"}
            
            topic_edit = QLineEdit(old_cfg.get("topic", ""))
            fields_layout.addWidget(topic_edit, i+1, 1)
            
            cb_value = QCheckBox()
            cb_value.setChecked(old_cfg.get("show_value", True))
            fields_layout.addWidget(cb_value, i+1, 2)
            
            cb_note = QCheckBox()
            cb_note.setChecked(old_cfg.get("show_note", True))
            fields_layout.addWidget(cb_note, i+1, 3)
            
            cb_newline = QCheckBox()
            cb_newline.setChecked(old_cfg.get("show_newline", True))
            fields_layout.addWidget(cb_newline, i+1, 4)
            
            combo_browser = QComboBox()
            combo_browser.setFixedWidth(200)
            fields_layout.addWidget(combo_browser, i+1, 5)
            
            self.field_settings.append((topic_edit, cb_value, cb_note, cb_newline, combo_browser, old_cfg.get("bind_browser", "随全局（混合抓取）")))
            self.row_combos.append(combo_browser)
            
        scroll_vars.setWidget(scroll_vars_widget)
        fields_group_layout.addWidget(scroll_vars)
        layout.addWidget(fields_group)

        btn_layout = QHBoxLayout()
        save_btn = QPushButton("保存并确定")
        save_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        
        btn_layout.addStretch()
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(save_btn)
        layout.addLayout(btn_layout)

        self.browser_type_combo.currentTextChanged.connect(self.update_profiles)
        self.browser_type_combo.currentTextChanged.connect(self.refresh_row_combos)
        self.browser_profile_combo.currentTextChanged.connect(self.refresh_row_combos)
        self.browser_multi_combo.selectionChanged.connect(self.refresh_row_combos)

        self.update_profiles()
        saved_profile = self.current_config.get("browser_profile", "Default")
        idx = self.browser_profile_combo.findText(saved_profile)
        if idx >= 0: 
            self.browser_profile_combo.setCurrentIndex(idx)
        self.refresh_row_combos()

    def update_profiles(self):
        b_type = self.browser_type_combo.currentText()
        if b_type == "所有浏览器 (可多选)":
            self.browser_profile_combo.hide()
            self.browser_multi_combo.show()
            
            self.browser_multi_combo.set_title("点此展开分组多选列表 ↓")
            saved_synced = self.current_config.get("synced_browsers", ["内置专属浏览器"])
            
            self.browser_multi_combo.add_group("【软件内置浏览器】")
            self.browser_multi_combo.add_checkable_item("内置专属浏览器", "内置专属浏览器" in saved_synced)
            
            local_app = os.environ.get('LOCALAPPDATA', '')
            chrome_base = os.path.join(local_app, r"Google\Chrome\User Data")
            if os.path.exists(chrome_base):
                self.browser_multi_combo.add_group("【谷歌 Chrome】")
                for d in os.listdir(chrome_base):
                    # Only accept simple profile directory names (Default, Profile 1, etc.)
                    if (re.fullmatch(r'[\w\-. ]+', d) and ".." not in d
                            and os.path.isdir(os.path.join(chrome_base, d))
                            and os.path.exists(os.path.join(chrome_base, d, "History"))):
                        name = f"谷歌 Chrome - {d}"
                        self.browser_multi_combo.add_checkable_item(name, name in saved_synced)
            
            edge_base = os.path.join(local_app, r"Microsoft\Edge\User Data")
            if os.path.exists(edge_base):
                self.browser_multi_combo.add_group("【微软 Edge】")
                for d in os.listdir(edge_base):
                    if (re.fullmatch(r'[\w\-. ]+', d) and ".." not in d
                            and os.path.isdir(os.path.join(edge_base, d))
                            and os.path.exists(os.path.join(edge_base, d, "History"))):
                        name = f"微软 Edge - {d}"
                        self.browser_multi_combo.add_checkable_item(name, name in saved_synced)
                        
        else:
            self.browser_multi_combo.hide()
            self.browser_profile_combo.show()
            self.browser_profile_combo.clear()
            
            if b_type in ["内置专属浏览器", "关闭外部同步"]:
                self.browser_profile_combo.setEnabled(False)
                self.browser_profile_combo.addItem("无需选择")
            else:
                self.browser_profile_combo.setEnabled(True)
                local_app = os.environ.get('LOCALAPPDATA', '')
                base_path = os.path.join(local_app, r"Google\Chrome\User Data") if b_type == "谷歌 Chrome" else os.path.join(local_app, r"Microsoft\Edge\User Data")
                if os.path.exists(base_path):
                    for d in os.listdir(base_path):
                        if (re.fullmatch(r'[\w\-. ]+', d) and ".." not in d
                                and os.path.isdir(os.path.join(base_path, d))
                                and os.path.exists(os.path.join(base_path, d, "History"))):
                            self.browser_profile_combo.addItem(d)
                if self.browser_profile_combo.count() == 0:
                    self.browser_profile_combo.addItem("Default")

    def get_all_active_sources(self):
        b_type = self.browser_type_combo.currentText()
        if b_type == "所有浏览器 (可多选)":
            return self.browser_multi_combo.get_checked_items()
        elif b_type == "谷歌 Chrome":
            return [f"谷歌 Chrome - {self.browser_profile_combo.currentText()}"]
        elif b_type == "微软 Edge":
            return [f"微软 Edge - {self.browser_profile_combo.currentText()}"]
        elif b_type == "内置专属浏览器":
            return ["内置专属浏览器"]
        return []

    def refresh_row_combos(self):
        active_browsers = self.get_all_active_sources()
        for topic_edit, cb_value, cb_note, cb_newline, combo, saved_text in self.field_settings:
            current_selected = combo.currentText() if combo.count() > 0 else saved_text
            combo.clear()
            combo.addItem("随全局（混合抓取）")
            combo.addItems(active_browsers)
            
            idx = combo.findText(current_selected)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            else:
                combo.setCurrentIndex(0)

    def get_new_config(self):
        new_config = {
            "json_key_file": self.json_file_input.text().strip(),
            "sheet_url": self.sheet_url_input.text().strip(),
            "sheet_name": self.sheet_name_input.text().strip(),
            "date_row": self.date_row_input.text().strip(),
            "topic_row": self.topic_row_input.text().strip(),
            "name_col": self.name_col_input.text().strip().upper(),
            "browser_type": self.browser_type_combo.currentText(),
            "browser_profile": self.browser_profile_combo.currentText(),
            "synced_browsers": self.browser_multi_combo.get_checked_items(),
            "last_name": self.current_config.get("last_name", ""),
            "fields_config": []
        }
        for topic_edit, cb_value, cb_note, cb_newline, combo, _ in self.field_settings:
            new_config["fields_config"].append({
                "topic": topic_edit.text().strip(), 
                "show_value": cb_value.isChecked(),
                "show_note": cb_note.isChecked(), 
                "show_newline": cb_newline.isChecked(),
                "bind_browser": combo.currentText() 
            })
        return new_config

# ==========================================
# 4. 主程序界面
# ==========================================
class DataEntryApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("表格数据填报系统")
        self.resize(780, 650) 
        icon_path = os.path.join(get_bundle_dir(), "222.ico")
        if os.path.exists(icon_path): 
            self.setWindowIcon(QIcon(icon_path))
        
        self.config = load_config()
        self.setup_ui()
        self.apply_white_theme()
        self.apply_config()
        self.set_default_date()

        self.name_input.setText(self.config.get("last_name", ""))
        self.date_input.editingFinished.connect(self.load_local_history)
        self.name_input.editingFinished.connect(self.handle_name_changed)

        if self.name_input.text().strip(): 
            self.load_local_history()

    def handle_name_changed(self):
        current_name = self.name_input.text().strip()
        if current_name != self.config.get("last_name", ""):
            self.config["last_name"] = current_name
            save_config(self.config)
        self.load_local_history()

    def apply_white_theme(self):
        self.setStyleSheet("""
            QWidget { background-color: #F3F4F6; color: #333333; } QLabel { font-weight: bold; }
            QLineEdit { border: 1px solid #CCCCCC; padding: 6px; border-radius: 4px; background-color: #FFFFFF; }
            QLineEdit:focus { border: 1px solid #D95E46; }
            QPushButton { background-color: #FFFFFF; border: 1px solid #CCCCCC; padding: 8px; border-radius: 4px; font-weight: bold; }
            QPushButton:hover { background-color: #E8E8E8; }
            QGroupBox { border: 1px solid #D0D0D0; border-radius: 6px; margin-top: 12px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px 0 5px; color: #555555; }
            QScrollArea { border: 1px solid #E0E0E0; background-color: #F3F4F6; }
        """)

    def setup_ui(self):
        main_layout = QVBoxLayout(self)

        top_layout = QHBoxLayout()
        top_layout.addStretch()
        
        self.inbuilt_browser_btn = QPushButton("🌐 打开专属浏览器")
        self.inbuilt_browser_btn.setStyleSheet("color: white; background-color: #2563EB; border: none;")
        self.inbuilt_browser_btn.clicked.connect(self.launch_dedicated_browser)
        top_layout.addWidget(self.inbuilt_browser_btn)
        
        self.fetch_yt_btn = QPushButton("🎬 抓取 YouTube")
        self.fetch_yt_btn.setStyleSheet("color: #D95E46; border: 1px solid #D95E46;")
        self.fetch_yt_btn.clicked.connect(self.fetch_browser_history)
        top_layout.addWidget(self.fetch_yt_btn)
        
        self.settings_btn = QPushButton("⚙️ 软件设置")
        self.settings_btn.clicked.connect(self.open_settings)
        top_layout.addWidget(self.settings_btn)
        main_layout.addLayout(top_layout)

        info_group = QGroupBox("定位信息")
        info_layout = QHBoxLayout()
        
        self.date_input = DoubleClickLineEdit()
        self.date_input.doubleClicked.connect(self.open_calendar)
        self.name_input = QLineEdit()
        
        info_layout.addWidget(QLabel("目标日期:"))
        info_layout.addWidget(self.date_input)
        info_layout.addWidget(QLabel("目标姓名:"))
        info_layout.addWidget(self.name_input)
        info_group.setLayout(info_layout)
        main_layout.addWidget(info_group)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_content = QWidget()
        self.entries_layout = QVBoxLayout(scroll_content)

        self.entry_fields = []
        for i in range(20):
            row_layout = QHBoxLayout()
            
            topic_label = QLabel()
            topic_label.setFixedWidth(120)
            topic_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter) 
            
            value_input = FocusLineEdit()
            value_input.setPlaceholderText("输入内容/数字")
            
            note_input = FocusLineEdit()
            note_input.setPlaceholderText("输入备注")
            
            row_tracker = {'target': note_input}
            value_input.focused.connect(lambda w, t=row_tracker: t.update({'target': w}))
            note_input.focused.connect(lambda w, t=row_tracker: t.update({'target': w}))
            
            newline_btn = QPushButton("↵ 换行")
            newline_btn.setFixedWidth(50)
            newline_btn.setStyleSheet("background-color: #E8E8E8; font-size: 9pt; padding: 4px; border: 1px solid #CCC; border-radius: 3px;")
            newline_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            newline_btn.clicked.connect(lambda checked=False, t=row_tracker: self.insert_symbol(t, "┃"))
            
            yt_btn = QPushButton("🎬 选视频")
            yt_btn.setFixedWidth(65)
            yt_btn.setStyleSheet("background-color: #E8E8E8; font-size: 9pt; padding: 4px; border: 1px solid #CCC; border-radius: 3px;")
            yt_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            yt_btn.clicked.connect(lambda checked=False, t=row_tracker, idx=i: self.open_video_selector(t, idx))
            
            row_layout.addWidget(topic_label)
            row_layout.addWidget(value_input, stretch=1)
            row_layout.addWidget(note_input, stretch=3)
            row_layout.addWidget(newline_btn)
            row_layout.addWidget(yt_btn) 
            
            self.entries_layout.addLayout(row_layout)
            self.entry_fields.append((topic_label, value_input, note_input, newline_btn, yt_btn))

        scroll_area.setWidget(scroll_content)
        main_layout.addWidget(scroll_area)

        self.submit_btn = QPushButton("开始定位并写入数据")
        self.submit_btn.setMinimumHeight(45)
        self.submit_btn.setStyleSheet("background-color: #D95E46; color: white; border: none; border-radius: 4px;")
        self.submit_btn.clicked.connect(self.process_data)
        main_layout.addWidget(self.submit_btn)

    def launch_dedicated_browser(self):
        chrome_exe = get_chrome_path()
        if not chrome_exe:
            QMessageBox.critical(self, "未找到 Chrome", "在您的电脑上没有找到 Google Chrome 浏览器！\n由于专属浏览器依赖于 Chrome 内核，请先安装 Chrome。")
            return
            
        if not os.path.exists(VIDEO_BROWSER_DIR):
            reply = QMessageBox.question(self, "首次创建专属浏览器", 
                                         "是否为您创建一个名为【视频默认浏览器】的专属运行环境？\n\n"
                                         "它和普通浏览器完全一样（记住密码、装插件），但数据完全存放在本软件目录下，绝不干扰您平时的主浏览器。", 
                                         QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.No: 
                return

        try:
            subprocess.Popen([
                chrome_exe,
                f"--user-data-dir={VIDEO_BROWSER_DIR}",
                "--no-first-run",
                "--no-default-browser-check",
                "https://www.youtube.com"
            ])
        except Exception as e:
            QMessageBox.critical(self, "启动失败", f"启动专属浏览器失败:\n{str(e)}")

    def fetch_browser_history(self):
        b_type = self.config.get("browser_type", "内置专属浏览器")
        
        sync_targets = []
        if b_type == "所有浏览器 (可多选)":
            sync_targets = self.config.get("synced_browsers", [])
        elif b_type == "谷歌 Chrome":
            sync_targets = [f"谷歌 Chrome - {self.config.get('browser_profile')}"]
        elif b_type == "微软 Edge":
            sync_targets = [f"微软 Edge - {self.config.get('browser_profile')}"]
        elif b_type == "内置专属浏览器":
            sync_targets = ["内置专属浏览器"]

        if not sync_targets:
            QMessageBox.information(self, "提示", "当前配置关闭了同步或未选中任何浏览器！")
            return

        self.fetch_yt_btn.setText("正在多源抓取...")
        self.fetch_yt_btn.setEnabled(False)
        QApplication.processEvents()

        try:
            conn_local = sqlite3.connect(DB_FILE)
            cursor_local = conn_local.cursor()
            cursor_local.execute("DELETE FROM youtube_videos")

            local_app = os.environ.get('LOCALAPPDATA', '')
            total_extracted = 0

            for target in sync_targets:
                hist_path = ""
                if target == "内置专属浏览器":
                    hist_path = os.path.join(VIDEO_BROWSER_DIR, "Default", "History")
                elif "谷歌 Chrome - " in target:
                    prof_name = target.replace("谷歌 Chrome - ", "").strip()
                    # Prevent path traversal: only allow simple profile directory names
                    if not re.fullmatch(r'[\w\-. ]+', prof_name) or ".." in prof_name or "/" in prof_name or "\\" in prof_name:
                        print(f"跳过非法配置文件名: {prof_name}")
                        continue
                    hist_path = os.path.join(local_app, r"Google\Chrome\User Data", prof_name, "History")
                elif "微软 Edge - " in target:
                    prof_name = target.replace("微软 Edge - ", "").strip()
                    if not re.fullmatch(r'[\w\-. ]+', prof_name) or ".." in prof_name or "/" in prof_name or "\\" in prof_name:
                        print(f"跳过非法配置文件名: {prof_name}")
                        continue
                    hist_path = os.path.join(local_app, r"Microsoft\Edge\User Data", prof_name, "History")

                if not hist_path or not os.path.exists(hist_path):
                    continue
                # Extra safety: ensure resolved path stays under expected base
                hist_path = os.path.normpath(hist_path)
                if "User Data" in hist_path:
                    base = os.path.normpath(os.path.join(local_app, r"Google\Chrome\User Data" if "Chrome" in target else r"Microsoft\Edge\User Data"))
                    if not hist_path.startswith(base):
                        print(f"路径越界拒绝: {hist_path}")
                        continue

                temp_path = None
                try:
                    temp_path = os.path.join(tempfile.gettempdir(), f"temp_sync_{int(time.time())}_{os.getpid()}.db")
                    shutil.copy2(hist_path, temp_path)
                    
                    conn_browser = sqlite3.connect(f"file:{temp_path}?mode=ro", uri=True)
                    cursor_browser = conn_browser.cursor()
                    cursor_browser.execute(
                        "SELECT title, url, last_visit_time FROM urls WHERE url LIKE ? ORDER BY last_visit_time DESC LIMIT 100",
                        ("%youtube.com/watch%",)
                    )
                    for row in cursor_browser.fetchall():
                        title = row[0]
                        url = row[1]
                        webkit_time = row[2]
                        if title and webkit_time > 0:
                            title = re.sub(r'^\(\d+\)\s*', '', title)
                            unix_ts = int((webkit_time / 1000000) - 11644473600)
                            if unix_ts > 0:
                                cursor_local.execute(
                                    "INSERT OR IGNORE INTO youtube_videos (title, url, visit_time, browser_source) VALUES (?, ?, ?, ?)", 
                                    (title, url, unix_ts, target)
                                )
                                total_extracted += 1
                    conn_browser.close()
                except Exception as ex:
                    print(f"提取源 {target} 失败: {ex}")
                finally:
                    if temp_path and os.path.exists(temp_path):
                        try:
                            os.remove(temp_path)
                        except OSError:
                            pass

            conn_local.commit()
            conn_local.close()
            QMessageBox.information(self, "抓取成功", f"🎉 同步队列处理完成！\n提取并统一换算了 {total_extracted} 条时间戳标准记录。")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"处理数据库时发生错误：{str(e)}")
            
        self.fetch_yt_btn.setText("🎬 抓取 YouTube")
        self.fetch_yt_btn.setEnabled(True)

    def open_video_selector(self, tracker, row_index):
        target_input = tracker['target']
        cfg_len = len(self.config["fields_config"])
        field_cfg = self.config["fields_config"][row_index] if row_index < cfg_len else {}
        target_browser_source = field_cfg.get("bind_browser", "随全局（混合抓取）")
        
        existing_text = target_input.text().strip()
        
        dialog = VideoSelectDialog(target_browser_source, existing_text, self)
        if dialog.exec() == QDialog.Accepted:
            selected_titles = dialog.get_selected_titles()
            if selected_titles:
                current_text = target_input.text()
                if current_text and not current_text.endswith("┃"): 
                    current_text += "┃"
                new_text = current_text + "┃".join(selected_titles)
                target_input.setText(new_text)
                target_input.setFocus()

    def insert_symbol(self, tracker, symbol):
        target = tracker['target']
        target.setFocus()
        target.insert(symbol)

    def set_default_date(self):
        self.date_input.setText(datetime.now().strftime("%m-%d"))

    def open_calendar(self):
        dialog = CalendarDialog(self)
        if dialog.exec() == QDialog.Accepted:
            self.date_input.setText(dialog.get_selected_date())
            self.load_local_history()

    def apply_config(self):
        self.name_input.setPlaceholderText(f"对应第 {self.config['name_col']} 列，如: 王五")
        for i, (topic_label, value_field, note_field, newline_btn, yt_btn) in enumerate(self.entry_fields):
            cfg_len = len(self.config["fields_config"])
            field_cfg = self.config["fields_config"][i] if i < cfg_len else {"topic": "", "show_value": True, "show_note": True, "show_newline": True}
            
            topic_text = field_cfg.get("topic", "")
            topic_label.setText(topic_text)
            
            if not topic_text:
                topic_label.setVisible(False)
                value_field.setVisible(False)
                note_field.setVisible(False)
                newline_btn.setVisible(False)
                yt_btn.setVisible(False)
            else:
                topic_label.setVisible(True)
                value_field.setVisible(field_cfg.get("show_value", True))
                note_field.setVisible(field_cfg.get("show_note", True))
                newline_btn.setVisible(field_cfg.get("show_newline", True))
                yt_btn.setVisible(True)

    def open_settings(self):
        dialog = SettingsDialog(self.config, self)
        if dialog.exec() == QDialog.Accepted:
            self.config = dialog.get_new_config()
            save_config(self.config)
            self.apply_config()
            self.load_local_history()

    def load_local_history(self):
        target_date = self.date_input.text().strip()
        target_name = self.name_input.text().strip()
        
        for _, value_field, note_field, _, _ in self.entry_fields:
            value_field.clear()
            note_field.clear()

        if not target_date or not target_name: 
            return

        try:
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute("SELECT topic, value, note FROM history WHERE date=? AND name=?", (target_date, target_name))
            rows = cursor.fetchall()
            conn.close()

            if not rows: 
                return 
            
            history_dict = {row[0]: {"value": row[1], "note": row[2]} for row in rows}

            for topic_label, value_field, note_field, _, _ in self.entry_fields:
                topic = topic_label.text().strip()
                if topic and topic in history_dict:
                    val = history_dict[topic]["value"].replace("\n", "┃")
                    nt = history_dict[topic]["note"].replace("\n", "┃")
                    value_field.setText(val)
                    note_field.setText(nt)
        except Exception as e:
            print(f"读取本地数据库失败: {e}")

    def col_letter_to_index(self, letter):
        index = 0
        for char in letter.upper(): 
            index = index * 26 + (ord(char) - ord('A')) + 1
        return index - 1

    def is_date_match(self, target, cell_val):
        target = target.strip()
        cell_val = cell_val.strip()
        
        if target == cell_val: 
            return True

        try:
            t_nums = re.findall(r'\d+', target)
            if len(t_nums) < 2: 
                return False
            tm, td = int(t_nums[0]), int(t_nums[1])

            c_nums = re.findall(r'\d+', cell_val)
            
            if len(c_nums) == 2:
                cm, cd = int(c_nums[0]), int(c_nums[1])
                if (cm == tm and cd == td) or (cm == td and cd == tm): 
                    return True
                    
            elif len(c_nums) >= 3:
                if len(c_nums[0]) == 4: 
                    cm, cd = int(c_nums[1]), int(c_nums[2])
                    if cm == tm and cd == td: 
                        return True
                        
                elif len(c_nums[2]) == 4: 
                    n1, n2 = int(c_nums[0]), int(c_nums[1])
                    if (n1 == tm and n2 == td) or (n1 == td and n2 == tm): 
                        return True
        except (ValueError, IndexError, TypeError, AttributeError):
            # Malformed date strings or unexpected cell content; treat as non-match
            pass
            
        return False

    def process_data(self):
        target_date = self.date_input.text().strip()
        target_name = self.name_input.text().strip()
        
        if not target_date or not target_name:
            QMessageBox.warning(self, "警告", "目标日期和姓名不能为空！")
            return

        sheet_url = self.config.get("sheet_url", "")
        if not sheet_url:
            QMessageBox.warning(self, "警告", "请先在【⚙️ 软件设置】中配置表格链接！")
            return

        json_input_path = self.config.get("json_key_file", "credentials.json")
        if not json_input_path:
            QMessageBox.warning(self, "警告", "请先在【⚙️ 软件设置】中填写您的 JSON 密钥文件名！")
            return
            
        json_file = ""
        if os.path.isabs(json_input_path):
            json_file = json_input_path
        else:
            bundled_path = os.path.join(get_bundle_dir(), json_input_path)
            external_path = os.path.join(get_app_dir(), json_input_path)
            if os.path.exists(bundled_path): 
                json_file = bundled_path
            else: 
                json_file = external_path

        entries_to_write = []
        for i, (topic_label, value_field, note_field, _, _) in enumerate(self.entry_fields):
            topic = topic_label.text().strip()
            if not topic: 
                continue
            
            cfg_len = len(self.config["fields_config"])
            f_cfg = self.config["fields_config"][i] if i < cfg_len else {"show_value": True, "show_note": True}
            
            value = value_field.text().strip() if f_cfg.get("show_value", True) else ""
            note = note_field.text().strip() if f_cfg.get("show_note", True) else ""
            
            value = value.replace("┃", "\n")
            note = note.replace("┃", "\n")
            if value or note: 
                entries_to_write.append({"topic": topic, "value": value, "note": note})

        if not entries_to_write:
            QMessageBox.information(self, "提示", "没有填写任何需要写入的数据。")
            return

        if not HAS_GSPREAD:
            QMessageBox.critical(self, "错误", "缺少核心网络库。\n\n请在 CMD 或终端运行: \npip install gspread")
            return

        if not json_file or not os.path.exists(json_file):
            QMessageBox.warning(self, "错误", f"找不到密钥文件：\n{json_input_path}\n\n请确保已将其放入软件所在的文件夹中！")
            return

        self.submit_btn.setText("正在连接表格并寻找坐标...")
        self.submit_btn.setEnabled(False)
        self.submit_btn.setStyleSheet("background-color: #888888; color: white; border: none; border-radius: 4px;")
        QApplication.processEvents()

        try:
            gc = gspread.service_account(filename=json_file)
            sh = gc.open_by_url(sheet_url)
            ws = sh.worksheet(self.config.get("sheet_name", "Sheet1"))
            all_data = ws.get_all_values() 
            
            row_date_idx = int(self.config.get("date_row", "1")) - 1
            row_topic_idx = int(self.config.get("topic_row", "2")) - 1
            col_name_idx = self.col_letter_to_index(self.config.get("name_col", "A"))

            target_row_number = -1
            for r_idx, row in enumerate(all_data):
                if len(row) > col_name_idx and row[col_name_idx].strip() == target_name:
                    target_row_number = r_idx + 1
                    break
            
            if target_row_number == -1: 
                raise ValueError(f"在 {self.config.get('name_col')} 列找不到姓名 '{target_name}'")

            row_dates = all_data[row_date_idx] if len(all_data) > row_date_idx else []
            start_col_idx = -1
            end_col_idx = len(row_dates)

            for c_idx, val in enumerate(row_dates):
                if self.is_date_match(target_date, val):
                    start_col_idx = c_idx
                    for next_c in range(c_idx + 1, len(row_dates)):
                        if row_dates[next_c].strip() != "":
                            end_col_idx = next_c
                            break
                    break

            if start_col_idx == -1: 
                raise ValueError(f"在第 {self.config.get('date_row')} 行找不到日期 '{target_date}'\n\n(已尝试智能匹配：{target_date})")

            row_topics = all_data[row_topic_idx] if len(all_data) > row_topic_idx else []
            cells_to_update = []
            notes_payloads = [] 
            search_end = min(end_col_idx, len(row_topics))
            
            for entry in entries_to_write:
                topic = entry['topic']
                topic_col_number = -1
                for c_idx in range(start_col_idx, search_end):
                    if row_topics[c_idx].strip() == topic:
                        topic_col_number = c_idx + 1
                        break
                
                if topic_col_number != -1:
                    if entry['value']: 
                        cells_to_update.append(gspread.Cell(target_row_number, topic_col_number, entry['value']))
                    if entry['note']: 
                        notes_payloads.append({
                            "updateCells": {
                                "range": {
                                    "sheetId": ws.id, 
                                    "startRowIndex": target_row_number - 1, 
                                    "endRowIndex": target_row_number, 
                                    "startColumnIndex": topic_col_number - 1, 
                                    "endColumnIndex": topic_col_number
                                }, 
                                "rows": [{"values": [{"note": entry['note']}]}], 
                                "fields": "note"
                            }
                        })
                else:
                    print(f"警告: 在 {target_date} 范围内找不到题目 '{topic}'")

            if not cells_to_update and not notes_payloads: 
                raise ValueError("坐标定位失败：没有找到匹配的题目列。")

            self.submit_btn.setText("坐标锁定，正在极速写入...")
            QApplication.processEvents()
            
            if cells_to_update: 
                ws.update_cells(cells_to_update, value_input_option='USER_ENTERED')
            if notes_payloads: 
                sh.batch_update({"requests": notes_payloads})

            try:
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                for entry in entries_to_write:
                    cursor.execute(
                        '''REPLACE INTO history (date, name, topic, value, note) VALUES (?, ?, ?, ?, ?)''', 
                        (target_date, target_name, entry['topic'], entry['value'], entry['note'])
                    )
                conn.commit()
                conn.close()
            except Exception as db_e:
                print(f"本地数据库保存失败: {db_e}")

            QMessageBox.information(self, "成功", f"🎉 {target_name} ({target_date}) 的数据已成功同步至云端与本地！")
        except Exception as e:
            QMessageBox.critical(self, "写入失败", f"发生了错误:\n{str(e)}")
        finally:
            self.submit_btn.setText("开始定位并写入数据")
            self.submit_btn.setEnabled(True)
            self.submit_btn.setStyleSheet("background-color: #D95E46; color: white; border: none; border-radius: 4px;")

if __name__ == "__main__":
    init_db()
    app = QApplication(sys.argv)
    default_font = app.font()
    default_font.setPointSize(10)
    app.setFont(default_font)
    window = DataEntryApp()
    window.show()
    sys.exit(app.exec())