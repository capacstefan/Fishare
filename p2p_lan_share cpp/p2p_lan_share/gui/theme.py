"""Central theme: global stylesheet + reusable widgets (toggle switch).

The QSS itself lives in ``gui/assets/app.qss`` so you can tweak colors and
spacing without touching Python. We load it at startup, substitute the
palette tokens defined below, and apply it to the QApplication.
"""
from __future__ import annotations

from pathlib import Path

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


_QSS_PATH = Path(__file__).with_name("assets") / "app.qss"


def _load_stylesheet() -> str:
    """Read app.qss and substitute @TOKEN@ placeholders with palette values."""
    text = _QSS_PATH.read_text(encoding="utf-8")
    tokens = {
        "BG": BG, "SURFACE": SURFACE, "BORDER": BORDER, "TEXT": TEXT,
        "MUTED": MUTED, "ACCENT": ACCENT, "ACCENT_HI": ACCENT_HI,
        "DANGER": DANGER, "SUCCESS": SUCCESS, "WARN": WARN,
    }
    for name, value in tokens.items():
        text = text.replace(f"@{name}@", value)
    return text


def apply_theme(app) -> None:
    """Apply global font + stylesheet."""
    font = QFont("Segoe UI Variable", 10)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(font)
    app.setStyleSheet(_load_stylesheet())


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
