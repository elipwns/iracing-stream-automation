# TODO — iRacing API restoration
# iRacing killed username/password auth in Dec 2025. iracingdataapi (v1.4.4) has not yet
# migrated to OAuth. Track: https://github.com/jasondilworth56/iracingdataapi/issues/65
#
# When the library ships OAuth support:
#   1. pip install -U iracingdataapi  (both .venv and .venv-win)
#   2. Uncomment the iracing_api import below
#   3. Replace run() with the full version from git history (commit before this one)
#      - Restores: iRating/SR changes, champ points, official flag, 30-min polling loop
#   4. Update session_monitor.py: pass subsession_id instead of session_info/session_num

import json
import os
from pathlib import Path
from dotenv import load_dotenv

import claude_summary
import session_data as sd

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


def _extract_from_irsdk(session_info: dict, session_num: int, cust_id: int) -> dict:
    weekend = session_info.get("WeekendInfo", {})
    track_name   = weekend.get("TrackDisplayName", weekend.get("TrackName", "Unknown Track"))
    track_config = weekend.get("TrackConfigName", "")
    full_track   = f"{track_name} – {track_config}" if track_config else track_name
    series_name  = weekend.get("SeriesName", "")
    category     = weekend.get("Category", "Unknown")
    subsession_id = weekend.get("SubSessionID") or None

    drivers_raw = session_info.get("DriverInfo", {}).get("Drivers", [])
    driver_map  = {d["CarIdx"]: d for d in drivers_raw}

    sessions    = session_info.get("SessionInfo", {}).get("Sessions", [])
    session     = sessions[session_num] if session_num < len(sessions) else {}
    positions   = session.get("ResultsPositions") or []

    results = []
    my_result = None

    for entry in positions:
        car_idx = entry.get("CarIdx", -1)
        drv     = driver_map.get(car_idx, {})
        d_cust  = drv.get("UserID") or drv.get("CustID") or drv.get("CarDriverIncidentCount")

        # FastestTime from irsdk is in seconds; convert to 1/10000 sec for _fmt_lap
        fastest_secs = entry.get("FastestTime", -1) or -1
        fastest_tenths = int(fastest_secs * 10000) if fastest_secs > 0 else -1

        row = {
            "finish_position": entry.get("Position", 0),
            "start_position":  None,
            "display_name":    drv.get("UserName", f"Car {car_idx}"),
            "car_number":      drv.get("CarNumber", "?"),
            "laps_complete":   entry.get("Lap", 0),
            "incidents":       entry.get("Incidents", 0),
            "best_lap_time":   fastest_tenths,
            "average_lap_time": -1,
            "cust_id":         d_cust,
            "oldi_rating":     None,
            "newi_rating":     None,
            "old_cpi":         None,
            "new_cpi":         None,
            "champ_points":    0,
        }
        results.append(row)
        if d_cust == cust_id:
            my_result = row

    results.sort(key=lambda r: r["finish_position"])
    total_laps = results[0]["laps_complete"] if results else 0

    my_best_secs = None
    if my_result:
        for entry in positions:
            car_idx = entry.get("CarIdx", -1)
            drv = driver_map.get(car_idx, {})
            if (drv.get("UserID") or drv.get("CustID")) == cust_id:
                my_best_secs = entry.get("FastestTime", -1) or -1
                break

    return {
        "subsession_id": subsession_id,
        "track":         full_track,
        "series_name":   series_name,
        "category":      category,
        "results":       results,
        "my_result":     my_result,
        "best_lap_time": int(my_best_secs * 10000) if my_best_secs and my_best_secs > 0 else -1,
        "total_laps":    total_laps,
        "official":      False,
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


def run(session_info: dict | None = None, session_num: int = 0,
        cust_id: int | None = None, dry_run: bool = False):
    if cust_id is None:
        cust_id = int(os.getenv("IRACING_CUST_ID", 0))
    if not cust_id:
        print("[results_pipeline] No IRACING_CUST_ID — skipping")
        return
    if not session_info:
        print("[results_pipeline] No session info — skipping")
        return

    print("[results_pipeline] Extracting results from irsdk session data...")
    race_data = _extract_from_irsdk(session_info, session_num, cust_id)

    if not race_data.get("results"):
        print("[results_pipeline] No results found in session data")
        return

    print("[results_pipeline] Generating race narrative...")
    narrative = claude_summary.generate_summary(race_data)
    _write_results(race_data, narrative, cust_id, dry_run)

    if not dry_run:
        sd.add_race(_build_session_entry(race_data, narrative, cust_id))
        session = sd.load()
        print("[results_pipeline] Generating session recap...")
        recap = claude_summary.generate_session_recap(session)
        sd.update_narrative(recap)
        print("[results_pipeline] Session recap updated")
