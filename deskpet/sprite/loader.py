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
    """Return QPixmaps for linear cell indices frm..to inclusive."""
    return slice_cells(sheet, frame_w, frame_h, columns, range(frm, to + 1))


def slice_cells(sheet: Any, frame_w: int, frame_h: int, columns: int,
                cells: Any) -> list[Any]:
    """Return QPixmaps for an explicit, possibly non-contiguous list of cell
    indices (cell = row * columns + col). Used for tags whose physical cells are
    broken up by nested-tag row padding in the exported sheet."""
    out = []
    for idx in cells:
        row, col = divmod(idx, columns)
        out.append(sheet.copy(col * frame_w, row * frame_h, frame_w, frame_h))
    return out
