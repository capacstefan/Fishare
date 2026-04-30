"""Reusable dialogs: Accept/Reject offer, Quick Text editor, Quick Text reader."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from .. import config
from ..util import fmt_size as _fmt_size


class AcceptOfferDialog(QDialog):
    """Shown when an incoming files/text/sync offer arrives."""

    def __init__(self, offer, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Incoming transfer")
        self.setMinimumWidth(420)
        self.offer = offer

        v = QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 18)
        v.setSpacing(14)

        title = QLabel(f"From {offer.sender_name}")
        title.setProperty("role", "h2")
        v.addWidget(title)

        if offer.kind == "files":
            n = len(offer.files)
            msg = (f"Wants to send you {n} file{'s' if n != 1 else ''} "
                   f"(Total: {_fmt_size(offer.total_size)}).")
        elif offer.kind == "text":
            msg = "Wants to send you a quick text message."
        elif offer.kind == "sync":
            msg = f"Wants to start syncing folder \u201c{offer.folder}\u201d to your computer."
        else:
            msg = "Wants to send you something."

        body = QLabel(msg)
        body.setWordWrap(True)
        v.addWidget(body)

        self.pin_edit: QLineEdit | None = None
        if offer.pin_required:
            lbl = QLabel("Enter PIN shown on sender's screen:")
            lbl.setProperty("role", "muted")
            v.addWidget(lbl)
            self.pin_edit = QLineEdit(self)
            self.pin_edit.setMaxLength(8)
            self.pin_edit.setPlaceholderText("PIN")
            self.pin_edit.setMinimumHeight(36)
            v.addWidget(self.pin_edit)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = btns.button(QDialogButtonBox.StandardButton.Ok)
        ok_btn.setText("Accept")
        ok_btn.setProperty("role", "primary")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("Reject")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

    def pin(self) -> str:
        return self.pin_edit.text().strip() if self.pin_edit else ""


class QuickTextEditor(QDialog):
    """Compose a quick text (max 500 chars)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Write Quick Text")
        self.resize(520, 320)

        v = QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 18)
        v.setSpacing(12)

        title = QLabel("Quick Text")
        title.setProperty("role", "h2")
        v.addWidget(title)

        self.edit = QPlainTextEdit(self)
        self.edit.setPlaceholderText(f"Write up to {config.QUICK_TEXT_MAX_CHARS} characters…")
        v.addWidget(self.edit, 1)

        self.counter = QLabel(f"0 / {config.QUICK_TEXT_MAX_CHARS}")
        self.counter.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.counter.setProperty("role", "muted")
        v.addWidget(self.counter)
        self.edit.textChanged.connect(self._update_counter)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = btns.button(QDialogButtonBox.StandardButton.Ok)
        ok_btn.setText("Send")
        ok_btn.setProperty("role", "primary")
        btns.accepted.connect(self._on_ok)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

    def _update_counter(self) -> None:
        n = len(self.edit.toPlainText())
        if n > config.QUICK_TEXT_MAX_CHARS:
            self.edit.blockSignals(True)
            self.edit.setPlainText(self.edit.toPlainText()[: config.QUICK_TEXT_MAX_CHARS])
            self.edit.blockSignals(False)
            n = config.QUICK_TEXT_MAX_CHARS
        self.counter.setText(f"{n} / {config.QUICK_TEXT_MAX_CHARS}")

    def _on_ok(self) -> None:
        if self.edit.toPlainText().strip():
            self.accept()

    def text(self) -> str:
        return self.edit.toPlainText().strip()


class QuickTextReader(QDialog):
    """Read a received quick text with a Copy button."""

    def __init__(self, sender: str, text: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"From {sender}")
        self.resize(520, 340)
        v = QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 18)
        v.setSpacing(12)

        title = QLabel(f"From {sender}")
        title.setProperty("role", "h2")
        v.addWidget(title)

        box = QPlainTextEdit(self)
        box.setPlainText(text)
        box.setReadOnly(True)
        v.addWidget(box, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        copy_btn = QPushButton("Copy to Clipboard")
        copy_btn.setProperty("role", "primary")
        close_btn = QPushButton("Close")
        row.addWidget(copy_btn)
        row.addStretch(1)
        row.addWidget(close_btn)
        v.addLayout(row)

        copy_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(text))
        close_btn.clicked.connect(self.accept)
