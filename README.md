# Mochi 🐱 — an LLM-powered virtual desktop pet

**Mochi is an AI desktop pet / desktop mascot driven by a multimodal LLM.** A
transparent, always-on-top pixel cat that *sees your screen* — window layout,
cursor, the active app — and a single vision LLM (local **Ollama**, or Gemini /
OpenAI / Anthropic) decides, in character, how it reacts: walking to your cursor,
peeking at the YouTube you're watching, perching on a window, napping when you're
idle, sulking when poked. It remembers things between sessions, so its
personality grows.

Think Shimeji / desktop buddy, but its behaviour is decided by an LLM agent that
actually looks at your screen — a virtual pet with a brain.

> **Keywords:** LLM desktop pet · AI desktop pet · virtual pet LLM · multimodal
> desktop mascot · Ollama desktop pet · Shimeji-style AI companion · Python /
> PyQt6 · Windows.

It's built to **just run**: a built-in placeholder cat animates before you add
real art, and it falls back to simple rule-based behaviour if the LLM is
unreachable. Nothing hard-crashes.

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
  screenshot + a distilled scene + retrieved memories, and emits ONE structured
  *intent* (verb + target + emotion + a line to say).
- The **body** executes that intent as smooth 60fps motion. It re-resolves the
  target every frame, so the pet tracks a window even while you drag it.
- **Reflexes** (grab / throw / poke) bypass the LLM for instant response.

The LLM runs on its own thread — the render loop never blocks on it.

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
| **openai** | gpt-4o-style. Set `OPENAI_API_KEY`. |
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

## Adding the real art (Bow.Pixel "Cat 85+")

The placeholder is intentionally simple. To use the real sprite pack:

1. Download **Bow.Pixel "Cat 85+"** (free / pay-what-you-want):
   https://bowpixel.itch.io/meow-cat-85-animation
2. Unzip into `assets/` (it ships `.aseprite` + matching `.png` sheets, 3 colors:
   `Cat_Ginger`, `Cat_Grey`, `Cat_Grey_White`).
3. Auto-generate the manifest from the aseprite tags (maps all ~94 animations +
   their exact frame ranges):
   ```bash
   python -m deskpet.tools.aseprite_to_manifest \
     assets/Cat_85_Animations/Cat_Ginger.aseprite \
     assets/Cat_85_Animations/Cat_Ginger.png \
     --out assets/anim_manifest.yaml \
     --sheet-rel Cat_85_Animations/Cat_Ginger.png
   ```
   (Swap `Cat_Ginger` → `Cat_Grey` / `Cat_Grey_White` for a different colour.)
4. Relaunch — it auto-detects the art and replaces the placeholder.

The body's verbs/emotions map to real tags via the manifest's `aliases:` block
(idle→`Idle_1`, walk→`W_1`, run→`Run_1`, sleep→`Dream`, happy→`Dance`,
angry→`Aggress`, …). Edit those to taste; all 94 tags are also available by name.

---

## Configuration

See `config.example.toml` for every option (vision on/off, screenshot size,
memory, trigger cadence, persona, render scale/fps). Key env overrides:

```
DESKPET_LLM_PROVIDER  DESKPET_LLM_MODEL  DESKPET_LLM_BASE_URL  DESKPET_LLM_API_KEY
DESKPET_CONFIG (path to config.toml)     DESKPET_LOG_LEVEL (DEBUG|INFO|...)
```

---

## Interacting with it

- **Grab & throw** — drag the cat; release to fling. It falls, bounces, and
  settles on the taskbar.
- **Poke** — a quick click annoys it.
- **Pet it** — hover over it (it becomes grabbable; clicks elsewhere pass through
  to your apps as normal).

---

## Privacy

With the default local Ollama, **screenshots never leave your machine**. The app
never writes screenshots to disk, stores only a *hash* of the clipboard (to
detect change, never the content), and keeps the model's private `thought` at
debug-log level only. Cloud providers necessarily send the screenshot to that
provider — use local Ollama if that matters to you.

---

## Development

The brain, memory, parsing, triggers, sprite, and body logic are
cross-platform and unit-tested (they run on Linux/Mac); only the perception and
window layers require Windows.

```bash
pip install -r requirements.txt
pytest -q                                   # logic tests
python -m deskpet.brain.agent --once        # one brain decision (needs Ollama)
```

The pet's behaviour scope is **body-only** — it moves and animates *itself* and
*pretends* to interact. It never injects clicks/keystrokes into your other apps.
