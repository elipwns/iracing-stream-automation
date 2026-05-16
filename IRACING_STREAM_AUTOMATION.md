# iRacing Stream Automation

Automated OBS scene management, post-race AI summaries, and ambient lighting control for iRacing broadcasts — built for endurance team events streamed to YouTube and Twitch.

---

## What This Does

- **Detects race session state** in real time via the iRacing SDK (racing, caution, cooldown, etc.)
- **Auto-switches OBS scenes** based on session state via OBS WebSocket
- **Pulls race results** from the iRacing Data API after the session ends
- **Generates an AI narrative summary** of the race using the Claude API
- **Updates an HTML overlay** served as an OBS browser source (end screen, standings, etc.)
- **Triggers WLED lighting events** on a DIG-QUAD-V3 controller for things like caution flags, race end, podium finish

---

## Architecture Overview

```
iRacing (SDK / telemetry)
        │
        ▼
  session_monitor.py          ← polls iRacing shared memory
        │
        ├──► OBS WebSocket    ← switches scenes (racing / caution / end screen)
        │
        ├──► WLED HTTP API    ← changes room lighting color/effect
        │
        └──► results_pipeline.py (on race end)
                  │
                  ├──► iRacing Data API    ← fetches lap data, positions, incidents
                  │
                  ├──► Claude API          ← generates narrative race summary
                  │
                  └──► overlay/index.html  ← written to disk, OBS browser source refreshes
```

---

## Prerequisites

### Accounts & Credentials
- iRacing account (for Data API authentication)
- Anthropic API key — [console.anthropic.com](https://console.anthropic.com)
- OBS 28+ (WebSocket server is built in)

### Hardware
- DIG-QUAD-V3 running WLED firmware — note your device's local IP address

### Python
- Python 3.10+
- pip packages (see `requirements.txt`)

---

## Project Structure

```
iracing-stream-automation/
│
├── README.md
├── requirements.txt
├── .env                        ← credentials, never commit this
│
├── session_monitor.py          ← main loop: watches iRacing state, drives everything
├── results_pipeline.py         ← fetches results + generates AI summary post-race
├── obs_controller.py           ← OBS WebSocket wrapper
├── wled_controller.py          ← WLED HTTP API wrapper
├── iracing_api.py              ← iRacing Data API wrapper
├── claude_summary.py           ← Claude API call for race narrative
│
└── overlay/
    ├── index.html              ← end screen overlay (OBS browser source)
    ├── style.css
    └── results.json            ← written by results_pipeline, read by overlay JS
```

---

## Setup

### 1. Clone and install dependencies

```bash
git clone https://github.com/yourname/iracing-stream-automation.git
cd iracing-stream-automation
pip install -r requirements.txt
```

### 2. Create your `.env` file

```env
# iRacing credentials
IRACING_USERNAME=your@email.com
IRACING_PASSWORD=yourpassword

# Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# OBS WebSocket (Settings > WebSocket Server in OBS)
OBS_HOST=localhost
OBS_PORT=4455
OBS_PASSWORD=yourpassword

# WLED - DIG-QUAD-V3 local IP
WLED_IP=192.168.1.xxx
```

### 3. Enable OBS WebSocket Server

In OBS: **Tools → WebSocket Server Settings → Enable WebSocket Server**

Note the port and set a password. Add both to `.env`.

### 4. Point OBS browser source at the overlay

In OBS, add a **Browser Source** to your end screen scene:
- URL: `file:///absolute/path/to/overlay/index.html`
- Width/Height: match your canvas (e.g. 1920×1080)
- Check **"Refresh browser when scene becomes active"**

### 5. Configure your OBS scene names

Edit the scene name constants at the top of `session_monitor.py` to match exactly what you have in OBS:

```python
SCENE_MENU       = "Menu / Lobby"
SCENE_RACING     = "In Car - Racing"
SCENE_REPLAY     = "Replay"
SCENE_END_SCREEN = "End Screen"
SCENE_STARTING   = "Starting Soon"
```

---

## Requirements.txt

```
iracingdataapi
irsdk
obs-websocket-py
anthropic
python-dotenv
requests
```

---

## Key Modules — What Claude Code Should Build

### `session_monitor.py`
The main loop. Runs continuously while OBS is open.

**Responsibilities:**
- Connect to iRacing via `irsdk`
- Poll `ir['SessionState']` on a ~1s interval
- On state change, call `obs_controller.switch_scene()` and `wled_controller.set_event()`
- On `SessionState == 6` (cooldown / checkered), fire `results_pipeline.run()`
- Handle iRacing not running gracefully (show "Starting Soon" scene or do nothing)

**iRacing session states to handle:**

| Value | State | Suggested OBS Scene | WLED |
|-------|-------|---------------------|------|
| 0 | Invalid / not in session | Menu scene | Default |
| 1 | Get in car | Menu scene | Default |
| 2 | Warmup | Racing scene | Warm white |
| 3 | Parade laps | Racing scene | Warm white |
| 4 | Racing | Racing scene | Racing color |
| 5 | Checkered | Racing scene | Podium effect |
| 6 | Cooldown | End Screen | Celebration |

Also watch `ir['SessionFlags']` for caution flag (`0x0002`) to trigger a yellow lighting event mid-race.

---

### `obs_controller.py`
Thin wrapper around `obs-websocket-py`.

**Methods to implement:**
- `connect()` — reads from `.env`, establishes WebSocket connection
- `switch_scene(scene_name: str)` — calls `SetCurrentProgramScene`
- `get_current_scene()` — returns active scene name
- `set_text_source(source_name: str, text: str)` — updates a text GDI+ source (useful for live lap count, position, etc.)

---

### `wled_controller.py`
Controls the DIG-QUAD-V3 via WLED's JSON HTTP API.

**WLED JSON API endpoint:** `http://{WLED_IP}/json/state`

**Methods to implement:**
- `set_color(r, g, b)` — solid color
- `set_effect(effect_id: int, color=None)` — named effect with optional color
- `set_brightness(level: int)` — 0–255
- `set_event(event: str)` — maps event names to presets

**Suggested event → lighting map:**

| Event | Color / Effect |
|-------|---------------|
| `"racing"` | Solid cool white or team color |
| `"caution"` | Pulsing yellow |
| `"race_end"` | Solid green fade |
| `"podium"` | Confetti / rainbow effect |
| `"incident"` | Brief red flash |
| `"idle"` | Dim ambient, whatever default |

WLED built-in effect IDs are documented at [kno.wled.ge/features/effects](https://kno.wled.ge/features/effects/) — notable ones: `0` = solid, `9` = bpm, `11` = breathe, `65` = fire, `78` = confetti.

**Example WLED API call:**
```python
import requests

def set_color(ip, r, g, b):
    payload = {"seg": [{"col": [[r, g, b]]}], "on": True}
    requests.post(f"http://{ip}/json/state", json=payload)
```

---

### `iracing_api.py`
Wrapper around the iRacing Data API.

**Use the `iracingdataapi` library** — it handles authentication and cookie management.

**Methods to implement:**
- `get_last_session_results(cust_id)` — fetches the most recent subsession result for a given driver
- `get_lap_data(subsession_id, cust_id)` — lap times for a driver in a session
- `get_session_results(subsession_id)` — full finishing order, incidents, laps completed for all drivers

**Data you want to extract for the summary:**
- Each driver: start position, finish position, laps completed, incidents, best lap time, average lap time
- Session: total laps, race duration, caution laps, lead changes
- Your car specifically: gap to winner, positions gained/lost, pit stop count

---

### `claude_summary.py`
Calls the Claude API to generate a narrative race summary.

**Model:** `claude-sonnet-4-20250514`

**Approach:** Pass structured race data as JSON in the prompt, ask for a broadcast-style paragraph.

**Prompt template to build around:**
```
You are a motorsport broadcast analyst writing a post-race summary for an iRacing 
endurance stream. Write 3-4 sentences in a professional but engaging tone.
Include: starting position, finishing position, key moments, best lap, incident count.
Do not make up details not present in the data provided.

Race data:
{json.dumps(race_data, indent=2)}
```

**Output** goes into `overlay/results.json` alongside the raw stats so the HTML overlay can render both the narrative and the data table.

---

### `overlay/index.html`
A locally served HTML page used as an OBS browser source on the end screen scene.

**What it should show:**
- Race summary narrative (from Claude)
- Results table: position, driver, car number, laps, incidents, best lap
- Your car highlighted in the table
- Upcoming event info (can be a static config section)
- Styled to match your stream aesthetic

**How it updates:**
- On page load (and on refresh), it `fetch()`es `results.json` from the same directory
- OBS browser source is set to refresh when the scene becomes active — so switching to the end screen scene automatically pulls the latest results

---

## Future Ideas / Stretch Goals

- **Discord webhook** — post the race summary automatically to your team Discord after the race ends
- **Streamlabs/StreamElements alerts** — trigger a custom alert via their APIs on podium finish
- **Multi-driver support** — if multiple team members race in the same session, show a combined team result card
- **Highlight reel trigger** — log timestamps when incidents or lead changes occur via SDK, export a cut list for post-race editing
- **Twitch/YouTube chat bot** — post the summary directly to chat at race end
- **Pre-race countdown scene** — auto-switch to "starting soon" X minutes before a scheduled session using the iRacing calendar API

---

## Notes for Claude Code

- Start with `session_monitor.py` and get OBS scene switching working first — this is the most immediately useful piece and validates the WebSocket connection before touching the API work
- Use `python-dotenv` and `load_dotenv()` everywhere — no hardcoded credentials
- Add a `--dry-run` flag to `session_monitor.py` that prints actions instead of executing them, useful for testing without OBS or iRacing open
- The iRacing SDK requires iRacing to be running to connect — handle the `None` state gracefully so the script doesn't crash when you're not in sim
- WLED calls should be fire-and-forget with a short timeout — don't let a network hiccup on the lighting controller stall the main loop
- The overlay HTML should work even if `results.json` doesn't exist yet (first run, race not completed) — show a placeholder state
