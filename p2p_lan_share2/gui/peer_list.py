"""Reusable peer list widget shared by all tabs."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QListWidget, QListWidgetItem

_PID = Qt.ItemDataRole.UserRole
_NAME = Qt.ItemDataRole.UserRole + 1


class PeerList(QListWidget):
    """List of peers. Optional right-click -> mute_requested(peer_id)."""

    mute_requested = pyqtSignal(str)

    def __init__(self, parent=None, *, mute_on_right_click: bool = False) -> None:
        super().__init__(parent)
        if mute_on_right_click:
            self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            self.customContextMenuRequested.connect(self._mute_at)

    def upsert(self, peer) -> None:
        for i in range(self.count()):
            it = self.item(i)
            if it.data(_PID) == peer.peer_id:
                it.setText(peer.display)
                it.setData(_NAME, peer.name)
                return
        it = QListWidgetItem(peer.display)
        it.setData(_PID, peer.peer_id)
        it.setData(_NAME, peer.name)
        self.addItem(it)

    def remove(self, peer_id: str) -> bool:
        for i in range(self.count()):
            if self.item(i).data(_PID) == peer_id:
                self.takeItem(i)
                return True
        return False

    @staticmethod
    def pid_of(item: QListWidgetItem) -> str:
        return item.data(_PID) or ""

    @staticmethod
    def name_of(item: QListWidgetItem) -> str:
        return item.data(_NAME) or item.text()

    def _mute_at(self, pos) -> None:
        it = self.itemAt(pos)
        if it is not None:
            self.mute_requested.emit(self.pid_of(it))
