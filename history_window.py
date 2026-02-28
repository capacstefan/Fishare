"""History dialog — transfer log viewer with modern Apple-inspired styling."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from history import TransferHistory


def _fmt_size(b: int) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {u}" if u != "B" else f"{b} {u}"
        b /= 1024
    return f"{b:.2f} TB"


class HistoryWindow(QDialog):
    """Modal dialog displaying past transfer records."""

    def __init__(self, history: TransferHistory, parent=None):
        super().__init__(parent)
        self.history = history
        self.setWindowTitle("Transfer History")
        self.resize(920, 600)
        self.setMinimumSize(600, 350)
        self._build_ui()
        self._apply_style()
        self._refresh()

    # ── Layout ──────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(18)
        root.setContentsMargins(24, 24, 24, 24)

        title = QLabel("Transfer History")
        title.setStyleSheet(
            "font-size: 20px; font-weight: 700; color: #ffffff; letter-spacing: 0.3px;"
        )
        root.addWidget(title)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ["Date & Time", "Direction", "Peer", "Files", "Size", "Speed", "Status"]
        )
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.itemDoubleClicked.connect(self._on_double_click)
        root.addWidget(self.table, 1)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.clear_btn = QPushButton("Clear All")
        self.clear_btn.clicked.connect(self._on_clear)
        btn_row.addWidget(self.clear_btn)

        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.accept)
        btn_row.addWidget(self.close_btn)
        root.addLayout(btn_row)

    # ── Style ───────────────────────────────────────────

    def _apply_style(self):
        self.setStyleSheet("""
        QDialog {
            background: #1c1c1e;
            color: #e5e5ea;
        }
        QTableWidget {
            background: #2c2c2e;
            color: #e5e5ea;
            border: 1px solid #3a3a3c;
            border-radius: 10px;
            font-size: 13px;
        }
        QTableWidget::item {
            padding: 10px 8px;
            border: none;
        }
        QTableWidget::item:selected {
            background: rgba(10, 132, 255, 0.25);
        }
        QTableWidget::item:alternate {
            background: #242426;
        }
        QHeaderView::section {
            background: #2c2c2e;
            color: #8e8e93;
            padding: 10px 8px;
            border: none;
            border-bottom: 1px solid #3a3a3c;
            font-weight: 700;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        QPushButton {
            background: #2c2c2e;
            color: #e5e5ea;
            border: 1px solid #3a3a3c;
            padding: 9px 22px;
            border-radius: 8px;
            font-size: 13px;
            font-weight: 600;
            min-width: 90px;
        }
        QPushButton:hover {
            background: #3a3a3c;
            border-color: #48484a;
        }
        QPushButton:pressed {
            background: #1c1c1e;
        }
        QScrollBar:vertical {
            background: transparent; width: 6px; margin: 4px 0;
        }
        QScrollBar::handle:vertical {
            background: #48484a; border-radius: 3px; min-height: 30px;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)

    # ── Data ────────────────────────────────────────────

    def _refresh(self):
        records = self.history.get_all()
        self.table.setRowCount(len(records))

        for row, rec in enumerate(records):
            self.table.setItem(row, 0, QTableWidgetItem(rec.timestamp_str))

            direction = "📤  Sent" if rec.direction == "sent" else "📥  Received"
            self.table.setItem(row, 1, QTableWidgetItem(direction))

            self.table.setItem(
                row, 2, QTableWidgetItem(f"{rec.peer_name}  ({rec.peer_host})")
            )
            self.table.setItem(row, 3, QTableWidgetItem(str(rec.num_files)))
            self.table.setItem(row, 4, QTableWidgetItem(_fmt_size(rec.total_size)))

            speed = f"{rec.speed_mbps:.1f} MB/s" if rec.status == "completed" else "—"
            self.table.setItem(row, 5, QTableWidgetItem(speed))

            status_item = QTableWidgetItem(rec.status.capitalize())
            color_map = {
                "completed": "#34c759",
                "canceled": "#ff9f0a",
                "rejected": "#ff9f0a",
                "error": "#ff453a",
            }
            from PyQt6.QtGui import QColor
            status_item.setForeground(QColor(color_map.get(rec.status, "#8e8e93")))
            self.table.setItem(row, 6, status_item)

    def _on_double_click(self, item):
        row = item.row()
        records = self.history.get_all()
        if row >= len(records):
            return
        rec = records[row]
        reply = QMessageBox.question(
            self,
            "Delete Record",
            f"Delete transfer record for {rec.peer_name}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.history.delete_record(row)
            self._refresh()

    def _on_clear(self):
        if not self.history.get_all():
            return
        reply = QMessageBox.question(
            self,
            "Clear History",
            "Delete all transfer history?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.history.clear_all()
            self._refresh()
