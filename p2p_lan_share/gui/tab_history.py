"""Tab 4: History table."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


COLUMNS = ["Date", "Size", "# Files", "Direction", "Peer", "Type"]


def _fmt_size(n) -> str:
    if n in ("-", None, 0):
        return "-" if n != 0 else "0 B"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


class HistoryTab(QWidget):
    def __init__(self, clear_cb, parent=None) -> None:
        super().__init__(parent)
        self._clear_cb = clear_cb

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        card = QFrame()
        card.setObjectName("card")
        v = QVBoxLayout(card)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(12)

        title = QLabel("Transfer History")
        title.setProperty("role", "h2")
        v.addWidget(title)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        v.addWidget(self.table, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        self.clear_btn = QPushButton("Clear History")
        self.clear_btn.setProperty("role", "danger")
        self.clear_btn.clicked.connect(self._on_clear)
        row.addWidget(self.clear_btn)
        v.addLayout(row)

        root.addWidget(card)

    def load(self, entries: list[dict]) -> None:
        self.table.setRowCount(0)
        for e in entries:
            self.append(e, save=False)

    def append(self, entry: dict, save: bool = True) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        is_text = entry.get("type") == "QuickText"
        cells = [
            entry.get("date", ""),
            "-" if is_text else _fmt_size(entry.get("size", 0)),
            "-" if is_text else str(entry.get("count", 0)),
            entry.get("direction", ""),
            entry.get("peer", ""),
            entry.get("type", ""),
        ]
        for c, text in enumerate(cells):
            it = QTableWidgetItem(str(text))
            it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, c, it)

    def _on_clear(self) -> None:
        self.table.setRowCount(0)
        if callable(self._clear_cb):
            self._clear_cb()
