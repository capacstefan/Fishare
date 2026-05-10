"""Tab 1: File Transfer UI."""
from __future__ import annotations

import random

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .peer_list import PeerList
from .theme import ToggleSwitch
from ._widgets import h2 as _h2, muted as _muted
from .. import config
from ..util import fmt_size


_CLEAR_DELAY_MS = 4000


class TransferProgressRow(QWidget):
    """Aggregate progress row per (direction, peer)."""

    def __init__(self, direction: str, peer_name: str) -> None:
        super().__init__()
        arrow = "↑" if direction == "up" else "↓"
        self._prefix = f"{arrow}  {peer_name}"

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 4, 8, 4)
        v.setSpacing(3)

        self.label = QLabel(f"{self._prefix} — queued")
        self.label.setProperty("role", "muted")
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setMaximumHeight(12)
        self.bar.setTextVisible(False)
        v.addWidget(self.label)
        v.addWidget(self.bar)

    def update_progress(self, filename, done, total, bps, eta) -> None:
        pct = int(done * 100 / total) if total else 0
        self.bar.setValue(pct)
        self.label.setText(
            f"{self._prefix} — {pct}%  ({filename})  {fmt_size(bps)}/s  ETA {eta}"
        )

    def set_status(self, status: str) -> None:
        self.label.setText(f"{self._prefix} — {status}")


class TransferTab(QWidget):
    device_name_changed = pyqtSignal(str)
    online_toggled = pyqtSignal(bool)
    choose_download_dir = pyqtSignal()
    send_requested = pyqtSignal(list, list, str)
    mute_toggled = pyqtSignal(str)

    def __init__(self, settings: dict, parent=None) -> None:
        super().__init__(parent)
        self._selected_files: list[str] = []
        self._rows: dict[tuple[str, str], TransferProgressRow] = {}
        self._pin_value = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(16)

        header = QFrame()
        header.setObjectName("card")
        hrow = QHBoxLayout(header)
        hrow.setContentsMargins(20, 16, 20, 16)
        hrow.setSpacing(14)

        self.name_edit = QLineEdit(settings["device_name"])
        self.name_edit.setMinimumWidth(260)
        self.name_edit.setMaximumWidth(420)
        self.name_edit.setMinimumHeight(40)
        self.name_edit.editingFinished.connect(
            lambda: self.device_name_changed.emit(self.name_edit.text().strip())
        )
        hrow.addWidget(_muted("Device"))
        hrow.addWidget(self.name_edit)

        self.reset_name_btn = QPushButton("⟳")
        self.reset_name_btn.setProperty("role", "icon")
        self.reset_name_btn.setToolTip("Reset device name to default")
        self.reset_name_btn.setFixedSize(40, 40)
        self.reset_name_btn.clicked.connect(self._reset_device_name)
        hrow.addWidget(self.reset_name_btn)

        hrow.addStretch(1)

        self.online_toggle = ToggleSwitch(on_text="Online", off_text="Offline")
        self.online_toggle.setChecked(bool(settings.get("online", True)))
        self.online_toggle.toggled_changed.connect(self.online_toggled.emit)
        hrow.addWidget(self.online_toggle)

        root.addWidget(header)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setHandleWidth(14)
        split.setChildrenCollapsible(False)

        peers_card = QFrame()
        peers_card.setObjectName("card")
        pv = QVBoxLayout(peers_card)
        pv.setContentsMargins(18, 16, 18, 16)
        pv.setSpacing(12)
        pv.addWidget(_h2("Peers"))

        peer_cols = QHBoxLayout()
        peer_cols.setSpacing(14)

        left = QVBoxLayout()
        left.setSpacing(8)
        left.addWidget(_muted("Discovered"))
        self.discovered_list = PeerList(mute_on_right_click=True)
        self.discovered_list.setMinimumHeight(80)
        self.discovered_list.mute_requested.connect(self.mute_toggled.emit)
        self.discovered_list.itemDoubleClicked.connect(self._on_discover_dclick)
        left.addWidget(self.discovered_list, 1)

        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(_muted("Selected"))
        self.selected_list = QListWidget()
        self.selected_list.setMinimumHeight(80)
        self.selected_list.itemDoubleClicked.connect(
            lambda it: self.selected_list.takeItem(self.selected_list.row(it))
        )
        right.addWidget(self.selected_list, 1)
        clear_sel_btn = QPushButton("Clear All")
        clear_sel_btn.clicked.connect(self.selected_list.clear)
        right.addWidget(clear_sel_btn)

        peer_cols.addLayout(left, 1)
        peer_cols.addLayout(right, 1)
        pv.addLayout(peer_cols, 1)

        files_card = QFrame()
        files_card.setObjectName("card")
        fv = QVBoxLayout(files_card)
        fv.setContentsMargins(18, 16, 18, 16)
        fv.setSpacing(12)
        fv.addWidget(_h2("Files"))

        self.files_list = QListWidget()
        self.files_list.itemDoubleClicked.connect(self._on_file_dclick)
        fv.addWidget(self.files_list, 1)

        file_btns = QHBoxLayout()
        file_btns.setSpacing(8)
        add_btn = QPushButton("+ Add Files")
        clr_btn = QPushButton("Clear All")
        dl_btn = QPushButton("Default Download Folder…")
        add_btn.clicked.connect(self._add_files)
        clr_btn.clicked.connect(self._clear_files)
        dl_btn.clicked.connect(self.choose_download_dir.emit)
        file_btns.addWidget(add_btn)
        file_btns.addWidget(clr_btn)
        file_btns.addStretch(1)
        file_btns.addWidget(dl_btn)
        fv.addLayout(file_btns)

        split.addWidget(peers_card)
        split.addWidget(files_card)
        split.setSizes([560, 440])
        root.addWidget(split, 1)

        action = QFrame()
        action.setObjectName("card")
        arow = QHBoxLayout(action)
        arow.setContentsMargins(20, 14, 20, 14)
        arow.setSpacing(12)

        self.pin_chk = QCheckBox("PIN Lock")
        self.pin_chk.toggled.connect(self._on_pin_toggle)
        arow.addWidget(self.pin_chk)

        self.pin_label = QLabel("")
        self.pin_label.setProperty("role", "pin")
        arow.addWidget(self.pin_label)
        arow.addStretch(1)

        self.send_btn = QPushButton("Send")
        self.send_btn.setProperty("role", "primary")
        self.send_btn.setMinimumWidth(140)
        self.send_btn.setMinimumHeight(42)
        self.send_btn.clicked.connect(self._on_send)
        arow.addWidget(self.send_btn)

        root.addWidget(action)

        progress_card = QFrame()
        progress_card.setObjectName("card")
        prl = QVBoxLayout(progress_card)
        prl.setContentsMargins(18, 14, 18, 14)
        prl.setSpacing(10)
        prl.addWidget(_h2("Transfers"))

        self._progress_host = QWidget()
        self._progress_layout = QVBoxLayout(self._progress_host)
        self._progress_layout.setContentsMargins(0, 0, 0, 0)
        self._progress_layout.setSpacing(4)
        self._progress_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._progress_host)
        scroll.setMinimumHeight(80)
        prl.addWidget(scroll)

        progress_card.setMaximumHeight(150)
        root.addWidget(progress_card)

    def _reset_device_name(self) -> None:
        name = config.default_device_name()
        self.name_edit.setText(name)
        self.name_edit.setFocus()
        self.name_edit.selectAll()
        self.device_name_changed.emit(name)

    def upsert_peer(self, peer) -> None:
        self.discovered_list.upsert(peer)

    def remove_peer(self, peer_id: str) -> None:
        self.discovered_list.remove(peer_id)
        self.remove_offline_selected(peer_id)

    def remove_offline_selected(self, peer_id: str) -> None:
        for i in range(self.selected_list.count()):
            if self.selected_list.item(i).data(Qt.ItemDataRole.UserRole) == peer_id:
                self.selected_list.takeItem(i)
                return

    def _on_discover_dclick(self, item: QListWidgetItem) -> None:
        pid = PeerList.pid_of(item)
        name = PeerList.name_of(item)
        for i in range(self.selected_list.count()):
            if self.selected_list.item(i).data(Qt.ItemDataRole.UserRole) == pid:
                self.selected_list.takeItem(i)
                return
        it = QListWidgetItem(name)
        it.setData(Qt.ItemDataRole.UserRole, pid)
        self.selected_list.addItem(it)

    def _add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Select files")
        for p in paths:
            if p not in self._selected_files:
                self._selected_files.append(p)
                self.files_list.addItem(p)

    def _clear_files(self) -> None:
        self._selected_files.clear()
        self.files_list.clear()

    def _on_file_dclick(self, item: QListWidgetItem) -> None:
        row = self.files_list.row(item)
        self.files_list.takeItem(row)
        if 0 <= row < len(self._selected_files):
            self._selected_files.pop(row)

    def _on_pin_toggle(self, checked: bool) -> None:
        if checked:
            self._pin_value = f"{random.randint(0, 9999):04d}"
            self.pin_label.setText(f"PIN: {self._pin_value}")
        else:
            self._pin_value = ""
            self.pin_label.setText("")

    def _on_send(self) -> None:
        peers = [self.selected_list.item(i).text() for i in range(self.selected_list.count())]
        files = list(self._selected_files)
        if peers and files:
            self.send_requested.emit(peers, files, self._pin_value)

    def _row(self, direction: str, peer: str) -> TransferProgressRow:
        key = (direction, peer)
        row = self._rows.get(key)
        if row is None:
            row = TransferProgressRow(direction, peer)
            self._progress_layout.insertWidget(self._progress_layout.count() - 1, row)
            self._rows[key] = row
        return row

    def _schedule_clear(self, direction: str, peer: str) -> None:
        key = (direction, peer)

        def drop():
            r = self._rows.pop(key, None)
            if r is not None:
                self._progress_layout.removeWidget(r)
                r.deleteLater()

        QTimer.singleShot(_CLEAR_DELAY_MS, drop)

    def on_task_progress(self, peer, filename, done, total, bps, eta) -> None:
        self._row("up", peer).update_progress(filename, done, total, bps, eta)

    def on_task_status(self, peer, status) -> None:
        self._row("up", peer).set_status(status)
        if status in ("done", "failed", "rejected", "offline"):
            self._schedule_clear("up", peer)

    def on_recv_progress(self, sender, filename, done, total, bps, eta) -> None:
        self._row("down", sender).update_progress(filename, done, total, bps, eta)

    def on_recv_completed(self, sender) -> None:
        self._row("down", sender).set_status("done")
        self._schedule_clear("down", sender)

    def on_recv_failed(self, sender, reason) -> None:
        self._row("down", sender).set_status(f"failed: {reason}")
        self._schedule_clear("down", sender)
