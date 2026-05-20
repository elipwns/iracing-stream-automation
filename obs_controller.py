import os
from dotenv import load_dotenv
import obsws_python as obs

load_dotenv()

_client = None


def connect():
    global _client
    _client = obs.ReqClient(
        host=os.getenv("OBS_HOST", "localhost"),
        port=int(os.getenv("OBS_PORT", 4455)),
        password=os.getenv("OBS_PASSWORD", ""),
        timeout=3,
    )


def _get_client():
    if _client is None:
        connect()
    return _client


def switch_scene(scene_name: str):
    _get_client().set_current_program_scene(scene_name)


def get_current_scene() -> str:
    return _get_client().get_current_program_scene().current_program_scene_name


def set_text_source(source_name: str, text: str):
    _get_client().set_input_settings(source_name, {"text": text}, overlay=True)


def get_media_state(source_name: str) -> str:
    """Returns the OBS media state string for a media input source."""
    resp = _get_client().get_media_input_status(source_name)
    return resp.media_state


def stop_stream():
    _get_client().stop_stream()
