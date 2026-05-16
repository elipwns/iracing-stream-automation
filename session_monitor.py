import argparse
import os
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

load_dotenv()

SCENE_MENU       = "Menu / Lobby"
SCENE_RACING     = "In Car - Racing"
SCENE_PIT        = "Pit / Garage"
SCENE_END_SCREEN = "End Screen"
SCENE_STARTING   = "Starting Soon"
SCENE_OUTRO      = "Stream Outro"

POLL_INTERVAL = 1.0

# iRacing SDK SessionFlags bit masks
FLAG_WHITE   = 0x0002       # white flag (last lap)
FLAG_GREEN   = 0x0004       # green flag (start / restart)
FLAG_YELLOW  = 0x0010       # local yellow / debris
FLAG_CAUTION = 0x8000       # full course caution
FLAG_BLACK   = 0x00020000   # black flag (penalty — personal)
FLAG_FURLED  = 0x00100000   # meatball / damage flag (personal)


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
    wled_controller.set_event(event)


def _resolve_flag(flags: int) -> str | None:
    """Highest-priority active flag → WLED event name, or None if clear."""
    if flags & FLAG_BLACK:   return "black_flag"
    if flags & FLAG_FURLED:  return "meatball"
    if flags & (FLAG_CAUTION | FLAG_YELLOW): return "caution"
    if flags & FLAG_GREEN:   return "green_flag"
    if flags & FLAG_WHITE:   return "white_flag"
    return None


def _target_scene_and_event(state: int, on_track: bool) -> tuple[str, str]:
    if state in (5, 6):
        if on_track:
            return SCENE_RACING, "checkered"
        return SCENE_END_SCREEN, "race_end"
    if state in (1, 2, 3, 4):
        if on_track:
            return SCENE_RACING, "idle"   # flags handle WLED while on track
        return SCENE_PIT, "idle"
    return SCENE_MENU, "idle"


def _command_listener(dry_run: bool):
    print("[monitor] Commands: 'end' = stream outro  |  'quit' = exit")
    for line in sys.stdin:
        cmd = line.strip().lower()
        if cmd == "end":
            print("[monitor] Stream outro — switching scene...")
            _switch(SCENE_OUTRO, "idle", dry_run)
        elif cmd in ("quit", "exit", "q"):
            print("[monitor] Shutting down")
            os._exit(0)
        elif cmd:
            print(f"[monitor] Unknown command: {cmd!r}  (try 'end' or 'quit')")


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
    current_session_type  = ""

    sd.reset()
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
                else:
                    print("[monitor] iRacing not running")
                    _switch(SCENE_STARTING, "idle", dry_run)
                prev_process_running = process_running

            if iracing_was_connected:
                iracing_was_connected   = False
                prev_state              = None
                prev_on_track           = None
                raw_on_track            = None
                on_track_changed_at     = None
                prev_flag_event         = None
                pipeline_fired          = False
                current_session_type    = ""

            time.sleep(POLL_INTERVAL)
            continue

        if not iracing_was_connected:
            print("[monitor] iRacing session connected")
            iracing_was_connected = True
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
            # Only show End Screen after a Race; qualify/practice → Pit/Garage
            if scene == SCENE_END_SCREEN and current_session_type != "Race":
                scene = SCENE_PIT
                event = "idle"
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
                    wled_controller.set_event(flag_event) if not dry_run else print(f"[dry-run] wled={flag_event!r}")
                else:
                    print("[monitor] Flag cleared")
                    wled_controller.set_event("idle") if not dry_run else print("[dry-run] wled='idle'")
                prev_flag_event = flag_event

        if state in (5, 6) and not pipeline_fired:
            pipeline_fired = True
            session_info_raw = ir["SessionInfo"] or {}
            sessions_list    = session_info_raw.get("Sessions") or []
            session_num      = ir["SessionNum"] or 0
            session_type     = sessions_list[session_num].get("SessionType", "") if sessions_list else ""
            current_session_type = session_type

            if session_type == "Race":
                print("[monitor] Race ended — launching results pipeline")
                session_snapshot = dict(session_info_raw)
                threading.Thread(
                    target=results_pipeline.run,
                    kwargs={"session_info": session_snapshot, "session_num": session_num, "dry_run": dry_run},
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
