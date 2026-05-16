import os
import requests
from dotenv import load_dotenv

load_dotenv()

WLED_IP = os.getenv("WLED_IP", "192.168.1.1")

EFFECT_BREATHE = 11
EFFECT_CONFETTI = 78
EFFECT_SOLID = 0

EVENTS = {
    "idle":        {"seg": [{"col": [[80, 80, 80]], "fx": EFFECT_SOLID}], "bri": 60,  "on": True},
    # flags — ordered by severity
    "black_flag":  {"seg": [{"col": [[180, 0, 0]],  "fx": EFFECT_BREATHE, "sx": 220}], "bri": 255, "on": True},
    "meatball":    {"seg": [{"col": [[255, 100, 0]], "fx": EFFECT_BREATHE, "sx": 160}], "bri": 220, "on": True},
    "caution":     {"seg": [{"col": [[255, 200, 0]], "fx": EFFECT_BREATHE, "sx": 150}], "bri": 255, "on": True},
    "green_flag":  {"seg": [{"col": [[0, 255, 80]],  "fx": EFFECT_BREATHE, "sx": 80}],  "bri": 220, "on": True},
    "white_flag":  {"seg": [{"col": [[255, 255, 255]], "fx": EFFECT_SOLID}], "bri": 130, "on": True},
    # session states
    "checkered":   {"seg": [{"col": [[220, 230, 255]], "fx": EFFECT_BREATHE, "sx": 40}], "bri": 160, "on": True},
    "race_end":    {"seg": [{"col": [[0, 220, 80]],   "fx": EFFECT_SOLID}], "bri": 200, "on": True},
    "podium":      {"seg": [{"fx": EFFECT_CONFETTI, "sx": 200}], "bri": 255, "on": True},
    "incident":    {"seg": [{"col": [[255, 0, 0]],   "fx": EFFECT_SOLID}], "bri": 255, "on": True},
}


def _post(payload: dict):
    try:
        requests.post(f"http://{WLED_IP}/json/state", json=payload, timeout=2)
    except Exception:
        pass


def set_color(r: int, g: int, b: int):
    _post({"seg": [{"col": [[r, g, b]]}], "on": True})


def set_effect(effect_id: int, color: tuple | None = None):
    seg = {"fx": effect_id}
    if color:
        seg["col"] = [list(color)]
    _post({"seg": [seg], "on": True})


def set_brightness(level: int):
    _post({"bri": level})


def set_event(event: str):
    payload = EVENTS.get(event)
    if payload:
        _post(payload)
