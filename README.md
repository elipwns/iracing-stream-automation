# iRacing Stream Automation

Automates OBS scene switching, WLED ambient lighting, real-time telemetry overlays, and AI-generated post-race summaries — all driven by live iRacing session data.

## What It Does

| Trigger | OBS Scene | WLED |
|---|---|---|
| iRacing not running | Starting Soon | Idle (dim white) |
| iRacing at menu | Menu / Lobby | Idle |
| In session, not on track | Pit / Garage | Idle |
| On track, racing | In Car - Racing | Idle (flags override) |
| Checkered flag, still on track | In Car - Racing | Slow white breathe |
| Race finished, off track | End Screen | Solid green |
| Green / white / caution / black / meatball flag | — | Corresponding colour |

After each race: Claude writes a post-race narrative and the end screen overlay populates with results. At stream end, type `end` for a full-session recap overlay.

---

## Prerequisites

- Windows PC (iRacing SDK requires Windows shared memory)
- [OBS Studio](https://obsproject.com/) 28+ with WebSocket server enabled
- [WLED](https://kno.wled.ge/) device on your local network
- Python 3.11+ installed on Windows
- An [Anthropic API key](https://console.anthropic.com/)

---

## First-Time Setup

### 1. Create your `.env` file

Copy the template and fill in your values:

```powershell
copy .env.example .env
```

Open `.env` and set:

```
IRACING_CUST_ID=123456          # your iRacing customer ID (found on your profile page)
ANTHROPIC_API_KEY=sk-ant-...    # from console.anthropic.com
OBS_HOST=localhost
OBS_PORT=4455
OBS_PASSWORD=yourpassword       # set in OBS > Tools > WebSocket Server Settings
WLED_IP=192.168.1.xxx           # your WLED device's local IP
```

> `IRACING_USERNAME` and `IRACING_PASSWORD` are in the file but currently unused — iRacing changed their API auth in Dec 2025. See [Known Limitations](#known-limitations).

### 2. Create the Windows virtual environment

```powershell
cd C:\Users\elikl\Documents\GitHub\iracing-stream-automation
python -m venv .venv-win
.venv-win\Scripts\pip install -r requirements.txt
```

### 3. Configure OBS

Create these scenes (names must match exactly):

| Scene Name |
|---|
| `Starting Soon` |
| `Menu / Lobby` |
| `Pit / Garage` |
| `In Car - Racing` |
| `End Screen` |
| `Stream Outro` |

Enable the WebSocket server: **Tools > WebSocket Server Settings** — check Enable, set port and password to match your `.env`.

### 4. Add browser source overlays

In OBS, add a **Browser Source** to each scene:

| Scene | Source | URL | Size |
|---|---|---|---|
| End Screen | Race Results | `file:///C:/Users/elikl/Documents/GitHub/iracing-stream-automation/overlay/index.html` | 1920×1080 |
| End Screen / Outro | Session Recap | `file:///C:/Users/elikl/Documents/GitHub/iracing-stream-automation/overlay/recap.html` | 1920×1080 |
| In Car - Racing | Relative Timing | `file:///C:/Users/elikl/Documents/GitHub/iracing-stream-automation/overlay/relative.html` | 280×900 |
| Pit / Garage | Pit Wall | `file:///C:/Users/elikl/Documents/GitHub/iracing-stream-automation/overlay/pitwall.html` | 400×800 |

For `recap.html`, `relative.html`, and `pitwall.html`: check **Allow transparency** in the browser source properties.

---

## Starting the Monitor

Open PowerShell in the repo folder and run:

```powershell
.venv-win\Scripts\python session_monitor.py
```

To test without touching OBS or WLED:

```powershell
.venv-win\Scripts\python session_monitor.py --dry-run
```

The script connects to OBS, starts the WebSocket telemetry server on `ws://localhost:8765`, and begins watching iRacing. Everything from there is automatic.

---

## Terminal Commands (while running)

Type these into the terminal and press Enter:

| Command | Effect |
|---|---|
| `end` | Switches to Stream Outro scene (use when done streaming) |
| `quit` | Shuts down the monitor |

---

## How the Overlays Work

All overlays are plain HTML files served as OBS browser sources. They poll local JSON files or connect to the local WebSocket server — no internet required.

- **`overlay/index.html`** — End-screen race results. Polls `results.json` every 5 seconds until data arrives, then stops.
- **`overlay/recap.html`** — Full-session recap. Polls `session_data.json` every 8 seconds. Shows all races from the current stream session with per-category iRating tracking.
- **`overlay/relative.html`** — Live relative timing strip. Connects to the WebSocket server and shows cars within ±45 seconds, colour-coded by class.
- **`overlay/pitwall.html`** — Crew chief view. Track map, strategy panel (pace delta, fuel, repair estimate, pit recommendation), and mini relative strip.

---

## Known Limitations

### iRacing API auth (as of Dec 2025)

iRacing discontinued username/password API authentication. The `iracingdataapi` library is waiting on iRacing to issue OAuth tokens.

**Current behaviour:** Race results still appear on the end screen (sourced from live irsdk data), and Claude still writes a narrative. The following are unavailable until the API is restored:
- iRating and safety rating change numbers
- Championship points
- Official vs unofficial race flag

**When it's fixed:** Update the library (`pip install -U iracingdataapi` in `.venv-win`) and restore `results_pipeline.py` from git history — the full API-backed version is one commit back. Track progress at [iracingdataapi issue #65](https://github.com/jasondilworth56/iracingdataapi/issues/65).

---

## Project Structure

```
session_monitor.py      # main loop — iRacing state → OBS + WLED
telemetry_server.py     # WebSocket server broadcasting live telemetry at 10Hz
wled_controller.py      # WLED HTTP API wrapper + event definitions
obs_controller.py       # OBS WebSocket wrapper
iracing_api.py          # iRacing data API wrapper (currently unused — see above)
results_pipeline.py     # post-race results extraction + Claude narrative
claude_summary.py       # Claude API calls for race narrative and session recap
session_data.py         # session_data.json read/write helpers
overlay/
  index.html            # end screen race results
  recap.html            # full-session recap
  relative.html         # live relative timing strip
  pitwall.html          # crew chief / pit wall view
  style.css             # shared styles
```
