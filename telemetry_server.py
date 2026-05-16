import asyncio
import json
import threading
import time
import irsdk

HOST = "localhost"
PORT = 8765

_current_data: dict = {}
_lock = threading.Lock()


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


def _polling_thread():
    ir = irsdk.IRSDK()
    lap_times: list[float] = []
    last_lap = -1

    while True:
        ir.startup()
        if not ir.is_connected:
            with _lock:
                _current_data.clear()
            time.sleep(0.5)
            continue

        ir.freeze_var_buffer_latest()

        player_idx   = ir["PlayerCarIdx"] or 0
        driver_info  = ir["DriverInfo"] or {}
        drivers      = driver_info.get("Drivers", [])
        current_lap  = ir["Lap"] or 0
        last_lap_t   = ir["LapLastLapTime"] or -1

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
