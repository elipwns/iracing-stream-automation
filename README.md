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
| Green / white / caution / local yellow / debris / black / meatball flag | — | Corresponding colour |

After each race: Claude writes a post-race narrative and the end screen overlay populates with results. At stream end, type `end` for a full-session recap overlay.

**WLED flag colours:**

| Flag | Colour | Notes |
|---|---|---|
| Full course caution | Yellow breathe (slow) | Full course yellow / waving |
| Local yellow | Yellow breathe (fast) | Sector only — common on long tracks like Nürburgring |
| Debris | Orange breathe | Red/yellow striped flag |
| Green | Green breathe | Start / restart |
| White | White solid | Last lap |
| Black | Red breathe | Personal penalty |
| Meatball | Orange breathe | Damage / service allowed |

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
IRACING_CLIENT_ID=              # see Known Limitations below
ANTHROPIC_API_KEY=sk-ant-...    # from console.anthropic.com
OBS_HOST=localhost
OBS_PORT=4455
OBS_PASSWORD=yourpassword       # set in OBS > Tools > WebSocket Server Settings
WLED_IP=192.168.1.xxx           # your WLED device's local IP
```

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
| Menu / Lobby | Lobby Background Frame | `file:///C:/Users/elikl/Documents/GitHub/iracing-stream-automation/overlay/lobby.html` | 1920×1080 |
| In Car - Racing | Odometer & Stats HUD | `file:///C:/Users/elikl/Documents/GitHub/iracing-stream-automation/overlay/odometer.html` | 310×350 |
| Pit / Garage | Pit Wall | `file:///C:/Users/elikl/Documents/GitHub/iracing-stream-automation/overlay/pitwall.html` | 400×800 |

For `recap.html`, `lobby.html`, `odometer.html`, and `pitwall.html`: check **Allow transparency** in the browser source properties.

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
- **`overlay/lobby.html`** — Lobby background & stats ticker. Provides a 1920x1080 premium sci-fi grid backdrop to sit behind windowed captures, plus a glassmorphic bottom bar featuring a live clock and dynamic scrolling telemetry marquee (active car, trip, total miles, and predicted iRating).
- **`overlay/odometer.html`** — Odometer & Live Stats HUD. Connects to the WebSocket server and shows active car name, lifetime odometer, session trip, predicted iRating, and session status.
- **`overlay/pitwall.html`** — Crew chief view. Track map, strategy panel (pace delta, fuel, repair estimate, pit recommendation), and mini relative strip.

---

## In Progress / TODO

### Twitch chat bot + betting system

`betting.py` and `chat_bot.py` are implemented but need Twitch credentials to activate.

**Setup (when ready):**
1. Create a separate Twitch bot account
2. Log into that account and get a token at `https://twitchapps.com/tmi/`
3. Add to `.env`: `TWITCH_TOKEN=oauth:...` and `TWITCH_CHANNEL=yourchannel`
4. `pip install -r requirements.txt` in `.venv-win`
5. Run alongside the monitor: `.venv-win\Scripts\python chat_bot.py`

**Commands:** `!bet [win/podium/finish/crash] [amount or all]` · `!points` · `!bets` · `!leaderboard`

**Odds:** 3x pre-race → 2x lap 1 → 1.5x lap 3 → 1.2x lap 5 → closed at lap 8

---

## Known Limitations

### iRacing API auth (as of Dec 2025)

iRacing moved to OAuth 2.0 (`oauth.iracing.com`). The auth flow is implemented in `iracing_auth.py` — on first run it opens a browser for one-time login, then caches tokens locally (7-day refresh window). No password is stored.

**Blocker:** iRacing's OAuth client registration is currently paused. You need a `client_id` before any of this works. Email `auth@iracing.com` to get on the list.

**Current behaviour:** Race results still appear on the end screen (sourced from live irsdk data), and Claude still writes a narrative. The following are unavailable until a `client_id` is obtained:
- iRating and safety rating change numbers
- Championship points
- Official vs unofficial race flag

---

## Project Structure

```
session_monitor.py      # main loop — iRacing state → OBS + WLED + betting hooks
telemetry_server.py     # WebSocket server broadcasting live telemetry at 10Hz
wled_controller.py      # WLED HTTP API wrapper + event definitions
obs_controller.py       # OBS WebSocket wrapper
iracing_api.py          # iRacing data API wrapper (blocked on OAuth client_id)
iracing_auth.py         # iRacing OAuth 2.0 flow (browser login + token cache)
results_pipeline.py     # post-race results extraction + Claude narrative + bet resolution
claude_summary.py       # Claude API calls for race narrative and session recap
session_data.py         # session_data.json read/write helpers
betting.py              # points economy, bet logic, SQLite storage, chat message queue
chat_bot.py             # Twitch chat bot (needs TWITCH_TOKEN — see TODO above)
overlay/
  index.html            # end screen race results
  recap.html            # full-session recap
  lobby.html            # lobby background frame & live stats ticker
  odometer.html         # live mechanical odometer & stats HUD
  pitwall.html          # crew chief / pit wall view
  style.css             # shared styles
```
