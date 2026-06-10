"""PetWindow — the transparent, always-on-top, click-through sprite window.

A small frameless window that follows the pet around the desktop. It paints the
current animation frame (nearest-neighbour upscaled for crisp pixels) plus an
optional speech bubble. Default click-through; when the cursor is over the sprite
the window becomes grabbable so the user can pet/drag/throw it (reflexes that
bypass the brain). Windows/Qt only.

Coordinate convention: the body & perception work in PHYSICAL pixels; this window
converts to Qt LOGICAL pixels via the screen's devicePixelRatio at the edges.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QPixmap
from PyQt6.QtWidgets import QWidget

from ..log import get
from ..types import Vec2
from . import win32_window
from .speech_bubble import SpeechBubble

log = get("ui.window")

SPRITE = 32  # logical sprite resolution


class PetWindow(QWidget):
    def __init__(self, body, player, scale: int = 3):
        super().__init__(None)
        self.body = body
        self.player = player
        self.scale = scale
        self.bubble = SpeechBubble()

        self.sprite_px = SPRITE * scale
        self.canvas_w = max(self.sprite_px + 40, 180)
        self.canvas_h = self.sprite_px + 80  # headroom for the bubble
        # where the sprite sits inside the canvas (logical px)
        self.sprite_x = (self.canvas_w - self.sprite_px) // 2
        self.sprite_y = self.canvas_h - self.sprite_px - 6

        self.setFixedSize(self.canvas_w, self.canvas_h)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self._dragging = False
        self._hwnd = 0
        self._t0 = time.monotonic()

    # ---- lifecycle --------------------------------------------------------- #
    def show(self) -> None:  # type: ignore[override]
        super().show()
        self._hwnd = int(self.winId())
        win32_window.make_overlay(self._hwnd)
        win32_window.set_click_through(self._hwnd, True)
        self._click_through = True
        self._last_move = (self.x(), self.y())
        self._last_paint_sig = None

    def _dpr(self) -> float:
        scr = self.screen()
        return scr.devicePixelRatio() if scr else 1.0

    # ---- speech ------------------------------------------------------------ #
    def say(self, text: str | None) -> None:
        if text:
            self.bubble.show(text)

    # ---- per-frame update (called by the app loop AFTER body.step) --------- #
    def tick(self, cursor_phys: Vec2) -> None:
        dpr = self._dpr()
        # follow the pet unless the user is actively dragging it — but only call
        # move() when the integer position actually changes (moving a layered
        # window every frame causes flicker).
        if not self._dragging:
            anchor_x = self.sprite_x + self.sprite_px / 2
            anchor_y = self.sprite_y + self.sprite_px / 2
            pos = (int(self.body.motion.pos.x / dpr - anchor_x),
                   int(self.body.motion.pos.y / dpr - anchor_y))
            if pos != self._last_move:
                self._last_move = pos
                self.move(pos[0], pos[1])

        # toggle grabbable only when the state flips — restyling the window every
        # frame (SetWindowLong) is the main flicker cause.
        over = self._sprite_rect_phys(dpr).contains(cursor_phys)
        want_click_through = not (over or self._dragging)
        if want_click_through != self._click_through:
            self._click_through = want_click_through
            win32_window.set_click_through(self._hwnd, want_click_through)

        # repaint only when something visible actually changed (the render loop is
        # 60fps but the animation is ~10fps — repainting a layered translucent
        # window every frame wastes CPU and can flicker).
        sig = (self.player.state, self.player.frame_index, self.player.facing,
               self._last_move, self.bubble.active)
        if sig != self._last_paint_sig:
            self._last_paint_sig = sig
            self.update()

    def _sprite_rect_phys(self, dpr: float):
        from ..types import Rect

        gx = self.x() + self.sprite_x
        gy = self.y() + self.sprite_y
        return Rect(gx * dpr, gy * dpr,
                    (gx + self.sprite_px) * dpr, (gy + self.sprite_px) * dpr)

    # ---- painting ---------------------------------------------------------- #
    def paintEvent(self, _evt) -> None:
        frame: QPixmap = self.player.current_frame()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)  # crisp pixels
        # bubble above
        self.bubble.paint(p, self.canvas_w)
        if frame is not None and not frame.isNull():
            scaled = frame.scaled(
                self.sprite_px, self.sprite_px,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
            if self.player.facing < 0:
                scaled = scaled.transformed(_flip())
            bob = int(self.player.idle_bob(time.monotonic() - self._t0))
            p.drawPixmap(self.sprite_x, self.sprite_y + bob, scaled)
        p.end()

    # ---- reflex mouse handling (only fires when not click-through) --------- #
    def mousePressEvent(self, evt) -> None:
        if evt.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._press_t = time.monotonic()
            self._moved = False
            self.body.grab()

    def mouseMoveEvent(self, evt) -> None:
        if self._dragging:
            self._moved = True
            g = evt.globalPosition()
            dpr = self._dpr()
            self.body.drag_to(Vec2(g.x() * dpr, g.y() * dpr), time.monotonic())

    def mouseReleaseEvent(self, evt) -> None:
        if not self._dragging:
            return
        self._dragging = False
        held = time.monotonic() - self._press_t
        if not self._moved and held < 0.25:
            self.body.poke()
        else:
            self.body.release(time.monotonic())


def _flip():
    from PyQt6.QtGui import QTransform

    return QTransform().scale(-1, 1)
