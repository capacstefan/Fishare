"""History window for viewing transfer history."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox
)

from history import TransferHistory


class HistoryWindow(QDialog):
    """Window displaying transfer history."""
    
    def __init__(self, history: TransferHistory, parent=None):
        super().__init__(parent)
        self.history = history
        
        self.setWindowTitle("Transfer History")
        self.resize(900, 600)
        self._setup_ui()
        self._apply_styles()
        self.refresh()
    
    def _setup_ui(self):
        """Setup UI components."""
        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)
        
        # Title
        title = QLabel("Transfer History")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #e6e9ee;")
        layout.addWidget(title)
        
        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "Date & Time", "Direction", "Peer", "Files", "Size", "Speed", "Status"
        ])
        
        # Configure table
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.itemDoubleClicked.connect(self._on_double_click)
        
        layout.addWidget(self.table)
        
        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        self.clear_btn = QPushButton("Clear History")
        self.clear_btn.clicked.connect(self._on_clear)
        btn_layout.addWidget(self.clear_btn)
        
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.close_btn)
        
        layout.addLayout(btn_layout)
    
    def _apply_styles(self):
        """Apply modern dark theme styles."""
        self.setStyleSheet("""
            QDialog {
                background: #0b0e12;
                color: #e6e9ee;
            }
            QTableWidget {
                background: #13171c;
                color: #e6e9ee;
                border: 1px solid #2a313a;
                border-radius: 8px;
                font-size: 13px;
                gridline-color: #2a313a;
            }
            QTableWidget::item {
                padding: 8px;
                border: none;
            }
            QTableWidget::item:selected {
                background: #1e3a5f;
            }
            QTableWidget::item:alternate {
                background: #0f1216;
            }
            QHeaderView::section {
                background: #1a1f26;
                color: #b7bfca;
                padding: 10px;
                border: none;
                border-bottom: 2px solid #2a313a;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton {
                background: #2b3037;
                color: #e6e9ee;
                border: none;
                padding: 10px 20px;
                border-radius: 6px;
                font-size: 13px;
                font-weight: 600;
                min-width: 100px;
            }
            QPushButton:hover {
                background: #3a4047;
            }
            QPushButton:pressed {
                background: #1f2428;
            }
        """)
    
    def refresh(self):
        """Refresh table with current history."""
        records = self.history.get_all()
        self.table.setRowCount(len(records))
        
        for row, record in enumerate(records):
            # Date & Time
            self.table.setItem(row, 0, QTableWidgetItem(record.timestamp_str))
            
            # Direction
            direction = "📤 Sent" if record.direction == "sent" else "📥 Received"
            self.table.setItem(row, 1, QTableWidgetItem(direction))
            
            # Peer
            peer_text = f"{record.peer_name} ({record.peer_host})"
            self.table.setItem(row, 2, QTableWidgetItem(peer_text))
            
            # Files
            self.table.setItem(row, 3, QTableWidgetItem(str(record.num_files)))
            
            # Size
            size_text = self._format_size(record.total_size)
            self.table.setItem(row, 4, QTableWidgetItem(size_text))
            
            # Speed
            if record.status == "completed":
                speed_text = f"{record.speed_mbps:.2f} MB/s"
            else:
                speed_text = "—"
            self.table.setItem(row, 5, QTableWidgetItem(speed_text))
            
            # Status
            status_item = QTableWidgetItem(record.status.upper())
            if record.status == "completed":
                status_item.setForeground(Qt.GlobalColor.green)
            elif record.status == "canceled":
                status_item.setForeground(Qt.GlobalColor.yellow)
            else:  # error
                status_item.setForeground(Qt.GlobalColor.red)
            self.table.setItem(row, 6, status_item)
    
    def _format_size(self, size_bytes: int) -> str:
        """Format size in human-readable format."""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
    
    def _on_double_click(self, item):
        """Handle double-click on a row to delete."""
        row = item.row()
        records = self.history.get_all()
        if row < len(records):
            record = records[row]
            reply = QMessageBox.question(
                self,
                "Delete Record",
                f"Delete transfer to/from {record.peer_name}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.history.delete_record(row)
                self.refresh()
    
    def _on_clear(self):
        """Handle clear all history."""
        if not self.history.get_all():
            return
        
        reply = QMessageBox.question(
            self,
            "Clear History",
            "Delete all transfer history?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.history.clear_all()
            self.refresh()
