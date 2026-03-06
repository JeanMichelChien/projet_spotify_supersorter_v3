from __future__ import annotations

import re
from typing import Any

AUDIO_FEATURE_KEYS = [
    "danceability",
    "energy",
    "valence",
    "tempo",
    "acousticness",
    "instrumentalness",
    "speechiness",
    "liveness",
]


def normalize_text(value: str) -> str:
    lowered = value.lower().strip()
    lowered = re.sub(r"[^a-z0-9\s]", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered.strip()


def parse_release_year(release_date: str | None) -> int | None:
    if not release_date:
        return None
    if len(release_date) >= 4 and release_date[:4].isdigit():
        return int(release_date[:4])
    return None


def normalize_playlist_tracks(
    playlist_id: str,
    playlist_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    for item in playlist_items:
        track = item.get("track")
        if not isinstance(track, dict):
            continue
        if track.get("is_local"):
            continue

        track_id = track.get("id")
        if not track_id:
            continue

        artists = track.get("artists", [])
        artist_ids = [artist.get("id") for artist in artists if artist.get("id")]
        artist_names = [artist.get("name", "") for artist in artists if artist.get("name")]

        album = track.get("album", {}) or {}
        release_date = album.get("release_date")

        records.append(
            {
                "track_id": track_id,
                "track_name": track.get("name", ""),
                "spotify_url": (track.get("external_urls") or {}).get("spotify", ""),
                "playlist_id": playlist_id,
                "artist_ids": artist_ids,
                "artist_names": artist_names,
                "album_name": album.get("name", ""),
                "release_date": release_date,
                "release_year": parse_release_year(release_date),
                "duration_ms": track.get("duration_ms"),
                "added_at": item.get("added_at"),
                "artist_genres_map": {},
                "genre_weight_map": {},
                "genres": [],
                "audio_features_available": False,
                "danceability": None,
                "energy": None,
                "valence": None,
                "tempo": None,
                "acousticness": None,
                "instrumentalness": None,
                "speechiness": None,
                "liveness": None,
            }
        )

    return records


def collect_artist_ids(tracks: list[dict[str, Any]]) -> list[str]:
    artist_ids: set[str] = set()
    for track in tracks:
        for artist_id in track.get("artist_ids", []):
            if artist_id:
                artist_ids.add(artist_id)
    return sorted(artist_ids)


def enrich_tracks_with_artist_genres(
    tracks: list[dict[str, Any]],
    genres_by_artist_id: dict[str, list[str]],
) -> list[dict[str, Any]]:
    for track in tracks:
        artist_ids = track.get("artist_ids", [])

        artist_genres_map: dict[str, list[str]] = {}
        genre_weight_map: dict[str, float] = {}
        # Spotify does not provide track-level genres. We intentionally use
        # only the main artist (first credited artist) to avoid collaborator
        # genres polluting track genre labels.
        main_artist_id = artist_ids[0] if artist_ids else None
        if main_artist_id:
            raw_genres = genres_by_artist_id.get(main_artist_id, [])
            normalized_genres = sorted(
                {
                    normalize_text(genre)
                    for genre in raw_genres
                    if isinstance(genre, str) and normalize_text(genre)
                }
            )
            artist_genres_map[main_artist_id] = normalized_genres

            if normalized_genres:
                per_genre_weight = 1.0 / len(normalized_genres)
                for genre in normalized_genres:
                    genre_weight_map[genre] = genre_weight_map.get(genre, 0.0) + per_genre_weight

        # Keep weight map stable and bounded in [0, 1].
        normalized_weight_map = {
            genre: round(min(max(weight, 0.0), 1.0), 6)
            for genre, weight in sorted(genre_weight_map.items())
        }

        track["artist_genres_map"] = artist_genres_map
        track["genre_weight_map"] = normalized_weight_map
        track["genres"] = sorted(normalized_weight_map.keys())

    return tracks


def enrich_tracks_with_audio_features(
    tracks: list[dict[str, Any]],
    audio_features_by_track_id: dict[str, dict[str, Any]],
    audio_features_available: bool,
) -> list[dict[str, Any]]:
    for track in tracks:
        track_id = track.get("track_id")
        row = audio_features_by_track_id.get(track_id, {}) if track_id else {}

        for key in AUDIO_FEATURE_KEYS:
            track[key] = row.get(key)

        # Mark as available only when endpoint is available and we have concrete values.
        track["audio_features_available"] = bool(
            audio_features_available
            and any(track.get(key) is not None for key in AUDIO_FEATURE_KEYS)
        )

    return tracks
