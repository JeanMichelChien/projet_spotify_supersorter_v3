from __future__ import annotations

import time
from typing import Any, Callable, Iterable

import requests
import spotipy
from spotipy.exceptions import SpotifyException

TRANSIENT_HTTP_STATUS = {429, 500, 502, 503, 504}


class SpotifyAPIError(RuntimeError):
    """Raised when Spotify API requests fail after retries."""


def _chunked(values: list[str], chunk_size: int) -> Iterable[list[str]]:
    for start in range(0, len(values), chunk_size):
        yield values[start : start + chunk_size]


def _with_retries(
    fn: Callable[..., Any],
    *args: Any,
    max_attempts: int = 5,
    initial_delay: float = 0.5,
    **kwargs: Any,
) -> Any:
    delay = initial_delay
    last_exc: Exception | None = None

    for _ in range(max_attempts):
        try:
            return fn(*args, **kwargs)
        except SpotifyException as exc:
            last_exc = exc
            status = getattr(exc, "http_status", None)
            if status in TRANSIENT_HTTP_STATUS:
                retry_after = None
                headers = getattr(exc, "headers", None)
                if isinstance(headers, dict):
                    retry_after_header = headers.get("Retry-After") or headers.get("retry-after")
                    if retry_after_header:
                        try:
                            retry_after = float(retry_after_header)
                        except ValueError:
                            retry_after = None
                time.sleep(retry_after or delay)
                delay = min(delay * 2.0, 8.0)
                continue
            raise
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            time.sleep(delay)
            delay = min(delay * 2.0, 8.0)

    raise SpotifyAPIError(f"Spotify request failed after retries: {last_exc}")


def get_current_user_profile(sp: spotipy.Spotify) -> dict[str, Any]:
    return _with_retries(sp.current_user)


def fetch_all_playlists(sp: spotipy.Spotify) -> list[dict[str, Any]]:
    """Fetch all current-user playlists with pagination."""
    playlists: list[dict[str, Any]] = []
    offset = 0
    limit = 50

    while True:
        page = _with_retries(sp.current_user_playlists, limit=limit, offset=offset)
        items = page.get("items", [])
        playlists.extend(items)
        if len(items) < limit:
            break
        offset += limit

    return playlists


def fetch_playlist_tracks(
    sp: spotipy.Spotify,
    playlist_id: str,
    fields: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch all tracks from a playlist with pagination."""
    items: list[dict[str, Any]] = []
    offset = 0
    limit = 100

    while True:
        params: dict[str, Any] = {
            "playlist_id": playlist_id,
            "limit": limit,
            "offset": offset,
            "additional_types": ("track",),
        }
        if fields:
            params["fields"] = fields
        page = _with_retries(sp.playlist_items, **params)
        batch = page.get("items", [])
        items.extend(batch)
        if len(batch) < limit:
            break
        offset += limit

    return items


def fetch_playlist_metadata(
    sp: spotipy.Spotify,
    playlist_id: str,
    fields: str = "id,name,public",
) -> dict[str, Any]:
    """Fetch lightweight playlist metadata to validate source accessibility."""
    return _with_retries(
        sp.playlist,
        playlist_id=playlist_id,
        fields=fields,
    )


def fetch_saved_tracks_total(sp: spotipy.Spotify) -> int:
    """Fetch total count of user's liked songs."""
    page = _with_retries(sp.current_user_saved_tracks, limit=1, offset=0)
    return int(page.get("total", 0) or 0)


def fetch_saved_tracks(
    sp: spotipy.Spotify,
    fields: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch all user's liked songs with pagination."""
    items: list[dict[str, Any]] = []
    offset = 0
    limit = 50

    while True:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if fields:
            params["fields"] = fields
        page = _with_retries(sp.current_user_saved_tracks, **params)
        batch = page.get("items", [])
        items.extend(batch)
        if len(batch) < limit:
            break
        offset += limit

    return items


def create_private_playlist(
    sp: spotipy.Spotify,
    user_id: str,
    name: str,
    description: str = "",
) -> dict[str, Any]:
    """Create a new private playlist."""
    return _with_retries(
        sp.user_playlist_create,
        user=user_id,
        name=name,
        public=False,
        collaborative=False,
        description=description,
    )


def ensure_playlist_private(sp: spotipy.Spotify, playlist_id: str) -> None:
    """Force playlist visibility to private."""
    _with_retries(
        sp.playlist_change_details,
        playlist_id=playlist_id,
        public=False,
        collaborative=False,
    )


def fetch_playlist_track_ids(sp: spotipy.Spotify, playlist_id: str) -> list[str]:
    """Fetch unique track ids currently present in a playlist."""
    items = fetch_playlist_tracks(sp, playlist_id)
    track_ids: list[str] = []
    seen: set[str] = set()
    for item in items:
        track = item.get("track") if isinstance(item, dict) else None
        track_id = track.get("id") if isinstance(track, dict) else None
        if track_id and track_id not in seen:
            seen.add(track_id)
            track_ids.append(track_id)
    return track_ids


def replace_playlist_items(sp: spotipy.Spotify, playlist_id: str, track_ids: list[str]) -> None:
    """
    Replace playlist items with first <=100 track ids.

    Spotify endpoint accepts up to 100 ids for replace.
    """
    first_chunk = [track_id for track_id in track_ids[:100] if track_id]
    _with_retries(sp.playlist_replace_items, playlist_id=playlist_id, items=first_chunk)


def add_playlist_items(sp: spotipy.Spotify, playlist_id: str, track_ids: list[str]) -> None:
    """Add items to a playlist in chunks of 100."""
    cleaned = [track_id for track_id in track_ids if track_id]
    for batch in _chunked(cleaned, 100):
        _with_retries(sp.playlist_add_items, playlist_id=playlist_id, items=batch)


def fetch_artists_by_ids(sp: spotipy.Spotify, artist_ids: list[str]) -> list[dict[str, Any]]:
    """Fetch artists in batches of 50 IDs."""
    if not artist_ids:
        return []

    unique_ids = sorted({artist_id for artist_id in artist_ids if artist_id})
    artists: list[dict[str, Any]] = []
    for batch in _chunked(unique_ids, 50):
        response = _with_retries(sp.artists, batch)
        artists.extend(response.get("artists", []))
    return artists


def fetch_audio_features_by_track_ids(
    sp: spotipy.Spotify, track_ids: list[str]
) -> tuple[dict[str, dict[str, Any]], bool, str | None]:
    """
    Fetch audio features in batches of 100 IDs.

    Returns (features_by_track_id, audio_features_available, warning_message).
    """
    if not track_ids:
        return {}, True, None

    unique_ids = sorted({track_id for track_id in track_ids if track_id})
    audio_map: dict[str, dict[str, Any]] = {}

    try:
        for batch in _chunked(unique_ids, 100):
            response = _with_retries(sp.audio_features, batch)
            for feature_row in response or []:
                if not feature_row:
                    continue
                track_id = feature_row.get("id")
                if track_id:
                    audio_map[track_id] = feature_row
        return audio_map, True, None
    except Exception as exc:  # noqa: BLE001
        return {}, False, f"Audio features unavailable: {exc}"
