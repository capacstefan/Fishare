"""Shared GUI helpers: label factories, confirm dialog, dual-list peer picker."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)

from .peer_list import PeerList


def h2(text: str) -> QLabel:
    lbl = QLabel(text); lbl.setProperty("role", "h2"); return lbl


def muted(text: str) -> QLabel:
    lbl = QLabel(text); lbl.setProperty("role", "muted"); return lbl


def confirm(parent, title: str, msg: str) -> bool:
    return QMessageBox.question(
        parent, title, msg,
        QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Cancel,
    ) == QMessageBox.StandardButton.Ok


class PeerSelector(QWidget):
    """Two side-by-side lists: Discovered | Selected. Double-click moves items."""

    mute_toggled = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)

        left = QVBoxLayout(); left.setSpacing(8)
        left.addWidget(muted("Discovered"))
        self.discovered = PeerList(mute_on_right_click=True)
        self.discovered.setMinimumHeight(80)
        self.discovered.mute_requested.connect(self.mute_toggled.emit)
        self.discovered.itemDoubleClicked.connect(self._on_pick)
        left.addWidget(self.discovered, 1)

        right = QVBoxLayout(); right.setSpacing(8)
        right.addWidget(muted("Selected"))
        self.selected = QListWidget()
        self.selected.setMinimumHeight(80)
        self.selected.itemDoubleClicked.connect(
            lambda it: self.selected.takeItem(self.selected.row(it))
        )
        right.addWidget(self.selected, 1)
        clr = QPushButton("Clear All")
        clr.clicked.connect(self.selected.clear)
        right.addWidget(clr)

        row.addLayout(left, 1)
        row.addLayout(right, 1)

    # ---- peer feed ----
    def upsert_peer(self, peer) -> None:
        self.discovered.upsert(peer)

    def remove_peer(self, peer_id: str) -> None:
        self.discovered.remove(peer_id)
        self._drop_selected(peer_id)

    def remove_selected_pid(self, peer_id: str) -> None:
        self._drop_selected(peer_id)

    def _drop_selected(self, peer_id: str) -> None:
        for i in range(self.selected.count()):
            if self.selected.item(i).data(Qt.ItemDataRole.UserRole) == peer_id:
                self.selected.takeItem(i)
                return

    def selected_names(self) -> list[str]:
        return [self.selected.item(i).text() for i in range(self.selected.count())]

    def _on_pick(self, item: QListWidgetItem) -> None:
        pid = PeerList.pid_of(item)
        name = PeerList.name_of(item)
        # Toggle: already selected -> remove; else -> add.
        for i in range(self.selected.count()):
            if self.selected.item(i).data(Qt.ItemDataRole.UserRole) == pid:
                self.selected.takeItem(i)
                return
        it = QListWidgetItem(name)
        it.setData(Qt.ItemDataRole.UserRole, pid)
        self.selected.addItem(it)
