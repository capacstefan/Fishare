"""Main application window — modern, airy, Apple-inspired dark UI."""

from __future__ import annotations

import os
import threading
from typing import Dict

from PyQt6.QtCore import QEvent, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from history_window import HistoryWindow
from network import TransferRequestEvent, TransferService
from state import AppStatus, TransferStatus

MAX_NAME_LEN = 32


# ── Helper: human-readable file size ───────────────────


def _human_size(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {unit}" if unit != "B" else f"{b} {unit}"
        b /= 1024
    return f"{b:.2f} TB"


# ════════════════════════════════════════════════════════
#  Status Toggle
# ════════════════════════════════════════════════════════


class _StatusToggle(QWidget):
    status_changed = pyqtSignal(AppStatus)

    _ACTIVE_CSS = {
        AppStatus.AVAILABLE: """
            QPushButton {
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #34c759, stop:1 #28a745);
                color: #fff; font-weight: 600;
                padding: 7px 18px; border-radius: 8px; border: none;
            }""",
        AppStatus.BUSY: """
            QPushButton {
                background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #ff453a, stop:1 #d63031);
                color: #fff; font-weight: 600;
                padding: 7px 18px; border-radius: 8px; border: none;
            }""",
    }
    _INACTIVE_CSS = """
        QPushButton {
            background: transparent; color: #8e8e93;
            padding: 7px 18px; border-radius: 8px; border: none;
        }
        QPushButton:hover { background: rgba(255,255,255,0.06); }
    """

    def __init__(self, current: AppStatus, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        self.btn_avail = QPushButton("Available")
        self.btn_busy = QPushButton("Busy")
        for b in (self.btn_avail, self.btn_busy):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setCheckable(True)
        lay.addWidget(self.btn_avail)
        lay.addWidget(self.btn_busy)

        self.btn_avail.clicked.connect(lambda: self._set(AppStatus.AVAILABLE))
        self.btn_busy.clicked.connect(lambda: self._set(AppStatus.BUSY))
        self._set(current, emit=False)

    def _set(self, status: AppStatus, emit=True):
        is_avail = status == AppStatus.AVAILABLE
        self.btn_avail.setChecked(is_avail)
        self.btn_busy.setChecked(not is_avail)
        self.btn_avail.setStyleSheet(
            self._ACTIVE_CSS[AppStatus.AVAILABLE] if is_avail else self._INACTIVE_CSS
        )
        self.btn_busy.setStyleSheet(
            self._ACTIVE_CSS[AppStatus.BUSY] if not is_avail else self._INACTIVE_CSS
        )
        if emit:
            self.status_changed.emit(status)


# ════════════════════════════════════════════════════════
#  Progress row / panel
# ════════════════════════════════════════════════════════


class _ProgressRow(QFrame):
    """A single device transfer progress indicator."""

    def __init__(self, dev_id: str, name: str, app_state, parent=None):
        super().__init__(parent)
        self.dev_id = dev_id
        self._state = app_state
        self.setObjectName("progressRow")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        self.lbl = QLabel(name)
        self.lbl.setStyleSheet("font-size: 13px; color: #e5e5ea;")

        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)

        lay.addWidget(self.lbl)
        lay.addWidget(self.bar)

    def set_ratio(self, ratio: float):
        self.bar.setValue(int(ratio * 100))
        status = self._state.get_transfer_status(self.dev_id)
        dev = self._state.devices.get(self.dev_id)
        name = dev.name if dev else self.dev_id

        if status == TransferStatus.CANCELED:
            self.lbl.setText(f"{name}  —  Canceled")
            self.bar.setStyleSheet(
                "QProgressBar::chunk { background: #ff9f0a; border-radius: 3px; }"
            )
        elif status == TransferStatus.ERROR:
            self.lbl.setText(f"{name}  —  Error")
            self.bar.setStyleSheet(
                "QProgressBar::chunk { background: #ff453a; border-radius: 3px; }"
            )
        else:
            speed = self._state.get_speed(self.dev_id)
            extra = f"  —  {speed:.1f} MB/s" if speed > 0 else ""
            self.lbl.setText(f"{name}{extra}")
            self.bar.setStyleSheet(
                "QProgressBar::chunk { background: #0a84ff; border-radius: 3px; }"
            )


class _ProgressPanel(QWidget):
    """Scrollable list of active transfer progress bars."""

    def __init__(self, app_state, parent=None):
        super().__init__(parent)
        self._state = app_state
        self.rows: Dict[str, _ProgressRow] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        self._inner = QWidget()
        self._inner_lay = QVBoxLayout(self._inner)
        self._inner_lay.setContentsMargins(0, 0, 0, 0)
        self._inner_lay.setSpacing(6)
        self._inner_lay.addStretch()
        scroll.setWidget(self._inner)

    def refresh(self, progress_snap: dict, devices_snap: dict):
        """Update progress display, with stale row cleanup."""
        # Add / update rows
        for dev_id, ratio in progress_snap.items():
            if dev_id not in self.rows:
                dev = devices_snap.get(dev_id)
                name = dev.name if dev else dev_id
                row = _ProgressRow(dev_id, name, self._state)
                self.rows[dev_id] = row
                self._inner_lay.insertWidget(self._inner_lay.count() - 1, row)
            self.rows[dev_id].set_ratio(ratio)

        # Remove finished rows
        gone = [d for d in self.rows if d not in progress_snap]
        for d in gone:
            self.rows[d].setParent(None)
            self.rows[d].deleteLater()
            del self.rows[d]


# ════════════════════════════════════════════════════════
#  Section card helper
# ════════════════════════════════════════════════════════


def _section(title: str, widget: QWidget) -> QVBoxLayout:
    """Wrap a widget in a titled section."""
    lay = QVBoxLayout()
    lay.setSpacing(8)
    lbl = QLabel(title)
    lbl.setStyleSheet(
        "font-size: 11px; font-weight: 700; color: #8e8e93; "
        "letter-spacing: 1px; text-transform: uppercase;"
    )
    lay.addWidget(lbl)
    lay.addWidget(widget, 1)
    return lay


# ════════════════════════════════════════════════════════
#  Main window
# ════════════════════════════════════════════════════════


class FIshareQtApp(QMainWindow):
    """Main FIshare application window."""

    def __init__(self, state, advertiser, scanner, history=None):
        super().__init__()
        self.state = state
        self.advertiser = advertiser
        self.scanner = scanner
        self.history = history
        self.transfer = TransferService(state, self, history)

        self.setWindowTitle("FIshare")
        self.resize(1060, 740)
        self.setMinimumSize(760, 500)

        self._build_ui()
        self._apply_global_style()

        # Periodic UI refresh
        self._timer = QTimer(self, timeout=self._refresh_ui)
        self._timer.start(500)

    # ── Build UI ────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(28, 22, 28, 22)
        root.setSpacing(20)
        self.setCentralWidget(central)

        root.addLayout(self._build_toolbar())

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background: #2c2c2e; max-height: 1px;")
        root.addWidget(sep)

        root.addLayout(self._build_body(), 1)
        root.addLayout(self._build_footer())

    # ── Toolbar ─────────────────────────────────────────

    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(16)

        # App title
        title = QLabel("FIshare")
        title.setStyleSheet(
            "font-size: 22px; font-weight: 700; color: #ffffff; letter-spacing: 0.5px;"
        )
        bar.addWidget(title)

        bar.addSpacing(20)

        # Device name
        name_lbl = QLabel("Name")
        name_lbl.setStyleSheet("color: #8e8e93; font-size: 13px;")
        bar.addWidget(name_lbl)

        self.name_edit = QLineEdit(self.state.cfg.device_name)
        self.name_edit.setMaxLength(MAX_NAME_LEN)
        self.name_edit.setFixedWidth(180)
        self.name_edit.textEdited.connect(self._on_name)
        bar.addWidget(self.name_edit)

        bar.addSpacing(10)

        # Status toggle
        self.status_toggle = _StatusToggle(self.state.status)
        self.status_toggle.status_changed.connect(self._on_status)
        bar.addWidget(self.status_toggle)

        bar.addStretch()

        # Folder button
        btn_folder = QPushButton("📂  Folder")
        btn_folder.setObjectName("toolBtn")
        btn_folder.clicked.connect(self._pick_folder)
        bar.addWidget(btn_folder)

        # History button
        btn_hist = QPushButton("🕘  History")
        btn_hist.setObjectName("toolBtn")
        btn_hist.clicked.connect(self._show_history)
        bar.addWidget(btn_hist)

        return bar

    # ── Body ────────────────────────────────────────────

    def _build_body(self) -> QHBoxLayout:
        body = QHBoxLayout()
        body.setSpacing(20)

        # ─ Left: discovered devices
        self.device_list = QListWidget()
        self.device_list.setObjectName("deviceList")
        self.device_list.itemDoubleClicked.connect(self._add_peer)
        left = QVBoxLayout()
        left.addLayout(_section("DISCOVERED DEVICES", self.device_list))

        # ─ Right column
        right = QVBoxLayout()
        right.setSpacing(16)

        # Targets
        self.target_list = QListWidget()
        self.target_list.setObjectName("targetList")
        self.target_list.itemDoubleClicked.connect(self._remove_peer)
        right.addLayout(_section("SEND TO", self.target_list))

        # Files
        self.file_list = QListWidget()
        self.file_list.setObjectName("fileList")
        file_section = _section("FILES", self.file_list)

        btn_add_files = QPushButton("＋  Add Files")
        btn_add_files.setObjectName("toolBtn")
        btn_add_files.clicked.connect(self._pick_files)
        file_section.addWidget(btn_add_files)
        right.addLayout(file_section)

        # Progress
        self.progress_panel = _ProgressPanel(self.state)
        right.addLayout(_section("PROGRESS", self.progress_panel))

        body.addLayout(left, 1)
        body.addLayout(right, 1)
        return body

    # ── Footer ──────────────────────────────────────────

    def _build_footer(self) -> QHBoxLayout:
        foot = QHBoxLayout()
        foot.addStretch()

        self.send_btn = QPushButton("Send")
        self.send_btn.setObjectName("sendBtn")
        self.send_btn.setEnabled(False)
        self.send_btn.setFixedWidth(160)
        self.send_btn.clicked.connect(self._send)
        foot.addWidget(self.send_btn)

        return foot

    # ── Global stylesheet — Apple-inspired dark ────────

    def _apply_global_style(self):
        self.setStyleSheet("""
        /* ── Window ── */
        QMainWindow {
            background: #1c1c1e;
            color: #e5e5ea;
        }

        /* ── Labels ── */
        QLabel {
            color: #e5e5ea;
            font-size: 13px;
        }

        /* ── Line edit ── */
        QLineEdit {
            background: #2c2c2e;
            color: #e5e5ea;
            border: 1px solid #3a3a3c;
            border-radius: 8px;
            padding: 8px 12px;
            font-size: 13px;
            selection-background-color: #0a84ff;
        }
        QLineEdit:focus {
            border: 1px solid #0a84ff;
        }

        /* ── Lists ── */
        QListWidget {
            background: #2c2c2e;
            color: #e5e5ea;
            border: 1px solid #3a3a3c;
            border-radius: 10px;
            padding: 6px;
            font-size: 13px;
            outline: none;
        }
        QListWidget::item {
            padding: 10px 12px;
            border-radius: 6px;
            margin: 2px 0;
        }
        QListWidget::item:hover {
            background: rgba(255, 255, 255, 0.05);
        }
        QListWidget::item:selected {
            background: rgba(10, 132, 255, 0.25);
            color: #ffffff;
        }

        /* ── Scroll bars ── */
        QScrollBar:vertical {
            background: transparent;
            width: 6px;
            margin: 4px 0;
        }
        QScrollBar::handle:vertical {
            background: #48484a;
            border-radius: 3px;
            min-height: 30px;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0;
        }
        QScrollBar:horizontal {
            height: 0;
        }

        /* ── Tool buttons ── */
        QPushButton#toolBtn {
            background: #2c2c2e;
            color: #e5e5ea;
            border: 1px solid #3a3a3c;
            padding: 8px 18px;
            border-radius: 8px;
            font-size: 13px;
            font-weight: 500;
        }
        QPushButton#toolBtn:hover {
            background: #3a3a3c;
            border-color: #48484a;
        }
        QPushButton#toolBtn:pressed {
            background: #1c1c1e;
        }

        /* ── Send button ── */
        QPushButton#sendBtn {
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                stop:0 #0a84ff, stop:1 #0070e0);
            color: #ffffff;
            border: none;
            padding: 12px 32px;
            border-radius: 10px;
            font-size: 15px;
            font-weight: 700;
            letter-spacing: 0.5px;
        }
        QPushButton#sendBtn:hover {
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                stop:0 #3a9fff, stop:1 #0a84ff);
        }
        QPushButton#sendBtn:pressed {
            background: #005ec4;
        }
        QPushButton#sendBtn:disabled {
            background: #2c2c2e;
            color: #636366;
        }

        /* ── Progress bar (base) ── */
        QProgressBar {
            background: #3a3a3c;
            border: none;
            border-radius: 3px;
        }
        QProgressBar::chunk {
            background: #0a84ff;
            border-radius: 3px;
        }

        /* ── Progress row card ── */
        QFrame#progressRow {
            background: #2c2c2e;
            border-radius: 8px;
        }

        /* ── Message boxes ── */
        QMessageBox {
            background: #1c1c1e;
            color: #e5e5ea;
        }
        QMessageBox QLabel {
            color: #e5e5ea;
            font-size: 13px;
        }
        QMessageBox QPushButton {
            background: #2c2c2e;
            color: #e5e5ea;
            border: 1px solid #3a3a3c;
            padding: 8px 20px;
            border-radius: 8px;
            min-width: 80px;
        }
        QMessageBox QPushButton:hover {
            background: #3a3a3c;
        }
        """)

    # ════════════════════════════════════════════════════
    #  Logic
    # ════════════════════════════════════════════════════

    @pyqtSlot()
    def _on_name(self):
        self.state.cfg.device_name = self.name_edit.text().strip()
        self.state.cfg.save()

    def _on_status(self, s: AppStatus):
        self.state.set_status(s)

    def _pick_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Choose download folder")
        if d:
            self.state.cfg.download_dir = d
            self.state.cfg.save()

    def _pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select files to send")
        if files:
            self.state.selected_files = files
            self._refresh_lists()

    def _add_peer(self):
        item = self.device_list.currentItem()
        if not item:
            return
        dev_id = item.data(Qt.ItemDataRole.UserRole)
        if not dev_id:
            return
        dev = self.state.devices.get(dev_id)
        if dev and dev.status == AppStatus.AVAILABLE and dev_id not in self.state.selected_device_ids:
            self.state.selected_device_ids.append(dev_id)
        self._refresh_lists()

    def _remove_peer(self):
        item = self.target_list.currentItem()
        if not item:
            return
        dev_id = item.data(Qt.ItemDataRole.UserRole)
        if dev_id and dev_id in self.state.selected_device_ids:
            self.state.selected_device_ids.remove(dev_id)
        self._refresh_lists()

    # ── Send ────────────────────────────────────────────

    @pyqtSlot()
    def _send(self):
        if not self.state.selected_files or not self.state.selected_device_ids:
            return
        self.send_btn.setEnabled(False)
        threading.Thread(target=self._do_send, daemon=True).start()

    def _do_send(self):
        try:
            for dev_id in list(self.state.selected_device_ids):
                dev = self.state.devices.get(dev_id)
                if dev:
                    self.transfer.send_to(dev, list(self.state.selected_files))
        finally:
            # Re-enable button from the GUI thread
            QApplication.instance().postEvent(
                self, _InvokeEvent(self._after_send)
            )

    def _after_send(self):
        self._refresh_lists()  # will re-evaluate send_btn enabled state

    # ── Refresh ─────────────────────────────────────────

    @pyqtSlot()
    def _refresh_ui(self):
        self._refresh_lists()
        devices_snap = self.state.snapshot_devices()
        progress_snap = self.state.snapshot_progress()
        self.progress_panel.refresh(progress_snap, devices_snap)

    def _refresh_lists(self):
        # Devices
        self.device_list.clear()
        devices_snap = self.state.snapshot_devices()
        for dev_id, dev in devices_snap.items():
            dot = "🟢" if dev.status == AppStatus.AVAILABLE else "🔴"
            item = QListWidgetItem(f"{dot}   {dev.name}   ({dev.host})")
            item.setData(Qt.ItemDataRole.UserRole, dev_id)
            self.device_list.addItem(item)

        # Targets
        self.target_list.clear()
        for dev_id in self.state.selected_device_ids:
            dev = devices_snap.get(dev_id)
            if dev:
                item = QListWidgetItem(f"➤  {dev.name}   ({dev.host})")
                item.setData(Qt.ItemDataRole.UserRole, dev_id)
                self.target_list.addItem(item)

        # Files
        self.file_list.clear()
        for path in self.state.selected_files:
            name = os.path.basename(path)
            size = _human_size(os.path.getsize(path)) if os.path.isfile(path) else "?"
            self.file_list.addItem(f"📄  {name}  ({size})")

        # Send button state
        can_send = bool(self.state.selected_files) and bool(self.state.selected_device_ids)
        self.send_btn.setEnabled(can_send)

    # ── History ─────────────────────────────────────────

    def _show_history(self):
        if self.history:
            HistoryWindow(self.history, self).exec()

    # ── Qt event handling ───────────────────────────────

    def event(self, e: QEvent) -> bool:
        if isinstance(e, _InvokeEvent):
            e.run()
            return True
        if isinstance(e, TransferRequestEvent):
            self._on_incoming(e)
            return True
        return super().event(e)

    def _on_incoming(self, ev: TransferRequestEvent):
        """Handle incoming transfer with auto-timeout dialog."""
        size_str = _human_size(ev.total_size)
        msg = (
            f"{ev.peer_name} wants to send {ev.num_files} file(s)\n"
            f"Total size: {size_str}\n\nAccept?"
        )
        
        # Create dialog with timeout
        dialog = QMessageBox(
            QMessageBox.Icon.Question,
            "Incoming Transfer",
            msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            self
        )
        
        # Auto-reject after 30 seconds (matching server timeout)
        timeout_ms = 30000
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: dialog.reject())
        timer.start(timeout_ms)
        
        reply = dialog.exec()
        timer.stop()  # Cancel timer if user responded
        
        ev.result["accepted"] = reply == QMessageBox.StandardButton.Yes
        ev.result["decided"] = True


# ── Invoke event (run a callback on the GUI thread) ────


class _InvokeEvent(QEvent):
    _TYPE = QEvent.Type(QEvent.registerEventType())

    def __init__(self, fn):
        super().__init__(self._TYPE)
        self._fn = fn

    def run(self):
        self._fn()
