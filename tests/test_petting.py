"""Petting = hover-stroke detection. Needs Qt (offscreen); skipped if unavailable."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from deskpet.body.body import Body  # noqa: E402
from deskpet.sprite.player import AnimationPlayer  # noqa: E402
from deskpet.types import Vec2  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _window():
    from deskpet.ui.pet_window import PetWindow
    b = Body(start_pos=Vec2(500, 500))
    b.attach_player(AnimationPlayer({}))
    pets = []
    b.on_interaction = lambda k: pets.append(k)
    return PetWindow(b, b.player, scale=3), pets


def test_back_and_forth_pets():
    w, pets = _window()
    for x in [500, 490, 500, 490, 500, 490, 500]:   # oscillation over the cat
        w._detect_stroke(x)
    assert "pet" in pets


def test_one_directional_motion_does_not_pet():
    w, pets = _window()
    for x in [100, 150, 200, 250, 300, 350, 400]:   # straight sweep, not a stroke
        w._detect_stroke(x)
    assert "pet" not in pets
