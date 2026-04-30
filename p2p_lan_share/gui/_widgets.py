"""Tiny shared GUI helpers (label factories, confirm dialog).

These were duplicated across tab_transfer.py, tab_quicktext.py, tab_tools.py.
Centralising them keeps the QSS "role" attribute hooks consistent.
"""
from __future__ import annotations

from PyQt6.QtWidgets import QLabel, QMessageBox


def h2(text: str) -> QLabel:
    """Section heading label (QSS role=h2)."""
    lbl = QLabel(text)
    lbl.setProperty("role", "h2")
    return lbl


def muted(text: str) -> QLabel:
    """Secondary/muted text label (QSS role=muted)."""
    lbl = QLabel(text)
    lbl.setProperty("role", "muted")
    return lbl


def confirm(parent, title: str, msg: str) -> bool:
    return QMessageBox.question(
        parent, title, msg,
        QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Cancel,
    ) == QMessageBox.StandardButton.Ok
