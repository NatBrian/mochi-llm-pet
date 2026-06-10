"""Screenshot capture for the vision channel.

Grabs the active monitor (or crops to the foreground window), downscales the
longest edge to bound token cost, and encodes to PNG/JPEG bytes. Returns None on
any failure (headless dev box, capture error) so the brain falls back to
text-only via scene_text — the pet keeps working.
"""

from __future__ import annotations

import io
from typing import Optional

from ..config import VisionConfig
from ..log import get
from ..types import WorldSnapshot

log = get("screenshot")


def capture(cfg: VisionConfig, world: Optional[WorldSnapshot] = None) -> Optional[bytes]:
    if not cfg.enabled:
        return None
    try:
        import mss  # noqa: WPS433
        from PIL import Image
    except Exception as e:  # noqa: BLE001
        log.debug("screenshot deps unavailable: %s", e)
        return None

    try:
        with mss.mss() as sct:
            monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            region = monitor
            if cfg.mode == "active_window" and world and world.foreground:
                r = world.foreground.rect
                region = {
                    "left": int(r.left), "top": int(r.top),
                    "width": max(1, int(r.width)), "height": max(1, int(r.height)),
                }
                # clamp to the monitor bounds
                region = _clamp(region, monitor)
            raw = sct.grab(region)
            img = Image.frombytes("RGB", raw.size, raw.rgb)
    except Exception as e:  # noqa: BLE001
        log.debug("screenshot capture failed: %s", e)
        return None

    img = _downscale(img, cfg.max_edge)
    buf = io.BytesIO()
    if cfg.format == "jpeg":
        img.save(buf, format="JPEG", quality=70)
    else:
        img.save(buf, format="PNG")
    return buf.getvalue()


def _clamp(region: dict, monitor: dict) -> dict:
    left = max(monitor["left"], region["left"])
    top = max(monitor["top"], region["top"])
    right = min(monitor["left"] + monitor["width"], region["left"] + region["width"])
    bottom = min(monitor["top"] + monitor["height"], region["top"] + region["height"])
    return {"left": left, "top": top,
            "width": max(1, right - left), "height": max(1, bottom - top)}


def _downscale(img, max_edge: int):
    w, h = img.size
    longest = max(w, h)
    if longest <= max_edge:
        return img
    scale = max_edge / longest
    from PIL import Image

    return img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
