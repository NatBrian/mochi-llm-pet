"""Parse an .aseprite file's frame tags + frame info and emit an
`anim_manifest.yaml` that maps every animation tag to a linear frame range in the
exported PNG sheet.

Usage:
    python -m deskpet.tools.aseprite_to_manifest <file.aseprite> <sheet.png> \
        [--out assets/anim_manifest.yaml] [--sheet-rel Cat_85_Animations/Cat_Ginger.png]

The PNG must be the Aseprite "horizontal/by-rows" spritesheet export of the same
file (frames flow left->right, top->bottom). Frame size + columns are derived
from the .aseprite canvas size and the PNG width.
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

# One-shot (non-looping) animations: transient sequences that should play once.
_ONESHOT_HINTS = (
    "jump", "start", "stop", "brake", "prepare", "stand_up", "sit_down", "sitdown",
    "attack", "dmg", "die", "spawn", "dig", "pooping", "fall", "ftot", "ttof",
    "scratching_start", "scratching_end", "stand", "climb_jump", "lift", "pushes",
    "pull", "frame",
)

# canonical body/emotion states  ->  preferred aseprite tag name
ALIASES = {
    "idle": "Idle_1",
    "walk": "W_1",
    "run": "Run_1",
    "sleep": "Dream",
    "sit": "Sit_1",
    "lie": "Rest_1",
    "stretch": "Idle_Lift_1",
    "lick": "Scratching_1",
    "happy": "Dance",
    "excited": "Dance",
    "angry": "Aggress",
    "scared": "Dmg_1",
    "bored": "Idle_2",
    "curious": "Idle_Tilt_1",
    "look": "Idle_Tilt_1",
    "watch": "Sit_1",
    "pounce": "Attack_1",
    "nudge": "Pushes",
    "fall": "Fall",
}


def parse_aseprite(path: str | Path):
    """Return (frames, w, h, durations[list], tags[list of (name, from, to)])."""
    d = Path(path).read_bytes()
    (_fsize, magic, frames, w, h, _depth) = struct.unpack_from("<IHHHHH", d, 0)
    if magic != 0xA5E0:
        raise ValueError(f"not an aseprite file (magic {magic:#x})")
    off = 128
    durations: list[int] = []
    tags: list[tuple[str, int, int]] = []
    for _ in range(frames):
        f0 = off
        (fbytes, fmagic, nch_old, dur, _r, nch_new) = struct.unpack_from("<IHHHHI", d, off)
        if fmagic != 0xF1FA:
            raise ValueError(f"bad frame magic {fmagic:#x}")
        durations.append(dur)
        nch = nch_new if nch_new else nch_old
        coff = off + 16
        for _c in range(nch):
            (csize, ctype) = struct.unpack_from("<IH", d, coff)
            if ctype == 0x2018:  # FRAME_TAGS
                ntags = struct.unpack_from("<H", d, coff + 6)[0]
                p = coff + 6 + 2 + 8
                for _t in range(ntags):
                    frm, to, _loop = struct.unpack_from("<HHB", d, p)
                    p += 5 + 2 + 6 + 3 + 1  # repeat(2) reserved(6) rgb(3) extra(1)
                    slen = struct.unpack_from("<H", d, p)[0]
                    p += 2
                    name = d[p:p + slen].decode("utf-8", "replace")
                    p += slen
                    tags.append((name, frm, to))
            coff += csize
        off = f0 + fbytes
    return frames, w, h, durations, tags


def _dedup(name: str, seen: dict[str, int]) -> str:
    if name not in seen:
        seen[name] = 1
        return name
    seen[name] += 1
    return f"{name}_{seen[name]}"


def _is_oneshot(name: str) -> bool:
    low = name.lower()
    return any(h in low for h in _ONESHOT_HINTS)


def dense_to_cell_map(png_path: str | Path, fw: int, fh: int, cols: int,
                      n_frames: int) -> list[int]:
    """Map each DENSE aseprite frame index -> its PHYSICAL cell index in the PNG.

    Aseprite exports the sheet ROW-ALIGNED per tag: each tag's frames start at
    col 0 of a fresh row and the row is padded with transparent cells. So dense
    frame index N does NOT live at cell N. We recover the true layout by reading
    the non-empty cells in row-major order: the i-th non-empty cell is frame i
    (the export writes frames in order and — verified — no frame is fully
    transparent, since non-empty cell count equals the frame count)."""
    from PIL import Image

    im = Image.open(png_path).convert("RGBA")
    W, H = im.size
    px = im.load()
    rows = H // fh
    cell_of: list[int] = []
    for r in range(rows):
        for c in range(cols):
            x0, y0 = c * fw, r * fh
            nonempty = any(
                px[x, y][3] != 0
                for y in range(y0, y0 + fh)
                for x in range(x0, x0 + fw)
            )
            if nonempty:
                cell_of.append(r * cols + c)
    if len(cell_of) != n_frames:
        # layout assumption broke (e.g. a genuinely transparent frame); fall back
        # to identity so we at least don't crash — frames may be misaligned.
        return list(range(n_frames))
    return cell_of


def build_yaml(sheet_rel: str, w: int, h: int, cols: int, durations, tags,
               cell_of: list[int] | None = None) -> str:
    lines = [
        "# Auto-generated from the Bow.Pixel 'Cat 85+' .aseprite by",
        "# deskpet.tools.aseprite_to_manifest. Frames are linear indices into the",
        "# sheet (row = idx // columns, col = idx % columns).",
        f"sheet: {sheet_rel}",
        f"frame_size: [{w}, {h}]",
        f"columns: {cols}",
        "default_fps: 10",
        "states:",
    ]
    seen: dict[str, int] = {}
    resolved_alias_targets: dict[str, str] = {}
    name_map: dict[str, str] = {}  # original tag -> deduped key (first occurrence)
    for name, frm, to in tags:
        key = _dedup(name, seen)
        if name not in name_map:
            name_map[name] = key
        # fps uses the DENSE frame durations; cells are PHYSICAL cell indices.
        rng = durations[frm:to + 1] or [100]
        fps = round(1000.0 / (sum(rng) / len(rng)), 1)
        loop = "false" if _is_oneshot(name) else "true"
        cells = [cell_of[d] if cell_of else d for d in range(frm, to + 1)]
        contiguous = cells == list(range(cells[0], cells[-1] + 1))
        if contiguous:
            spec = f"{{ from: {cells[0]}, to: {cells[-1]}, fps: {fps}, loop: {loop} }}"
        else:
            # nested-tag row padding broke the run -> list the exact cells
            spec = f"{{ frames: {cells}, fps: {fps}, loop: {loop} }}"
        lines.append(f"  {key}: {spec}")
    lines.append("aliases:")
    for canon, tag in ALIASES.items():
        target = name_map.get(tag, tag)
        resolved_alias_targets[canon] = target
        lines.append(f"  {canon}: {target}")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("aseprite")
    ap.add_argument("png")
    ap.add_argument("--out", default="assets/anim_manifest.yaml")
    ap.add_argument("--sheet-rel", default=None,
                    help="sheet path as written into the manifest (relative to assets/)")
    args = ap.parse_args(argv)

    frames, w, h, durations, tags = parse_aseprite(args.aseprite)
    from PIL import Image

    pw, _ph = Image.open(args.png).size
    cols = pw // w
    sheet_rel = args.sheet_rel or Path(args.png).name
    cell_of = dense_to_cell_map(args.png, w, h, cols, frames)
    aligned = cell_of != list(range(frames))
    yaml_text = build_yaml(sheet_rel, w, h, cols, durations, tags, cell_of)
    Path(args.out).write_text(yaml_text, encoding="utf-8")
    print(f"parsed {frames} frames, {len(tags)} tags, {cols} cols, "
          f"row-aligned={aligned} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
