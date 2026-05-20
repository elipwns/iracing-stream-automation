import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent / "chat_bot.db"
STARTING_POINTS = 1000
RESET_POINTS = 1000
VALID_OUTCOMES = {"win", "podium", "finish", "crash"}

# (min_lap, multiplier) — first matching entry wins
_ODDS = [
    (8, None),   # betting closes
    (5, 1.2),
    (3, 1.5),
    (1, 2.0),
    (0, 3.0),    # pre-race default
]


@contextmanager
def _db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with _db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                points INTEGER NOT NULL DEFAULT 1000
            );
            CREATE TABLE IF NOT EXISTS bets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                username TEXT NOT NULL,
                outcome TEXT NOT NULL,
                amount INTEGER NOT NULL,
                multiplier REAL NOT NULL,
                race_id TEXT NOT NULL,
                placed_at REAL NOT NULL,
                won INTEGER DEFAULT NULL
            );
            CREATE TABLE IF NOT EXISTS race_state (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS chat_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message TEXT NOT NULL,
                sent INTEGER NOT NULL DEFAULT 0
            );
        """)


def _state(conn, key, default=None):
    row = conn.execute("SELECT value FROM race_state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def _set_state(conn, key, value):
    conn.execute(
        "INSERT INTO race_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value) if value is not None else ""),
    )


def _queue(conn, message: str):
    conn.execute("INSERT INTO chat_queue(message,sent) VALUES(?,0)", (message,))


def _multiplier_for_lap(lap: int) -> float | None:
    for min_lap, mult in _ODDS:
        if lap >= min_lap:
            return mult
    return 3.0


def open_betting(race_id: str):
    init_db()
    with _db() as conn:
        conn.execute("DELETE FROM chat_queue WHERE sent=0")
        _set_state(conn, "race_id", race_id)
        _set_state(conn, "betting_open", "1")
        _set_state(conn, "current_multiplier", "3.0")
        _set_state(conn, "current_lap", "0")
        _queue(conn, "Betting is OPEN! Use !bet [win/podium/finish/crash] [amount or all] — 3x payout pre-race!")


def update_lap(lap: int):
    with _db() as conn:
        if _state(conn, "betting_open") != "1":
            return

        prev_lap = int(_state(conn, "current_lap", "0"))
        if lap <= prev_lap:
            return
        _set_state(conn, "current_lap", str(lap))

        mult = _multiplier_for_lap(lap)
        prev_mult = _state(conn, "current_multiplier", "3.0")

        if mult is None:
            _set_state(conn, "betting_open", "0")
            _queue(conn, "Betting is CLOSED for this race!")
            return

        if str(mult) != prev_mult:
            _set_state(conn, "current_multiplier", str(mult))
            _queue(conn, f"Odds updated: {mult}x payout (lap {lap}). !bet to get in!")


def close_betting():
    with _db() as conn:
        _set_state(conn, "betting_open", "0")


def place_bet(user_id: str, username: str, outcome: str, amount_str: str) -> tuple[bool, str]:
    if outcome not in VALID_OUTCOMES:
        return False, "Pick an outcome: win, podium, finish, or crash"

    with _db() as conn:
        if _state(conn, "betting_open") != "1":
            return False, "Betting isn't open right now."

        race_id = _state(conn, "race_id", "")
        multiplier = float(_state(conn, "current_multiplier", "1.0"))

        existing = conn.execute(
            "SELECT id FROM bets WHERE user_id=? AND race_id=?", (user_id, race_id)
        ).fetchone()
        if existing:
            return False, "You already have a bet this race."

        user = conn.execute("SELECT points FROM users WHERE user_id=?", (user_id,)).fetchone()
        if user:
            points = user["points"]
        else:
            conn.execute(
                "INSERT INTO users(user_id,username,points) VALUES(?,?,?)",
                (user_id, username, STARTING_POINTS),
            )
            points = STARTING_POINTS

        if points <= 0:
            conn.execute(
                "UPDATE users SET points=?,username=? WHERE user_id=?",
                (RESET_POINTS, username, user_id),
            )
            points = RESET_POINTS
            _queue(conn, f"@{username} was broke and got reset to {RESET_POINTS:,} points!")

        if amount_str == "all":
            amount = points
        else:
            try:
                amount = int(amount_str)
            except ValueError:
                return False, "Amount must be a number or 'all'."

        if amount <= 0:
            return False, "Amount must be positive."
        if amount > points:
            return False, f"Not enough points — you have {points:,}."

        conn.execute(
            "UPDATE users SET points=points-?,username=? WHERE user_id=?",
            (amount, username, user_id),
        )
        conn.execute(
            "INSERT INTO bets(user_id,username,outcome,amount,multiplier,race_id,placed_at) VALUES(?,?,?,?,?,?,?)",
            (user_id, username, outcome, amount, multiplier, race_id, time.time()),
        )

        potential = int(amount * multiplier)
        return True, f"Bet placed! {amount:,} on {outcome} at {multiplier}x = {potential:,} if correct"


def get_points(user_id: str, username: str) -> int:
    init_db()
    with _db() as conn:
        user = conn.execute("SELECT points FROM users WHERE user_id=?", (user_id,)).fetchone()
        if user:
            return user["points"]
        conn.execute(
            "INSERT INTO users(user_id,username,points) VALUES(?,?,?)",
            (user_id, username, STARTING_POINTS),
        )
        return STARTING_POINTS


def get_active_bets() -> list[dict]:
    with _db() as conn:
        race_id = _state(conn, "race_id", "")
        rows = conn.execute(
            "SELECT username,outcome,amount,multiplier FROM bets WHERE race_id=? AND won IS NULL ORDER BY placed_at",
            (race_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_leaderboard(limit: int = 5) -> list[dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT username,points FROM users ORDER BY points DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def resolve_bets(race_data: dict):
    my = race_data.get("my_result")
    if not my:
        return

    total_laps = race_data.get("total_laps", 1) or 1
    laps = my.get("laps_complete", 0)
    pos = my.get("finish_position", 99)

    if laps < total_laps * 0.85:
        actual = "crash"
    elif pos == 1:
        actual = "win"
    elif pos <= 3:
        actual = "podium"
    else:
        actual = "finish"

    _LABELS = {"win": "WIN", "podium": "PODIUM", "finish": "normal finish", "crash": "CRASH/DNF"}

    with _db() as conn:
        race_id = _state(conn, "race_id", "")
        _set_state(conn, "betting_open", "0")

        pending = conn.execute(
            "SELECT * FROM bets WHERE race_id=? AND won IS NULL", (race_id,)
        ).fetchall()

        winners = []
        for bet in pending:
            won = bet["outcome"] == actual
            conn.execute("UPDATE bets SET won=? WHERE id=?", (1 if won else 0, bet["id"]))
            if won:
                payout = int(bet["amount"] * bet["multiplier"])
                conn.execute(
                    "UPDATE users SET points=points+? WHERE user_id=?", (payout, bet["user_id"])
                )
                winners.append((bet["username"], payout))

        label = _LABELS.get(actual, actual)
        if winners:
            top = sorted(winners, key=lambda x: x[1], reverse=True)[:3]
            winner_str = " | ".join(f"{u} +{p:,}" for u, p in top)
            _queue(conn, f"Race result: {label}! Winners: {winner_str}")
        else:
            _queue(conn, f"Race result: {label}! No winners this time.")


def dequeue_messages() -> list[str]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT id,message FROM chat_queue WHERE sent=0 ORDER BY id"
        ).fetchall()
        if rows:
            ids = [r["id"] for r in rows]
            conn.execute(
                f"UPDATE chat_queue SET sent=1 WHERE id IN ({','.join('?'*len(ids))})", ids
            )
        return [r["message"] for r in rows]
