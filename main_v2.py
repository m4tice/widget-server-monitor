from __future__ import annotations

import math
import sys
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from main import (
    APP_NAME,
    CONFIG_PATH,
    CheckPool,
    CheckSignals,
    ensure_config,
    save_config,
    validate_server,
)


GLASS = {
    "surface": "rgba(224, 234, 244, 194)",
    "expanded_surface": "#E0EAF4",
    "surface_soft": "rgba(255, 255, 255, 52)",
    "line": "rgba(255, 255, 255, 92)",
    "line_strong": "rgba(255, 255, 255, 178)",
    "text": "#152331",
    "muted": "#52687A",
    "green": "#00E98A",
    "red": "#C80032",
    "amber": "#E5A900",
    "accent": "#1678A8",
}

DARK_GLASS = {
    "surface": "rgba(24, 34, 45, 226)",
    "expanded_surface": "#202A2F",
    "surface_soft": "rgba(255, 255, 255, 12)",
    "line": "rgba(255, 255, 255, 42)",
    "line_strong": "rgba(255, 255, 255, 88)",
    "text": "#F1F7FB",
    "muted": "#A7BBCB",
    "green": GLASS["green"],
    "red": GLASS["red"],
    "amber": GLASS["amber"],
    "accent": "#73D9FF",
}

COMPACT_TILE_SIZE = 14
COMPACT_TILE_GAP = 6
COMPACT_EDGE_OFFSET = 2
COMPACT_EDGE_PADDING = COMPACT_TILE_GAP + COMPACT_EDGE_OFFSET


class StatusMark(QWidget):
    def __init__(self, name: str, scale: float = 1.0, dot: bool = False, parent=None):
        super().__init__(parent)
        self.scale = scale
        self.dot = dot
        self.color = QColor(GLASS["amber"])
        size = 12 if dot else round(26 * scale)
        self.setFixedSize(size, size)
        self.setToolTip(f"{name} · waiting")

    def set_result(self, online: bool, name: str) -> None:
        self.color = QColor(GLASS["green"] if online else GLASS["red"])
        self.setToolTip(f"{name} · {'online' if online else 'offline'}")
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        size = self.width()
        if self.dot:
            painter.setBrush(self.color)
            painter.drawEllipse(2, 2, size - 4, size - 4)
            return
        inset = max(2, round(3 * self.scale))
        radius = max(3, round(7 * self.scale))
        glow = QColor(self.color)
        glow.setAlpha(62)
        painter.setBrush(glow)
        painter.drawRoundedRect(0, 0, size, size, radius, radius)
        painter.setBrush(self.color)
        painter.drawRoundedRect(inset, inset, size - inset * 2, size - inset * 2, radius - 1, radius - 1)
        highlight = QColor(255, 255, 255, 95)
        painter.setBrush(highlight)
        painter.drawRoundedRect(inset + 1, inset + 1, size - (inset + 1) * 2, max(1, round(2 * self.scale)), 1, 1)


class ServerEditor(QDialog):
    def __init__(self, parent=None, server: dict[str, Any] | None = None):
        super().__init__(parent)
        self.server_data: dict[str, str] | None = None
        self.setWindowTitle("Edit server" if server else "Add server")
        self.setModal(True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setMinimumWidth(390)
        self.setStyleSheet(f"""
            QDialog {{ background: #DCE7F0; color: {GLASS['text']}; }}
            QLabel {{ color: {GLASS['muted']}; }}
            QLineEdit, QComboBox {{
                background: rgba(255,255,255,118); color: {GLASS['text']};
                border: 1px solid {GLASS['line_strong']}; border-radius: 6px; padding: 8px;
            }}
            QPushButton {{
                background: rgba(255,255,255,96); color: {GLASS['text']};
                border: 1px solid {GLASS['line']}; border-radius: 6px; padding: 7px 12px;
            }}
            QPushButton:hover {{ background: rgba(255,255,255,165); }}
            QPushButton#primary {{ background: rgba(0,190,112,150); border-color: rgba(255,255,255,180); color: white; }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(14)
        form = QFormLayout()
        form.setSpacing(10)
        self.name_input = QLineEdit(server.get("name", "") if server else "")
        self.target_input = QLineEdit(server.get("url", "") if server else "")
        self.target_input.setPlaceholderText("https://host/health")
        self.type_input = QComboBox()
        self.type_input.addItem("HTTP / HTTPS", "http")
        self.type_input.addItem("TCP host:port", "tcp")
        if server:
            index = self.type_input.findData(server.get("type", "http"))
            self.type_input.setCurrentIndex(max(0, index))
        self.type_input.currentIndexChanged.connect(self._update_placeholder)
        form.addRow("Name", self.name_input)
        form.addRow("Target", self.target_input)
        form.addRow("Protocol", self.type_input)
        layout.addLayout(form)

        actions = QHBoxLayout()
        actions.addStretch()
        cancel = QPushButton("Cancel")
        save = QPushButton("Save")
        save.setObjectName("primary")
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self._save)
        actions.addWidget(cancel)
        actions.addWidget(save)
        layout.addLayout(actions)
        self._update_placeholder()
        QTimer.singleShot(0, self._activate_on_top)

    def _activate_on_top(self) -> None:
        self.raise_()
        self.activateWindow()

    def _update_placeholder(self) -> None:
        placeholder = "server.example.com:443" if self.type_input.currentData() == "tcp" else "https://host/health"
        self.target_input.setPlaceholderText(placeholder)

    def _save(self) -> None:
        name = self.name_input.text().strip()
        target = self.target_input.text().strip()
        check_type = str(self.type_input.currentData())
        error = validate_server(name, target, check_type)
        if error:
            QMessageBox.warning(self, "Invalid server", error)
            return
        self.server_data = {"name": name, "url": target, "type": check_type}
        self.accept()


class ServerLine(QFrame):
    edit_requested = Signal(str)
    delete_requested = Signal(str)

    def __init__(self, server: dict[str, Any], parent=None):
        super().__init__(parent)
        self.server_name = str(server["name"])
        self.setObjectName("serverLine")
        self.setFixedHeight(58)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 4, 16, 4)
        layout.setSpacing(12)
        self.mark = StatusMark(self.server_name, dot=True)
        layout.addWidget(self.mark)
        self.name_label = QLabel(self.server_name)
        self.name_label.setObjectName("serverName")
        self.name_label.setFixedWidth(170)
        layout.addWidget(self.name_label)

        self.target_label = QLabel(str(server.get("url", "")))
        self.target_label.setObjectName("serverDetail")
        self.target_label.setToolTip(str(server.get("url", "")))
        self.target_label.setFixedWidth(300)
        layout.addWidget(self.target_label)

        self.state_label = QLabel("WAITING")
        self.state_label.setObjectName("stateLabel")
        self.state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.state_label.setFixedSize(68, 22)
        self.state_label.hide()
        self.response_label = QLabel("—")
        self.response_label.setObjectName("responseLabel")
        self.response_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.response_label.setFixedWidth(84)
        layout.addWidget(self.response_label)
        edit = QPushButton("✎")
        edit.setObjectName("actionButton")
        edit.setToolTip("Edit server")
        remove = QPushButton("×")
        remove.setObjectName("actionButton")
        remove.setToolTip("Delete server")
        edit.clicked.connect(lambda: self.edit_requested.emit(self.server_name))
        remove.clicked.connect(lambda: self.delete_requested.emit(self.server_name))
        layout.addWidget(edit)
        layout.addWidget(remove)

    def set_result(self, result: dict[str, Any]) -> None:
        online = bool(result.get("online"))
        color = GLASS["green"] if online else GLASS["red"]
        self.mark.set_result(online, self.server_name)
        self.state_label.setText("ONLINE" if online else "OFFLINE")
        self.state_label.setStyleSheet(
            f"background: {color}; color: white; border: none; border-radius: 7px; padding: 3px 5px;"
        )
        latency = result.get("latency_ms")
        self.response_label.setText(f"{latency} ms" if latency is not None else "—")
        self.response_label.setToolTip(str(result.get("detail", "")))


def monitor_styles(colors: dict[str, str]) -> str:
    return f"""
        QWidget {{ font-family: "Segoe UI Variable", "Segoe UI"; color: {colors['text']}; }}
        QFrame#glassPanel {{ background: {colors['surface']}; border: 1px solid {colors['line_strong']}; border-radius: 14px; }}
        QWidget#expandedContent, QWidget#compactContent, QWidget#listHost {{ background: transparent; border: none; }}
        QLabel#eyebrow {{ color: {colors['text']}; font-size: 11px; font-weight: 700; }}
        QLabel#summary {{ color: {colors['text']}; font-size: 11px; font-weight: 700; }}
        QLabel#columnHeader {{ color: {colors['muted']}; font-size: 9px; font-weight: 700; }}
        QFrame#serverLine {{ background: transparent; border: none; }}
        QFrame#serverLine:hover {{ background: {colors['surface_soft']}; }}
        QLabel#serverName {{ color: {colors['text']}; font-size: 12px; font-weight: 600; }}
        QLabel#serverDetail {{ color: {colors['muted']}; font-size: 10px; }}
        QLabel#stateLabel {{ color: white; background: {colors['amber']}; border: none; border-radius: 7px; padding: 3px 5px; font-size: 9px; font-weight: 700; }}
        QLabel#responseLabel {{ color: {colors['text']}; font-size: 10px; font-weight: 600; }}
        QPushButton {{ background: rgba(255,255,255,56); color: {colors['text']}; border: none; border-radius: 5px; padding: 6px 9px; }}
        QPushButton:hover {{ background: rgba(255,255,255,100); }}
        QPushButton#iconButton {{ background: transparent; min-width: 26px; max-width: 26px; min-height: 26px; max-height: 26px; padding: 0; font-size: 14px; }}
        QPushButton#quietButton {{ background: transparent; border-color: transparent; color: {colors['muted']}; }}
        QPushButton#actionButton {{ background: transparent; border: none; color: {colors['text']}; min-width: 30px; max-width: 30px; min-height: 34px; max-height: 34px; padding: 0; font-size: 25px; }}
        QPushButton#actionButton:hover {{ background: {colors['surface_soft']}; }}
        QPushButton#primaryButton {{ background: rgba(0,190,112,150); border-color: rgba(255,255,255,160); color: #FFFFFF; }}
        QSpinBox {{ background: rgba(255,255,255,75); color: {colors['text']}; border: 1px solid rgba(255,255,255,110); border-radius: 5px; padding: 5px 7px; }}
        QCheckBox {{ color: {colors['text']}; spacing: 7px; }}
        QCheckBox#themeToggle {{ color: {colors['muted']}; font-size: 10px; font-weight: 600; spacing: 5px; }}
        QCheckBox#themeToggle::indicator {{ width: 22px; height: 13px; border-radius: 7px; background: rgba(255,255,255,60); border: 1px solid rgba(255,255,255,100); }}
        QCheckBox#themeToggle::indicator:checked {{ background: #1678A8; border-color: #B6E9FF; }}
        QScrollArea {{ background: transparent; border: none; }}
        QScrollBar:vertical {{ background: transparent; width: 5px; margin: 2px 0; }}
        QScrollBar::handle:vertical {{ background: rgba(255,255,255,35); border-radius: 2px; min-height: 24px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    """


class ServerMonitorV2(QWidget):
    def __init__(self):
        super().__init__()
        self.config = ensure_config()
        self.results: dict[str, dict[str, Any]] = {}
        self.expanded = False
        self.settings_open = False
        self.dark_mode = False
        self.quitting = False
        self.drag_offset = None
        self.signals = CheckSignals()
        self.signals.result.connect(self._on_result)
        self.check_pool = CheckPool(self.signals)
        self.rows: dict[str, ServerLine] = {}
        self.marks: dict[str, StatusMark] = {}
        self._build_ui()
        self._set_window_flags()
        self._refresh_servers()
        self._setup_timer()
        self._setup_tray()
        self._position_window()
        QTimer.singleShot(200, self.check_all)

    def _build_ui(self) -> None:
        self.setWindowTitle(f"{APP_NAME} v2")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setStyleSheet(monitor_styles(GLASS))

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        self.panel = QFrame()
        self.panel.setObjectName("glassPanel")
        root.addWidget(self.panel)
        self.shadow = QGraphicsDropShadowEffect(self)
        self.shadow.setBlurRadius(34)
        self.shadow.setOffset(0, 10)
        self.shadow.setColor(QColor(28, 54, 76, 115))
        self.panel.setGraphicsEffect(self.shadow)

        self.panel_layout = QVBoxLayout(self.panel)
        self.panel_layout.setContentsMargins(5, 5, 5, 5)
        self.panel_layout.setSpacing(0)

        self.compact_content = QWidget()
        self.compact_content.setObjectName("compactContent")
        self.compact_grid = QGridLayout(self.compact_content)
        self.compact_grid.setContentsMargins(
            COMPACT_EDGE_PADDING,
            COMPACT_EDGE_PADDING,
            COMPACT_EDGE_PADDING,
            COMPACT_EDGE_PADDING,
        )
        self.compact_grid.setHorizontalSpacing(COMPACT_TILE_GAP)
        self.compact_grid.setVerticalSpacing(COMPACT_TILE_GAP)
        self.panel_layout.addWidget(self.compact_content)

        self.expanded_content = QWidget()
        self.expanded_content.setObjectName("expandedContent")
        expanded_layout = QVBoxLayout(self.expanded_content)
        expanded_layout.setContentsMargins(9, 7, 9, 8)
        expanded_layout.setSpacing(7)

        header = QHBoxLayout()
        header.setSpacing(5)
        title = QLabel("SERVER ACTIVITY")
        title.setObjectName("eyebrow")
        header.addWidget(title)
        header.addStretch()
        self.summary = QLabel("—")
        self.summary.setObjectName("summary")
        header.addWidget(self.summary)
        self.dark_mode_toggle = QCheckBox("Dark")
        self.dark_mode_toggle.setObjectName("themeToggle")
        self.dark_mode_toggle.setToolTip("Use dark mode")
        self.dark_mode_toggle.toggled.connect(self.set_dark_mode)
        header.addWidget(self.dark_mode_toggle)
        self.settings_button = QPushButton("•••")
        self.settings_button.setObjectName("iconButton")
        self.settings_button.setCheckable(True)
        self.settings_button.setToolTip("Settings")
        self.settings_button.clicked.connect(self.toggle_settings)
        close = QPushButton("×")
        close.setObjectName("iconButton")
        close.setToolTip("Quit")
        close.clicked.connect(self.quit_application)
        header.addWidget(self.settings_button)
        header.addWidget(close)
        expanded_layout.addLayout(header)

        columns = QHBoxLayout()
        columns.setContentsMargins(40, 0, 16, 0)
        columns.setSpacing(12)
        for text, width in (("SERVER", 170), ("TARGET", 300), ("RESPONSE", 84)):
            label = QLabel(text)
            label.setObjectName("columnHeader")
            label.setFixedWidth(width)
            columns.addWidget(label)
        actions_label = QLabel("ACTIONS")
        actions_label.setObjectName("columnHeader")
        columns.addWidget(actions_label)
        expanded_layout.addLayout(columns)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_host = QWidget()
        self.list_host.setObjectName("listHost")
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(0)
        self.scroll.setWidget(self.list_host)
        expanded_layout.addWidget(self.scroll)

        footer = QHBoxLayout()
        self.updated_label = QLabel("Checking…")
        self.updated_label.setObjectName("serverDetail")
        footer.addWidget(self.updated_label, 1)
        refresh = QPushButton("↻")
        refresh.setObjectName("iconButton")
        refresh.setToolTip("Check all now")
        refresh.clicked.connect(self.check_all)
        footer.addWidget(refresh)
        expanded_layout.addLayout(footer)

        self.settings_sheet = QFrame()
        self.settings_sheet.setObjectName("serverLine")
        settings = QVBoxLayout(self.settings_sheet)
        settings.setContentsMargins(8, 8, 8, 4)
        form = QFormLayout()
        self.interval_input = QSpinBox()
        self.interval_input.setRange(5, 3600)
        self.interval_input.setSuffix(" sec")
        self.interval_input.setValue(int(self.config.get("check_interval_seconds", 15)))
        self.timeout_input = QSpinBox()
        self.timeout_input.setRange(1, 30)
        self.timeout_input.setSuffix(" sec")
        self.timeout_input.setValue(int(self.config.get("timeout_seconds", 3)))
        form.addRow("Interval", self.interval_input)
        form.addRow("Timeout", self.timeout_input)
        settings.addLayout(form)
        self.startup_input = QCheckBox("Start with Windows")
        self.startup_input.setChecked(bool(self.config.get("start_with_windows", False)))
        settings.addWidget(self.startup_input)
        settings_actions = QHBoxLayout()
        add = QPushButton("Add server")
        add.setObjectName("primaryButton")
        add.clicked.connect(self.add_server)
        save = QPushButton("Save")
        save.clicked.connect(self.save_settings)
        settings_actions.addWidget(add)
        settings_actions.addStretch()
        settings_actions.addWidget(save)
        settings.addLayout(settings_actions)
        expanded_layout.addWidget(self.settings_sheet)
        self.panel_layout.addWidget(self.expanded_content)
        self.settings_sheet.hide()
        self.expanded_content.hide()

    def set_dark_mode(self, enabled: bool) -> None:
        self.dark_mode = enabled
        self.setStyleSheet(monitor_styles(DARK_GLASS if enabled else GLASS))
        self._apply_panel_surface()

    def _apply_panel_surface(self) -> None:
        colors = DARK_GLASS if self.dark_mode else GLASS
        background = colors["expanded_surface"] if self.expanded else colors["surface"]
        border = "none" if self.expanded else f"1px solid {colors['line_strong']}"
        self.panel.setStyleSheet(
            f"QFrame#glassPanel {{ background: {background}; border: {border}; border-radius: 8px; }}"
        )

    def _set_window_flags(self) -> None:
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )

    def _servers(self) -> list[dict[str, Any]]:
        return [
            server
            for server in self.config.get("servers", [])
            if isinstance(server, dict) and server.get("name") and server.get("url")
        ]

    @staticmethod
    def _clear_layout(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _refresh_servers(self) -> None:
        self._clear_layout(self.compact_grid)
        self._clear_layout(self.list_layout)
        self.rows.clear()
        self.marks.clear()
        servers = self._servers()
        columns = min(5, max(1, len(servers)))
        for index, server in enumerate(servers):
            name = str(server["name"])
            mark = StatusMark(name, scale=0.52)
            self.compact_grid.addWidget(mark, index // columns, index % columns)
            self.marks[name] = mark
            row = ServerLine(server)
            row.edit_requested.connect(self.edit_server)
            row.delete_requested.connect(self.delete_server)
            self.list_layout.addWidget(row)
            self.rows[name] = row
            if name in self.results:
                mark.set_result(bool(self.results[name].get("online")), name)
                row.set_result(self.results[name])
        if not servers:
            empty = QLabel("No servers")
            empty.setObjectName("serverDetail")
            self.compact_grid.addWidget(empty, 0, 0)
            self.list_layout.addWidget(QLabel("No servers configured"))
        self.list_layout.addStretch(1)
        self._update_summary()
        self._apply_mode()

    def _apply_mode(self) -> None:
        count = max(1, len(self._servers()))
        self._apply_panel_surface()
        self.shadow.setEnabled(not self.expanded)
        if self.expanded:
            self.panel_layout.setContentsMargins(5, 5, 5, 5)
            self.compact_content.hide()
            self.expanded_content.show()
            self.settings_sheet.setVisible(self.settings_open)
            list_height = min(522, max(64, count * 58))
            self.scroll.setFixedHeight(list_height)
            width = 760
            height = 116 + list_height + (142 if self.settings_open else 0)
        else:
            self.panel_layout.setContentsMargins(0, 0, 0, 0)
            self.expanded_content.hide()
            self.compact_content.show()
            columns = min(5, count)
            rows = math.ceil(count / 5)
            width = (
                2
                + columns * COMPACT_TILE_SIZE
                + (columns - 1) * COMPACT_TILE_GAP
                + 2 * COMPACT_EDGE_PADDING
            )
            height = (
                2
                + rows * COMPACT_TILE_SIZE
                + (rows - 1) * COMPACT_TILE_GAP
                + 2 * COMPACT_EDGE_PADDING
            )
        self.setFixedSize(width + 20, min(height + 20, 740))

    def _update_summary(self) -> None:
        servers = self._servers()
        online = sum(1 for server in servers if self.results.get(str(server["name"]), {}).get("online") is True)
        percent = round((online / len(servers)) * 100) if servers else 0
        self.summary.setText(f"{online}/{len(servers)} ACTIVE · {percent}%")
        if self.results:
            self.updated_label.setText("Updated " + time.strftime("%H:%M:%S"))

    def _setup_timer(self) -> None:
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_all)
        self.timer.start(int(self.config.get("check_interval_seconds", 15)) * 1000)

    def _setup_tray(self) -> None:
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon), self)
        self.tray.setToolTip(f"{APP_NAME} v2")
        menu = QMenu()
        show = QAction("Show / hide", self)
        show.triggered.connect(self.toggle_visibility)
        check = QAction("Check now", self)
        check.triggered.connect(self.check_all)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.quit_application)
        menu.addAction(show)
        menu.addAction(check)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.show()

    def _position_window(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen:
            area = screen.availableGeometry()
            self.move(area.right() - self.width() - 24, area.top() + 120)

    def enterEvent(self, event) -> None:
        if not self.expanded:
            self.expanded = True
            self._apply_mode()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if not self.settings_open and not QApplication.activeModalWidget():
            self.expanded = False
            self._apply_mode()
        super().leaveEvent(event)

    def check_all(self) -> None:
        timeout = int(self.config.get("timeout_seconds", 3))
        for server in self._servers():
            self.check_pool.submit(server, timeout)

    def _on_result(self, name: str, result: dict[str, Any]) -> None:
        self.results[name] = result
        if name in self.marks:
            self.marks[name].set_result(bool(result.get("online")), name)
        if name in self.rows:
            self.rows[name].set_result(result)
        self._update_summary()

    def toggle_settings(self) -> None:
        self.settings_open = not self.settings_open
        self.settings_button.setChecked(self.settings_open)
        self._apply_mode()

    def add_server(self) -> None:
        editor = ServerEditor(self)
        if editor.exec() != QDialog.DialogCode.Accepted or not editor.server_data:
            return
        if any(server.get("name") == editor.server_data["name"] for server in self._servers()):
            QMessageBox.warning(self, "Duplicate name", "Each server must have a unique name.")
            return
        self.config.setdefault("servers", []).append(editor.server_data)
        self._save_and_refresh()

    def edit_server(self, name: str) -> None:
        server = next((item for item in self._servers() if item.get("name") == name), None)
        if not server:
            return
        editor = ServerEditor(self, server)
        if editor.exec() != QDialog.DialogCode.Accepted or not editor.server_data:
            return
        new_name = editor.server_data["name"]
        if new_name != name and any(item.get("name") == new_name for item in self._servers()):
            QMessageBox.warning(self, "Duplicate name", "Each server must have a unique name.")
            return
        server.update(editor.server_data)
        if name in self.results:
            self.results[new_name] = self.results.pop(name)
        self._save_and_refresh()

    def delete_server(self, name: str) -> None:
        if QMessageBox.question(self, "Delete server", f"Remove '{name}'?") != QMessageBox.StandardButton.Yes:
            return
        self.config["servers"] = [server for server in self._servers() if server.get("name") != name]
        self.results.pop(name, None)
        self._save_and_refresh()

    def save_settings(self) -> None:
        self.config["check_interval_seconds"] = self.interval_input.value()
        self.config["timeout_seconds"] = self.timeout_input.value()
        self.config["start_with_windows"] = self.startup_input.isChecked()
        save_config(self.config)
        self.timer.start(self.interval_input.value() * 1000)
        self.updated_label.setText("Settings saved")

    def _save_and_refresh(self) -> None:
        save_config(self.config)
        self._refresh_servers()
        self.check_all()

    def toggle_visibility(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    def quit_application(self) -> None:
        if self.quitting:
            return
        self.quitting = True
        self.check_pool.shutdown()
        if self.tray:
            self.tray.hide()
        QApplication.quit()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self.drag_offset = None
        super().mouseReleaseEvent(event)

    def closeEvent(self, event) -> None:
        self.quit_application()
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(f"{APP_NAME} v2")
    app.setQuitOnLastWindowClosed(False)
    try:
        window = ServerMonitorV2()
    except RuntimeError as exc:
        QMessageBox.critical(None, "Server Monitor startup error", str(exc))
        return 1
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())