import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

_TOKEN_FILE = Path(__file__).parent / ".iracing_tokens.json"
_BASE_URL = "https://oauth.iracing.com/oauth2"
_EXPIRY_BUFFER = 30  # seconds before expiry to treat token as stale


def _mask(secret: str, identifier: str) -> str:
    normalized = identifier.strip().lower()
    return base64.b64encode(
        hashlib.sha256(f"{secret}{normalized}".encode()).digest()
    ).decode()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _load_tokens() -> dict:
    if _TOKEN_FILE.exists():
        return json.loads(_TOKEN_FILE.read_text())
    return {}


def _save_tokens(tokens: dict):
    _TOKEN_FILE.write_text(json.dumps(tokens, indent=2))


def _parse_response(resp: dict) -> dict:
    now = time.time()
    tokens = {
        "access_token": resp["access_token"],
        "access_token_expires_at": now + resp["expires_in"] - _EXPIRY_BUFFER,
    }
    if "refresh_token" in resp:
        tokens["refresh_token"] = resp["refresh_token"]
        tokens["refresh_token_expires_at"] = (
            now + resp.get("refresh_token_expires_in", 604800) - _EXPIRY_BUFFER
        )
    return tokens


def _post_token(data: dict) -> dict:
    r = requests.post(f"{_BASE_URL}/token", data=data, timeout=10)
    if r.status_code != 200:
        raise RuntimeError(f"iRacing auth failed ({r.status_code}): {r.text}")
    return _parse_response(r.json())


def _refresh(refresh_token: str) -> dict:
    client_id = os.getenv("IRACING_CLIENT_ID")
    client_secret = os.getenv("IRACING_CLIENT_SECRET", "")

    data = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": refresh_token,
    }
    if client_secret:
        data["client_secret"] = _mask(client_secret, client_id)

    tokens = _post_token(data)
    _save_tokens(tokens)
    return tokens


def _browser_login() -> dict:
    client_id = os.getenv("IRACING_CLIENT_ID")
    if not client_id:
        raise RuntimeError(
            "IRACING_CLIENT_ID not set in .env\n"
            "iRacing OAuth client registration is currently paused — "
            "email auth@iracing.com to request access."
        )

    client_secret = os.getenv("IRACING_CLIENT_SECRET", "")

    # PKCE
    code_verifier = _b64url(secrets.token_bytes(32))
    code_challenge = _b64url(hashlib.sha256(code_verifier.encode()).digest())
    state = secrets.token_hex(16)
    auth_code = None

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal auth_code
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            auth_code = params.get("code", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body><h2>Logged in — you can close this tab.</h2></body></html>")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}/callback"

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        "scope": "iracing.auth",
    }
    auth_url = f"{_BASE_URL}/authorize?" + urllib.parse.urlencode(params)

    print(f"Opening browser for iRacing login...\n{auth_url}\n")
    webbrowser.open(auth_url)
    server.handle_request()
    server.server_close()

    if not auth_code:
        raise RuntimeError("No auth code received — login may have been cancelled")

    data = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": auth_code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }
    if client_secret:
        data["client_secret"] = _mask(client_secret, client_id)

    tokens = _post_token(data)
    _save_tokens(tokens)
    return tokens


def get_access_token() -> str:
    tokens = _load_tokens()
    now = time.time()

    if tokens.get("access_token") and now < tokens.get("access_token_expires_at", 0):
        return tokens["access_token"]

    refresh_token = tokens.get("refresh_token")
    if refresh_token and now < tokens.get("refresh_token_expires_at", 0):
        return _refresh(refresh_token)["access_token"]

    return _browser_login()["access_token"]
