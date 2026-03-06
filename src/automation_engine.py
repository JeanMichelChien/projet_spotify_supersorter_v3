from __future__ import annotations

from datetime import datetime
from typing import Any

import spotipy

from src.cache_store import CacheStore, utc_now_iso
from src.enrichment import collect_artist_ids, enrich_tracks_with_artist_genres, normalize_playlist_tracks
from src.enrichment import normalize_text
from src.spotify_api import (
    add_playlist_items,
    create_private_playlist,
    ensure_playlist_private,
    fetch_artists_by_ids,
    fetch_playlist_tracks,
    fetch_saved_tracks,
    get_current_user_profile,
    replace_playlist_items,
)

LIKED_SONGS_SOURCE_ID = "__liked_songs__"
LIKED_SONGS_SOURCE_LABEL = "Liked Songs"
AUTOMATION_SOURCE_FIELDS = "items(added_at,track(id,name,is_local,artists(id,name))),next,total"


def _parse_added_at(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        return datetime.min
    raw = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return datetime.min


def _query_matches_genre(track_genre: str, selected_genre: str) -> bool:
    track_norm = normalize_text(track_genre)
    selected_norm = normalize_text(selected_genre)
    if not track_norm or not selected_norm:
        return False
    return selected_norm in track_norm or track_norm in selected_norm


def filter_tracks_for_automation(
    tracks: list[dict[str, Any]],
    genres: list[str],
    genre_mode: str,
) -> list[dict[str, Any]]:
    selected = [normalize_text(genre) for genre in genres if normalize_text(genre)]
    if not selected:
        return []

    filtered: list[dict[str, Any]] = []
    for track in tracks:
        track_genres = [
            normalize_text(genre)
            for genre in track.get("genres", [])
            if normalize_text(genre)
        ]
        if not track_genres:
            continue

        matched = [
            any(_query_matches_genre(track_genre, query) for track_genre in track_genres)
            for query in selected
        ]
        mode = str(genre_mode or "OR").upper()
        if (mode == "AND" and all(matched)) or (mode != "AND" and any(matched)):
            filtered.append(track)

    filtered.sort(
        key=lambda row: (
            _parse_added_at(row.get("added_at")),
            str(row.get("track_name", "")).lower(),
            str(row.get("track_id", "")),
        ),
        reverse=True,
    )
    return filtered


def ordered_unique_track_ids(tracks: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for row in tracks:
        track_id = row.get("track_id")
        if not track_id or track_id in seen:
            continue
        seen.add(track_id)
        output.append(track_id)
    return output


def _resolve_source_tracks(
    sp: spotipy.Spotify,
    cache_store: CacheStore,
    rule: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    source_type = str(rule.get("source_type", "liked_songs"))
    source_playlist_id = rule.get("source_playlist_id")

    if source_type == "playlist":
        if not source_playlist_id:
            raise RuntimeError("Automation source is playlist but source_playlist_id is missing.")
        source_label = str(rule.get("source_label") or source_playlist_id)
        source_items = fetch_playlist_tracks(
            sp,
            source_playlist_id,
            fields=AUTOMATION_SOURCE_FIELDS,
        )
        normalized = normalize_playlist_tracks(source_playlist_id, source_items)
    else:
        source_label = LIKED_SONGS_SOURCE_LABEL
        source_items = fetch_saved_tracks(sp, fields=AUTOMATION_SOURCE_FIELDS)
        normalized = normalize_playlist_tracks(LIKED_SONGS_SOURCE_ID, source_items)

    artist_ids = collect_artist_ids(normalized)
    genres_by_artist_id: dict[str, list[str]] = {}
    missing_artist_ids: list[str] = []
    for artist_id in artist_ids:
        cached_artist = cache_store.get_artist_cache(artist_id)
        if cached_artist and isinstance(cached_artist.get("genres"), list):
            genres_by_artist_id[artist_id] = cached_artist["genres"]
        else:
            missing_artist_ids.append(artist_id)

    if missing_artist_ids:
        fetched_artists = fetch_artists_by_ids(sp, missing_artist_ids)
        for artist in fetched_artists:
            artist_id = artist.get("id")
            if not artist_id:
                continue
            genres = artist.get("genres", [])
            genres_by_artist_id[artist_id] = genres
            cache_store.set_artist_cache(artist_id, genres)

    enriched = enrich_tracks_with_artist_genres(normalized, genres_by_artist_id)
    return enriched, source_label


def _ensure_target_playlist(
    sp: spotipy.Spotify,
    rule: dict[str, Any],
) -> tuple[str, str]:
    target_playlist_id = rule.get("target_playlist_id")
    target_playlist_name = str(rule.get("target_playlist_name") or rule.get("name") or "Auto playlist")

    if target_playlist_id:
        # Avoid metadata read on every sync; use stored target info.
        return str(target_playlist_id), target_playlist_name

    profile = get_current_user_profile(sp)
    user_id = profile.get("id")
    if not user_id:
        raise RuntimeError("Could not determine current Spotify user id for playlist creation.")

    created = create_private_playlist(
        sp=sp,
        user_id=user_id,
        name=target_playlist_name,
        description="Automatically managed by Spotify SuperSorter",
    )
    created_id = created.get("id")
    if not created_id:
        raise RuntimeError("Spotify playlist creation did not return a playlist id.")
    resolved_id = str(created_id)
    # Enforce private once on creation.
    ensure_playlist_private(sp=sp, playlist_id=resolved_id)
    return resolved_id, str(created.get("name") or target_playlist_name)


def sync_automation_rule(
    sp: spotipy.Spotify,
    cache_store: CacheStore,
    rule: dict[str, Any],
    allow_empty_clear: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    now = utc_now_iso()
    updated_rule = dict(rule)

    try:
        source_tracks, source_label = _resolve_source_tracks(sp, cache_store, updated_rule)
        updated_rule["source_label"] = source_label
        matched_tracks = filter_tracks_for_automation(
            source_tracks,
            genres=updated_rule.get("genres", []),
            genre_mode=str(updated_rule.get("genre_mode", "OR")),
        )
        matched_track_ids = ordered_unique_track_ids(matched_tracks)

        if not matched_track_ids and not allow_empty_clear:
            result = {
                "status": "warning",
                "matched_count": 0,
                "written_count": 0,
                "message": "No matching tracks. Confirm clear to empty the target playlist.",
                "requires_clear_confirmation": True,
            }
            updated_rule["last_sync_at"] = now
            updated_rule["last_sync_result"] = result
            return updated_rule, result

        target_id, target_name = _ensure_target_playlist(sp, updated_rule)
        updated_rule["target_playlist_id"] = target_id
        updated_rule["target_playlist_name"] = target_name

        replace_playlist_items(
            sp=sp,
            playlist_id=target_id,
            track_ids=matched_track_ids[:100],
        )
        if len(matched_track_ids) > 100:
            add_playlist_items(
                sp=sp,
                playlist_id=target_id,
                track_ids=matched_track_ids[100:],
            )

        result = {
            "status": "success",
            "matched_count": len(matched_track_ids),
            "written_count": len(matched_track_ids),
            "message": f"Synced {len(matched_track_ids)} tracks to '{target_name}'.",
            "requires_clear_confirmation": False,
        }
        updated_rule["last_sync_at"] = now
        updated_rule["last_sync_result"] = result
        return updated_rule, result
    except Exception as exc:  # noqa: BLE001
        result = {
            "status": "error",
            "matched_count": 0,
            "written_count": 0,
            "message": f"Sync failed: {exc}",
            "requires_clear_confirmation": False,
        }
        updated_rule["last_sync_at"] = now
        updated_rule["last_sync_result"] = result
        return updated_rule, result
