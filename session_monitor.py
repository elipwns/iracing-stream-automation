import argparse
import os
import subprocess
import sys
import time
import threading
import psutil
import irsdk
from dotenv import load_dotenv

import obs_controller
import wled_controller
import results_pipeline
import session_data as sd
import telemetry_server
import betting

load_dotenv()

SCENE_MENU       = "Menu / Lobby"
SCENE_RACING     = "In Car - Racing"
SCENE_WIDE       = "Mega Wide"
SCENE_PIT        = "Pit / Garage"
SCENE_END_SCREEN = "End Screen"
SCENE_STARTING   = "Starting Soon"
SCENE_OUTRO      = "Stream Outro"

POLL_INTERVAL = 1.0

WIDE_SHOWCASE_DELAY    = 90   # seconds after race start before auto-switching to wide
WIDE_SHOWCASE_DURATION = 60   # seconds to stay in wide before returning

# iRacing SDK SessionFlags bit masks (from irsdk.Flags)
FLAG_WHITE         = 0x0002     # white flag (last lap)
FLAG_GREEN         = 0x0004     # green flag (start / restart)
FLAG_YELLOW        = 0x0008     # local sector yellow
FLAG_DEBRIS        = 0x0040     # red/yellow striped — debris on track
FLAG_YELLOW_WAVING = 0x0100     # local yellow waving (more urgent)
FLAG_CAUTION       = 0x4000     # full course caution
FLAG_CAUTION_WAVE  = 0x8000     # full course caution waving
FLAG_BLACK         = 0x010000   # black flag (penalty — personal)
FLAG_FURLED        = 0x080000   # meatball / damage flag (personal)


def _is_iracing_running() -> bool:
    for proc in psutil.process_iter(['name']):
        try:
            if (proc.info['name'] or '').lower() in ('iracingui.exe', 'iracingsim64.exe'):
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return False


def _switch(scene: str, event: str, dry_run: bool):
    if dry_run:
        print(f"[dry-run] scene={scene!r}  wled={event!r}")
        return
    try:
        obs_controller.switch_scene(scene)
    except Exception as e:
        print(f"[obs] Error switching scene: {e}")
    if _wled_enabled:
        wled_controller.set_event(event)


def _resolve_flag(flags: int) -> str | None:
    """Highest-priority active flag → WLED event name, or None if clear."""
    if flags & FLAG_BLACK:                              return "black_flag"
    if flags & FLAG_FURLED:                             return "meatball"
    if flags & (FLAG_CAUTION | FLAG_CAUTION_WAVE):      return "caution"
    if flags & (FLAG_YELLOW | FLAG_YELLOW_WAVING):      return "local_yellow"
    if flags & FLAG_DEBRIS:                             return "debris"
    if flags & FLAG_GREEN:                              return "green_flag"
    if flags & FLAG_WHITE:                              return "white_flag"
    return None


def _target_scene_and_event(state: int, on_track: bool) -> tuple[str, str]:
    if state in (5, 6):
        if on_track:
            return (SCENE_WIDE if _wide_mode else SCENE_RACING), "checkered"
        return SCENE_PIT, "idle"   # pipeline switches to End Screen once results are written
    if state in (1, 2, 3, 4):
        if on_track:
            return (SCENE_WIDE if _wide_mode else SCENE_RACING), "idle"
        return SCENE_PIT, "idle"
    return SCENE_MENU, "idle"


_bot_process = None
_wled_enabled = False
_wide_mode    = False


def _start_bot():
    global _bot_process
    if _bot_process and _bot_process.poll() is None:
        print("[monitor] Bot is already running")
        return
    python = os.path.join(os.path.dirname(sys.executable), "python")
    bot_script = os.path.join(os.path.dirname(__file__), "chat_bot.py")
    _bot_process = subprocess.Popen([python, bot_script])
    print(f"[monitor] Chat bot started (pid {_bot_process.pid})")


def _play_outro_and_exit(dry_run: bool):
    print("[monitor] iRacing closed — playing stream outro")
    _switch(SCENE_OUTRO, "idle", dry_run)

    # Read duration (in seconds) from env, default to 60 seconds
    try:
        duration = int(os.getenv("OUTRO_DURATION", "60"))
    except ValueError:
        duration = 60

    if dry_run:
        print(f"[dry-run] Would display outro endcap for {duration}s, then exit")
        os._exit(0)

    print(f"[monitor] Displaying stream recap endcap for {duration}s before stopping stream...")
    time.sleep(duration)

    print("[monitor] Outro finished — stopping stream")
    try:
        obs_controller.stop_stream()
    except Exception as e:
        print(f"[monitor] Could not stop stream: {e}")
    os._exit(0)


def _toggle_wled():
    global _wled_enabled
    _wled_enabled = not _wled_enabled
    state = "ON" if _wled_enabled else "OFF"
    print(f"[monitor] WLED {state}")


def _toggle_wide(dry_run: bool):
    global _wide_mode
    _wide_mode = not _wide_mode
    scene = SCENE_WIDE if _wide_mode else SCENE_RACING
    print(f"[monitor] Wide mode {'ON' if _wide_mode else 'OFF'} — switching to {scene!r}")
    _switch(scene, "idle", dry_run)


def _command_listener(dry_run: bool):
    print("[monitor] Commands: 'end' = outro  |  'wide' = toggle wide view  |  'wled' = toggle lights  |  'bot' = chat bot  |  'quit' = exit")
    for line in sys.stdin:
        cmd = line.strip().lower()
        if cmd == "end":
            print("[monitor] Stream outro — switching scene...")
            _switch(SCENE_OUTRO, "idle", dry_run)
        elif cmd == "wide":
            _toggle_wide(dry_run)
        elif cmd == "wled":
            _toggle_wled()
        elif cmd == "bot":
            _start_bot()
        elif cmd in ("quit", "exit", "q"):
            print("[monitor] Shutting down")
            os._exit(0)
        elif cmd:
            print(f"[monitor] Unknown command: {cmd!r}  (try 'end', 'wide', 'wled', 'bot', or 'quit')")


def monitor_loop(dry_run: bool):
    ir = irsdk.IRSDK()

    prev_state          = None
    prev_on_track       = None
    raw_on_track        = None
    on_track_changed_at = None
    DEBOUNCE_SECS       = 3.0
    prev_flag_event     = None
    prev_process_running = None
    pipeline_fired      = False
    iracing_was_connected = False
    had_session         = False   # set when SDK connects; never reset — used for outro trigger
    current_session_type  = ""
    betting_race_id      = ""
    current_lap          = 0
    flag_timeline        = []   # [{lap, flag}] recorded during the race
    wide_showcase_fired  = False
    wide_showcase_active = False
    wide_showcase_start  = None
    race_green_time      = None

    sd.reset()
    betting.init_db()
    telemetry_server.start()
    threading.Thread(target=_command_listener, args=(dry_run,), daemon=True).start()
    print("[monitor] Starting session monitor...")

    if not dry_run:
        try:
            obs_controller.connect()
            print("[monitor] OBS connected")
        except Exception as e:
            print(f"[monitor] OBS connection failed: {e}")

    while True:
        ir.startup()
        connected = ir.is_connected
        process_running = _is_iracing_running()

        if not connected:
            if process_running != prev_process_running or iracing_was_connected:
                if process_running:
                    print("[monitor] iRacing at menu")
                    _switch(SCENE_MENU, "idle", dry_run)
                elif had_session:
                    # iRacing fully closed after a session — play outro and exit
                    threading.Thread(
                        target=_play_outro_and_exit, args=(dry_run,), daemon=True
                    ).start()
                else:
                    print("[monitor] iRacing not running")
                    _switch(SCENE_STARTING, "idle", dry_run)
                prev_process_running = process_running

            if iracing_was_connected:
                iracing_was_connected    = False
                prev_state               = None
                prev_on_track            = None
                raw_on_track             = None
                on_track_changed_at      = None
                prev_flag_event          = None
                pipeline_fired           = False
                current_session_type     = ""
                betting_race_id          = ""
                current_lap              = 0
                flag_timeline            = []
                wide_showcase_fired      = False
                wide_showcase_active     = False
                wide_showcase_start      = None
                race_green_time          = None
                betting.close_betting()

            time.sleep(POLL_INTERVAL)
            continue

        if not iracing_was_connected:
            print("[monitor] iRacing session connected")
            iracing_was_connected = True
            had_session = True
            prev_process_running = True

        ir.freeze_var_buffer_latest()
        state        = ir["SessionState"]
        flags        = ir["SessionFlags"] or 0
        on_track_raw = bool(ir["IsOnTrack"])

        # Keep session type current so scene decisions have it before the pipeline block runs
        _si = ir["SessionInfo"] or {}
        _sessions = _si.get("Sessions") or []
        _snum = ir["SessionNum"] or 0
        if _sessions and _snum < len(_sessions):
            current_session_type = _sessions[_snum].get("SessionType", "")

        # Debounce IsOnTrack — only commit change after it holds for DEBOUNCE_SECS
        now = time.monotonic()
        if on_track_raw != raw_on_track:
            raw_on_track = on_track_raw
            on_track_changed_at = now
        if on_track_changed_at and now - on_track_changed_at >= DEBOUNCE_SECS:
            on_track = on_track_raw
            on_track_changed_at = None
        else:
            on_track = prev_on_track if prev_on_track is not None else on_track_raw

        if state != prev_state or on_track != prev_on_track:
            scene, event = _target_scene_and_event(state, on_track)
            print(f"[monitor] State: {prev_state}→{state}  on_track={on_track}  scene={scene!r}")
            _switch(scene, event, dry_run)
            prev_state      = state
            prev_on_track   = on_track
            prev_flag_event = None   # reset so flag detection re-fires after scene change
            if state not in (5, 6):
                pipeline_fired = False

        # Flag detection — only when actively on track in a live session
        if on_track and state in (1, 2, 3, 4):
            flag_event = _resolve_flag(flags)
            if flag_event != prev_flag_event:
                if flag_event:
                    print(f"[monitor] Flag: {flag_event}")
                    flag_timeline.append({"lap": current_lap, "flag": flag_event})
                    if _wled_enabled and not dry_run:
                        wled_controller.set_event(flag_event)
                else:
                    print("[monitor] Flag cleared")
                    if _wled_enabled and not dry_run:
                        wled_controller.set_event("idle")
                prev_flag_event = flag_event

        # Auto wide showcase — once per race, fires WIDE_SHOWCASE_DELAY seconds after race start
        if (on_track and state in (1, 2, 3, 4) and current_session_type == "Race"
                and race_green_time and not wide_showcase_fired and not _wide_mode):
            if now - race_green_time >= WIDE_SHOWCASE_DELAY:
                wide_showcase_fired  = True
                wide_showcase_active = True
                wide_showcase_start  = now
                print(f"[monitor] Auto wide showcase — switching to {SCENE_WIDE!r} for {WIDE_SHOWCASE_DURATION}s")
                _switch(SCENE_WIDE, "idle", dry_run)

        if wide_showcase_active and now - wide_showcase_start >= WIDE_SHOWCASE_DURATION:
            wide_showcase_active = False
            if not _wide_mode:
                print("[monitor] Auto wide showcase done — returning to normal")
                _switch(SCENE_RACING if on_track else SCENE_PIT, "idle", dry_run)

        # Open betting when a race session goes live — keyed by SubSessionID so it only fires once per race
        if state in (1, 2, 3, 4) and current_session_type == "Race":
            wi = ir["WeekendInfo"] or {}
            sub_id = wi.get("SubSessionID")
            if sub_id:
                race_id = str(sub_id)
            elif not betting_race_id:
                race_id = f"local_{int(time.monotonic())}"  # generated once; stays stable after
            else:
                race_id = betting_race_id
            if race_id != betting_race_id:
                betting.open_betting(race_id)
                betting_race_id      = race_id
                current_lap          = 0
                flag_timeline        = []
                wide_showcase_fired  = False
                wide_showcase_active = False
                wide_showcase_start  = None
                race_green_time      = time.monotonic()

        # Track lap count to drive odds changes
        if state in (1, 2, 3, 4) and current_session_type == "Race":
            lap = ir["Lap"] or 0
            if lap != current_lap:
                current_lap = lap
                betting.update_lap(lap)

        if state in (5, 6) and not on_track and not pipeline_fired:
            pipeline_fired = True
            session_info_raw = ir["SessionInfo"] or {}
            sessions_list    = session_info_raw.get("Sessions") or []
            session_num      = ir["SessionNum"] or 0
            session_type     = sessions_list[session_num].get("SessionType", "") if sessions_list else ""
            current_session_type = session_type

            if session_type == "Race":
                print("[monitor] Race ended — launching results pipeline")
                def _on_results_ready():
                    _switch(SCENE_END_SCREEN, "race_end", dry_run)
                threading.Thread(
                    target=results_pipeline.run,
                    kwargs={"ir": ir, "session_num": session_num, "dry_run": dry_run,
                            "on_complete": _on_results_ready, "flag_timeline": list(flag_timeline)},
                    daemon=True,
                ).start()
            else:
                print(f"[monitor] Session ended ({session_type or 'unknown'}) — skipping results pipeline")

        time.sleep(POLL_INTERVAL)


def main():
    parser = argparse.ArgumentParser(description="iRacing stream automation monitor")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without executing them")
    args = parser.parse_args()
    monitor_loop(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
