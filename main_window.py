# main_window_pyqt.py
from __future__ import annotations

import os
import threading
from typing import Dict

from PyQt6.QtCore import (
    Qt, QTimer, pyqtSlot, pyqtSignal, QRect, QEasingCurve, QPropertyAnimation, QObject, QEvent
)
from PyQt6.QtGui import QFont, QColor, QPainter, QPen, QBrush
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QApplication, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QListWidget, QListWidgetItem,
    QFileDialog, QMessageBox, QScrollArea, QFrame, QProgressBar, QSizePolicy
)

from state import AppStatus
from network import TransferService


MAX_NAME_LEN = 32

STATUS_DOT = {
    AppStatus.AVAILABLE: "🟢",
    AppStatus.BUSY: "🔴",
}


# ----------------- Animated Status Toggle (Available / Busy) -----------------

class StatusToggleWidget(QWidget):
    """
    Toggle modern cu slide între două opțiuni: Available / Busy.
    Afișează ambele opțiuni; indicatorul glisează pe opțiunea selectată.
    """
    toggled = pyqtSignal(AppStatus)

    def __init__(self, current_status: AppStatus = AppStatus.AVAILABLE, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("StatusToggle")
        self._status = current_status

        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(220)
        self.setFixedHeight(36)

        self._bg = QFrame(self)
        self._bg.setObjectName("ToggleBg")
        self._bg.setGeometry(0, 0, self.width(), self.height())

        self._indicator = QFrame(self)
        self._indicator.setObjectName("ToggleIndicator")
        self._indicator.setGeometry(self._target_rect_for(self._status))

        self.btn_available = QPushButton("Available", self)
        self.btn_busy = QPushButton("Busy", self)
        for b in (self.btn_available, self.btn_busy):
            b.setObjectName("ToggleBtn")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setCheckable(False)
            b.setFlat(True)

        self.btn_available.clicked.connect(lambda: self._set(AppStatus.AVAILABLE))
        self.btn_busy.clicked.connect(lambda: self._set(AppStatus.BUSY))

        # Layout intern manual pentru precizie (evităm interferența cu indicatorul)
        self._update_buttons_geometry()

        # Animație pentru indicator
        self._anim = QPropertyAnimation(self._indicator, b"geometry", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutQuad)

        self._apply_style()

    def _apply_style(self):
        # QSS minimalist, modern — theme neutral + accente discrete
        self.setStyleSheet("""
        #StatusToggle {
            background: transparent;
        }
        #ToggleBg {
            background: #111418;
            border: 1px solid #262b31;
            border-radius: 18px;
        }
        #ToggleIndicator {
            background: #1f6f43; /* verde discret */
            border-radius: 16px;
        }
        #ToggleBtn {
            color: #e6e9ee;
            padding: 0;
            font-weight: 600;
        }
        #ToggleBtn:hover {
            color: white;
        }
        """)

    def resizeEvent(self, _ev):
        self._bg.setGeometry(0, 0, self.width(), self.height())
        self._update_buttons_geometry()
        self._indicator.setGeometry(self._target_rect_for(self._status))

    def _update_buttons_geometry(self):
        w = self.width() // 2
        h = self.height()
        self.btn_available.setGeometry(0, 0, w, h)
        self.btn_busy.setGeometry(w, 0, w, h)

    def _target_rect_for(self, status: AppStatus) -> QRect:
        w = self.width() // 2
        h = self.height()
        if status == AppStatus.AVAILABLE:
            return QRect(2, 2, w - 4, h - 4)
        else:
            return QRect(w + 2, 2, w - 4, h - 4)

    def _set(self, status: AppStatus):
        if status == self._status:
            return
        self._status = status
        self._anim.stop()
        self._anim.setStartValue(self._indicator.geometry())
        self._anim.setEndValue(self._target_rect_for(status))
        self._anim.start()
        self.toggled.emit(self._status)

    def status(self) -> AppStatus:
        return self._status


# ----------------- Progres pe device / fișier -----------------

class DeviceRowWidget(QFrame):
    """
    Container pentru un device și barele de progres ale fișierelor — aerisit și curat.
    """
    def __init__(self, device_id: str, device_name: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.device_id = device_id
        self.device_name = device_name
        self._file_bars: Dict[str, QProgressBar] = {}

        self.setObjectName("DeviceRow")
        self.setFrameShape(QFrame.Shape.NoFrame)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(8)

        title = QLabel(f"{device_id}  {device_name}")
        title.setObjectName("DeviceRowTitle")
        outer.addWidget(title)

        self.files_container = QVBoxLayout()
        self.files_container.setSpacing(6)
        outer.addLayout(self.files_container)

        self.setStyleSheet("""
        #DeviceRow {
            background: #0f1216;
            border: 1px solid #222831;
            border-radius: 12px;
        }
        #DeviceRowTitle {
            color: #dfe3e8;
            font-weight: 600;
        }
        QProgressBar {
            background: #13171c;
            border: 1px solid #2a313a;
            border-radius: 6px;
            height: 12px;
        }
        QProgressBar::chunk {
            background-color: #2c7a52;
            border-radius: 6px;
        }
        QLabel {
            color: #c9ced6;
        }
        """)

    def update_files(self, files_progress: Dict[str, float]):
        # șterge intrările dispărute
        for fname in list(self._file_bars.keys()):
            if fname not in files_progress:
                bar = self._file_bars.pop(fname)
                bar.setParent(None)
                bar.deleteLater()

        # adaugă / actualizează
        for fname, ratio in files_progress.items():
            ratio = max(0.0, min(1.0, float(ratio)))
            bar = self._file_bars.get(fname)
            if bar is None:
                lbl = QLabel(fname)
                self.files_container.addWidget(lbl)
                bar = QProgressBar()
                bar.setMinimum(0)
                bar.setMaximum(100)
                bar.setValue(0)
                self.files_container.addWidget(bar)
                self._file_bars[fname] = bar
            bar.setValue(int(ratio * 100))


class ProgressPanel(QWidget):
    """
    Panou scrollabil ce grupează progresul pe device și pe fișier.
    """
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._rows: Dict[str, DeviceRowWidget] = {}

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self._root.addWidget(self.scroll)

        self.inner = QWidget()
        self.scroll.setWidget(self.inner)
        self.inner_layout = QVBoxLayout(self.inner)
        self.inner_layout.setContentsMargins(2, 2, 2, 2)
        self.inner_layout.setSpacing(10)
        self.inner_layout.addStretch()

    def update_from_state(self, state):
        current_ids = set(state.progress.keys())

        # șterge rânduri pentru device-urile dispărute
        for dev_id in list(self._rows.keys()):
            if dev_id not in current_ids:
                row = self._rows.pop(dev_id)
                row.setParent(None)
                row.deleteLater()

        # actualizează / creează rânduri
        for dev_id, files in state.progress.items():
            row = self._rows.get(dev_id)
            if row is None:
                name = state.devices.get(dev_id).name if state.devices.get(dev_id) else dev_id
                row = DeviceRowWidget(dev_id, name)
                self.inner_layout.insertWidget(self.inner_layout.count() - 1, row)
                self._rows[dev_id] = row
            row.update_files(files)


# ----------------- Fereastra principală -----------------

class FIshareQtApp(QMainWindow):
    """
    Fereastra principală PyQt6 pentru FIshare.
    Modernă, minimalistă, cu toggle animat și layout aerisit.
    """
    def __init__(self, state, advertiser, scanner):
        super().__init__()
        self.app_state = state
        self.advertiser = advertiser
        self.scanner = scanner

        self.setWindowTitle("FIshare")
        self.resize(1040, 680)

        # Transfer service (server RX) – folosește callback către PyQt pentru confirmări
        self.transfer = TransferService(state, ui_root=self)

        # ---------- Stil global (QSS) ----------
        self._apply_global_style()

        # ---------- Layout principal ----------
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(14)

        # ---------- Top bar ----------
        top = QHBoxLayout()
        top.setSpacing(10)

        title = QLabel("FIshare")
        title.setObjectName("HeaderTitle")
        title.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        top.addWidget(title)

        top.addSpacing(12)
        top.addWidget(self._make_divider(), 0)

        name_lbl = QLabel("Nume")
        name_lbl.setObjectName("FieldLabel")
        top.addWidget(name_lbl)

        self.name_edit = QLineEdit(self.app_state.cfg.device_name)
        self.name_edit.setMaxLength(MAX_NAME_LEN)
        self.name_edit.textEdited.connect(self.on_name_changed)
        self.name_edit.setPlaceholderText("Nume dispozitiv...")
        self.name_edit.setObjectName("NameEdit")
        top.addWidget(self.name_edit, 1)

        status_lbl = QLabel("Status")
        status_lbl.setObjectName("FieldLabel")
        top.addWidget(status_lbl)

        self.status_toggle = StatusToggleWidget(self.app_state.status)
        self.status_toggle.toggled.connect(self.on_status_toggled)
        top.addWidget(self.status_toggle, 0)

        self.pick_dir_btn = QPushButton("Folder download")
        self.pick_dir_btn.setObjectName("ActionBtn")
        self.pick_dir_btn.clicked.connect(self.pick_download_dir)
        top.addWidget(self.pick_dir_btn, 0)

        root.addLayout(top)

        # ---------- Body (stânga: dispozitive / dreapta: selecții + fișiere + progres) ----------
        body = QHBoxLayout()
        body.setSpacing(16)
        root.addLayout(body, 1)

        # Stânga — dispozitive descoperite
        left = QVBoxLayout()
        left.setSpacing(8)

        left_lbl = QLabel("Dispozitive în LAN")
        left_lbl.setObjectName("SectionLabel")
        left.addWidget(left_lbl)

        self.devices_list = QListWidget()
        self.devices_list.setObjectName("ListBox")
        self.devices_list.itemDoubleClicked.connect(self.add_selected_device)
        left.addWidget(self.devices_list, 1)
        body.addLayout(left, 1)

        # Dreapta — selecții + fișiere + progres
        right = QVBoxLayout()
        right.setSpacing(8)

        sel_lbl = QLabel("Destinații selectate (dublu-click pentru a șterge)")
        sel_lbl.setObjectName("SectionLabel")
        right.addWidget(sel_lbl)

        self.selected_list = QListWidget()
        self.selected_list.setObjectName("ListBox")
        self.selected_list.itemDoubleClicked.connect(self.remove_selected_device)
        right.addWidget(self.selected_list)

        files_lbl = QLabel("Fișiere selectate")
        files_lbl.setObjectName("SectionLabel")
        right.addWidget(files_lbl)

        self.files_list = QListWidget()
        self.files_list.setObjectName("ListBox")
        right.addWidget(self.files_list)

        pick_files_btn = QPushButton("Adaugă fișiere")
        pick_files_btn.setObjectName("ActionBtn")
        pick_files_btn.clicked.connect(self.pick_files)
        right.addWidget(pick_files_btn, alignment=Qt.AlignmentFlag.AlignRight)

        prog_lbl = QLabel("Progres transfer")
        prog_lbl.setObjectName("SectionLabel")
        right.addWidget(prog_lbl)

        self.progress_panel = ProgressPanel()
        right.addWidget(self.progress_panel, 1)

        body.addLayout(right, 1)

        # ---------- Bottom ----------
        bottom = QHBoxLayout()
        bottom.addStretch()
        self.send_btn = QPushButton("Trimite")
        self.send_btn.setObjectName("PrimaryBtn")
        self.send_btn.clicked.connect(self.on_send)
        self.send_btn.setEnabled(False)
        bottom.addWidget(self.send_btn)
        root.addLayout(bottom)

        # ---------- Refresh periodic UI ----------
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_ui)
        self.timer.start(600)

    # ----------------- UI helpers -----------------

    def _apply_global_style(self):
        # Tema dark minimalistă, cu accente discrete; font clar, paddings aerisite
        self.setStyleSheet("""
        QMainWindow {
            background: #0b0e12;
        }
        #HeaderTitle {
            color: #f0f3f7;
            font-size: 18px;
            font-weight: 700;
        }
        #FieldLabel, #SectionLabel {
            color: #b7bfca;
            font-weight: 600;
        }
        #SectionLabel {
            margin-top: 2px;
            margin-bottom: 2px;
        }
        #NameEdit {
            background: #111418;
            border: 1px solid #262b31;
            border-radius: 10px;
            color: #e6e9ee;
            padding: 6px 10px;
        }
        #NameEdit:focus {
            border: 1px solid #2f7bff;
        }
        #ActionBtn, #PrimaryBtn {
            background: #141820;
            color: #e6e9ee;
            border: 1px solid #262b31;
            border-radius: 10px;
            padding: 8px 14px;
            font-weight: 600;
        }
        #ActionBtn:hover {
            background: #19202a;
        }
        #PrimaryBtn {
            background: #2f7bff;
            color: white;
            border: none;
        }
        #PrimaryBtn:hover {
            background: #2a6ee6;
        }
        #ListBox {
            background: #0f1216;
            border: 1px solid #222831;
            border-radius: 12px;
            color: #dee3ea;
            padding: 6px;
        }
        QListWidget::item {
            padding: 8px 6px;
            border-radius: 8px;
        }
        QListWidget::item:selected {
            background: #1a212b;
        }
        """)

    def _make_divider(self) -> QWidget:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.VLine)
        line.setStyleSheet("color: #1a1f25;")
        return line

    # ----------------- Slots -----------------

    @pyqtSlot(AppStatus)
    def on_status_toggled(self, status: AppStatus):
        self.app_state.set_status(status)

    @pyqtSlot(str)
    def on_name_changed(self, _):
        text = self.name_edit.text().strip()
        if not text:
            return
        if text != self.app_state.cfg.device_name:
            self.app_state.cfg.device_name = text
            self.app_state.cfg.save()

    @pyqtSlot()
    def pick_download_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Alege folderul de descarcare", self.app_state.cfg.download_dir)
        if path and os.path.isdir(path):
            self.app_state.cfg.download_dir = path
            self.app_state.cfg.save()
        else:
            QMessageBox.critical(self, "Eroare", "Folder invalid sau inaccesibil.")

    @pyqtSlot()
    def pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Selecteaza fisierele de trimis")
        files = [p for p in files if os.path.isfile(p)]
        if not files:
            return
        self.app_state.selected_files = list(files)
        self.refresh_lists()
        self.update_send_btn()

    @pyqtSlot()
    def add_selected_device(self):
        item = self.devices_list.currentItem()
        if not item:
            return
        line = item.text().strip()
        device_id = line.split(" ")[0]  # format: "<dev_id>  <name>  <dot>"
        dev = self.app_state.devices.get(device_id)
        if not dev:
            return
        if dev.status != AppStatus.AVAILABLE:
            QMessageBox.information(self, "Indisponibil", "Dispozitivul nu este Available.")
            return
        if device_id not in self.app_state.selected_device_ids:
            self.app_state.selected_device_ids.append(device_id)
            self.refresh_lists()
            self.update_send_btn()

    @pyqtSlot()
    def remove_selected_device(self):
        item = self.selected_list.currentItem()
        if not item:
            return
        device_id = item.text().split(" ")[0]
        if device_id in self.app_state.selected_device_ids:
            self.app_state.selected_device_ids.remove(device_id)
            self.refresh_lists()
            self.update_send_btn()

    def update_send_btn(self):
        self.send_btn.setEnabled(bool(self.app_state.selected_files and self.app_state.selected_device_ids))

    # ----------------- Refresh Loop -----------------

    @pyqtSlot()
    def refresh_ui(self):
        self.refresh_lists()
        self.progress_panel.update_from_state(self.app_state)

    def refresh_lists(self):
        # Devices
        self.devices_list.clear()
        for dev_id, dev in sorted(self.app_state.devices.items()):
            dot = STATUS_DOT.get(dev.status, "🔴")
            self.devices_list.addItem(QListWidgetItem(f"{dev_id}  {dev.name}  {dot}"))

        # Selected devices
        self.selected_list.clear()
        for dev_id in self.app_state.selected_device_ids:
            dev = self.app_state.devices.get(dev_id)
            if dev:
                self.selected_list.addItem(QListWidgetItem(f"{dev_id}  {dev.name}"))

        # Files
        self.files_list.clear()
        for p in self.app_state.selected_files:
            self.files_list.addItem(QListWidgetItem(p))

    # ----------------- Trimitere -----------------

    @pyqtSlot()
    def on_send(self):
        if not (self.app_state.selected_files and self.app_state.selected_device_ids):
            return

        prev_status = self.app_state.status
        self.app_state.set_status(AppStatus.BUSY)
        self.update_send_btn()

        devices = [self.app_state.devices[d] for d in self.app_state.selected_device_ids if d in self.app_state.devices]

        def worker():
            ok = 0
            for dev in devices:
                if self.transfer.send_to(dev, list(self.app_state.selected_files)):
                    ok += 1
            self.app_state.set_status(prev_status)

            def show_done():
                QMessageBox.information(self, "Rezultat", f"Transfer complet: {ok}/{len(devices)} dispozitive.")
                self.update_send_btn()

            QApplication.instance().postEvent(self, _InvokeEvent(show_done))

        threading.Thread(target=worker, daemon=True, name="send-ui").start()

    # ---- Folosit de TransferService pentru confirmarea primirii ----
    def ask_incoming_confirmation(self, host: str, files_count: int, total_bytes: int) -> bool:
        size_kb = total_bytes // 1024
        ret = QMessageBox.question(
            self, "Incoming transfer",
            f"{host} doreste sa trimita {files_count} fisiere ({size_kb} KB). Accepti?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        return ret == QMessageBox.StandardButton.Yes


# -------------- Utilitar mic pentru execuție pe firul UI --------------

class _InvokeEvent(QEvent):
    _TYPE = QEvent.Type(QEvent.registerEventType())

    def __init__(self, fn):
        super().__init__(self._TYPE)
        self.fn = fn


class _Invoker(QObject):
    def event(self, e):
        if isinstance(e, _InvokeEvent):
            e.fn()
            return True
        return super().event(e)
