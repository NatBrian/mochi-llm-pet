"""AnimationRegistry — builds {state -> AnimationClip} from real sprite sheets
(per the manifest) or from the procedural placeholder.

Real path: load the sheet once, slice every tag's linear frame range into a clip,
then add the canonical aliases (idle/walk/run/sleep/happy/…) pointing at the
mapped tag's frames so the body's verbs/emotions resolve to real animations.
"""

from __future__ import annotations

from pathlib import Path

from ..log import friendly, get
from . import manifest as manifest_mod
from .player import AnimationClip

log = get("sprite.registry")


def _sheet_path(assets_dir: Path, mani: "manifest_mod.Manifest") -> Path:
    return assets_dir / mani.sheet if mani.sheet else assets_dir / "cat.png"


def _has_real_assets(assets_dir: Path, mani: "manifest_mod.Manifest | None") -> bool:
    if mani is None or not mani.specs or not mani.sheet:
        return False
    return _sheet_path(assets_dir, mani).exists()


def build(assets_dir: str | Path, color: str = "#e0a060") -> dict[str, AnimationClip]:
    """Return clips from assets if present, else the procedural placeholder."""
    import os

    assets_dir = Path(assets_dir)
    manifest_name = os.environ.get("DESKPET_MANIFEST", "anim_manifest.yaml")
    mani = manifest_mod.load(assets_dir / manifest_name)

    if _has_real_assets(assets_dir, mani):
        try:
            clips = _from_assets(assets_dir, mani)
            if clips:
                aliases = sum(1 for k in clips if k in manifest_mod.CANONICAL_STATES)
                friendly(f"Loaded {len(clips)} animations from {mani.sheet} "
                         f"({aliases} canonical states mapped).")
                return clips
        except Exception as e:  # noqa: BLE001
            log.warning("failed loading sprite sheet (%s); using placeholder", e)

    friendly("No sprite art found — using the built-in placeholder cat. "
             "Drop the Bow.Pixel 'Cat 85+' files into assets/ and generate the "
             "manifest to upgrade.")
    from .placeholder import build_placeholder_clips

    return build_placeholder_clips(color)


def _from_assets(assets_dir: Path, mani: "manifest_mod.Manifest") -> dict[str, AnimationClip]:
    from .loader import load_sheet, slice_cells

    sheet = load_sheet(_sheet_path(assets_dir, mani))
    if sheet is None:
        return {}

    clips: dict[str, AnimationClip] = {}
    for state, spec in mani.specs.items():
        frames = slice_cells(sheet, mani.frame_w, mani.frame_h, mani.columns,
                             spec.cell_list)
        if frames:
            clips[state] = AnimationClip(state=state, frames=frames,
                                         fps=spec.fps, loop=spec.loop)

    # canonical aliases -> the mapped tag's clip (so verbs/emotions resolve)
    for canon, tag in mani.aliases.items():
        if tag in clips and canon not in clips:
            src = clips[tag]
            clips[canon] = AnimationClip(state=canon, frames=src.frames,
                                         fps=src.fps, loop=src.loop)
    return clips
