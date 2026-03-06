from __future__ import annotations

import os
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import spotipy
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth

REQUIRED_SCOPES = [
    "playlist-read-private",
    "playlist-read-collaborative",
    "playlist-modify-private",
    "user-library-read",
]
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8888/callback"
DEFAULT_CACHE_PATH = ".spotify_cache"


class AuthConfigurationError(RuntimeError):
    """Raised when Spotify OAuth cannot be configured."""


class AuthFlowError(RuntimeError):
    """Raised when interactive Spotify OAuth flow fails."""


@dataclass
class AuthSession:
    client: spotipy.Spotify
    token_info: dict[str, Any]


def _scope_string() -> str:
    return " ".join(REQUIRED_SCOPES)


def create_auth_manager() -> SpotifyOAuth:
    """Create SpotifyOAuth from local .env configuration."""
    load_dotenv()
    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI", DEFAULT_REDIRECT_URI)

    missing: list[str] = []
    if not client_id:
        missing.append("SPOTIFY_CLIENT_ID")
    if not client_secret:
        missing.append("SPOTIFY_CLIENT_SECRET")

    if missing:
        raise AuthConfigurationError(
            "Missing required environment variable(s): " + ", ".join(missing)
        )

    return SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=_scope_string(),
        open_browser=False,
        cache_path=DEFAULT_CACHE_PATH,
    )


def get_cached_session(auth_manager: SpotifyOAuth) -> AuthSession | None:
    """Return a cached Spotify session if token exists and is valid."""
    token_info = auth_manager.get_cached_token()
    if token_info and auth_manager.validate_token(token_info):
        return AuthSession(
            client=spotipy.Spotify(auth=token_info["access_token"]),
            token_info=token_info,
        )
    return None


def get_missing_scopes(token_info: dict[str, Any] | None) -> list[str]:
    """Compare token scopes to required scopes and return missing entries."""
    if not token_info:
        return REQUIRED_SCOPES

    scope_string = token_info.get("scope", "")
    granted = {scope.strip() for scope in scope_string.split() if scope.strip()}
    return [scope for scope in REQUIRED_SCOPES if scope not in granted]


def authenticate_with_local_callback(
    auth_manager: SpotifyOAuth,
    timeout_seconds: int = 180,
) -> AuthSession:
    """
    Run browser OAuth flow and capture callback on a local HTTP server.

    Supports:
    - direct loopback redirect URIs (e.g. http://127.0.0.1:8888/callback)
    - HTTPS tunnel redirect URIs (e.g. ngrok), when tunnel forwards to local bind host/port
    """
    parsed = urllib.parse.urlparse(auth_manager.redirect_uri)
    expected_path = parsed.path or "/"
    bind_host = os.getenv("SPOTIFY_CALLBACK_BIND_HOST", "127.0.0.1")
    bind_port_raw = os.getenv("SPOTIFY_CALLBACK_BIND_PORT")
    if bind_port_raw:
        try:
            bind_port = int(bind_port_raw)
        except ValueError as exc:
            raise AuthFlowError(
                "SPOTIFY_CALLBACK_BIND_PORT must be an integer. "
                f"Current value: {bind_port_raw}"
            ) from exc
    elif parsed.hostname in {"127.0.0.1", "localhost"} and parsed.port:
        bind_port = parsed.port
    else:
        bind_port = 8888

    auth_payload: dict[str, str | None] = {"code": None, "error": None}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed_request = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed_request.query)

            if parsed_request.path != expected_path:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"Invalid callback path.")
                return

            auth_payload["code"] = params.get("code", [None])[0]
            auth_payload["error"] = params.get("error", [None])[0]

            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                b"Spotify authorization complete. You can close this tab and return to Streamlit."
            )

        def log_message(self, fmt: str, *args: object) -> None:
            return

    try:
        server = HTTPServer((bind_host, bind_port), CallbackHandler)
    except OSError as exc:
        raise AuthFlowError(
            "Failed to start local callback server. "
            "Check SPOTIFY_CALLBACK_BIND_HOST/SPOTIFY_CALLBACK_BIND_PORT and whether the local port is free. "
            f"Current bind target: {bind_host}:{bind_port}"
        ) from exc

    try:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        webbrowser.open(auth_manager.get_authorize_url())

        started_at = time.time()
        while not auth_payload["code"] and not auth_payload["error"]:
            if time.time() - started_at > timeout_seconds:
                raise AuthFlowError(
                    "Timed out waiting for Spotify callback. Confirm the app opened in your browser "
                    "and that SPOTIFY_REDIRECT_URI is whitelisted in your Spotify dashboard. "
                    "If using an HTTPS tunnel (ngrok), ensure it forwards to "
                    f"http://{bind_host}:{bind_port}{expected_path}."
                )
            time.sleep(0.2)

        if auth_payload["error"]:
            raise AuthFlowError(f"Spotify authorization failed: {auth_payload['error']}")

        token_result = auth_manager.get_access_token(auth_payload["code"], as_dict=False)
        if isinstance(token_result, str):
            access_token = token_result
            token_info = auth_manager.get_cached_token() or {"access_token": access_token}
        elif isinstance(token_result, dict):
            access_token = token_result.get("access_token")
            token_info = token_result
        else:
            token_info = auth_manager.get_cached_token() or {}
            access_token = token_info.get("access_token")

        if not access_token:
            raise AuthFlowError("Spotify did not return an access token.")

        return AuthSession(client=spotipy.Spotify(auth=access_token), token_info=token_info)
    finally:
        server.shutdown()
        server.server_close()
