"""Tab 1: File Transfer UI."""
from __future__ import annotations

import random
from pathlib import Path

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

from .theme import ToggleSwitch
from ..util import fmt_size as _fmt_size


def _fmt_speed(bps: float) -> str:
    return f"{_fmt_size(bps)}/s"


def _section_title(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("role", "h2")
    return lbl


def _card(layout_widget: QWidget) -> QFrame:
    """Wrap a widget in a rounded "card" frame."""
    card = QFrame()
    card.setObjectName("card")
    v = QVBoxLayout(card)
    v.setContentsMargins(16, 14, 16, 14)
    v.setSpacing(10)
    v.addWidget(layout_widget)
    return card


# Auto-clear finished/failed rows after this many ms.
_CLEAR_DELAY_MS = 4000


class TransferProgressRow(QWidget):
    """One aggregate progress row per (direction, peer).

    Shows total progress across all files of a single transfer. The
    ``currently transmitting” filename is only shown in the caption; the bar
    reflects whole-transfer bytes.
    """

    def __init__(self, direction: str, peer_name: str) -> None:
        super().__init__()
        self.direction = direction            # "up" (sending) | "down" (receiving)
        self.peer_name = peer_name
        arrow = "↑" if direction == "up" else "↓"
        self._prefix = f"{arrow}  {peer_name}"

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 4, 8, 4)
        v.setSpacing(3)

        self.label = QLabel(f"{self._prefix} — queued")
        self.label.setProperty("role", "muted")
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setMaximumHeight(10)
        self.bar.setTextVisible(False)
        v.addWidget(self.label)
        v.addWidget(self.bar)

    def update_progress(self, filename: str, done: int, total: int,
                        bps: float, eta: str) -> None:
        pct = int(done * 100 / total) if total else 0
        self.bar.setValue(pct)
        self.label.setText(
            f"{self._prefix} — {pct}%  ({filename})  {_fmt_speed(bps)}  ETA {eta}"
        )

    def set_status(self, status: str) -> None:
        self.label.setText(f"{self._prefix} — {status}")


class TransferTab(QWidget):
    """File transfer tab. Talks to services via signals handled by MainWindow."""

    # signals out to main window
    device_name_changed = pyqtSignal(str)
    online_toggled = pyqtSignal(bool)
    choose_download_dir = pyqtSignal()
    send_requested = pyqtSignal(list, list, str)  # peers_names, files_paths, pin
    mute_toggled = pyqtSignal(str)

    def __init__(self, settings: dict, parent=None) -> None:
        super().__init__(parent)
        self._selected_files: list[str] = []
        # Rows keyed by (direction, peer_name): direction is "up"|"down".
        self._rows: dict[tuple[str, str], TransferProgressRow] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(16)

        # ---------- Header card: Device name + Online toggle ----------
        header = QFrame()
        header.setObjectName("card")
        hrow = QHBoxLayout(header)
        hrow.setContentsMargins(20, 16, 20, 16)
        hrow.setSpacing(14)

        name_lbl = QLabel("Device")
        name_lbl.setProperty("role", "muted")
        self.name_edit = QLineEdit(settings["device_name"])
        self.name_edit.setMaximumWidth(320)
        self.name_edit.setMinimumHeight(36)
        self.name_edit.editingFinished.connect(
            lambda: self.device_name_changed.emit(self.name_edit.text().strip())
        )
        hrow.addWidget(name_lbl)
        hrow.addWidget(self.name_edit)
        hrow.addStretch(1)

        self.online_toggle = ToggleSwitch(on_text="Online", off_text="Offline")
        self.online_toggle.setChecked(bool(settings.get("online", True)))
        self.online_toggle.toggled_changed.connect(self.online_toggled.emit)
        hrow.addWidget(self.online_toggle)

        root.addWidget(header)

        # ---------- Main split: peers card | files card ----------
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setHandleWidth(14)
        split.setChildrenCollapsible(False)

        # --- Peers card ---
        peers_card = QFrame()
        peers_card.setObjectName("card")
        pv = QVBoxLayout(peers_card)
        pv.setContentsMargins(18, 16, 18, 16)
        pv.setSpacing(12)
        pv.addWidget(_section_title("Peers"))

        peer_cols = QHBoxLayout()
        peer_cols.setSpacing(14)

        left = QVBoxLayout()
        left.setSpacing(8)
        disc_lbl = QLabel("Discovered")
        disc_lbl.setProperty("role", "muted")
        left.addWidget(disc_lbl)
        self.discovered_list = QListWidget()
        self.discovered_list.setMinimumHeight(110)
        self.discovered_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.discovered_list.customContextMenuRequested.connect(self._on_discovered_menu)
        self.discovered_list.itemDoubleClicked.connect(self._on_discover_dclick)
        left.addWidget(self.discovered_list)

        right = QVBoxLayout()
        right.setSpacing(8)
        sel_lbl = QLabel("Selected")
        sel_lbl.setProperty("role", "muted")
        right.addWidget(sel_lbl)
        self.selected_list = QListWidget()
        self.selected_list.setMinimumHeight(110)
        self.selected_list.itemDoubleClicked.connect(self._on_selected_dclick)
        right.addWidget(self.selected_list)
        clear_sel_btn = QPushButton("Clear All")
        clear_sel_btn.clicked.connect(self.selected_list.clear)
        right.addWidget(clear_sel_btn)

        peer_cols.addLayout(left, 1)
        peer_cols.addLayout(right, 1)
        pv.addLayout(peer_cols)

        # --- Files card ---
        files_card = QFrame()
        files_card.setObjectName("card")
        fv = QVBoxLayout(files_card)
        fv.setContentsMargins(18, 16, 18, 16)
        fv.setSpacing(12)
        fv.addWidget(_section_title("Files"))

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

        # ---------- Action bar: PIN + Send ----------
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
        self.send_btn.setMinimumHeight(38)
        self.send_btn.clicked.connect(self._on_send)
        arow.addWidget(self.send_btn)

        root.addWidget(action)

        # ---------- Progress area ----------
        progress_card = QFrame()
        progress_card.setObjectName("card")
        prl = QVBoxLayout(progress_card)
        prl.setContentsMargins(18, 14, 18, 14)
        prl.setSpacing(10)
        prl.addWidget(_section_title("Transfers"))

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

    # ---------- peer list management ----------
    def upsert_peer(self, peer) -> None:
        # Update discovered list
        for i in range(self.discovered_list.count()):
            it = self.discovered_list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == peer.peer_id:
                it.setText(peer.display)
                it.setData(Qt.ItemDataRole.UserRole + 1, peer.name)
                return
        it = QListWidgetItem(peer.display)
        it.setData(Qt.ItemDataRole.UserRole, peer.peer_id)
        it.setData(Qt.ItemDataRole.UserRole + 1, peer.name)
        self.discovered_list.addItem(it)

    def remove_peer(self, peer_id: str) -> None:
        for i in range(self.discovered_list.count()):
            it = self.discovered_list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == peer_id:
                self.discovered_list.takeItem(i)
                break
        # also remove from selected if present
        name_removed = None
        for i in range(self.selected_list.count()):
            it = self.selected_list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == peer_id:
                name_removed = it.text()
                self.selected_list.takeItem(i)
                break

    def remove_offline_selected(self, peer_id: str) -> None:
        """When a peer goes offline, drop it from Selected."""
        for i in range(self.selected_list.count()):
            it = self.selected_list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == peer_id:
                self.selected_list.takeItem(i)
                return

    # ---------- events ----------
    def _on_discover_dclick(self, item: QListWidgetItem) -> None:
        pid = item.data(Qt.ItemDataRole.UserRole)
        name = item.data(Qt.ItemDataRole.UserRole + 1) or item.text()
        # toggle in selected
        for i in range(self.selected_list.count()):
            if self.selected_list.item(i).data(Qt.ItemDataRole.UserRole) == pid:
                self.selected_list.takeItem(i)
                return
        it = QListWidgetItem(name)
        it.setData(Qt.ItemDataRole.UserRole, pid)
        self.selected_list.addItem(it)

    def _on_selected_dclick(self, item: QListWidgetItem) -> None:
        self.selected_list.takeItem(self.selected_list.row(item))

    def _on_discovered_menu(self, pos) -> None:
        # Right-click on a discovered peer directly toggles mute (no menu).
        # Emit the stable peer_id (cert fingerprint) so mute survives renames.
        item = self.discovered_list.itemAt(pos)
        if item is None:
            return
        pid = item.data(Qt.ItemDataRole.UserRole)
        if pid:
            self.mute_toggled.emit(pid)

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
            pin = f"{random.randint(0, 9999):04d}"
            self.pin_label.setText(f"PIN: {pin}")
        else:
            self.pin_label.setText("")

    def _current_pin(self) -> str:
        if not self.pin_chk.isChecked():
            return ""
        txt = self.pin_label.text()
        return txt.replace("PIN:", "").strip()

    def _on_send(self) -> None:
        peers = [self.selected_list.item(i).text() for i in range(self.selected_list.count())]
        files = list(self._selected_files)
        if not peers or not files:
            return
        pin = self._current_pin()
        # Rows are created lazily on the first progress/status event.
        self.send_requested.emit(peers, files, pin)

    # ---------- internal row management ----------
    def _get_or_create_row(self, direction: str, peer: str) -> TransferProgressRow:
        key = (direction, peer)
        row = self._rows.get(key)
        if row is None:
            row = TransferProgressRow(direction, peer)
            self._progress_layout.insertWidget(self._progress_layout.count() - 1, row)
            self._rows[key] = row
        return row

    def _schedule_clear(self, direction: str, peer: str) -> None:
        key = (direction, peer)
        QTimer.singleShot(_CLEAR_DELAY_MS, lambda: self._remove_row(key))

    def _remove_row(self, key: tuple[str, str]) -> None:
        row = self._rows.pop(key, None)
        if row is not None:
            self._progress_layout.removeWidget(row)
            row.deleteLater()

    # ---------- slots from network: sending ----------
    def on_task_progress(self, peer: str, filename: str, done: int, total: int,
                        bps: float, eta: str) -> None:
        self._get_or_create_row("up", peer).update_progress(filename, done, total, bps, eta)

    def on_task_status(self, peer: str, status: str) -> None:
        row = self._get_or_create_row("up", peer)
        row.set_status(status)
        if status in ("done", "failed", "rejected", "offline"):
            self._schedule_clear("up", peer)

    # ---------- slots from network: receiving ----------
    def on_recv_progress(self, sender: str, filename: str, done: int, total: int,
                         bps: float, eta: str) -> None:
        self._get_or_create_row("down", sender).update_progress(filename, done, total, bps, eta)

    def on_recv_completed(self, sender: str) -> None:
        self._get_or_create_row("down", sender).set_status("done")
        self._schedule_clear("down", sender)

    def on_recv_failed(self, sender: str, reason: str) -> None:
        self._get_or_create_row("down", sender).set_status(f"failed: {reason}")
        self._schedule_clear("down", sender)
