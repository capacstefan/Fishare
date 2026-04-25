"""Tab 3: Tools — Folder Sync + QR Web Server."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .peer_list import PeerList


class ToolsTab(QWidget):
    sync_start_requested = pyqtSignal(str, str)  # peer_name, folder
    sync_stop_requested = pyqtSignal()

    qr_start_requested = pyqtSignal()
    qr_stop_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._sync_folder = ""

        root = QHBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(16)

        # ---------- Folder sync ----------
        sync_gb = QGroupBox("One-Way Folder Sync")
        sv = QVBoxLayout(sync_gb)
        sv.setContentsMargins(20, 26, 20, 18)
        sv.setSpacing(12)

        sv.addWidget(_muted("Sender → Receiver. Changes mirror automatically."))
        sv.addWidget(_muted("Pick one peer"))

        self.peer_list = PeerList()
        self.peer_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        sv.addWidget(self.peer_list, 1)

        self.folder_label = QLabel("No folder selected")
        self.folder_label.setWordWrap(True)
        self.folder_label.setProperty("role", "muted")
        sv.addWidget(self.folder_label)

        folder_btn = QPushButton("Select Folder…")
        folder_btn.clicked.connect(self._pick_folder)
        sv.addWidget(folder_btn)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        self.start_btn = QPushButton("Start Sync")
        self.start_btn.setProperty("role", "primary")
        self.stop_btn = QPushButton("Cancel Sync")
        self.stop_btn.setProperty("role", "danger")
        self.stop_btn.setEnabled(False)
        self.start_btn.setMinimumHeight(38)
        self.stop_btn.setMinimumHeight(38)
        self.start_btn.clicked.connect(self._start_sync)
        self.stop_btn.clicked.connect(self.sync_stop_requested.emit)
        btns.addWidget(self.start_btn)
        btns.addWidget(self.stop_btn)
        sv.addLayout(btns)

        self.sync_status = QLabel("Idle")
        self.sync_status.setProperty("role", "muted")
        sv.addWidget(self.sync_status)

        # ---------- QR web server ----------
        qr_gb = QGroupBox("QR Web Server")
        qv = QVBoxLayout(qr_gb)
        qv.setContentsMargins(20, 26, 20, 18)
        qv.setSpacing(12)

        qv.addWidget(_muted("Scan with your phone to upload files or send text."))

        self.qr_label = QLabel("Server stopped")
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setMinimumSize(280, 280)
        self.qr_label.setStyleSheet(
            "border: 1px dashed #d6d8dc;"
            "border-radius: 12px;"
            "background: #fafafa;"
            "color: #8a8a8f;"
            "font-size: 12pt;"
        )
        qv.addWidget(self.qr_label, 1)

        self.url_label = QLabel("")
        self.url_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.url_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.url_label.setProperty("role", "muted")
        qv.addWidget(self.url_label)

        qr_btns = QHBoxLayout()
        qr_btns.setSpacing(8)
        self.qr_start_btn = QPushButton("Host Web Server")
        self.qr_start_btn.setProperty("role", "primary")
        self.qr_stop_btn = QPushButton("Cancel Server")
        self.qr_stop_btn.setProperty("role", "danger")
        self.qr_stop_btn.setEnabled(False)
        self.qr_start_btn.setMinimumHeight(38)
        self.qr_stop_btn.setMinimumHeight(38)
        self.qr_start_btn.clicked.connect(self.qr_start_requested.emit)
        self.qr_stop_btn.clicked.connect(self.qr_stop_requested.emit)
        qr_btns.addWidget(self.qr_start_btn)
        qr_btns.addWidget(self.qr_stop_btn)
        qv.addLayout(qr_btns)

        root.addWidget(sync_gb, 1)
        root.addWidget(qr_gb, 1)

    # ---- peer list ----
    def upsert_peer(self, peer) -> None:
        self.peer_list.upsert(peer)

    def remove_peer(self, peer_id: str) -> None:
        self.peer_list.remove(peer_id)

    # ---- sync ----
    def _pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select folder to sync")
        if folder:
            self._sync_folder = folder
            self.folder_label.setText(folder)

    def _start_sync(self) -> None:
        it = self.peer_list.currentItem()
        if it is None or not self._sync_folder:
            return
        self.sync_start_requested.emit(PeerList.name_of(it), self._sync_folder)

    def set_sync_running(self, running: bool, status: str = "") -> None:
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.sync_status.setText(status or ("Syncing" if running else "Idle"))

    # ---- QR ----
    def show_qr(self, url: str, pixmap: QPixmap) -> None:
        self.qr_label.setPixmap(pixmap.scaled(
            280, 280,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))
        self.url_label.setText(url)
        self.qr_start_btn.setEnabled(False)
        self.qr_stop_btn.setEnabled(True)

    def hide_qr(self) -> None:
        self.qr_label.clear()
        self.qr_label.setText("Server stopped")
        self.url_label.setText("")
        self.qr_start_btn.setEnabled(True)
        self.qr_stop_btn.setEnabled(False)


def _muted(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("role", "muted")
    return lbl
