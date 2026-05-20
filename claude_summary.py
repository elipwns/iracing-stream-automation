import json
import anthropic
from dotenv import load_dotenv

load_dotenv()

RACE_SYSTEM_PROMPT = (
    "You are a motorsport broadcast analyst writing a post-race summary for an iRacing stream. "
    "The streamer is the driver whose result is under 'my_finish', 'my_best_lap', 'my_incidents', 'my_laps_led'. "
    "Write 3-4 sentences in a professional but engaging tone. "
    "Use race_events to describe what happened during the race — flags like meatball (damage taken), "
    "local yellows (chaotic sectors), and cautions tell the story of the race. "
    "Also mention: finishing position out of field size, best lap, incident counts from the classification "
    "(call them out if > 0), laps led if notable, and any retirements (status != 'Running'). "
    "Do not mention official/unofficial status. Do not mention iRating or safety rating. "
    "Do not invent details not present in the data."
)

SESSION_SYSTEM_PROMPT = (
    "You are a motorsport broadcast analyst writing an end-of-stream recap for an iRacing "
    "stream session. Write 4-5 sentences covering the full session: overall performance arc, "
    "best and worst results, and any standout moments. "
    "Omit iRating or championship points unless they are present in the data. "
    "Professional but enthusiastic tone. "
    "Do not make up details not present in the data provided."
)

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def generate_summary(race_data: dict) -> str:
    response = _get_client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        system=[{"type": "text", "text": RACE_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": f"Race data:\n{json.dumps(race_data, indent=2)}"}],
    )
    return response.content[0].text


def generate_session_recap(session_data: dict) -> str:
    response = _get_client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        system=[{"type": "text", "text": SESSION_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": f"Session data:\n{json.dumps(session_data, indent=2)}"}],
    )
    return response.content[0].text
