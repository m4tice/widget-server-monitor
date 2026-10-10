from __future__ import annotations

import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from PySide6.QtCore import Qt, QTimer, Signal, QObject, QPropertyAnimation, QEasingCurve, QSize
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QLineEdit, QComboBox, QSpinBox, QDialog, QFormLayout,
    QMessageBox, QScrollArea, QSystemTrayIcon, QMenu, QCheckBox, QSizePolicy
)

APP_NAME = "Server Monitor"
APP_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "ServerMonitor"
CONFIG_PATH = APP_DIR / "servers.json"
DEFAULT_CONFIG = {
    "check_interval_seconds": 15,
    "timeout_seconds": 3,
    "start_with_windows": False,
    "servers": [
        {"name": "Example HTTPS", "url": "https://example.com", "type": "http"},
        {"name": "Example TCP", "url": "example.com:443", "type": "tcp"}
    ]
}

COLORS = {
    "green": "#36D77A",
    "red": "#F04F58",
    "amber": "#F2B84B",
    "text": "#F2F4F7",
    "muted": "#A5ABB5",
    "panel": "rgba(28, 31, 36, 238)",
    "border": "rgba(255, 255, 255, 28)",
}


def ensure_config() -> dict[str, Any]:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Configuration root must be an object")
        data.setdefault("check_interval_seconds", 15)
        data.setdefault("timeout_seconds", 3)
        data.setdefault("start_with_windows", False)
        data.setdefault("servers", [])
        return data
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        raise RuntimeError(f"Cannot read configuration file:\n{CONFIG_PATH}\n\n{exc}") from exc


def save_config(data: dict[str, Any]) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    temp = CONFIG_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temp.replace(CONFIG_PATH)


def validate_server(name: str, url: str, check_type: str) -> str | None:
    if not name.strip():
        return "Enter a server name."
    if not url.strip():
        return "Enter a URL or host:port."
    if check_type == "http":
        parsed = urlparse(url.strip())
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return "HTTP checks require a full http:// or https:// URL."
    else:
        value = url.strip()
        if "://" in value:
            return "TCP checks use host:port, without a URL scheme."
        if ":" not in value:
            return "TCP checks require host:port, for example server.example.com:443."
        host, port_text = value.rsplit(":", 1)
        try:
            port = int(port_text)
        except ValueError:
            return "TCP port must be a number."
        if not host.strip() or not 1 <= port <= 65535:
            return "Enter a valid hostname and port (1–65535)."
    return None


def check_server(server: dict[str, Any], timeout: float) -> dict[str, Any]:
    started = time.perf_counter()
    name = str(server.get("name", "Unnamed"))
    check_type = str(server.get("type", "http")).lower()
    target = str(server.get("url", "")).strip()
    try:
        if check_type == "tcp":
            host, port_text = target.rsplit(":", 1)
            with socket.create_connection((host.strip(), int(port_text)), timeout=timeout):
                pass
            detail = "TCP connection succeeded"
        else:
            request = urllib.request.Request(
                target,
                headers={"User-Agent": "ServerMonitor/1.0"},
                method="GET",
            )
            # Use the system's standard certificate validation. Do not disable TLS checks.
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = int(response.status)
                if 200 <= status < 400:
                    detail = f"HTTP {status}"
                else:
                    return {
                        "name": name, "online": False,
                        "latency_ms": round((time.perf_counter() - started) * 1000),
                        "detail": f"HTTP {status}", "checked_at": time.time()
                    }
        return {
            "name": name, "online": True,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "detail": detail, "checked_at": time.time()
        }
    except urllib.error.HTTPError as exc:
        # HTTPError still means the server answered; classify HTTP 4xx/5xx as unhealthy.
        return {
            "name": name, "online": False,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "detail": f"HTTP {exc.code}", "checked_at": time.time()
        }
    except Exception as exc:
        reason = str(exc).strip() or exc.__class__.__name__
        return {
            "name": name, "online": False,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "detail": reason[:100], "checked_at": time.time()
        }


class CheckSignals(QObject):
    result = Signal(str, dict)


class CheckPool:
    """Bounded background worker pool; all network operations happen off the UI thread."""
    def __init__(self, signals: CheckSignals):
        self.signals = signals
        self.pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="server-check")
        self.in_flight: set[str] = set()

    def submit(self, server: dict[str, Any], timeout: int) -> None:
        name = str(server.get("name", "Unnamed"))
        if name in self.in_flight:
            return
        self.in_flight.add(name)
        future = self.pool.submit(check_server, dict(server), timeout)
        future.add_done_callback(lambda f, n=name: self._done(n, f))

    def _done(self, name, future):
        try:
            result = future.result()
        except Exception as exc:
            result = {"name": name, "online": False, "latency_ms": 0, "detail": str(exc), "checked_at": time.time()}
        self.signals.result.emit(name, result)
        self.in_flight.discard(name)

    def shutdown(self):
        self.pool.shutdown(wait=False, cancel_futures=True)


class ServerDialog(QDialog):
    def __init__(self, parent=None, server=None):
        super().__init__(parent)
        self.setWindowTitle("Edit server" if server else "Add server")
        self.setModal(True)
        self.setMinimumWidth(390)
        self.setStyleSheet("""
            QDialog { background: #202328; color: #F2F4F7; }
            QLabel { color: #D8DCE2; }
            QLineEdit, QComboBox { background: #15171A; color: #F2F4F7; border: 1px solid #454A53; border-radius: 6px; padding: 8px; }
            QPushButton { background: #343941; color: #F2F4F7; border: 0; border-radius: 6px; padding: 8px 12px; }
            QPushButton:hover { background: #454B55; }
        """)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_input = QLineEdit(server.get("name", "") if server else "")
        self.url_input = QLineEdit(server.get("url", "") if server else "")
        self.url_input.setPlaceholderText("https://host/health  or  host:443")
        self.type_input = QComboBox()
        self.type_input.addItem("HTTP / HTTPS", "http")
        self.type_input.addItem("TCP host:port", "tcp")
        if server:
            idx = self.type_input.findData(server.get("type", "http"))
            if idx >= 0:
                self.type_input.setCurrentIndex(idx)
        form.addRow("Display name", self.name_input)
        form.addRow("URL / address", self.url_input)
        form.addRow("Check type", self.type_input)
        layout.addLayout(form)
        self.hint = QLabel("HTTP checks treat HTTP 200–399 as online.")
        self.hint.setStyleSheet("color: #A5ABB5;")
        layout.addWidget(self.hint)
        self.type_input.currentIndexChanged.connect(self._update_hint)
        self._update_hint()
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Cancel")
        save = QPushButton("Save")
        save.setStyleSheet("QPushButton { background: #287A50; } QPushButton:hover { background: #329460; }")
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)
        self.server_data = None

    def _update_hint(self):
        if self.type_input.currentData() == "tcp":
            self.hint.setText("TCP checks only test whether a port accepts connections.")
            self.url_input.setPlaceholderText("server.example.com:443")
        else:
            self.hint.setText("HTTP checks treat HTTP 200–399 as online.")
            self.url_input.setPlaceholderText("https://host/health")

    def _save(self):
        name = self.name_input.text().strip()
        url = self.url_input.text().strip()
        check_type = self.type_input.currentData()
        error = validate_server(name, url, check_type)
        if error:
            QMessageBox.warning(self, "Invalid server", error)
            return
        self.server_data = {"name": name, "url": url, "type": check_type}
        self.accept()


class ServerRow(QFrame):
    edit_requested = Signal(str)
    delete_requested = Signal(str)
    check_requested = Signal(str)

    def __init__(self, server: dict[str, Any], expanded: bool, parent=None):
        super().__init__(parent)
        self.server_name = server["name"]
        self.expanded = expanded
        self.setObjectName("serverRow")
        self.setStyleSheet("""
            QFrame#serverRow { background: rgba(255,255,255,5); border: 1px solid rgba(255,255,255,13); border-radius: 10px; }
            QLabel { border: none; background: transparent; }
            QPushButton { background: rgba(255,255,255,12); color: #D8DCE2; border: none; border-radius: 5px; padding: 4px 7px; }
            QPushButton:hover { background: rgba(255,255,255,25); }
        """)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(8)
        self.dot = QLabel("●")
        self.dot.setFixedWidth(12)
        self.dot.setStyleSheet(f"color: {COLORS['amber']}; font-size: 12px;")
        row.addWidget(self.dot)
        labels = QVBoxLayout()
        labels.setSpacing(2)
        self.name_label = QLabel(server["name"])
        self.name_label.setStyleSheet("color: #F2F4F7; font-weight: 600;")
        self.detail_label = QLabel(f"{server.get('type', 'http').upper()} · Not checked yet")
        self.detail_label.setStyleSheet("color: #A5ABB5; font-size: 11px;")
        self.detail_label.setWordWrap(True)
        labels.addWidget(self.name_label)
        labels.addWidget(self.detail_label)
        row.addLayout(labels, 1)
        self.status_label = QLabel("UNKNOWN")
        self.status_label.setStyleSheet("color: #F2B84B; font-size: 10px; font-weight: 700;")
        row.addWidget(self.status_label)
        self.edit_btn = QPushButton("Edit")
        self.delete_btn = QPushButton("×")
        self.edit_btn.setToolTip("Edit server")
        self.delete_btn.setToolTip("Delete server")
        self.edit_btn.clicked.connect(lambda: self.edit_requested.emit(self.server_name))
        self.delete_btn.clicked.connect(lambda: self.delete_requested.emit(self.server_name))
        if expanded:
            row.addWidget(self.edit_btn)
            row.addWidget(self.delete_btn)
        else:
            self.detail_label.hide()
            self.status_label.hide()

    def set_result(self, result: dict[str, Any]):
        online = bool(result.get("online"))
        color = COLORS["green"] if online else COLORS["red"]
        self.dot.setStyleSheet(f"color: {color}; font-size: 12px;")
        self.status_label.setStyleSheet(f"color: {color}; font-size: 10px; font-weight: 700;")
        self.status_label.setText("ONLINE" if online else "OFFLINE")
        detail = result.get("detail", "")
        latency = result.get("latency_ms")
        latency_text = f"{latency} ms" if latency is not None else ""
        self.detail_label.setText(f"{detail} · {latency_text}".strip(" ·"))


class ServerMonitor(QWidget):
    def __init__(self):
        super().__init__()
        self.config = ensure_config()
        self.results: dict[str, dict[str, Any]] = {}
        self.is_expanded = False
        self.drag_pos = None
        self.signals = CheckSignals()
        self.signals.result.connect(self.on_result)
        self.check_pool = CheckPool(self.signals)
        self.rows: dict[str, ServerRow] = {}
        self._build_ui()
        self._apply_window_flags()
        self._position_window()
        self._refresh_rows()
        self._setup_timers()
        self._setup_tray()
        self._apply_startup_setting(bool(self.config.get("start_with_windows", False)))
        QTimer.singleShot(250, self.check_all)

    def _build_ui(self):
        self.setWindowTitle(APP_NAME)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setMouseTracking(True)
        self.setMinimumWidth(230)
        self.setMaximumWidth(420)
        self.setStyleSheet("""
            QWidget#panel { background: rgba(25, 28, 33, 238); border: 1px solid rgba(255,255,255,28); border-radius: 15px; }
            QLabel { color: #F2F4F7; }
            QPushButton { background: rgba(255,255,255,12); color: #D8DCE2; border: none; border-radius: 6px; padding: 6px 9px; }
            QPushButton:hover { background: rgba(255,255,255,25); }
            QScrollArea { border: none; background: transparent; }
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.panel = QFrame()
        self.panel.setObjectName("panel")
        outer.addWidget(self.panel)
        self.panel_layout = QVBoxLayout(self.panel)
        self.panel_layout.setContentsMargins(12, 10, 12, 12)
        self.panel_layout.setSpacing(9)

        header = QHBoxLayout()
        self.title = QLabel("SERVER MONITOR")
        self.title.setStyleSheet("color: #A5ABB5; font-size: 10px; font-weight: 700; letter-spacing: 1px;")
        header.addWidget(self.title)
        header.addStretch()
        self.summary = QLabel("…")
        self.summary.setStyleSheet("color: #A5ABB5; font-size: 11px;")
        header.addWidget(self.summary)
        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setToolTip("Settings")
        self.settings_btn.clicked.connect(self.open_settings)
        header.addWidget(self.settings_btn)
        self.panel_layout.addLayout(header)

        self.rows_host = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_host)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(6)
        self.panel_layout.addWidget(self.rows_host)

        self.footer = QHBoxLayout()
        self.last_checked = QLabel("Starting checks…")
        self.last_checked.setStyleSheet("color: #A5ABB5; font-size: 10px;")
        self.footer.addWidget(self.last_checked, 1)
        self.check_btn = QPushButton("↻")
        self.check_btn.setToolTip("Check all now")
        self.check_btn.clicked.connect(self.check_all)
        self.footer.addWidget(self.check_btn)
        self.panel_layout.addLayout(self.footer)

        self.settings_area = QFrame()
        self.settings_area.setStyleSheet("QFrame { border-top: 1px solid rgba(255,255,255,20); }")
        settings_layout = QVBoxLayout(self.settings_area)
        settings_layout.setContentsMargins(0, 8, 0, 0)
        form = QFormLayout()
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(5, 3600)
        self.interval_spin.setSuffix(" sec")
        self.interval_spin.setValue(int(self.config.get("check_interval_seconds", 15)))
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 30)
        self.timeout_spin.setSuffix(" sec")
        self.timeout_spin.setValue(int(self.config.get("timeout_seconds", 3)))
        for spin in (self.interval_spin, self.timeout_spin):
            spin.setStyleSheet("QSpinBox { background: #15171A; color: #F2F4F7; border: 1px solid #454A53; border-radius: 5px; padding: 5px; }")
        form.addRow("Check interval", self.interval_spin)
        form.addRow("Timeout", self.timeout_spin)
        settings_layout.addLayout(form)
        self.startup_check = QCheckBox("Start with Windows")
        self.startup_check.setChecked(bool(self.config.get("start_with_windows", False)))
        self.startup_check.setStyleSheet("color: #D8DCE2;")
        settings_layout.addWidget(self.startup_check)
        buttons = QHBoxLayout()
        add_btn = QPushButton("+ Add server")
        add_btn.clicked.connect(self.add_server)
        open_json_btn = QPushButton("Open JSON")
        open_json_btn.clicked.connect(self.open_json)
        save_btn = QPushButton("Save settings")
        save_btn.clicked.connect(self.save_settings)
        buttons.addWidget(add_btn)
        buttons.addWidget(open_json_btn)
        buttons.addWidget(save_btn)
        settings_layout.addLayout(buttons)
        self.panel_layout.addWidget(self.settings_area)
        self.settings_area.hide()
        self._resize_to_state()

    def _apply_window_flags(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )

    def _position_window(self):
        screen = QGuiApplication.primaryScreen()
        if screen:
            area = screen.availableGeometry()
            self.move(area.right() - self.width() - 24, area.top() + 140)

    def _setup_timers(self):
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.check_all)
        self.poll_timer.start(int(self.config.get("check_interval_seconds", 15)) * 1000)

    def _setup_tray(self):
        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(self)
            self.tray.setToolTip(APP_NAME)
            menu = QMenu()
            show_action = QAction("Show / hide widget", self)
            show_action.triggered.connect(self.toggle_visibility)
            check_action = QAction("Check all now", self)
            check_action.triggered.connect(self.check_all)
            settings_action = QAction("Settings", self)
            settings_action.triggered.connect(self.expand)
            quit_action = QAction("Quit", self)
            quit_action.triggered.connect(QApplication.quit)
            menu.addAction(show_action)
            menu.addAction(check_action)
            menu.addAction(settings_action)
            menu.addSeparator()
            menu.addAction(quit_action)
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(lambda reason: self.toggle_visibility() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
            self.tray.show()

    def _refresh_rows(self):
        while self.rows_layout.count():
            item = self.rows_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.rows.clear()
        for server in self.config.get("servers", []):
            if not isinstance(server, dict) or not server.get("name") or not server.get("url"):
                continue
            row = ServerRow(server, self.is_expanded)
            row.edit_requested.connect(self.edit_server)
            row.delete_requested.connect(self.delete_server)
            self.rows_layout.addWidget(row)
            self.rows[server["name"]] = row
            if server["name"] in self.results:
                row.set_result(self.results[server["name"]])
        if not self.rows:
            empty = QLabel("No servers yet. Open settings to add one.")
            empty.setStyleSheet("color: #A5ABB5; padding: 8px;")
            self.rows_layout.addWidget(empty)
        self.rows_layout.addStretch(1)
        self._update_summary()
        self._resize_to_state()

    def _update_summary(self):
        total = len(self.rows)
        online = sum(1 for name in self.rows if self.results.get(name, {}).get("online") is True)
        checked = sum(1 for name in self.rows if name in self.results)
        if total == 0:
            self.summary.setText("No servers")
        elif checked < total:
            self.summary.setText(f"{online}/{total} online")
        else:
            self.summary.setText(f"{online}/{total} online")
        if checked:
            self.last_checked.setText("Updated " + time.strftime("%H:%M:%S"))
        else:
            self.last_checked.setText("Waiting for checks…")

    def _resize_to_state(self):
        self.settings_area.setVisible(self.is_expanded)
        for row in self.rows.values():
            row.deleteLater()
        # Rows are rebuilt when expansion changes to show/hide details/actions.
        if hasattr(self, "rows_layout") and self.rows:
            # avoid recursive rebuild during initial construction
            pass
        width = 365 if self.is_expanded else 245
        count = max(1, len(self.config.get("servers", [])))
        # Keep every row visible without forcing the user to scroll in the compact state.
        height = (150 + count * 58 + 160) if self.is_expanded else (90 + count * 38 + 30)
        self.setFixedSize(width, min(height, 760))

    def enterEvent(self, event):
        self.expand()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if not self.settings_area.underMouse():
            self.collapse()
        super().leaveEvent(event)

    def expand(self):
        if self.is_expanded:
            return
        self.is_expanded = True
        self.settings_area.show()
        self._refresh_rows()

    def collapse(self):
        if not self.is_expanded:
            return
        self.is_expanded = False
        self.settings_area.hide()
        self._refresh_rows()

    def check_all(self):
        timeout = int(self.config.get("timeout_seconds", 3))
        for server in self.config.get("servers", []):
            if server.get("name") and server.get("url"):
                self.check_pool.submit(server, timeout)

    def on_result(self, name: str, result: dict[str, Any]):
        self.results[name] = result
        if name in self.rows:
            self.rows[name].set_result(result)
        self._update_summary()
        if self.tray:
            state = "online" if result.get("online") else "offline"
            self.tray.setToolTip(f"{APP_NAME} — {name}: {state}")

    def add_server(self):
        dialog = ServerDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.server_data:
            if any(s.get("name") == dialog.server_data["name"] for s in self.config["servers"]):
                QMessageBox.warning(self, "Duplicate name", "Each server must have a unique display name.")
                return
            self.config["servers"].append(dialog.server_data)
            self._persist_and_refresh()

    def edit_server(self, name: str):
        server = next((s for s in self.config["servers"] if s.get("name") == name), None)
        if not server:
            return
        dialog = ServerDialog(self, server)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.server_data:
            new_name = dialog.server_data["name"]
            if new_name != name and any(s.get("name") == new_name for s in self.config["servers"]):
                QMessageBox.warning(self, "Duplicate name", "Each server must have a unique display name.")
                return
            server.update(dialog.server_data)
            if new_name != name and name in self.results:
                self.results[new_name] = self.results.pop(name)
            self._persist_and_refresh()

    def delete_server(self, name: str):
        answer = QMessageBox.question(self, "Delete server", f"Remove '{name}' from the monitor?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.config["servers"] = [s for s in self.config["servers"] if s.get("name") != name]
        self.results.pop(name, None)
        self._persist_and_refresh()

    def save_settings(self):
        self.config["check_interval_seconds"] = int(self.interval_spin.value())
        self.config["timeout_seconds"] = int(self.timeout_spin.value())
        self.config["start_with_windows"] = self.startup_check.isChecked()
        try:
            save_config(self.config)
            self.poll_timer.start(self.config["check_interval_seconds"] * 1000)
            self._apply_startup_setting(self.config["start_with_windows"])
            self.last_checked.setText("Settings saved")
            self.check_all()
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))

    def _persist_and_refresh(self):
        try:
            save_config(self.config)
            self._refresh_rows()
            self.check_all()
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))

    def open_settings(self):
        self.expand()

    def open_json(self):
        try:
            save_config(self.config)
            os.startfile(str(CONFIG_PATH))
        except Exception as exc:
            QMessageBox.warning(self, "Cannot open JSON", f"Configuration path:\n{CONFIG_PATH}\n\n{exc}")

    def _apply_startup_setting(self, enabled: bool):
        if sys.platform != "win32":
            return
        startup_dir = Path(os.environ.get("APPDATA", str(Path.home()))) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        shortcut = startup_dir / "ServerMonitor.cmd"
        try:
            startup_dir.mkdir(parents=True, exist_ok=True)
            if enabled:
                # Launch using the current Python executable; works when run from source.
                script = Path(__file__).resolve()
                content = f'@echo off\r\nstart "" "{sys.executable}" "{script}"\r\n'
                shortcut.write_text(content, encoding="utf-8")
            elif shortcut.exists():
                shortcut.unlink()
        except OSError as exc:
            QMessageBox.warning(self, "Startup setting", f"Could not update Windows startup:\n{exc}")

    def toggle_visibility(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_pos)
            event.accept()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.drag_pos = None
        super().mouseReleaseEvent(event)

    def closeEvent(self, event):
        self.check_pool.shutdown()
        if self.tray:
            self.tray.hide()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    try:
        window = ServerMonitor()
    except RuntimeError as exc:
        QMessageBox.critical(None, "Server Monitor startup error", str(exc))
        return 1
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
