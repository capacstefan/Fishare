"""Tab 1: File Transfer."""
from __future__ import annotations

import secrets

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QProgressBar, QPushButton, QScrollArea,
    QSplitter, QVBoxLayout, QWidget,
)

from .. import config
from ..util import fmt_size
from ._widgets import PeerSelector, h2, muted
from .theme import ToggleSwitch

_CLEAR_DELAY_MS = 4000
_TERMINAL_STATES = {"done", "failed", "rejected", "offline", "cancelled"}


class _ProgressRow(QWidget):
    """One row per (direction, peer)."""

    cancel_clicked = pyqtSignal()

    def __init__(self, direction: str, peer_name: str) -> None:
        super().__init__()
        arrow = "↑" if direction == "up" else "↓"
        self._prefix = f"{arrow}  {peer_name}"
        self._finished = False

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 4, 8, 4); v.setSpacing(3)

        self.label = QLabel(f"{self._prefix} — queued")
        self.label.setProperty("role", "muted")

        bar_row = QHBoxLayout(); bar_row.setContentsMargins(0, 0, 0, 0); bar_row.setSpacing(6)
        self.bar = QProgressBar(); self.bar.setRange(0, 100)
        self.bar.setMaximumHeight(12); self.bar.setTextVisible(False)
        self.cancel_btn = QPushButton("✕")
        self.cancel_btn.setProperty("role", "icon")
        self.cancel_btn.setToolTip("Cancel transfer")
        self.cancel_btn.setFixedSize(18, 18)
        self.cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_btn.clicked.connect(self._on_cancel)
        bar_row.addWidget(self.bar, 1)
        bar_row.addWidget(self.cancel_btn)

        v.addWidget(self.label); v.addLayout(bar_row)

    def _on_cancel(self) -> None:
        if self._finished:
            return
        self.cancel_btn.setEnabled(False)
        self.set_status("cancelling…", terminal=False)
        self.cancel_clicked.emit()

    def update_progress(self, filename, done, total, bps, eta) -> None:
        if self._finished:
            return
        pct = int(done * 100 / total) if total else 0
        pct = max(0, min(100, pct))
        self.bar.setValue(pct)
        self.label.setText(
            f"{self._prefix} — {pct}%  ({filename})  {fmt_size(bps)}/s  ETA {eta}"
        )

    def set_status(self, status: str, terminal: bool | None = None) -> None:
        self.label.setText(f"{self._prefix} — {status}")
        is_terminal = terminal if terminal is not None else status in _TERMINAL_STATES
        if is_terminal:
            self._finished = True
            self.cancel_btn.hide()


def _card() -> QFrame:
    f = QFrame(); f.setObjectName("card"); return f


class TransferTab(QWidget):
    device_name_changed = pyqtSignal(str)
    online_toggled = pyqtSignal(bool)
    choose_download_dir = pyqtSignal()
    send_requested = pyqtSignal(list, list, str)
    mute_toggled = pyqtSignal(str)
    cancel_requested = pyqtSignal(str, str)  # direction ("up"/"down"), peer name

    def __init__(self, settings: dict, parent=None) -> None:
        super().__init__(parent)
        self._files: list[str] = []
        self._rows: dict[tuple[str, str], _ProgressRow] = {}
        self._pin = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4); root.setSpacing(16)

        root.addWidget(self._build_header(settings))
        root.addWidget(self._build_body(), 1)
        root.addWidget(self._build_action())
        root.addWidget(self._build_progress())

    # ---- header (device + online toggle) ----
    def _build_header(self, settings: dict) -> QFrame:
        card = _card()
        row = QHBoxLayout(card); row.setContentsMargins(20, 16, 20, 16); row.setSpacing(14)

        self.name_edit = QLineEdit(settings["device_name"])
        self.name_edit.setMinimumWidth(260); self.name_edit.setMaximumWidth(420)
        self.name_edit.setMinimumHeight(40)
        self.name_edit.editingFinished.connect(
            lambda: self.device_name_changed.emit(self.name_edit.text().strip())
        )
        row.addWidget(muted("Device"))
        row.addWidget(self.name_edit)

        reset = QPushButton("⟳")
        reset.setProperty("role", "icon"); reset.setToolTip("Reset device name to default")
        reset.setFixedSize(40, 40); reset.clicked.connect(self._reset_name)
        row.addWidget(reset)
        row.addStretch(1)

        self.online_toggle = ToggleSwitch(on_text="Online", off_text="Offline")
        self.online_toggle.setChecked(bool(settings.get("online", True)))
        self.online_toggle.toggled_changed.connect(self.online_toggled.emit)
        row.addWidget(self.online_toggle)
        return card

    # ---- body (peers + files split) ----
    def _build_body(self) -> QSplitter:
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setHandleWidth(14); split.setChildrenCollapsible(False)

        peers = _card()
        pv = QVBoxLayout(peers); pv.setContentsMargins(18, 16, 18, 16); pv.setSpacing(12)
        pv.addWidget(h2("Peers"))
        self.selector = PeerSelector()
        self.selector.mute_toggled.connect(self.mute_toggled.emit)
        pv.addWidget(self.selector, 1)

        files = _card()
        fv = QVBoxLayout(files); fv.setContentsMargins(18, 16, 18, 16); fv.setSpacing(12)
        fv.addWidget(h2("Files"))
        self.files_list = QListWidget()
        self.files_list.itemDoubleClicked.connect(self._remove_file)
        fv.addWidget(self.files_list, 1)

        btns = QHBoxLayout(); btns.setSpacing(8)
        add = QPushButton("+ Add Files"); add.clicked.connect(self._add_files)
        clr = QPushButton("Clear All"); clr.clicked.connect(self._clear_files)
        dlb = QPushButton("Default Download Folder…")
        dlb.clicked.connect(self.choose_download_dir.emit)
        btns.addWidget(add); btns.addWidget(clr); btns.addStretch(1); btns.addWidget(dlb)
        fv.addLayout(btns)

        split.addWidget(peers); split.addWidget(files)
        split.setSizes([560, 440])
        return split

    # ---- action bar ----
    def _build_action(self) -> QFrame:
        card = _card()
        row = QHBoxLayout(card); row.setContentsMargins(20, 14, 20, 14); row.setSpacing(12)

        self.pin_chk = QCheckBox("PIN Lock"); self.pin_chk.toggled.connect(self._on_pin_toggle)
        row.addWidget(self.pin_chk)
        self.pin_label = QLabel(""); self.pin_label.setProperty("role", "pin")
        row.addWidget(self.pin_label)
        row.addStretch(1)

        self.send_btn = QPushButton("Send")
        self.send_btn.setProperty("role", "primary")
        self.send_btn.setMinimumWidth(140); self.send_btn.setMinimumHeight(42)
        self.send_btn.clicked.connect(self._on_send)
        row.addWidget(self.send_btn)
        return card

    # ---- progress area ----
    def _build_progress(self) -> QFrame:
        card = _card()
        v = QVBoxLayout(card); v.setContentsMargins(18, 14, 18, 14); v.setSpacing(10)
        v.addWidget(h2("Transfers"))

        self._progress_host = QWidget()
        self._progress_layout = QVBoxLayout(self._progress_host)
        self._progress_layout.setContentsMargins(0, 0, 0, 0); self._progress_layout.setSpacing(4)
        self._progress_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame); scroll.setWidgetResizable(True)
        scroll.setWidget(self._progress_host); scroll.setMinimumHeight(80)
        v.addWidget(scroll)
        card.setMaximumHeight(150)
        return card

    # ---- peer feed ----
    def upsert_peer(self, peer) -> None:
        self.selector.upsert_peer(peer)

    def remove_peer(self, peer_id: str) -> None:
        self.selector.remove_peer(peer_id)

    def remove_offline_selected(self, peer_id: str) -> None:
        self.selector.remove_selected_pid(peer_id)

    # ---- files ----
    def _add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Select files")
        for p in paths:
            if p not in self._files:
                self._files.append(p)
                self.files_list.addItem(p)

    def _clear_files(self) -> None:
        self._files.clear()
        self.files_list.clear()

    def _remove_file(self, item: QListWidgetItem) -> None:
        row = self.files_list.row(item)
        self.files_list.takeItem(row)
        if 0 <= row < len(self._files):
            self._files.pop(row)

    # ---- pin / send ----
    def _on_pin_toggle(self, checked: bool) -> None:
        if checked:
            self._pin = f"{secrets.randbelow(10000):04d}"
            self.pin_label.setText(f"PIN: {self._pin}")
        else:
            self._pin = ""
            self.pin_label.setText("")

    def _on_send(self) -> None:
        peers = self.selector.selected_names()
        if peers and self._files:
            self.send_requested.emit(peers, list(self._files), self._pin)

    def _reset_name(self) -> None:
        name = config.default_device_name()
        self.name_edit.setText(name)
        self.name_edit.setFocus(); self.name_edit.selectAll()
        self.device_name_changed.emit(name)

    # ---- progress rows ----
    def _row(self, direction: str, peer: str) -> _ProgressRow:
        key = (direction, peer)
        row = self._rows.get(key)
        if row is None:
            row = _ProgressRow(direction, peer)
            row.cancel_clicked.connect(
                lambda d=direction, p=peer: self.cancel_requested.emit(d, p)
            )
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
        if status in _TERMINAL_STATES:
            self._schedule_clear("up", peer)

    def on_recv_progress(self, sender, filename, done, total, bps, eta) -> None:
        self._row("down", sender).update_progress(filename, done, total, bps, eta)

    def on_recv_completed(self, sender) -> None:
        self._row("down", sender).set_status("done")
        self._schedule_clear("down", sender)

    def on_recv_failed(self, sender, reason) -> None:
        self._row("down", sender).set_status(f"failed: {reason}")
        self._schedule_clear("down", sender)

    def on_recv_cancelled(self, sender) -> None:
        self._row("down", sender).set_status("cancelled")
        self._schedule_clear("down", sender)
