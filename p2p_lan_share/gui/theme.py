"""Central theme: global stylesheet + reusable widgets (toggle switch).

Keep it simple: one QSS string applied app-wide. No per-widget styling scattered
around the code — if you want to tweak colors, do it here.
"""
from __future__ import annotations

from PyQt6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QSize,
    Qt,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import QAbstractButton


# ---------------------------------------------------------------------------
# Palette (Apple-inspired, light)
# ---------------------------------------------------------------------------
BG        = "#f4f5f7"
SURFACE   = "#ffffff"
BORDER    = "#e4e6eb"
TEXT      = "#1d1d1f"
MUTED     = "#6e6e73"
ACCENT    = "#0a84ff"
ACCENT_HI = "#409cff"
DANGER    = "#ff3b30"
SUCCESS   = "#30d158"
WARN      = "#ff9f0a"


# ---------------------------------------------------------------------------
# Global stylesheet
# ---------------------------------------------------------------------------
STYLESHEET = f"""
* {{
    font-family: "Segoe UI Variable", "Segoe UI", "SF Pro Text", sans-serif;
    color: {TEXT};
}}

QMainWindow, QDialog {{
    background: {BG};
}}

QStatusBar {{
    background: transparent;
    color: {MUTED};
    padding: 4px 10px;
    font-size: 11pt;
}}

/* -------- Tabs -------- */
QTabWidget::pane {{
    border: none;
    background: {BG};
    top: 4px;
}}
QTabWidget, QTabBar {{
    background: {BG};
}}
QTabBar::tab {{
    background: transparent;
    padding: 10px 22px;
    margin-right: 4px;
    font-size: 12pt;
    color: {MUTED};
    border: none;
    border-radius: 8px;
}}
QTabBar::tab:selected {{
    background: {SURFACE};
    color: {TEXT};
    font-weight: 600;
}}
QTabBar::tab:hover:!selected {{
    color: {TEXT};
}}

/* -------- Cards (GroupBox) -------- */
QGroupBox {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 14px;
    margin-top: 18px;
    padding: 18px;
    font-size: 12pt;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 8px;
    left: 12px;
    color: {TEXT};
}}

/* Generic "card" frame (objectName='card') */
QFrame#card {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 14px;
}}

/* -------- Labels -------- */
QLabel {{
    font-size: 11pt;
    background: transparent;
}}
QLabel[role="h1"] {{
    font-size: 20pt;
    font-weight: 700;
    color: {TEXT};
}}
QLabel[role="h2"] {{
    font-size: 13pt;
    font-weight: 600;
    color: {TEXT};
}}
QLabel[role="muted"] {{
    color: {MUTED};
    font-size: 10pt;
}}
QLabel[role="pin"] {{
    font-size: 14pt;
    font-weight: 700;
    color: {WARN};
    letter-spacing: 3px;
}}

/* -------- Inputs -------- */
QLineEdit, QPlainTextEdit, QTextEdit {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 8px 12px;
    font-size: 11pt;
    selection-background-color: {ACCENT};
    selection-color: white;
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border: 1px solid {ACCENT};
}}

/* -------- Buttons -------- */
QPushButton {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 9px 18px;
    font-size: 11pt;
    font-weight: 500;
    color: {TEXT};
}}
QPushButton:hover {{
    background: #fafafa;
    border-color: #d6d8dc;
}}
QPushButton:pressed {{
    background: #eeeef0;
}}
QPushButton:disabled {{
    color: #b5b5bb;
    background: #fafafa;
}}

QPushButton[role="primary"] {{
    background: {ACCENT};
    color: white;
    border: 1px solid {ACCENT};
}}
QPushButton[role="primary"]:hover {{
    background: {ACCENT_HI};
    border-color: {ACCENT_HI};
}}
QPushButton[role="primary"]:disabled {{
    background: #b8d3fb;
    border-color: #b8d3fb;
    color: white;
}}

QPushButton[role="danger"] {{
    background: {DANGER};
    color: white;
    border: 1px solid {DANGER};
}}
QPushButton[role="danger"]:hover {{
    background: #ff5a52;
    border-color: #ff5a52;
}}

/* -------- Lists / Tables -------- */
QListWidget, QTableWidget {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 12px;
    padding: 6px;
    font-size: 11pt;
    outline: 0;
}}
QListWidget::item, QTableWidget::item {{
    padding: 8px 10px;
    border-radius: 8px;
}}
QListWidget::item:selected, QTableWidget::item:selected {{
    background: rgba(10, 132, 255, 0.12);
    color: {TEXT};
}}
QListWidget::item:hover {{
    background: rgba(0, 0, 0, 0.03);
}}
QHeaderView::section {{
    background: {SURFACE};
    padding: 10px;
    border: none;
    border-bottom: 1px solid {BORDER};
    font-weight: 600;
    color: {MUTED};
    font-size: 10pt;
}}
QTableWidget {{
    gridline-color: transparent;
}}

/* -------- CheckBox (for PIN Lock) -------- */
QCheckBox {{
    font-size: 11pt;
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 20px; height: 20px;
    border: 1px solid {BORDER};
    border-radius: 6px;
    background: {SURFACE};
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
    image: none;
}}

/* -------- ProgressBar -------- */
QProgressBar {{
    background: #eceef2;
    border: 1px solid {BORDER};
    border-radius: 7px;
    height: 12px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background: {ACCENT};
    border-radius: 6px;
}}

/* -------- Containers (no more black/system fallback) -------- */
QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget {{
    background: transparent;
    border: none;
}}
QSplitter {{
    background: transparent;
}}
QWidget#tabPage {{
    background: {BG};
}}

/* -------- Scrollbars (minimal) -------- */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 4px;
}}
QScrollBar::handle:vertical {{
    background: #c9ccd1;
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: #a7abb3; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 4px;
}}
QScrollBar::handle:horizontal {{
    background: #c9ccd1;
    border-radius: 5px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{ background: #a7abb3; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ height: 0; width: 0; }}

/* -------- Splitter -------- */
QSplitter::handle {{
    background: transparent;
}}

/* -------- Menu (right-click) -------- */
QMenu {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 6px;
}}
QMenu::item {{
    padding: 8px 18px;
    border-radius: 6px;
}}
QMenu::item:selected {{
    background: rgba(10, 132, 255, 0.12);
}}
"""


def apply_theme(app) -> None:
    """Apply global font + stylesheet."""
    font = QFont("Segoe UI Variable", 10)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(font)
    app.setStyleSheet(STYLESHEET)


# ---------------------------------------------------------------------------
# Custom toggle switch (Online/Offline pill)
# ---------------------------------------------------------------------------
class ToggleSwitch(QAbstractButton):
    """Smooth iOS-style on/off switch.

    All visuals (knob, track color, label) are driven by a single animated
    progress value in [0.0, 1.0]. This keeps motion and color perfectly in
    sync and is fully robust to rapid clicks.
    """

    toggled_changed = pyqtSignal(bool)

    _PAD = 3  # inner padding around the knob

    def __init__(
        self,
        parent=None,
        *,
        on_text: str = "Online",
        off_text: str = "Offline",
        on_color: str = SUCCESS,
        off_color: str = "#c7c9ce",
    ) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(QSize(150, 34))

        self._on_text = on_text
        self._off_text = off_text
        self._on_color = QColor(on_color)
        self._off_color = QColor(off_color)

        # 0.0 = fully OFF, 1.0 = fully ON. Single source of truth.
        self._progress = 1.0 if self.isChecked() else 0.0

        self._anim = QPropertyAnimation(self, b"progress", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.toggled.connect(self._animate_to)
        self.toggled.connect(self.toggled_changed.emit)

    def sizeHint(self) -> QSize:
        return QSize(150, 34)

    # ---- animated progress (the one thing that moves) ----
    def _get_progress(self) -> float:
        return self._progress

    def _set_progress(self, v: float) -> None:
        self._progress = max(0.0, min(1.0, v))
        self.update()

    progress = pyqtProperty(float, fget=_get_progress, fset=_set_progress)

    def _animate_to(self, checked: bool) -> None:
        # Always restart from the current shown progress so mashing never jumps.
        self._anim.stop()
        self._anim.setStartValue(self._progress)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    # ---- painting ----
    @staticmethod
    def _lerp(a: QColor, b: QColor, t: float) -> QColor:
        return QColor(
            int(a.red()   + (b.red()   - a.red())   * t),
            int(a.green() + (b.green() - a.green()) * t),
            int(a.blue()  + (b.blue()  - a.blue())  * t),
        )

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        r = self.rect()
        t = self._progress  # 0..1 — the single driver

        # Track color: blend off -> on
        track = self._lerp(self._off_color, self._on_color, t)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)

        # Label: show the label that corresponds to the dominant side
        p.setPen(QColor("white"))
        font = self.font()
        font.setPointSize(10)
        font.setBold(True)
        p.setFont(font)
        on_side = t >= 0.5
        text = self._on_text if on_side else self._off_text
        text_rect = r.adjusted(
            r.height() if on_side else 0,
            0,
            0 if on_side else -r.height(),
            0,
        )
        p.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, text)

        # Knob: interpolate X between padded-left and padded-right
        knob_d = r.height() - 2 * self._PAD
        x_off = self._PAD
        x_on = r.width() - self._PAD - knob_d
        x = x_off + (x_on - x_off) * t
        p.setBrush(QColor("white"))
        p.drawEllipse(int(x), self._PAD, knob_d, knob_d)
