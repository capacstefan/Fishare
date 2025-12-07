from __future__ import annotations

import os
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


class StatusButtonToggle(QWidget):
    status_changed = pyqtSignal(AppStatus)

    def __init__(self, current_status: AppStatus, parent=None):
        super().__init__(parent)
        self._status = current_status

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.btn_available = self._make_button("Available")
        self.btn_busy = self._make_button("Busy")

        layout.addWidget(self.btn_available)
        layout.addWidget(self.btn_busy)

        self.btn_available.clicked.connect(lambda: self._set(AppStatus.AVAILABLE))
        self.btn_busy.clicked.connect(lambda: self._set(AppStatus.BUSY))

        self._apply_styles()
        self._set(current_status, init=True)

    def _make_button(self, text: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        return btn

    def _set(self, status: AppStatus, init=False):
        self._status = status
        self.btn_available.setChecked(status == AppStatus.AVAILABLE)
        self.btn_busy.setChecked(status == AppStatus.BUSY)
        self._apply_styles()
        if not init:
            self.status_changed.emit(status)

    def _apply_styles(self):
        self.btn_available.setStyleSheet(
            self._active("green") if self.btn_available.isChecked() else self._inactive()
        )
        self.btn_busy.setStyleSheet(
            self._active("red") if self.btn_busy.isChecked() else self._inactive()
        )

    def _active(self, color: str) -> str:
        return f"""
        QPushButton {{
            background: {color};
            color: white;
            font-weight: bold;
            padding: 8px;
            border-radius: 6px;
            min-width: 90px;
        }}"""

    def _inactive(self) -> str:
        return """
        QPushButton {
            background: #2b3037;
            color: #b7bfca;
            padding: 6px;
            border-radius: 4px;
            min-width: 70px;
        }
        QPushButton:hover {
            background: #40464f;
            border-radius: 6px;
        }"""


class DeviceProgressRow(QFrame):
    def __init__(self, device_id: str, device_name: str, app_state=None, parent=None):
        super().__init__(parent)
        self.device_id = device_id
        self.app_state = app_state
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        self.lbl = QLabel(f"{device_id}  {device_name}")
        self.bar = QProgressBar()
        self.bar.setMinimum(0)
        self.bar.setMaximum(100)

        layout.addWidget(self.lbl)
        layout.addWidget(self.bar)

        self._update_style()

    def _update_style(self):
        """Update style based on transfer status."""
        if self.app_state:
            status = self.app_state.get_transfer_status(self.device_id)
            if status == TransferStatus.CANCELED:
                # Red progress bar for canceled
                self.setStyleSheet("""
                QFrame { background: #0f1216; border: 1px solid #222831; border-radius: 12px; }
                QLabel { color: #ff6b6b; font-weight: 600; font-size: 14px; }
                QProgressBar { background: #13171c; border: 1px solid #2a313a; height: 14px; border-radius: 6px; }
                QProgressBar::chunk { background: #d32f2f; border-radius: 6px; }
                """)
            elif status == TransferStatus.ERROR:
                # Orange progress bar for error
                self.setStyleSheet("""
                QFrame { background: #0f1216; border: 1px solid #222831; border-radius: 12px; }
                QLabel { color: #ff9800; font-weight: 600; font-size: 14px; }
                QProgressBar { background: #13171c; border: 1px solid #2a313a; height: 14px; border-radius: 6px; }
                QProgressBar::chunk { background: #f57c00; border-radius: 6px; }
                """)
            else:
                # Green progress bar for completed
                self.setStyleSheet("""
                QFrame { background: #0f1216; border: 1px solid #222831; border-radius: 12px; }
                QLabel { color: #dfe3e8; font-weight: 600; font-size: 14px; }
                QProgressBar { background: #13171c; border: 1px solid #2a313a; height: 14px; border-radius: 6px; }
                QProgressBar::chunk { background: #2c7a52; border-radius: 6px; }
                """)
    
    def set_ratio(self, ratio: float):
        self.bar.setValue(int(round(max(0.0, min(1.0, ratio)) * 100)))
        
        # Update label with speed or status
        if self.app_state:
            status = self.app_state.get_transfer_status(self.device_id)
            device = self.app_state.devices.get(self.device_id)
            device_name = device.name if device else self.device_id
            
            if status == TransferStatus.CANCELED:
                self.lbl.setText(f"{self.device_id}  {device_name} - CANCELED")
                self._update_style()
            elif status == TransferStatus.ERROR:
                self.lbl.setText(f"{self.device_id}  {device_name} - ERROR")
                self._update_style()
            else:
                speed = self.app_state.get_speed(self.device_id)
                if speed > 0:
                    self.lbl.setText(f"{self.device_id}  {device_name} - {speed:.2f} MB/s")
                else:
                    self.lbl.setText(f"{self.device_id}  {device_name}")
                self._update_style()


class ProgressPanel(QWidget):
    def __init__(self, app_state=None, parent=None):
        super().__init__(parent)
        self.app_state = app_state
        self.rows: Dict[str, DeviceProgressRow] = {}

        layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll)

        self.inner = QWidget()
        scroll.setWidget(self.inner)
        self.inner_layout = QVBoxLayout(self.inner)
        self.inner_layout.addStretch()

    def update(self, state):
        for dev_id, ratio in list(state.progress.items()):
            if dev_id not in state.devices:
                continue
            if dev_id not in self.rows:
                row = DeviceProgressRow(dev_id, state.devices[dev_id].name, state)
                self.rows[dev_id] = row
                self.inner_layout.insertWidget(self.inner_layout.count() - 1, row)
            self.rows[dev_id].set_ratio(ratio)

        to_remove = []
        for dev_id, row in list(self.rows.items()):
            if dev_id not in state.progress:
                to_remove.append(dev_id)
            elif state.get_progress(dev_id) >= 0.999:
                # For canceled/error transfers, wait 3 seconds before removing
                status = state.get_transfer_status(dev_id)
                if status in (TransferStatus.CANCELED, TransferStatus.ERROR):
                    # Schedule removal after 3 seconds
                    QTimer.singleShot(3000, lambda d=dev_id: self._remove_row(d))
                else:
                    to_remove.append(dev_id)

        for dev_id in to_remove:
            self._remove_row(dev_id)
    
    def _remove_row(self, dev_id: str):
        """Remove a progress row."""
        row = self.rows.pop(dev_id, None)
        if row:
            row.setParent(None)
            row.deleteLater()


class FIshareQtApp(QMainWindow):
    def __init__(self, state, advertiser, scanner, history=None):
        super().__init__()
        self.app_state = state
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

        root.addLayout(self._build_top_bar())
        root.addLayout(self._build_body())
        root.addWidget(self._build_bottom_bar())

        self.timer = QTimer(self, timeout=self.refresh_ui)
        self.timer.start(600)

    def _build_top_bar(self):
        layout = QHBoxLayout()
        layout.addWidget(QLabel("FIshare"))
        layout.addWidget(QLabel("Nume"))
        self.name_edit = QLineEdit(self.app_state.cfg.device_name)
        self.name_edit.setMaxLength(MAX_NAME_LEN)
        self.name_edit.textEdited.connect(self.on_name_changed)
        layout.addWidget(self.name_edit)
        layout.addWidget(QLabel("Status"))
        self.status_toggle = StatusButtonToggle(self.app_state.status, self)
        self.status_toggle.status_changed.connect(self.on_status_toggled)
        layout.addWidget(self.status_toggle)

        btn = QPushButton("Folder")
        btn.clicked.connect(self.pick_download_dir)
        layout.addWidget(btn)
        
        btn_history = QPushButton("Istoric")
        btn_history.clicked.connect(self.show_history)
        layout.addWidget(btn_history)
        
        return layout

    def _build_body(self):
        body = QHBoxLayout()

        left = QVBoxLayout()
        left.addWidget(QLabel("Dispozitive"))
        self.devices_list = QListWidget()
        self.devices_list.itemDoubleClicked.connect(self.add_selected_device)
        left.addWidget(self.devices_list)

        right = QVBoxLayout()
        right.addWidget(QLabel("Destinații"))
        self.selected_list = QListWidget()
        self.selected_list.itemDoubleClicked.connect(self.remove_selected_device)
        right.addWidget(self.selected_list)
        right.addWidget(QLabel("Fișiere"))
        self.files_list = QListWidget()
        right.addWidget(self.files_list)

        btn = QPushButton("Adaugă fișiere")
        btn.clicked.connect(self.pick_files)
        right.addWidget(btn)
        right.addWidget(QLabel("Progres"))
        self.progress_panel = ProgressPanel(self.app_state)
        right.addWidget(self.progress_panel)

        body.addLayout(left, 1)
        body.addLayout(right, 1)
        return body

    def _build_bottom_bar(self):
        layout = QHBoxLayout()
        layout.addStretch()
        self.send_btn = QPushButton("Trimite")
        self.send_btn.clicked.connect(self.on_send)
        layout.addWidget(self.send_btn)
        container = QWidget()
        container.setLayout(layout)
        return container

        for dev_id in to_remove:
            row = self.rows.pop(dev_id, None)
            if row:
                row.setParent(None)
                row.deleteLater()


class FIshareQtApp(QMainWindow):
    def __init__(self, state, advertiser, scanner, history=None):
        super().__init__()
        self.app_state = state
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

        root.addLayout(self._build_top_bar())
        root.addLayout(self._build_body())
        root.addWidget(self._build_bottom_bar())

        self.timer = QTimer(self, timeout=self.refresh_ui)
        self.timer.start(600)

    def _build_top_bar(self):
        layout = QHBoxLayout()
        layout.addWidget(QLabel("FIshare"))
        layout.addWidget(QLabel("Nume"))
        self.name_edit = QLineEdit(self.app_state.cfg.device_name)
        self.name_edit.setMaxLength(MAX_NAME_LEN)
        self.name_edit.textEdited.connect(self.on_name_changed)
        layout.addWidget(self.name_edit)
        layout.addWidget(QLabel("Status"))
        self.status_toggle = StatusButtonToggle(self.app_state.status, self)
        self.status_toggle.status_changed.connect(self.on_status_toggled)
        layout.addWidget(self.status_toggle)

        btn = QPushButton("Folder")
        btn.clicked.connect(self.pick_download_dir)
        layout.addWidget(btn)
        
        btn_history = QPushButton("Istoric")
        btn_history.clicked.connect(self.show_history)
        layout.addWidget(btn_history)
        
        return layout

    def _build_body(self):
        body = QHBoxLayout()

        left = QVBoxLayout()
        left.addWidget(QLabel("Dispozitive"))
        self.devices_list = QListWidget()
        self.devices_list.itemDoubleClicked.connect(self.add_selected_device)
        left.addWidget(self.devices_list)

        right = QVBoxLayout()
        right.addWidget(QLabel("Destinații"))
        self.selected_list = QListWidget()
        self.selected_list.itemDoubleClicked.connect(self.remove_selected_device)
        right.addWidget(self.selected_list)
        right.addWidget(QLabel("Fișiere"))
        self.files_list = QListWidget()
        right.addWidget(self.files_list)

        btn = QPushButton("Adaugă fișiere")
        btn.clicked.connect(self.pick_files)
        right.addWidget(btn)
        right.addWidget(QLabel("Progres"))
        self.progress_panel = ProgressPanel(self.app_state)
        right.addWidget(self.progress_panel)

        body.addLayout(left, 1)
        body.addLayout(right, 1)
        return body

    def _build_bottom_bar(self):
        layout = QHBoxLayout()
        layout.addStretch()
        self.send_btn = QPushButton("Trimite")
        self.send_btn.clicked.connect(self.on_send)
        layout.addWidget(self.send_btn)
        container = QWidget()
        container.setLayout(layout)
        return container

    def _apply_style(self):
        self.setStyleSheet("""
        QMainWindow { 
            background: #0b0e12; 
            color: #e6e9ee; 
            font-size: 15px;
        }
        QLabel {
            font-size: 15px;
            color: #e6e9ee;
        }
        QPushButton {
            font-size: 14px;
            padding: 10px 18px;
            background: #1c2128;
            color: #e6e9ee;
            border: 1px solid #30363d;
            border-radius: 8px;
            min-width: 80px;
        }
        QPushButton:hover {
            background: #2d333b;
            border-color: #484f58;
        }
        QPushButton:pressed {
            background: #1c2128;
        }
        QLineEdit {
            font-size: 15px;
            padding: 8px 12px;
            background: #13171c;
            color: #e6e9ee;
            border: 1px solid #30363d;
            border-radius: 6px;
        }
        QLineEdit:focus {
            border-color: #58a6ff;
        }
        QListWidget {
            font-size: 15px;
            background: #13171c;
            color: #e6e9ee;
            border: 1px solid #30363d;
            border-radius: 8px;
        }
        QListWidget::item {
            padding: 8px;
        }
        QListWidget::item:hover {
            background: #1c2128;
        }
        QListWidget::item:selected {
            background: #2d333b;
        }
        """)

    @pyqtSlot(AppStatus)
    def on_status_toggled(self, status):
        self.app_state.set_status(status)

    @pyqtSlot()
    def on_name_changed(self):
        self.app_state.cfg.device_name = self.name_edit.text().strip()
        self.app_state.cfg.save()

    def pick_download_dir(self):
        dir = QFileDialog.getExistingDirectory(self, "Alege folder")
        if dir:
            self.app_state.cfg.download_dir = dir
            self.app_state.cfg.save()

    def pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Selectează fișiere")
        self.app_state.selected_files = files
        self.refresh_lists()

    @pyqtSlot()
    def add_selected_device(self):
        item = self.devices_list.currentItem()
        if not item:
            return
        device_id = item.text().split()[0]
        dev = self.app_state.devices.get(device_id)
        if not dev:
            return
        if dev.status == AppStatus.BUSY:
            QMessageBox.information(self, "Ocupat", "Dispozitivul este în modul BUSY și nu poate fi adăugat.")
            return
        if device_id not in self.app_state.selected_device_ids:
            self.app_state.selected_device_ids.append(device_id)
        self.refresh_lists()

    @pyqtSlot()
    def remove_selected_device(self):
        item = self.selected_list.currentItem()
        if not item:
            return
        device_id = item.text().split()[0]
        if device_id in self.app_state.selected_device_ids:
            self.app_state.selected_device_ids.remove(device_id)
        self.refresh_lists()

    @pyqtSlot()
    def on_send(self):
        self.send_btn.setEnabled(False)
        threading.Thread(target=self._do_send, daemon=True).start()

    def _do_send(self):
        for dev_id in list(self.app_state.selected_device_ids):
            if dev_id in self.app_state.devices:
                self.transfer.send_to(self.app_state.devices[dev_id], self.app_state.selected_files)

        QApplication.instance().postEvent(self, _InvokeEvent(
            lambda: self.send_btn.setEnabled(True)
        ))

    @pyqtSlot()
    def refresh_ui(self):
        self.refresh_lists()
        self.progress_panel.update(self.app_state)

    def refresh_lists(self):
        self.devices_list.clear()
        for dev_id, dev in self.app_state.devices.items():
            self.devices_list.addItem(QListWidgetItem(f"{dev_id} {dev.name} {STATUS_DOT[dev.status]}"))

        self.selected_list.clear()
        for dev_id in self.app_state.selected_device_ids:
            if dev_id in self.app_state.devices:
                self.selected_list.addItem(QListWidgetItem(f"{dev_id} {self.app_state.devices[dev_id].name}"))

        self.files_list.clear()
        for file in self.app_state.selected_files:
            self.files_list.addItem(QListWidgetItem(file))

    @pyqtSlot()
    def show_history(self):
        if self.history:
            dialog = HistoryWindow(self.history, self)
            dialog.exec()
    
    def event(self, event):
        if isinstance(event, _InvokeEvent):
            event.performAction()
            return True
        elif isinstance(event, _TransferRequestEvent):
            self._handle_transfer_request(event)
            return True
        return super().event(event)
    
    def _handle_transfer_request(self, event: _TransferRequestEvent):
        """Handle incoming transfer request dialog."""
        size_mb = event.total_size / (1024 * 1024)
        msg = (f"{event.peer_name} wants to send you {event.num_files} file(s) "
               f"with a total size of {size_mb:.2f} MB.\n\n"
               f"Do you want to accept this transfer?")
        
        reply = QMessageBox.question(
            self,
            "Incoming Transfer",
            msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )
        
        event.result["accepted"] = (reply == QMessageBox.StandardButton.Yes)
        event.result["decided"] = True


class _InvokeEvent(QEvent):
    _TYPE = QEvent.Type(QEvent.registerEventType())

    def __init__(self, callback):
        super().__init__(self._TYPE)
        self.callback = callback

    def performAction(self):
        self.callback()
