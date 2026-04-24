"""Reusable peer-selection widget.

Three tabs (Transfer, Quick Text, Tools) all need the same "Discovered |
Selected | Clear" column pair. This module owns that shared piece so each
tab just imports ``PeerListPair`` and wires its own handlers.

Design is intentionally thin: no business logic, just two ``QListWidget``s,
a Clear button, and the handful of helper methods the tabs call.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


def _muted_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("role", "muted")
    return lbl


class PeerListPair(QWidget):
    """Discovered + Selected side-by-side lists with a Clear All button.

    Peer identity is stored in ``Qt.ItemDataRole.UserRole`` (peer_id, a stable
    cert fingerprint). The human-readable name is stored in ``UserRole + 1``
    and is what the Selected list displays.

    Signals:
      * ``peer_right_clicked(peer_id)`` - on the Discovered list.
        Wire this to your mute toggle.
    """

    peer_right_clicked = pyqtSignal(str)

    def __init__(self, list_min_height: int = 110, parent=None) -> None:
        super().__init__(parent)

        cols = QHBoxLayout(self)
        cols.setContentsMargins(0, 0, 0, 0)
        cols.setSpacing(14)

        # --- Discovered column ---
        left = QVBoxLayout()
        left.setSpacing(8)
        left.addWidget(_muted_label("Discovered"))
        self.discovered = QListWidget()
        self.discovered.setMinimumHeight(list_min_height)
        self.discovered.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.discovered.customContextMenuRequested.connect(self._on_right_click)
        self.discovered.itemDoubleClicked.connect(self._on_discover_dclick)
        left.addWidget(self.discovered)

        # --- Selected column + Clear button ---
        right = QVBoxLayout()
        right.setSpacing(8)
        right.addWidget(_muted_label("Selected"))
        self.selected = QListWidget()
        self.selected.setMinimumHeight(list_min_height)
        self.selected.itemDoubleClicked.connect(
            lambda it: self.selected.takeItem(self.selected.row(it))
        )
        right.addWidget(self.selected)

        clear_btn = QPushButton("Clear All")
        clear_btn.clicked.connect(self.selected.clear)
        right.addWidget(clear_btn)

        cols.addLayout(left, 1)
        cols.addLayout(right, 1)

    # ---------- public API used by tabs ----------
    def upsert_peer(self, peer) -> None:
        """Insert or update a discovered peer (matched on peer_id)."""
        for i in range(self.discovered.count()):
            it = self.discovered.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == peer.peer_id:
                it.setText(peer.display)
                it.setData(Qt.ItemDataRole.UserRole + 1, peer.name)
                return
        it = QListWidgetItem(peer.display)
        it.setData(Qt.ItemDataRole.UserRole, peer.peer_id)
        it.setData(Qt.ItemDataRole.UserRole + 1, peer.name)
        self.discovered.addItem(it)

    def remove_peer(self, peer_id: str) -> None:
        """Drop a peer from both Discovered and Selected lists."""
        for lst in (self.discovered, self.selected):
            for i in range(lst.count()):
                if lst.item(i).data(Qt.ItemDataRole.UserRole) == peer_id:
                    lst.takeItem(i)
                    break

    def selected_names(self) -> list[str]:
        """Names of the currently selected peers (display text of the list)."""
        return [self.selected.item(i).text() for i in range(self.selected.count())]

    # ---------- internal slots ----------
    def _on_discover_dclick(self, item: QListWidgetItem) -> None:
        """Double-click in Discovered: toggle membership in Selected."""
        pid = item.data(Qt.ItemDataRole.UserRole)
        name = item.data(Qt.ItemDataRole.UserRole + 1) or item.text()
        for i in range(self.selected.count()):
            if self.selected.item(i).data(Qt.ItemDataRole.UserRole) == pid:
                self.selected.takeItem(i)
                return
        it = QListWidgetItem(name)
        it.setData(Qt.ItemDataRole.UserRole, pid)
        self.selected.addItem(it)

    def _on_right_click(self, pos) -> None:
        item = self.discovered.itemAt(pos)
        if item is None:
            return
        pid = item.data(Qt.ItemDataRole.UserRole)
        if pid:
            self.peer_right_clicked.emit(pid)
