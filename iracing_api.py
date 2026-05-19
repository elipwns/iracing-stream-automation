import warnings
from iracingdataapi.client import irDataClient
from iracingdataapi.exceptions import AccessTokenInvalid
from iracing_auth import get_access_token

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = _new_client()
    return _client


def _new_client() -> irDataClient:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return irDataClient(access_token=get_access_token())


def _call(fn, *args, **kwargs):
    global _client
    try:
        return fn(*args, **kwargs)
    except AccessTokenInvalid:
        _client = _new_client()
        return fn(*args, **kwargs)


def get_last_session_results(cust_id: int) -> dict | None:
    results = _call(_get_client().result_search_series, cust_id=cust_id, finish_range_begin=1)
    if not results:
        return None
    return sorted(results, key=lambda r: r.get("start_time", ""), reverse=True)[0]


def get_session_results(subsession_id: int) -> dict:
    return _call(_get_client().result, subsession_id=subsession_id)


def get_lap_data(subsession_id: int, cust_id: int) -> list:
    laps = _call(_get_client().result_lap_data, subsession_id=subsession_id, cust_id=cust_id)
    return laps or []


def extract_race_data(subsession_id: int, cust_id: int) -> dict:
    session = get_session_results(subsession_id)
    laps = get_lap_data(subsession_id, cust_id)

    results_list = []
    my_result = None

    for simsession in session.get("session_results", []):
        if simsession.get("simsession_type_name") != "Race":
            continue
        for driver in simsession.get("results", []):
            entry = {
                "finish_position": driver.get("finish_position", 0) + 1,
                "start_position": driver.get("starting_position", 0) + 1,
                "display_name": driver.get("display_name", "Unknown"),
                "car_number": driver.get("livery", {}).get("car_number", "?"),
                "laps_complete": driver.get("laps_complete", 0),
                "incidents": driver.get("incidents", 0),
                "best_lap_time": driver.get("best_lap_time", -1),
                "average_lap_time": driver.get("average_lap_time", -1),
                "cust_id": driver.get("cust_id"),
                "oldi_rating": driver.get("oldi_rating"),
                "newi_rating": driver.get("newi_rating"),
                "old_cpi": driver.get("old_cpi"),
                "new_cpi": driver.get("new_cpi"),
                "champ_points": driver.get("champ_points", 0),
            }
            results_list.append(entry)
            if driver.get("cust_id") == cust_id:
                my_result = entry

    results_list.sort(key=lambda r: r["finish_position"])

    my_laps = [lap for lap in laps if lap.get("lap_time", -1) > 0]
    best_lap = min((lap["lap_time"] for lap in my_laps), default=-1)

    official = bool(my_result and my_result.get("newi_rating") is not None)

    track = session.get("track", {})
    track_name = track.get("track_name", "Unknown Track")
    config_name = track.get("config_name", "")
    full_track = f"{track_name} – {config_name}" if config_name else track_name

    category_id = session.get("license_category_id")
    category_names = {1: "Oval", 2: "Road", 3: "Dirt Oval", 4: "Dirt Road"}
    category = category_names.get(category_id, f"Category {category_id}" if category_id else "Unknown")

    return {
        "subsession_id": subsession_id,
        "series_name": session.get("series_name", ""),
        "track": full_track,
        "category": category,
        "category_id": category_id,
        "results": results_list,
        "my_result": my_result,
        "best_lap_time": best_lap,
        "total_laps": results_list[0]["laps_complete"] if results_list else 0,
        "official": official,
    }
