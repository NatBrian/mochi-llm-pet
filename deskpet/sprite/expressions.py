"""Animation richness layer — pure data + helpers, no Qt, no heavy imports.

Two jobs, both about USING the whole sprite pack instead of one tag per state:

1. POOLS: a canonical locomotion/rest state -> several interchangeable tag
   variants. The body picks one at random on state-entry, so idle/walk/run/sit
   stop looking identical every loop (and every boot).

2. EMOTE_CATALOG: a small, friendly, CLOSED vocabulary of expressive
   animations the LLM may request via the Intent.emote field. Each token maps to
   one or more real sprite tags (random pick among those present). The token —
   not the raw tag name — is what the model sees, so the prompt stays clean and
   validation stays a closed set (unknown token -> ignored).

Together these route ALL ~93 Bow.Pixel "Cat 85+" tags to something the pet can
actually show. The body resolves tokens/states against the tags ACTUALLY loaded,
so other (smaller) packs degrade gracefully — a missing tag just drops out.
"""

from __future__ import annotations

from typing import Iterable, Optional

# --------------------------------------------------------------------------- #
# Locomotion / rest variant pools — chosen automatically by the body.
# Keyed by the canonical state names the executors/emotions emit.
# --------------------------------------------------------------------------- #
POOLS: dict[str, list[str]] = {
    "idle": ["Idle_1", "Idle_2", "Idle_3", "Idle_4", "Idle_5", "Idle_two_1"],
    "walk": ["W_1", "W_2", "W_two"],
    "run": ["Run_1", "Run_2", "Run_3", "Run_4", "Run_4_2"],
    "sit": ["Sit_1", "Sit_2"],
    "lie": ["Rest_1", "Rest_2", "Rset_3", "Rest_4"],
    "sleep": ["Dream"],
    "look": ["Idle_Tilt_1", "Idle_Tilt_2"],
    "curious": ["Idle_Tilt_1", "Idle_Tilt_2", "Sit_Tilt_1"],
    "bored": ["Idle_2", "Idle_3", "Rest_1"],
    "watch": ["Sit_1", "Sit_2"],
}

# --------------------------------------------------------------------------- #
# Emote catalog — the LLM-facing expressive vocabulary. token -> (tags, blurb).
# `tags` are tried in order, filtered to what's loaded, then random-picked.
# Grouped only for the prompt; functionally flat.
# --------------------------------------------------------------------------- #
EMOTE_CATALOG: dict[str, tuple[list[str], str]] = {
    # --- everyday / cute --------------------------------------------------- #
    "nod":         (["Idle_Yes", "Sit_Yes"], "nod yes, agree, approve"),
    "shake_head":  (["Idle_No", "Sit_No"], "shake head no, disagree, refuse"),
    "tilt_head":   (["Idle_Tilt_1", "Idle_Tilt_2", "Sit_Tilt_1", "Sit_Tilt_2"],
                    "curious head-tilt, confused, intrigued"),
    "lift_paw":    (["Idle_Lift_1", "Idle_Lift_2", "Sit_Lift_1", "Sit_Lift_2"],
                    "raise a paw — wave, ask, high-five, point"),
    "stretch":     (["Idle_Lift_1", "Sit_Lift_1"], "stretch / limber up"),
    "scratch":     (["Scratching_1", "Scratchng_2_85", "Scratching_Start", "Scratching_End"],
                    "scratch / groom itself"),
    "back_away":   (["W_Back"], "back away / retreat warily, walk backward"),
    "eat":         (["Idle_Eat", "Idle_Eat_2", "Sit_Eat"], "nibble / snack / eat"),
    "dig":         (["Dig_1", "Dig_2", "Dig_3"], "dig / paw at the ground"),
    "knead":       (["Pushes"], "knead / make biscuits / push something"),
    "pull":        (["Pull_Back"], "tug / pull something back"),
    "dance":       (["Dance"], "happy little dance, celebrate, hype"),
    "sit_down":    (["SIt_Down"], "sit down (one-shot)"),
    "stand_up":    (["Stand_Up"], "stand up (one-shot)"),
    # --- playful drama (all pretend — never touches other apps) ------------- #
    "pounce":      (["Attack_1", "Attack_2", "Attack_3", "Attack_4"],
                    "playful pounce / swipe"),
    "hiss":        (["Aggress"], "hiss / puff up / mock-angry"),
    "flinch":      (["Dmg_1", "Dmg_2", "Dmg_3", "Dmg_4", "Dmg_5"],
                    "flinch / startled / dramatic 'ow'"),
    "sneak":       (["Sneak_up_Idle", "SU_move"], "crouch and sneak, stalk"),
    "crouch":      (["Prepare", "Prepare_2", "Start"], "crouch, wind up, get ready"),
    "jump":        (["Jump_1", "Jump", "Jump_2"], "a big jump / leap"),
    "skid":        (["Brake", "Stop", "Stop_1", "Stop_2", "Stop_3"],
                    "skid / screech to a stop"),
    "spin":        (["FToT", "TToF", "TTOF_2"], "quick turn / spin around"),
    # --- big bits (use sparingly) ------------------------------------------ #
    "play_dead":   (["Die_1", "Die_2", "Die_3", "Die_4", "Die_5", "Die_6",
                     "Die_End_1", "Die_End_2"],
                    "flop over dramatically, play dead, 'I'm dead'"),
    "poof":        (["Spawn_1", "Spawn_2"], "magic appear / reappear / ta-da"),
    "climb":       (["Climb_1", "Climb_2", "Climb_3"], "climb (best near a window edge)"),
    "climb_jump":  (["Climb_Jump_1", "Climb_Jump_2"], "leap off a climb"),
    "poop":        (["Pooping"], "gag: do its business (comedic, rare)"),
}

# Tokens that are momentary (play once, then the pet returns to normal). Anything
# not listed loops until the next decision.
ONESHOT_EMOTES: frozenset[str] = frozenset({
    "nod", "shake_head", "lift_paw", "stretch", "sit_down", "stand_up", "pounce",
    "flinch", "crouch", "jump", "skid", "spin", "play_dead", "poof",
    "climb_jump", "poop", "dig",
})

EMOTE_TOKENS: frozenset[str] = frozenset(EMOTE_CATALOG.keys())


def _first_present(tags: Iterable[str], available: set[str], rng) -> Optional[str]:
    present = [t for t in tags if t in available]
    if not present:
        return None
    return present[rng.randrange(len(present))]


def pick_variant(state: str, available: set[str], rng) -> Optional[str]:
    """A random loaded tag for a canonical locomotion/rest state, or None if the
    state has no pool / none of its variants are loaded."""
    pool = POOLS.get(state)
    if not pool:
        return None
    return _first_present(pool, available, rng)


def resolve_emote(token: Optional[str], available: set[str], rng) -> Optional[str]:
    """Map an emote token to a concrete loaded tag (random among variants), or
    None if the token is unknown / none of its tags are present."""
    if not token:
        return None
    entry = EMOTE_CATALOG.get(token)
    if not entry:
        return None
    return _first_present(entry[0], available, rng)


def is_oneshot_emote(token: Optional[str]) -> bool:
    return bool(token) and token in ONESHOT_EMOTES


def prompt_menu() -> str:
    """A compact, grouped menu of emote tokens for the system prompt."""
    lines = []
    for token, (_tags, desc) in EMOTE_CATALOG.items():
        lines.append(f"  {token} — {desc}")
    return "\n".join(lines)
