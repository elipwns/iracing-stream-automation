import json
from datetime import datetime
from pathlib import Path

SESSION_FILE = Path(__file__).parent / "overlay" / "session_data.json"

_EMPTY = {
    "stream_date": "",
    "races": [],
    "session_narrative": "",
    "video_ended": False,
    "totals": {
        "races_completed": 0,
        "by_category": {},
        "total_champ_points": 0,
        "best_finish": None,
    },
}


def reset():
    data = {**_EMPTY, "stream_date": datetime.now().strftime("%B %d, %Y")}
    SESSION_FILE.parent.mkdir(exist_ok=True)
    SESSION_FILE.write_text(json.dumps(data, indent=2))
    print("[session] Session data reset")


def load() -> dict:
    if not SESSION_FILE.exists():
        reset()
    return json.loads(SESSION_FILE.read_text())


def save(data: dict):
    SESSION_FILE.write_text(json.dumps(data, indent=2))


def add_race(race_entry: dict):
    data = load()
    existing = next(
        (r for r in data["races"] if r.get("subsession_id") == race_entry.get("subsession_id")),
        None,
    )
    if existing:
        existing.update(race_entry)
    else:
        data["races"].append(race_entry)
    data["totals"] = _compute_totals(data["races"])
    save(data)


def update_narrative(narrative: str):
    data = load()
    data["session_narrative"] = narrative
    save(data)


def update_video_ended(ended: bool):
    data = load()
    data["video_ended"] = ended
    save(data)


def _compute_totals(races: list) -> dict:
    official_races = [r for r in races if r.get("official")]
    by_category = {}
    for r in official_races:
        cat = r.get("category", "Unknown")
        if cat not in by_category:
            by_category[cat] = {"iracing_net": 0, "safety_rating_net": 0.0, "races": 0}
        by_category[cat]["iracing_net"] += r.get("iracing_change") or 0
        by_category[cat]["safety_rating_net"] = round(
            by_category[cat]["safety_rating_net"] + (r.get("safety_rating_change") or 0.0), 2
        )
        by_category[cat]["races"] += 1

    points = sum(r.get("champ_points") or 0 for r in races)
    finishes = [r["finish_position"] for r in races if r.get("finish_position")]
    return {
        "races_completed": len(races),
        "by_category": by_category,
        "total_champ_points": points,
        "best_finish": min(finishes) if finishes else None,
    }
