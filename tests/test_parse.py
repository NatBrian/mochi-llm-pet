"""Parse robustness — the reliability core. Every fixture mimics a real-world
mangled model output across the five backends."""

from deskpet.brain.parse import extract_json, safe_parse, coerce_intent
from deskpet.types import Emotion, Verb


def test_clean_json():
    raw = '{"thought":"hi","verb":"walk_to","target":"cursor","emotion":"curious","say":null,"confidence":0.8}'
    it = safe_parse(raw)
    assert it.verb is Verb.WALK_TO
    assert it.target == "cursor"
    assert it.emotion is Emotion.CURIOUS
    assert it.say is None
    assert abs(it.confidence - 0.8) < 1e-6


def test_gemma_think_tokens():
    raw = ('<think>The user is coding hard. I should be a menace.</think>\n'
           '{"thought":"menace time","verb":"chase","emotion":"mischievous","say":"meow","confidence":0.6}')
    it = safe_parse(raw)
    assert it.verb is Verb.CHASE
    assert it.emotion is Emotion.MISCHIEVOUS
    assert it.say == "meow"


def test_markdown_fenced():
    raw = "Sure! Here you go:\n```json\n{\"verb\":\"nap\",\"emotion\":\"sleepy\",\"thought\":\"tired\",\"say\":null,\"confidence\":0.9}\n```\nHope that helps!"
    it = safe_parse(raw)
    assert it.verb is Verb.NAP
    assert it.emotion is Emotion.SLEEPY


def test_trailing_prose():
    raw = '{"verb":"sit_on","target":"active_window","edge":"top","emotion":"bored","thought":"x","say":null,"confidence":0.5} -- I will sit there now.'
    it = safe_parse(raw)
    assert it.verb is Verb.SIT_ON
    assert it.edge == "top"


def test_near_miss_verb_and_emotion():
    # "walk" should snap to "walk_to"; "happy!" stays happy after lower/strip-ish
    raw = '{"verb":"walk","emotion":"happy","thought":"go","say":null,"confidence":0.7}'
    it = safe_parse(raw)
    assert it.verb is Verb.WALK_TO
    assert it.emotion is Emotion.HAPPY


def test_invalid_verb_defaults_idle():
    raw = '{"verb":"teleport","emotion":"???","thought":"x","say":null,"confidence":2.0}'
    it = safe_parse(raw)
    assert it.verb is Verb.IDLE
    assert it.emotion is Emotion.NEUTRAL
    assert it.confidence == 1.0  # clamped


def test_explicit_point():
    raw = '{"verb":"walk_to","point":{"x":1200,"y":340},"emotion":"curious","thought":"x","say":null,"confidence":0.5}'
    it = safe_parse(raw)
    assert it.point is not None
    assert (it.point.x, it.point.y) == (1200.0, 340.0)


def test_garbage_is_idle_not_crash():
    for raw in ("", "not json at all", "}{", "<think>only thinking</think>", "null"):
        it = safe_parse(raw)
        assert it.verb is Verb.IDLE


def test_dict_input_passthrough():
    it = safe_parse({"verb": "pounce", "emotion": "excited", "thought": "", "say": None, "confidence": 0.5})
    assert it.verb is Verb.POUNCE


def test_say_null_string_normalized():
    it = coerce_intent({"verb": "say", "say": "null", "emotion": "neutral", "confidence": 0.5})
    assert it.say is None


def test_extract_json_raises_on_garbage():
    import pytest
    with pytest.raises(Exception):
        extract_json("absolutely no braces here")
