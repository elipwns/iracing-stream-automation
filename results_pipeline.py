# TODO — restore iRacing API enrichment once IRACING_CLIENT_ID is obtained (auth@iracing.com)
# Uncomment iracing_api import and restore run() from git history (one commit back) to re-enable:
# iRating/SR changes, champ points, official flag, 30-min post-race polling

import json
import os
import time
from pathlib import Path
from dotenv import load_dotenv

import claude_summary
import session_data as sd
import betting

load_dotenv()

OVERLAY_DIR = Path(__file__).parent / "overlay"
RESULTS_JSON = OVERLAY_DIR / "results.json"


def _fmt_lap(tenths: int) -> str:
    """Format a lap time given in 1/10000 seconds."""
    if tenths <= 0:
        return "—"
    total = tenths / 10000
    m = int(total // 60)
    s = total % 60
    return f"{m}:{s:06.3f}"


def _fmt_lap_secs(secs: float) -> str:
    """Format a lap time given in seconds (irsdk SessionInfo unit)."""
    if not secs or secs <= 0:
        return "—"
    m = int(secs // 60)
    s = secs % 60
    return f"{m}:{s:06.3f}"


def _extract_from_irsdk(ir, session_num: int, cust_id: int) -> dict:
    # WeekendInfo, DriverInfo, SessionInfo are separate top-level irsdk keys
    weekend      = ir["WeekendInfo"] or {}
    driver_info  = ir["DriverInfo"] or {}
    session_info = ir["SessionInfo"] or {}

    track_name    = weekend.get("TrackDisplayName", weekend.get("TrackName", "Unknown Track"))
    track_config  = weekend.get("TrackConfigName", "")
    full_track    = f"{track_name} – {track_config}" if track_config else track_name
    series_name   = weekend.get("SeriesName", "")
    category      = weekend.get("Category", "Unknown")
    subsession_id = weekend.get("SubSessionID") or None

    drivers_raw = driver_info.get("Drivers", [])
    driver_map  = {d["CarIdx"]: d for d in drivers_raw}

    sessions  = session_info.get("Sessions", [])
    session   = sessions[session_num] if session_num < len(sessions) else {}
    positions = session.get("ResultsPositions") or []

    results   = []
    my_result = None

    for entry in positions:
        car_idx = entry.get("CarIdx", -1)
        drv     = driver_map.get(car_idx, {})
        d_cust  = drv.get("UserID") or drv.get("CustID")

        fastest_secs   = entry.get("FastestTime", -1) or -1
        fastest_tenths = int(fastest_secs * 10000) if fastest_secs > 0 else -1

        row = {
            "finish_position":  entry.get("Position", 0),
            "start_position":   None,
            "display_name":     drv.get("UserName", f"Car {car_idx}"),
            "car_number":       drv.get("CarNumber", "?"),
            "laps_complete":    entry.get("LapsComplete", entry.get("Lap", 0)),
            "laps_led":         entry.get("LapsLed", 0),
            "incidents":        entry.get("Incidents", 0),
            "reason_out":       entry.get("ReasonOutStr", "Running"),
            "best_lap_time":    fastest_tenths,
            "average_lap_time": -1,
            "cust_id":          d_cust,
            "oldi_rating":      None,
            "newi_rating":      None,
            "old_cpi":          None,
            "new_cpi":          None,
            "champ_points":     0,
        }
        results.append(row)
        if d_cust == cust_id:
            my_result = row

    results.sort(key=lambda r: r["finish_position"])
    total_laps   = results[0]["laps_complete"] if results else 0
    my_best_secs = my_result["best_lap_time"] / 10000 if my_result and my_result["best_lap_time"] > 0 else -1

    return {
        "subsession_id": subsession_id,
        "track":         full_track,
        "series_name":   series_name,
        "category":      category,
        "results":       results,
        "my_result":     my_result,
        "best_lap_time": my_result["best_lap_time"] if my_result else -1,
        "total_laps":    total_laps,
        "official":      False,
    }


_FLAG_LABELS = {
    "green_flag":   "green flag (start/restart)",
    "white_flag":   "white flag (final lap)",
    "caution":      "full-course caution",
    "local_yellow": "local yellow (sector incident)",
    "debris":       "debris flag",
    "black_flag":   "black flag (penalty)",
    "meatball":     "meatball flag (damage — car repairable)",
    "checkered":    "checkered flag",
}


def _build_claude_context(race_data: dict, flag_timeline: list) -> dict:
    my      = race_data.get("my_result") or {}
    results = race_data.get("results", [])

    events = [
        f"lap {e['lap']}: {_FLAG_LABELS.get(e['flag'], e['flag'])}"
        for e in flag_timeline
    ]

    return {
        "track":        race_data.get("track"),
        "series":       race_data.get("series_name"),
        "category":     race_data.get("category"),
        "total_laps":   race_data.get("total_laps"),
        "field_size":   len(results),
        "my_finish":    my.get("finish_position"),
        "my_best_lap":  _fmt_lap(race_data.get("best_lap_time", -1)),
        "my_incidents": my.get("incidents", 0),
        "my_laps_led":  my.get("laps_led", 0),
        "race_events":  events,
        "classification": [
            {
                "pos":       r.get("finish_position"),
                "driver":    r.get("display_name"),
                "laps":      r.get("laps_complete"),
                "laps_led":  r.get("laps_led", 0),
                "incidents": r.get("incidents", 0),
                "best_lap":  _fmt_lap(r.get("best_lap_time", -1)),
                "status":    r.get("reason_out", "Running"),
            }
            for r in results[:20]
        ],
    }


def _write_results(race_data: dict, narrative: str, cust_id: int, dry_run: bool):
    output = {
        "subsession_id": race_data["subsession_id"],
        "track":         race_data.get("track", ""),
        "series_name":   race_data.get("series_name", ""),
        "narrative":     narrative,
        "results":       race_data["results"],
        "my_cust_id":    cust_id,
        "total_laps":    race_data["total_laps"],
        "official":      race_data.get("official", False),
    }
    for entry in output["results"]:
        entry["best_lap_display"] = _fmt_lap(entry.get("best_lap_time", -1))
        entry["avg_lap_display"]  = _fmt_lap(entry.get("average_lap_time", -1))

    if dry_run:
        print("[dry-run] Would write overlay/results.json:")
        print(json.dumps(output, indent=2))
        return

    OVERLAY_DIR.mkdir(exist_ok=True)
    RESULTS_JSON.write_text(json.dumps(output, indent=2))
    print(f"[results_pipeline] Written to {RESULTS_JSON}")


def _build_session_entry(race_data: dict, narrative: str, cust_id: int) -> dict:
    my = race_data.get("my_result") or {}
    return {
        "subsession_id":                race_data["subsession_id"],
        "track":                        race_data.get("track", "Unknown"),
        "category":                     race_data.get("category", "Unknown"),
        "series_name":                  race_data.get("series_name", ""),
        "finish_position":              my.get("finish_position"),
        "start_position":               my.get("start_position"),
        "total_drivers":                len(race_data.get("results", [])),
        "laps":                         race_data.get("total_laps", 0),
        "incidents":                    my.get("incidents", 0),
        "best_lap_display":             _fmt_lap(race_data.get("best_lap_time", -1)),
        "iracing_before":               None,
        "iracing_after":                None,
        "iracing_change":               None,
        "iracing_change_display":       "—",
        "safety_rating_before":         None,
        "safety_rating_after":          None,
        "safety_rating_change":         None,
        "safety_rating_change_display": "—",
        "champ_points":                 0,
        "narrative":                    narrative,
        "official":                     False,
    }


def run(ir=None, session_num: int = 0, cust_id: int | None = None,
        dry_run: bool = False, on_complete=None, flag_timeline: list | None = None):
    if cust_id is None:
        cust_id = int(os.getenv("IRACING_CUST_ID", 0))
    if not cust_id:
        print("[results_pipeline] No IRACING_CUST_ID — skipping")
        return
    if ir is None:
        print("[results_pipeline] No irsdk handle — skipping")
        return

    race_data = None
    for attempt in range(4):
        if attempt > 0:
            time.sleep(5)
        print(f"[results_pipeline] Extracting results (attempt {attempt + 1}/4)...")
        race_data = _extract_from_irsdk(ir, session_num, cust_id)
        if race_data.get("results"):
            break
        print(f"[results_pipeline] ResultsPositions empty — {'retrying in 5s' if attempt < 3 else 'giving up'}")

    if not race_data or not race_data.get("results"):
        print("[results_pipeline] No results found — skipping End Screen")
        return

    print("[results_pipeline] Generating race narrative...")
    narrative = claude_summary.generate_summary(_build_claude_context(race_data, flag_timeline or []))
    _write_results(race_data, narrative, cust_id, dry_run)
    betting.resolve_bets(race_data)

    if on_complete:
        on_complete()

    if not dry_run:
        sd.add_race(_build_session_entry(race_data, narrative, cust_id))
        session = sd.load()
        print("[results_pipeline] Generating session recap...")
        recap = claude_summary.generate_session_recap(session)
        sd.update_narrative(recap)
        print("[results_pipeline] Session recap updated")
