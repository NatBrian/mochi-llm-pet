"""Speech-bubble painting — drawn into the pet window canvas above the sprite.
Holds the current text + an expiry; the window calls paint() each frame.

The bubble word-wraps multi-sentence dialogue into a rounded box that grows to
fit, and is bottom-anchored just above the sprite so it stays attached to the cat
no matter how many lines it needs."""

from __future__ import annotations

import time
from typing import Optional

MAX_TEXT_W = 252      # px: wrap width for the dialogue text
PAD_X = 10            # px: horizontal padding inside the bubble
PAD_Y = 7             # px: vertical padding inside the bubble
TAIL = 6             # px: little pointer below the bubble
GAP = 4             # px: gap between the tail tip and the sprite top


class SpeechBubble:
    def __init__(self) -> None:
        self.text: Optional[str] = None
        self._expires: float = 0.0

    def show(self, text: str, duration: Optional[float] = None) -> None:
        if not text:
            return
        self.text = text
        if duration is None:
            # longer lines linger longer; clamp so it never hogs the screen
            duration = max(2.5, min(10.0, 1.8 + len(text) * 0.05))
        self._expires = time.monotonic() + duration

    @property
    def active(self) -> bool:
        if self.text and time.monotonic() < self._expires:
            return True
        self.text = None
        return False

    @staticmethod
    def _wrap(metrics, text: str, max_w: int) -> list[str]:
        """Greedy word-wrap to max_w, honoring explicit newlines."""
        lines: list[str] = []
        for para in text.split("\n"):
            cur = ""
            for word in para.split(" "):
                trial = word if not cur else f"{cur} {word}"
                if not cur or metrics.horizontalAdvance(trial) <= max_w:
                    cur = trial
                else:
                    lines.append(cur)
                    cur = word
            lines.append(cur)
        return lines or [""]

    def paint(self, painter, canvas_w: int, anchor_y: Optional[int] = None) -> None:
        """Draw the bubble. `anchor_y` is the sprite's top edge (logical px); the
        bubble sits just above it. Falls back to the canvas top if not given."""
        if not self.active:
            return
        from PyQt6.QtCore import QPointF, QRectF, Qt
        from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygonF

        text = self.text or ""
        font = QFont("Segoe UI", 10)
        painter.setFont(font)
        metrics = painter.fontMetrics()

        max_w = min(MAX_TEXT_W, canvas_w - 16)
        lines = self._wrap(metrics, text, max_w)
        line_h = metrics.height()
        text_w = max(metrics.horizontalAdvance(ln) for ln in lines)

        tw = min(canvas_w - 8, text_w + 2 * PAD_X)
        th = len(lines) * line_h + 2 * PAD_Y
        x = (canvas_w - tw) / 2

        # bottom-anchor just above the sprite (tail tip near anchor_y)
        if anchor_y is None:
            y = 6
        else:
            y = max(2, anchor_y - GAP - TAIL - th)
        rect = QRectF(x, y, tw, th)

        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor(40, 40, 40, 220), 1))
        painter.setBrush(QBrush(QColor(255, 255, 255, 238)))
        painter.drawRoundedRect(rect, 9, 9)

        # little tail pointing down at the cat
        cx = canvas_w / 2
        painter.drawPolygon(QPolygonF([
            QPointF(cx - 5, y + th), QPointF(cx + 5, y + th), QPointF(cx, y + th + TAIL)
        ]))

        # text, left-aligned and line-wrapped inside the padded box
        painter.setPen(QPen(QColor(30, 30, 30)))
        ty = y + PAD_Y
        align = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
        for ln in lines:
            painter.drawText(QRectF(x + PAD_X, ty, tw - 2 * PAD_X, line_h), align, ln)
            ty += line_h
