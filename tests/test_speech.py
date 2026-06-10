"""The pet must talk like a cat, not an AI assistant. The speech guard is a
structural backstop independent of the prompt."""

from deskpet.brain.speech import sounds_like_assistant
from deskpet.brain.parse import coerce_intent


ASSISTANT_LINES = [
    "You just woke me up from terminal, I can do anything",
    "Wow look at that python code, what do you want me to do",
    "Twitch on one screen, code on another screen, want me to pester about it?",
    "How can I help you today?",
    "Let me know if you need anything!",
    "Happy to help with that.",
    "Would you like me to summarize it?",
    "Do you need any help?",
]

CAT_LINES = [
    "the warm spot is mine now. you may stand.",
    "feed me. this is not a request.",
    "pet me. i won't ask twice. ...i will. pet me.",
    "you smell like outside. explain.",
    "*knocks something off the desk* ...it fell. mysterious.",
    "you've been staring at that box for three hours. i've decided you're broken.",
    "i wasn't waiting for you. ...you're late, though.",
    "mrrp.",
]


def test_blocks_assistant_register():
    for line in ASSISTANT_LINES:
        assert sounds_like_assistant(line), line


def test_passes_cat_speech():
    for line in CAT_LINES:
        assert not sounds_like_assistant(line), line


def test_parse_nulls_assistant_say():
    # an assistant-sounding line is dropped to silence at parse time
    intent = coerce_intent({"verb": "idle", "emotion": "neutral",
                            "say": "What can I do for you?"})
    assert intent.say is None
    # a cat line survives
    intent = coerce_intent({"verb": "idle", "emotion": "neutral",
                            "say": "feed me. this is not a request."})
    assert intent.say == "feed me. this is not a request."


def test_guard_handles_empty():
    assert not sounds_like_assistant(None)
    assert not sounds_like_assistant("")
