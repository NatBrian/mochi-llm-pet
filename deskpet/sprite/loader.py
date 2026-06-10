"""Sprite-sheet loading — slice a PNG into per-frame QPixmaps by LINEAR index.

Frames are laid out left->right, top->bottom across `columns`; frame `idx` lives
at (row = idx // columns, col = idx % columns). Windows/Qt only (lazy import)."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def load_sheet(path: str | Path) -> Any:
    """Load the whole sheet as a QPixmap (or None if it can't be read)."""
    from PyQt6.QtGui import QPixmap

    pm = QPixmap(str(path))
    return None if pm.isNull() else pm


def slice_range(sheet: Any, frame_w: int, frame_h: int, columns: int,
                frm: int, to: int) -> list[Any]:
    """Return QPixmaps for linear frame indices frm..to inclusive."""
    out = []
    for idx in range(frm, to + 1):
        row, col = divmod(idx, columns)
        out.append(sheet.copy(col * frame_w, row * frame_h, frame_w, frame_h))
    return out
