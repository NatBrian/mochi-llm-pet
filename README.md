# Mochi 🐱 — an LLM-powered virtual desktop pet

**Mochi is an AI desktop pet / desktop mascot driven by a multimodal LLM.** A
transparent, always-on-top pixel cat that *sees your whole desktop* — window
layout, cursor, the active app — and a single vision LLM (local **Ollama**, or
Gemini / OpenAI / Anthropic / any OpenAI-compatible endpoint) decides, in
character, how it reacts: stalking your cursor, pouncing, perching on a window,
napping when you're idle, sulking when poked, demanding pets. It talks like a
self-absorbed cat (not an assistant), learns your habits, and its energy / mood /
bond persist across sessions — so its personality grows.

Think Shimeji / desktop buddy, but its behaviour is decided by an LLM agent that
actually looks at your screen — a virtual pet with a brain.

> **Keywords:** LLM desktop pet · AI desktop pet · virtual pet LLM · multimodal
> desktop mascot · Ollama desktop pet · Shimeji-style AI companion · Python /
> PyQt6 · Windows.

It's built to **just run**: a built-in placeholder cat animates before you add
real art, and it falls back to simple rule-based behaviour if the LLM is
unreachable. Nothing hard-crashes.

---

## What the cat does

- **Sees your desktop** — a screenshot of the whole screen plus a distilled scene
  (windows, cursor, active app, idle time) goes to the LLM each decision.
- **Moves with intent** — walks to your cursor, chases/pounces the "little arrow",
  perches on a window edge, naps on the taskbar, hides, follows you around.
- **Talks like a cat** — short, playful, self-centred lines (demands, judgement,
  drama) — never an assistant. Mostly silent, occasionally vocal.
- **Expresses itself** — picks from **27 expressive animations** (nod, sneak,
  knead, dig, dance, play-dead, tilt-head…) to match its mood.
- **Remembers** — learns which apps you live in, recalls being petted / poked /
  thrown, levels up. Energy / mood / bond survive restarts and feed its behaviour.
- **Reacts to touch** — pet it, poke it, grab-and-throw it (see below).
- **Obeys physics** — thrown, it falls, bounces, and lands on a **window top** or
  the taskbar.

The pet's scope is **body-only and safe**: it moves and animates *itself* and
*pretends* to interact. It never injects clicks or keystrokes into your apps.

---

## How it works (three clocks)

```
Sensors (ms, Win32)  ─►  WorldState  ─►  [trigger]  ─►  ONE multimodal LLM
                              ▲                               │ JSON intent
        Memory (SQLite) ──────┘                               ▼
                                          Body + Render (60fps) executes it
```

- **Sensors** read window rects / cursor / foreground app every few ms.
- The **brain** (an LLM) wakes on a heartbeat or a real change, looks at a
  screenshot + a distilled scene + recalled memories + its own recent actions,
  and emits ONE structured *intent*.
- The **body** executes that intent as smooth 60fps motion + animation. It
  re-resolves the target every frame, so the pet tracks a window even while you
  drag it.
- **Reflexes** (pet / poke / grab / throw) bypass the LLM for instant response.

The LLM runs on its own thread — the render loop never blocks on it.

### The LLM has exactly one job: control the pet

To keep a small local model reliable, the model's output is deliberately tiny —
it only decides *behaviour*. Everything mechanical (pixel math, memory,
persistence) is handled by code, not the model.

| field | meaning |
|-------|---------|
| `verb` | the action — `walk_to, follow_cursor, chase, sit_on, watch, nap, nudge, pounce, look_at, hide, idle, say, emotion` |
| `target` | *where*, by name — `cursor`, `active_window`, `window:Chrome`, `taskbar`, … (code resolves to pixels) |
| `edge` | for `sit_on`: which side of the window to perch on |
| `emotion` | its mood — `happy, curious, annoyed, sleepy, mischievous, …` |
| `emote` | optional expressive animation (one of 27), to match the moment |
| `say` | a short spoken line, or null (silence is normal) |
| `thought` | a private one-liner (logged, never shown) |

That's it — no "remember", no confidence, no coordinates. The model just *is* the
cat.

---

## Quick start (Windows)

```bat
python -m pip install -r requirements.txt
python run.py
```

That's it. With no art and no LLM configured it runs the placeholder cat with
rule-based behaviour. To give it a real brain, point it at an LLM (below).

---

## Choosing a brain (LLM provider)

All five providers are supported and switchable. Configure via `config.toml`
(copy `config.example.toml`) or environment variables (env always wins).

| Provider | Notes |
|----------|-------|
| **ollama** (default) | Local, free, **private** — screenshots never leave your machine. Model `gemma4:12b` (multimodal). |
| **gemini** | Cloud multimodal. Set `GEMINI_API_KEY`. |
| **openai** | gpt-4o / gpt-5-style. Set `OPENAI_API_KEY`. |
| **anthropic** | Claude. Set `ANTHROPIC_API_KEY`. |
| **openai_compat** | Any OpenAI-compatible endpoint (vLLM / LM Studio / proxy). Set `base_url`. |

Examples:

```bat
:: cloud (Gemini)
set DESKPET_LLM_PROVIDER=gemini
set GEMINI_API_KEY=...your key...
python run.py
```

### Using a remote Ollama (e.g. a Linux GPU box)

If Ollama runs on **another machine**, start it there bound to all interfaces:

```bash
OLLAMA_HOST=0.0.0.0:11434 ollama serve     # and allow port 11434 through the firewall
```

then on the Windows pet:

```bat
set DESKPET_LLM_PROVIDER=ollama
set DESKPET_LLM_MODEL=gemma4:12b
set DESKPET_LLM_BASE_URL=http://192.168.1.50:11434   :: <- the box's IP
python run.py
```

If it can't reach the brain, the pet prints a friendly note and runs rule-based
until the brain is back.

---

## Interacting with it

Three mouse gestures over the cat (clicks elsewhere pass through to your apps as
normal):

- **Pet** — hover over the cat and move the cursor *back and forth* across it. It
  loves it: gets affectionate, kneads, sometimes purrs, and your **bond grows
  fastest**.
- **Poke** — a quick click (tap, no drag). Annoys it — it flinches and grumbles.
- **Grab & throw** — click-drag the cat, then release to fling. It falls, bounces,
  and lands on a **window top** (if it's over one) or the taskbar. Window tops are
  one-way platforms: it lands on them but passes through their sides.

The cat remembers being petted / poked / thrown, and these touches nudge its mood
and bond.

---

## Animations — the whole pack, LLM-driven

Mochi uses the **Bow.Pixel "Cat 85+"** sprite pack — **all ~92 animations** are
wired up, via four routes:

- **Verbs → motion** (walk, run, sit, sleep, pounce, nudge, fall…)
- **Emotions → resting pose** (content, grumpy, sleepy, curious…)
- **27 LLM-pickable emotes** → the expressive/dramatic ones (nod, shake-head,
  tilt-head, scratch, dig, eat, knead, dance, sneak, hiss, flinch, play-dead,
  climb, …)
- **Random variant pools** → idle/walk/run/sit rotate through their variants so it
  never looks looped or identical between runs.

### Adding the real art

The placeholder cat is intentionally simple. To use the real pack:

1. Download **Bow.Pixel "Cat 85+"** (free / pay-what-you-want):
   https://bowpixel.itch.io/meow-cat-85-animation
2. Unzip into `assets/` (it ships `.aseprite` + matching `.png` sheets, 3 colours:
   `Cat_Ginger`, `Cat_Grey`, `Cat_Grey_White`).
3. Auto-generate the manifest from the aseprite tags (maps every animation to its
   exact frames in the sheet, handling the pack's row-aligned export):
   ```bash
   python -m deskpet.tools.aseprite_to_manifest \
     assets/Cat_85_Animations/Cat_Ginger.aseprite \
     assets/Cat_85_Animations/Cat_Ginger.png \
     --out assets/anim_manifest.yaml \
     --sheet-rel Cat_85_Animations/Cat_Ginger.png
   ```
   (Swap `Cat_Ginger` → `Cat_Grey` / `Cat_Grey_White` for a different colour.)
4. Relaunch — it auto-detects the art and replaces the placeholder.

> The art is licensed for use but **not for redistribution**, so it's gitignored —
> each user downloads it themselves. Only the generated `anim_manifest.yaml` is
> tracked.

---

## Memory & personality (code-driven, persistent)

State lives in `deskpet.db` (SQLite). The **LLM never decides what to remember** —
code does, so the model stays focused on behaviour. Two stores:

- **`memories`** — two kinds, written automatically:
  - **Facts** about you — which apps you live in, being petted / poked / thrown,
    level-ups.
  - **Episodes** — the cat's own recollections of notable moments (what it saw +
    did + felt, e.g. *"those kibble pictures on screen are torture; I must be
    fed"*), captured on emotional turns and throttled so they don't flood.

  Both are retrieved by relevance (current app + recency) back into the prompt, so
  the cat acts on what it knows *and* can call back to what it's lived through
  ("you teased me with food earlier — I haven't forgotten").
- **`pet_state`** — energy, mood, bond, level, xp. Energy drains awake and regens
  while napping; mood eases to neutral; bond/xp climb with positive interaction.
  Saved periodically (survives a hard kill) and reloaded on start — so the
  personality drifts and persists across sessions.

---

## Watching it think (logs)

Run with `INFO` (default) for a readable per-decision block:

```
┌─ wake [heartbeat:changed]  via LLM
│  sees: app=Code.exe[code]  cursor=(500,400) NEAR-pet  idle=8s  energy=0.84(ok) mood=content
│  does: chase → cursor   feeling mischievous  emote=pounce
│  💬 "Tiny arrow, prepare for ambush."
│  🧠 the cursor skitters; ideal practice for my pounce
└─
```

`via LLM` vs `via instinct (rule-based)` tells you whether the brain is actually
connected. Run with **DEBUG** to also see the exact scene sent, the raw LLM JSON,
the screenshot size, and recalled memories:

```bat
:: PowerShell
$env:DESKPET_LOG_LEVEL="DEBUG"; python .\run.py
:: cmd
set DESKPET_LOG_LEVEL=DEBUG
python run.py
```

---

## Configuration

See `config.example.toml` for every option. Highlights:

- `[vision]` — `enabled`, `mode` (`monitor` = whole desktop, the default; or
  `active_window`), `max_edge` (downscale for token cost).
- `[llm]` — provider / model / base_url / api_key / temperature.
- `[memory]`, `[triggers]`, `[perception]`, `[persona]`, `[render]`.

Key env overrides (env always wins):

```
DESKPET_LLM_PROVIDER  DESKPET_LLM_MODEL  DESKPET_LLM_BASE_URL  DESKPET_LLM_API_KEY
DESKPET_CONFIG (path to config.toml)     DESKPET_LOG_LEVEL (DEBUG|INFO|...)
```

---

## Privacy

With the default local Ollama, **screenshots never leave your machine**. The app
never writes screenshots to disk, stores only a *hash* of the clipboard (to detect
change, never the content), and the model's private `thought` is shown only in
*your* local logs. Cloud providers necessarily send the screenshot to that
provider — use local Ollama if that matters to you.

---

## Development

The brain, memory, parsing, triggers, sprite, body, and physics logic are
cross-platform and unit-tested (they run on Linux/Mac); only the perception and
window layers require Windows.

```bash
pip install -r requirements.txt
pytest -q                                   # logic tests
python -m deskpet.brain.agent --once        # one brain decision (needs an LLM)
```

The pet's behaviour scope is **body-only** — it moves and animates *itself* and
*pretends* to interact. It never injects clicks/keystrokes into your other apps.
