from __future__ import annotations
import threading
from typing import Dict

from PyQt6.QtCore import Qt, QTimer, pyqtSlot, QEvent, pyqtSignal
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QApplication, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QListWidget, QListWidgetItem,
    QFileDialog, QMessageBox, QScrollArea, QFrame, QProgressBar
)

from state import AppStatus, TransferStatus
from network import TransferService, _TransferRequestEvent
from history_window import HistoryWindow

MAX_NAME_LEN = 32
STATUS_DOT = {AppStatus.AVAILABLE: "🟢", AppStatus.BUSY: "🔴"}


# ========================================================
# Status Toggle (păstrează culorile tale)
# ========================================================

class StatusButtonToggle(QWidget):
    status_changed = pyqtSignal(AppStatus)

    def __init__(self, current_status: AppStatus, parent=None):
        super().__init__(parent)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)

        self.btn_available = self._make_btn("Available")
        self.btn_busy = self._make_btn("Busy")

        layout.addWidget(self.btn_available)
        layout.addWidget(self.btn_busy)

        self.btn_available.clicked.connect(lambda: self._set(AppStatus.AVAILABLE))
        self.btn_busy.clicked.connect(lambda: self._set(AppStatus.BUSY))
        self._set(current_status, init=True)

    def _make_btn(self, text):
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        return btn

    def _set(self, status: AppStatus, init=False):
        self.btn_available.setChecked(status == AppStatus.AVAILABLE)
        self.btn_busy.setChecked(status == AppStatus.BUSY)

        self._apply_style(self.btn_available, "green")
        self._apply_style(self.btn_busy, "red")

        if not init:
            self.status_changed.emit(status)

    def _apply_style(self, btn, color):
        if btn.isChecked():
            btn.setStyleSheet(
                f"""
                QPushButton {{
                    background:{color};
                    color:white;
                    padding:8px;
                    border-radius:6px;
                }}"""
            )
        else:
            btn.setStyleSheet("""
                QPushButton {
                    background:#2b3037;
                    color:#b7bfca;
                    padding:6px;
                    border-radius:4px;
                }
                QPushButton:hover {
                    background:#40464f;
                }"""
            )


# ========================================================
# PROGRESS ROW (culori păstrate)
# ========================================================

class DeviceProgressRow(QFrame):
    def __init__(self, dev_id: str, name: str, app_state, parent=None):
        super().__init__(parent)
        self.dev_id = dev_id
        self.app_state = app_state

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10,10,10,10)

        self.lbl = QLabel(f"{dev_id}  {name}")
        self.bar = QProgressBar()

        layout.addWidget(self.lbl)
        layout.addWidget(self.bar)

    def set_ratio(self, ratio):
        self.bar.setValue(int(ratio * 100))
        status = self.app_state.get_transfer_status(self.dev_id)
        device = self.app_state.devices.get(self.dev_id)
        name = device.name if device else self.dev_id

        if status == TransferStatus.CANCELED:
            self.lbl.setText(f"{name} - CANCELED")
        elif status == TransferStatus.ERROR:
            self.lbl.setText(f"{name} - ERROR")
        else:
            speed = self.app_state.get_speed(self.dev_id)
            self.lbl.setText(f"{name} - {speed:.2f} MB/s" if speed > 0 else name)


# ========================================================
# PROGRESS PANEL
# ========================================================

class ProgressPanel(QWidget):
    def __init__(self, app_state, parent=None):
        super().__init__(parent)
        self.app_state = app_state
        self.rows: Dict[str, DeviceProgressRow] = {}

        layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll)

        self.inner = QWidget()
        self.inner_layout = QVBoxLayout(self.inner)
        self.inner_layout.addStretch()
        scroll.setWidget(self.inner)

    def update(self, state):
        for dev_id, ratio in state.progress.items():
            if dev_id not in self.rows:
                row = DeviceProgressRow(dev_id, state.devices[dev_id].name, state)
                self.rows[dev_id] = row
                self.inner_layout.insertWidget(self.inner_layout.count()-1, row)
            self.rows[dev_id].set_ratio(ratio)

        remove = [d for d in self.rows if d not in state.progress]
        for d in remove:
            self.rows[d].deleteLater()
            del self.rows[d]


# ========================================================
# MAIN WINDOW (tema dark păstrată)
# ========================================================

class FIshareQtApp(QMainWindow):
    def __init__(self, state, advertiser, scanner, history=None):
        super().__init__()

        self.state = state
        self.advertiser = advertiser
        self.scanner = scanner
        self.history = history
        self.transfer = TransferService(state, self, history)

        self.setWindowTitle("FIshare")
        self.resize(1024, 720)
        self._apply_style()

        central = QWidget()
        root = QVBoxLayout(central)
        self.setCentralWidget(central)

        root.addLayout(self._top())
        root.addLayout(self._body())
        root.addWidget(self._bottom())

        self.timer = QTimer(timeout=self.refresh_ui)
        self.timer.start(500)

    # ---------------- TOP ----------------

    def _top(self):
        layout = QHBoxLayout()
        layout.addWidget(QLabel("FIshare"))
        layout.addWidget(QLabel("Name"))

        self.name_edit = QLineEdit(self.state.cfg.device_name)
        self.name_edit.setMaxLength(MAX_NAME_LEN)
        self.name_edit.textEdited.connect(self._on_name)
        layout.addWidget(self.name_edit)

        layout.addWidget(QLabel("Status"))
        self.status_toggle = StatusButtonToggle(self.state.status)
        self.status_toggle.status_changed.connect(self._on_status)
        layout.addWidget(self.status_toggle)

        btn_folder = QPushButton("Folder")
        btn_folder.clicked.connect(self._pick_folder)
        layout.addWidget(btn_folder)

        btn_hist = QPushButton("History")
        btn_hist.clicked.connect(self._show_history)
        layout.addWidget(btn_hist)

        return layout

    # ---------------- BODY ----------------

    def _body(self):
        body = QHBoxLayout()

        # left
        left = QVBoxLayout()
        left.addWidget(QLabel("Devices"))
        self.devices = QListWidget()
        self.devices.itemDoubleClicked.connect(self._add_peer)
        left.addWidget(self.devices)

        # right
        right = QVBoxLayout()
        right.addWidget(QLabel("Targets"))
        self.targets = QListWidget()
        self.targets.itemDoubleClicked.connect(self._remove_peer)
        right.addWidget(self.targets)

        right.addWidget(QLabel("Files"))
        self.files = QListWidget()
        right.addWidget(self.files)

        btn_file = QPushButton("Add Files")
        btn_file.clicked.connect(self._pick_files)
        right.addWidget(btn_file)

        right.addWidget(QLabel("Progress"))
        self.progress_panel = ProgressPanel(self.state)
        right.addWidget(self.progress_panel)

        body.addLayout(left,1)
        body.addLayout(right,1)
        return body

    # ---------------- BOTTOM ----------------

    def _bottom(self):
        layout = QHBoxLayout()
        layout.addStretch()

        self.send_btn = QPushButton("Send")
        self.send_btn.setEnabled(False)
        self.send_btn.clicked.connect(self._send)
        layout.addWidget(self.send_btn)

        cont = QWidget()
        cont.setLayout(layout)
        return cont

    # ---------------- STYLE ----------------

    def _apply_style(self):
        self.setStyleSheet("""
        QMainWindow { background:#0b0e12; color:#e6e9ee; }

        QLabel { color:#e6e9ee; }

        QPushButton {
            background:#1c2128;
            color:#e6e9ee;
            border:1px solid #30363d;
            padding:10px 18px;
            border-radius:8px;
        }
        QPushButton:hover {
            background:#2d333b;
        }
        QPushButton:disabled {
            background:#2b3037;
            color:#888;
        }

        QListWidget {
            background:#13171c;
            color:#e6e9ee;
            border:1px solid #30363d;
            border-radius:8px;
        }

        QLineEdit {
            background:#13171c;
            color:#e6e9ee;
            border:1px solid #30363d;
            padding:8px;
            border-radius:6px;
        }
        """)

    # =====================================================
    # LOGIC
    # =====================================================

    @pyqtSlot()
    def _on_name(self):
        self.state.cfg.device_name = self.name_edit.text().strip()
        self.state.cfg.save()

    def _on_status(self, s):
        self.state.set_status(s)

    def _pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Choose Folder")
        if d:
            self.state.cfg.download_dir = d
            self.state.cfg.save()

    def _pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select Files")
        self.state.selected_files = files
        self.refresh_lists()

    def _add_peer(self):
        item = self.devices.currentItem()
        if not item:
            return
        dev_id = item.text().split()[0]
        if dev_id not in self.state.selected_device_ids:
            dev = self.state.devices.get(dev_id)
            if dev and dev.status == AppStatus.AVAILABLE:
                self.state.selected_device_ids.append(dev_id)
        self.refresh_lists()

    def _remove_peer(self):
        item = self.targets.currentItem()
        if not item:
            return
        dev_id = item.text().split()[0]
        if dev_id in self.state.selected_device_ids:
            self.state.selected_device_ids.remove(dev_id)
        self.refresh_lists()

    # SEND
    @pyqtSlot()
    def _send(self):
        self.send_btn.setEnabled(False)
        threading.Thread(target=self._do_send, daemon=True).start()

    def _do_send(self):
        for dev_id in list(self.state.selected_device_ids):
            if dev_id in self.state.devices:
                self.transfer.send_to(self.state.devices[dev_id], self.state.selected_files)

        QApplication.instance().postEvent(self, _InvokeEvent(lambda: self.send_btn.setEnabled(True)))

    # REFRESH
    @pyqtSlot()
    def refresh_ui(self):
        self.refresh_lists()
        self.progress_panel.update(self.state)

    def refresh_lists(self):
        self.devices.clear()
        for dev_id, dev in self.state.devices.items():
            self.devices.addItem(f"{dev_id} {dev.name} {STATUS_DOT[dev.status]}")

        self.targets.clear()
        for dev_id in self.state.selected_device_ids:
            dev = self.state.devices.get(dev_id)
            if dev:
                self.targets.addItem(f"{dev_id} {dev.name}")

        self.files.clear()
        for f in self.state.selected_files:
            self.files.addItem(f)

        # ACTIVATE SEND ONLY IF VALID
        ok_files = len(self.state.selected_files) > 0
        ok_peers = len(self.state.selected_device_ids) > 0
        self.send_btn.setEnabled(ok_files and ok_peers)

    # HISTORY
    def _show_history(self):
        if self.history:
            HistoryWindow(self.history, self).exec()

    # EVENTS
    def event(self, e):
        if isinstance(e, _InvokeEvent):
            e.performAction()
            return True
        if isinstance(e, _TransferRequestEvent):
            self._incoming(e)
            return True
        return super().event(e)

    def _incoming(self, event):
        mb = event.total_size / (1024 * 1024)
        msg = f"{event.peer_name} wants to send {event.num_files} file(s)\nSize: {mb:.2f} MB\nAccept?"
        reply = QMessageBox.question(self, "Incoming Transfer", msg,
                                     QMessageBox.StandardButton.Yes |
                                     QMessageBox.StandardButton.No)
        event.result["accepted"] = (reply == QMessageBox.StandardButton.Yes)
        event.result["decided"] = True


# Custom event
class _InvokeEvent(QEvent):
    TYPE = QEvent.Type(QEvent.registerEventType())

    def __init__(self, cb):
        super().__init__(self.TYPE)
        self.cb = cb

    def performAction(self):
        self.cb()
