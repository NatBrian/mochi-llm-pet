"""ProceduralCat — a QPainter-drawn placeholder so the app runs perfectly BEFORE
any real art is added. Synthesizes every canonical state as a few simple frames.
Windows/Qt only (lazy import)."""

from __future__ import annotations

from typing import Any

from .player import AnimationClip

BASE = 32  # logical sprite size; the UI upscales by render.scale


def _new_pixmap():
    from PyQt6.QtGui import QPixmap
    from PyQt6.QtCore import Qt

    pm = QPixmap(BASE, BASE)
    pm.fill(Qt.GlobalColor.transparent)
    return pm


def _draw_cat(body_color, *, bob=0, ear_perk=1.0, eyes_open=True, tail=0.0,
              leg_phase=0.0, mouth="", blush=False) -> Any:
    from PyQt6.QtCore import QPointF, Qt, QRectF
    from PyQt6.QtGui import QBrush, QColor, QPainter, QPen

    pm = _new_pixmap()
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    cx = BASE / 2
    base_y = 22 + bob

    body = QColor(body_color)
    dark = QColor(body).darker(140)
    p.setPen(QPen(dark, 1))
    p.setBrush(QBrush(body))

    # tail
    p.save()
    p.translate(cx + 8, base_y - 2)
    p.rotate(tail * 25)
    p.drawRoundedRect(QRectF(0, -2, 9, 4), 2, 2)
    p.restore()

    # legs (two, animate with phase)
    leg_dx = 2.0 * leg_phase
    p.drawRoundedRect(QRectF(cx - 6 + leg_dx, base_y + 6, 3, 5), 1, 1)
    p.drawRoundedRect(QRectF(cx + 3 - leg_dx, base_y + 6, 3, 5), 1, 1)

    # body
    p.drawEllipse(QRectF(cx - 9, base_y - 4, 18, 12))
    # head
    p.drawEllipse(QRectF(cx - 7, base_y - 14, 14, 13))

    # ears
    from PyQt6.QtGui import QPolygonF

    eh = 5 * ear_perk
    p.drawPolygon(QPolygonF([QPointF(cx - 6, base_y - 12), QPointF(cx - 3, base_y - 12 - eh), QPointF(cx - 1, base_y - 11)]))
    p.drawPolygon(QPolygonF([QPointF(cx + 6, base_y - 12), QPointF(cx + 3, base_y - 12 - eh), QPointF(cx + 1, base_y - 11)]))

    # eyes
    p.setBrush(QBrush(QColor("#222")))
    p.setPen(Qt.PenStyle.NoPen)
    if eyes_open:
        p.drawEllipse(QRectF(cx - 4, base_y - 9, 2.2, 2.6))
        p.drawEllipse(QRectF(cx + 2, base_y - 9, 2.2, 2.6))
    else:
        p.setPen(QPen(QColor("#222"), 1))
        p.drawLine(QPointF(cx - 4, base_y - 7), QPointF(cx - 1.5, base_y - 7))
        p.drawLine(QPointF(cx + 2, base_y - 7), QPointF(cx + 4.5, base_y - 7))

    if blush:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(255, 130, 130, 140)))
        p.drawEllipse(QRectF(cx - 6, base_y - 6, 3, 2))
        p.drawEllipse(QRectF(cx + 3, base_y - 6, 3, 2))

    if mouth == "open":
        p.setPen(QPen(QColor("#a33"), 1))
        p.setBrush(QBrush(QColor("#c55")))
        p.drawEllipse(QRectF(cx - 1.5, base_y - 5, 3, 2.5))

    p.end()
    return pm


def _clip(state, frames, fps, loop) -> AnimationClip:
    return AnimationClip(state=state, frames=frames, fps=fps, loop=loop)


def build_placeholder_clips(color: str = "#e0a060") -> dict[str, AnimationClip]:
    """One AnimationClip per canonical state, drawn procedurally."""
    clips: dict[str, AnimationClip] = {}

    # idle: gentle bob + blink
    idle = [
        _draw_cat(color, bob=0, eyes_open=True, tail=0.2),
        _draw_cat(color, bob=-1, eyes_open=True, tail=0.4),
        _draw_cat(color, bob=0, eyes_open=False, tail=0.2),
        _draw_cat(color, bob=-1, eyes_open=True, tail=0.0),
    ]
    clips["idle"] = _clip("idle", idle, 6, True)

    # walk: leg phases
    walk = [_draw_cat(color, leg_phase=p, tail=0.3, bob=(-1 if i % 2 else 0))
            for i, p in enumerate((-1, 0, 1, 0))]
    clips["walk"] = _clip("walk", walk, 10, True)
    clips["run"] = _clip("run", walk, 16, True)

    # sleep: closed eyes, low body, slow
    sleep = [_draw_cat(color, bob=2, eyes_open=False, ear_perk=0.4, tail=0.0),
             _draw_cat(color, bob=3, eyes_open=False, ear_perk=0.4, tail=0.0)]
    clips["sleep"] = _clip("sleep", sleep, 2, True)

    # sit / lie / watch / look
    sit = [_draw_cat(color, bob=1, eyes_open=True, tail=0.1),
           _draw_cat(color, bob=1, eyes_open=True, tail=0.3)]
    clips["sit"] = _clip("sit", sit, 3, True)
    clips["lie"] = _clip("lie", sleep, 2, True)
    clips["watch"] = _clip("watch", sit, 3, True)
    clips["look"] = _clip("look", sit, 3, True)

    # happy / excited: ears perked, mouth open, tail wag, blush
    happy = [_draw_cat(color, bob=-1, ear_perk=1.3, mouth="open", tail=0.6, blush=True),
             _draw_cat(color, bob=-2, ear_perk=1.3, mouth="open", tail=-0.6, blush=True)]
    clips["happy"] = _clip("happy", happy, 8, True)
    clips["excited"] = _clip("excited", happy, 12, True)

    # angry: flat ears, red-tinted
    angry = [_draw_cat("#d06050", ear_perk=0.2, eyes_open=True, tail=-0.8),
             _draw_cat("#d06050", ear_perk=0.2, eyes_open=True, tail=0.8)]
    clips["angry"] = _clip("angry", angry, 8, True)

    # scared: pale, ears back
    scared = [_draw_cat("#cfcfcf", ear_perk=0.1, eyes_open=True, bob=1)]
    clips["scared"] = _clip("scared", scared, 4, True)

    # bored / curious -> reuse idle/sit
    clips["bored"] = _clip("bored", idle, 5, True)
    clips["curious"] = _clip("curious", sit, 4, True)

    # stretch / lick / pounce / nudge / fall
    clips["stretch"] = _clip("stretch", [_draw_cat(color, bob=-2, ear_perk=1.2),
                                          _draw_cat(color, bob=-3, ear_perk=1.2)], 8, True)
    clips["lick"] = _clip("lick", [_draw_cat(color, mouth="open"),
                                   _draw_cat(color, mouth="")], 6, True)
    clips["pounce"] = _clip("pounce", walk, 14, False)
    clips["nudge"] = _clip("nudge", walk, 12, False)
    clips["fall"] = _clip("fall", [_draw_cat(color, ear_perk=1.4, tail=0.9, eyes_open=True)], 8, True)

    return clips
