import asyncio
import json
import threading
import time
from pathlib import Path
import irsdk

HOST = "localhost"
PORT = 8765

_current_data: dict = {}
_lock = threading.Lock()

OVERLAY_DIR = Path(__file__).parent / "overlay"
MILEAGE_JSON = OVERLAY_DIR / "mileage.json"

# Global state for mileage tracking
_mileage_data = {
    "grand_total_miles": 0.0,
    "cars": {}
}
_session_meters = 0.0
_active_car_path = "unknown"
_active_car_name = "Unknown Car"


def _load_mileage():
    global _mileage_data
    try:
        if MILEAGE_JSON.exists():
            _mileage_data = json.loads(MILEAGE_JSON.read_text())
    except Exception as e:
        print(f"[telemetry] Error loading mileage: {e}")


def _save_mileage():
    global _mileage_data, _session_meters, _active_car_path, _active_car_name
    if _session_meters <= 0:
        return
    
    session_miles = _session_meters * 0.000621371
    
    if _active_car_path not in _mileage_data["cars"]:
        _mileage_data["cars"][_active_car_path] = {
            "name": _active_car_name,
            "miles": 0.0
        }
    
    _mileage_data["cars"][_active_car_path]["miles"] += session_miles
    _mileage_data["grand_total_miles"] += session_miles
    
    try:
        OVERLAY_DIR.mkdir(exist_ok=True)
        MILEAGE_JSON.write_text(json.dumps(_mileage_data, indent=2))
        print(f"[telemetry] Saved mileage: +{session_miles:.3f} miles to {_active_car_name}")
    except Exception as e:
        print(f"[telemetry] Error saving mileage: {e}")
    
    _session_meters = 0.0


def _fmt_gap(secs: float) -> str:
    if abs(secs) < 0.05:
        return "—"
    sign = "+" if secs > 0 else "-"
    s = abs(secs)
    if s >= 60:
        m = int(s // 60)
        return f"{sign}{m}:{s % 60:05.2f}"
    return f"{sign}{s:.2f}"


def _build_strategy(ir, lap_times: list[float]) -> dict:
    fuel_level = ir["FuelLevel"] or 0.0
    fuel_per_hour = ir["FuelUsePerHour"] or 0.0
    pit_repair = ir["PitRepairLeft"] or 0.0
    session_time_remain = ir["SessionTimeRemain"] or 0.0

    baseline = min(lap_times[-10:]) if len(lap_times) >= 3 else None
    recent = lap_times[-3:] if len(lap_times) >= 3 else lap_times
    rolling_avg = sum(recent) / len(recent) if recent else None

    pace_delta = None
    if baseline and rolling_avg:
        pace_delta = round(rolling_avg - baseline, 3)

    fuel_laps = None
    if fuel_per_hour > 0 and rolling_avg:
        fuel_laps = round(fuel_level / (fuel_per_hour * rolling_avg / 3600), 1)

    recommend_pit = False
    reason = ""

    if pit_repair > 30:
        recommend_pit = True
        reason = f"Repair needed ({int(pit_repair)}s)"
    elif pace_delta is not None and pace_delta > 1.5:
        recommend_pit = True
        reason = f"Pace loss {pace_delta:+.2f}s — possible damage"
    elif fuel_laps is not None and fuel_laps < 3:
        recommend_pit = True
        reason = f"Fuel critical ({fuel_laps} laps)"

    return {
        "baseline_lap": round(baseline, 3) if baseline else None,
        "rolling_avg":  round(rolling_avg, 3) if rolling_avg else None,
        "pace_delta":   pace_delta,
        "fuel_level":   round(fuel_level, 2),
        "fuel_laps":    fuel_laps,
        "pit_repair_secs": round(pit_repair, 1),
        "recommend_pit": recommend_pit,
        "reason":       reason,
        "session_time_remain": round(session_time_remain),
    }


def _build_cars(ir, player_idx: int, drivers: list[dict], rolling_avg: float | None) -> list[dict]:
    lap_pcts      = ir["CarIdxLapDistPct"] or []
    laps_complete = ir["CarIdxLapCompleted"] or []
    positions     = ir["CarIdxPosition"] or []
    classes       = ir["CarIdxClass"] or []

    player_pct = lap_pcts[player_idx] if player_idx < len(lap_pcts) else 0.0
    player_lap = laps_complete[player_idx] if player_idx < len(laps_complete) else 0

    cars = []
    for i, drv in enumerate(drivers):
        if i >= len(lap_pcts):
            break
        pct = lap_pcts[i]
        if pct < 0:
            continue

        lap = laps_complete[i] if i < len(laps_complete) else 0
        pos = positions[i] if i < len(positions) else 0
        cls = classes[i] if i < len(classes) else 0

        raw_gap = ((lap + pct) - (player_lap + player_pct))
        # Normalize to ±0.5 lap window
        if raw_gap > 0.5:
            raw_gap -= 1.0
        elif raw_gap < -0.5:
            raw_gap += 1.0

        gap_secs = round(raw_gap * rolling_avg, 2) if rolling_avg else None

        cars.append({
            "idx":       i,
            "name":      drv.get("UserName", ""),
            "car_num":   drv.get("CarNumber", ""),
            "class_id":  cls,
            "position":  pos,
            "lap_pct":   round(pct, 4),
            "is_player": i == player_idx,
            "gap_secs":  gap_secs,
            "gap_str":   _fmt_gap(gap_secs) if gap_secs is not None else "?",
        })

    cars.sort(key=lambda c: c["gap_secs"] if c["gap_secs"] is not None else 999)
    return cars

def _predict_ir_change(ir, player_idx: int, drivers: list[dict]) -> int:
    """Calculates the predicted iRating change based on the Elo rating system within the player's class."""
    if not drivers or player_idx >= len(drivers):
        return 0
    
    player_driver = drivers[player_idx]
    player_class_id = player_driver.get("CarClassID", -1)
    player_irating = player_driver.get("IRating", 1350) or 1350
    
    # We need the current standings order
    positions = ir["CarIdxPosition"] or []
    if player_idx >= len(positions):
        return 0
    
    player_pos = positions[player_idx]
    if player_pos <= 0:
        return 0  # not officially running / scored yet
    
    # Gather other drivers in the same class
    competitors = []
    for idx, drv in enumerate(drivers):
        if idx == player_idx:
            continue
        if idx >= len(positions):
            continue
            
        drv_class_id = drv.get("CarClassID", -1)
        if drv_class_id != player_class_id:
            continue
            
        drv_pos = positions[idx]
        if drv_pos <= 0:
            continue
            
        competitors.append({
            "irating": drv.get("IRating", 1350) or 1350,
            "position": drv_pos
        })
        
    N = len(competitors) + 1
    if N <= 1:
        return 0
        
    # iRating Elo scaling factor K
    K = 200.0 / (N - 1)
    
    elo_sum = 0.0
    for comp in competitors:
        comp_irating = comp["irating"]
        comp_pos = comp["position"]
        
        # Expected probability of us beating them
        P = 1.0 / (1.0 + 10.0 ** ((comp_irating - player_irating) / 400.0))
        
        # Actual result (1.0 if we finish ahead of them, 0.0 if behind)
        if player_pos < comp_pos:
            W = 1.0
        elif player_pos > comp_pos:
            W = 0.0
        else:
            W = 0.5  # tie
            
        elo_sum += (W - P)
        
    return int(round(K * elo_sum))


def _polling_thread():
    global _session_meters, _active_car_path, _active_car_name
    ir = irsdk.IRSDK()
    lap_times: list[float] = []
    last_lap = -1
    iracing_was_connected = False
    
    _load_mileage()
    last_tick_time = time.time()

    while True:
        ir.startup()
        if not ir.is_connected:
            if iracing_was_connected:
                _save_mileage()
                iracing_was_connected = False
                lap_times.clear()
                last_lap = -1
            with _lock:
                _current_data.clear()
            time.sleep(0.5)
            continue

        if not iracing_was_connected:
            iracing_was_connected = True
            _load_mileage()
            _session_meters = 0.0
            last_tick_time = time.time()
            print("[telemetry] iRacing connected")

        ir.freeze_var_buffer_latest()

        # Speed integration for persistent odometer
        now = time.time()
        dt = now - last_tick_time
        last_tick_time = now
        
        speed = ir["Speed"] or 0.0  # m/s
        if speed > 0.1:  # ignore stationary noise
            _session_meters += speed * dt

        player_idx   = ir["PlayerCarIdx"] or 0
        driver_info  = ir["DriverInfo"] or {}
        drivers      = driver_info.get("Drivers", [])
        current_lap  = ir["Lap"] or 0
        last_lap_t   = ir["LapLastLapTime"] or -1

        # Detect active car path/name from drivers list
        p_driver = {}
        if drivers and player_idx < len(drivers):
            p_driver = drivers[player_idx]
            _active_car_path = p_driver.get("CarPath", "unknown").strip("/")
            _active_car_name = p_driver.get("CarScreenName", "Unknown Car")

        if current_lap != last_lap and last_lap_t > 0:
            lap_times.append(last_lap_t)
            if len(lap_times) > 20:
                lap_times.pop(0)
            last_lap = current_lap

        recent = lap_times[-3:] if len(lap_times) >= 3 else lap_times
        rolling_avg = sum(recent) / len(recent) if recent else None

        strategy = _build_strategy(ir, lap_times)
        cars     = _build_cars(ir, player_idx, drivers, rolling_avg)

        player_car = next((c for c in cars if c["is_player"]), {})

        # Compute persistent odometer and iRating metrics
        session_miles = _session_meters * 0.000621371
        
        car_miles_base = 0.0
        if _active_car_path in _mileage_data["cars"]:
            car_miles_base = _mileage_data["cars"][_active_car_path]["miles"]
            
        grand_miles_base = _mileage_data.get("grand_total_miles", 0.0)
        
        car_lifetime_miles = car_miles_base + session_miles
        grand_total_miles = grand_miles_base + session_miles
        
        predicted_ir_change = _predict_ir_change(ir, player_idx, drivers)
        player_starting_ir = p_driver.get("IRating", 1350) or 1350

        payload = {
            "connected":    True,
            "session_state": ir["SessionState"],
            "on_track":     bool(ir["IsOnTrack"]),
            "lap":          current_lap,
            "player_idx":   player_idx,
            "player_car":   player_car,
            "strategy":     strategy,
            "cars":         cars,
            "ts":           time.time(),
            
            # Odometer and iRating predictor details
            "active_car_path":     _active_car_path,
            "active_car_name":     _active_car_name,
            "session_miles":       round(session_miles, 2),
            "car_lifetime_miles":  round(car_lifetime_miles, 1),
            "grand_total_miles":   round(grand_total_miles, 1),
            "player_starting_ir":  player_starting_ir,
            "predicted_ir_change": predicted_ir_change,
        }

        with _lock:
            _current_data.clear()
            _current_data.update(payload)

        time.sleep(0.1)


_clients: set = set()


async def _handler(websocket):
    _clients.add(websocket)
    try:
        while True:
            with _lock:
                data = dict(_current_data)
            await websocket.send(json.dumps(data))
            await asyncio.sleep(0.1)
    except Exception:
        pass
    finally:
        _clients.discard(websocket)


async def _serve():
    import websockets
    async with websockets.serve(_handler, HOST, PORT):
        await asyncio.Future()  # run forever


def _async_thread():
    asyncio.run(_serve())


def start():
    threading.Thread(target=_polling_thread, daemon=True).start()
    threading.Thread(target=_async_thread,   daemon=True).start()
    print(f"[telemetry] WebSocket server on ws://{HOST}:{PORT}")
