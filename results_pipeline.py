import json
import time
import os
from pathlib import Path
from dotenv import load_dotenv

import iracing_api
import claude_summary
import session_data as sd

load_dotenv()

OVERLAY_DIR = Path(__file__).parent / "overlay"
RESULTS_JSON = OVERLAY_DIR / "results.json"

POLL_INTERVAL_SECS = 180   # check every 3 minutes
POLL_MAX_ATTEMPTS  = 10    # give up after 30 minutes


def _fmt_lap(ms: int) -> str:
    if ms <= 0:
        return "—"
    total = ms / 10000
    m = int(total // 60)
    s = total % 60
    return f"{m}:{s:06.3f}"


def _ir_change(old, new):
    return (new - old) if old is not None and new is not None else None


def _sr_change(old, new):
    if old is None or new is None:
        return None
    return round((new - old) / 100, 2)


def _write_results(race_data: dict, narrative: str, cust_id: int, dry_run: bool):
    output = {
        "subsession_id": race_data["subsession_id"],
        "track": race_data.get("track", ""),
        "series_name": race_data.get("series_name", ""),
        "narrative": narrative,
        "results": race_data["results"],
        "my_cust_id": cust_id,
        "total_laps": race_data["total_laps"],
        "official": race_data.get("official", False),
    }
    for entry in output["results"]:
        entry["best_lap_display"] = _fmt_lap(entry.get("best_lap_time", -1))
        entry["avg_lap_display"]  = _fmt_lap(entry.get("average_lap_time", -1))
        if entry.get("cust_id") == cust_id:
            ir = _ir_change(entry.get("oldi_rating"), entry.get("newi_rating"))
            sr = _sr_change(entry.get("old_cpi"), entry.get("new_cpi"))
            entry["iracing_change"]              = ir
            entry["iracing_change_display"]      = f"{ir:+d}" if ir is not None else "—"
            entry["safety_rating_change"]        = sr
            entry["safety_rating_change_display"] = f"{sr:+.2f}" if sr is not None else "—"

    if dry_run:
        print("[dry-run] Would write overlay/results.json:")
        print(json.dumps(output, indent=2))
        return

    OVERLAY_DIR.mkdir(exist_ok=True)
    RESULTS_JSON.write_text(json.dumps(output, indent=2))
    print(f"[results_pipeline] Written to {RESULTS_JSON}")


def _build_session_entry(race_data: dict, narrative: str, cust_id: int) -> dict:
    my = race_data.get("my_result") or {}
    ir = _ir_change(my.get("oldi_rating"), my.get("newi_rating"))
    sr = _sr_change(my.get("old_cpi"), my.get("new_cpi"))
    return {
        "subsession_id":            race_data["subsession_id"],
        "track":                    race_data.get("track", "Unknown"),
        "category":                 race_data.get("category", "Unknown"),
        "series_name":              race_data.get("series_name", ""),
        "finish_position":          my.get("finish_position"),
        "start_position":           my.get("start_position"),
        "total_drivers":            len(race_data.get("results", [])),
        "laps":                     race_data.get("total_laps", 0),
        "incidents":                my.get("incidents", 0),
        "best_lap_display":         _fmt_lap(race_data.get("best_lap_time", -1)),
        "iracing_before":           my.get("oldi_rating"),
        "iracing_after":            my.get("newi_rating"),
        "iracing_change":           ir,
        "iracing_change_display":   f"{ir:+d}" if ir is not None else "—",
        "safety_rating_before":     round(my["old_cpi"] / 100, 2) if my.get("old_cpi") else None,
        "safety_rating_after":      round(my["new_cpi"] / 100, 2) if my.get("new_cpi") else None,
        "safety_rating_change":     sr,
        "safety_rating_change_display": f"{sr:+.2f}" if sr is not None else "—",
        "champ_points":             my.get("champ_points", 0),
        "narrative":                narrative,
        "official":                 race_data.get("official", False),
    }


def _finalize(race_data: dict, narrative: str, cust_id: int, dry_run: bool):
    if dry_run:
        return
    sd.add_race(_build_session_entry(race_data, narrative, cust_id))
    session = sd.load()
    print("[results_pipeline] Generating session recap...")
    recap = claude_summary.generate_session_recap(session)
    sd.update_narrative(recap)
    print("[results_pipeline] Session recap updated")


def run(cust_id: int | None = None, subsession_id: int | None = None, dry_run: bool = False):
    if cust_id is None:
        cust_id = int(os.getenv("IRACING_CUST_ID", 0))
    if not cust_id:
        print("[results_pipeline] No IRACING_CUST_ID — skipping")
        return

    if subsession_id is None:
        print("[results_pipeline] Fetching last session...")
        last = iracing_api.get_last_session_results(cust_id)
        if not last:
            print("[results_pipeline] No recent session found")
            return
        subsession_id = last.get("subsession_id")

    print(f"[results_pipeline] Processing subsession {subsession_id}")
    race_data = iracing_api.extract_race_data(subsession_id, cust_id)

    if not race_data.get("results"):
        print("[results_pipeline] No race results found — likely practice, qualify, or AI session")
        return

    print("[results_pipeline] Generating race narrative...")
    narrative = claude_summary.generate_summary(race_data)
    _write_results(race_data, narrative, cust_id, dry_run)

    if not dry_run:
        sd.add_race(_build_session_entry(race_data, narrative, cust_id))

    if race_data.get("official"):
        print("[results_pipeline] Official results already available")
        _finalize(race_data, narrative, cust_id, dry_run)
        return

    my = race_data.get("my_result") or {}
    if my.get("oldi_rating") is None:
        print("[results_pipeline] No iRating data — unofficial/AI race, skipping poll")
        return

    print(f"[results_pipeline] Polling for official results every {POLL_INTERVAL_SECS // 60} min...")
    for attempt in range(1, POLL_MAX_ATTEMPTS + 1):
        time.sleep(POLL_INTERVAL_SECS)
        print(f"[results_pipeline] Official results check {attempt}/{POLL_MAX_ATTEMPTS}...")
        race_data = iracing_api.extract_race_data(subsession_id, cust_id)
        if race_data.get("official"):
            print("[results_pipeline] Official results in — updating summary...")
            narrative = claude_summary.generate_summary(race_data)
            _write_results(race_data, narrative, cust_id, dry_run)
            _finalize(race_data, narrative, cust_id, dry_run)
            return

    print("[results_pipeline] Official results never arrived — giving up")
