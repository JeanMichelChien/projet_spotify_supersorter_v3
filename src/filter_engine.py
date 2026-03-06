from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.enrichment import normalize_text


@dataclass
class FilterConfig:
    mode: str = "AND"
    selected_genres: list[str] = field(default_factory=list)
    selected_artists: list[str] = field(default_factory=list)
    year_range: tuple[int, int] | None = None
    year_bounds: tuple[int, int] | None = None
    audio_ranges: dict[str, tuple[float, float]] = field(default_factory=dict)
    audio_bounds: dict[str, tuple[float, float]] = field(default_factory=dict)


def _range_active(selected: tuple[float, float] | None, bounds: tuple[float, float] | None) -> bool:
    if selected is None or bounds is None:
        return False
    return float(selected[0]) > float(bounds[0]) or float(selected[1]) < float(bounds[1])


def _genre_query_matches(artist_genre: str, selected_genre: str) -> bool:
    artist_norm = normalize_text(artist_genre)
    selected_norm = normalize_text(selected_genre)
    if not artist_norm or not selected_norm:
        return False
    return selected_norm in artist_norm or artist_norm in selected_norm


def _evaluate_genre_group(
    track: dict[str, Any],
    selected_genres: list[str],
) -> tuple[bool, float, list[str]]:
    if not selected_genres:
        return True, 0.0, []

    reasons: list[str] = []
    selected_norm = [normalize_text(genre) for genre in selected_genres if normalize_text(genre)]
    artist_ids = track.get("artist_ids", [])
    artist_names = track.get("artist_names", [])
    artist_genres_map = track.get("artist_genres_map", {})

    if not artist_ids:
        return False, 0.0, ["No artist metadata available for genre matching"]

    evaluated_artist_ids = [artist_id for artist_id in artist_ids if artist_genres_map.get(artist_id)]
    if not evaluated_artist_ids:
        return False, 0.0, ["No artist metadata available for genre matching"]

    artist_weight = 1.0 / len(evaluated_artist_ids)
    matched_weight = 0.0

    index_by_artist_id = {artist_id: idx for idx, artist_id in enumerate(artist_ids)}
    for artist_id in evaluated_artist_ids:
        idx = index_by_artist_id.get(artist_id, -1)
        genres = artist_genres_map.get(artist_id, [])
        matched_queries = {
            query
            for query in selected_norm
            if any(_genre_query_matches(genre, query) for genre in genres)
        }
        if matched_queries:
            matched_weight += artist_weight
            artist_name = artist_names[idx] if 0 <= idx < len(artist_names) else artist_id
            reasons.append(
                f"Genre match on {artist_name} (+{artist_weight:.2f}): {', '.join(sorted(matched_queries))}"
            )

    return matched_weight > 0.0, min(matched_weight, 1.0), reasons


def apply_filters(
    tracks: list[dict[str, Any]],
    config: FilterConfig,
) -> list[dict[str, Any]]:
    filtered_rows: list[dict[str, Any]] = []

    selected_artists_norm = {
        normalize_text(artist) for artist in config.selected_artists if normalize_text(artist)
    }

    for track in tracks:
        group_results: list[bool] = []
        reasons: list[str] = []

        # Genre group (dominant scoring component).
        genre_match, genre_weight, genre_reasons = _evaluate_genre_group(track, config.selected_genres)
        if config.selected_genres:
            group_results.append(genre_match)
            reasons.extend(genre_reasons)
        genre_score = 60.0 * genre_weight if config.selected_genres else 0.0

        metadata_group_states: list[bool] = []

        # Artist group.
        if selected_artists_norm:
            artist_names_norm = {
                normalize_text(name)
                for name in track.get("artist_names", [])
                if normalize_text(name)
            }
            artist_match = bool(artist_names_norm & selected_artists_norm)
            metadata_group_states.append(artist_match)
            group_results.append(artist_match)
            if artist_match:
                matched_artists = sorted(artist_names_norm & selected_artists_norm)
                reasons.append(f"Artist matched: {', '.join(matched_artists)}")

        # Year group.
        if _range_active(config.year_range, config.year_bounds):
            track_year = track.get("release_year")
            year_match = bool(
                isinstance(track_year, int)
                and config.year_range
                and config.year_range[0] <= track_year <= config.year_range[1]
            )
            metadata_group_states.append(year_match)
            group_results.append(year_match)
            if year_match:
                reasons.append(f"Year in range: {track_year}")

        metadata_score = 0.0
        if metadata_group_states:
            metadata_score = 25.0 * (
                sum(1 for is_match in metadata_group_states if is_match) / len(metadata_group_states)
            )

        # Audio groups (one group per active slider).
        audio_states: list[bool] = []
        for feature_name, selected_range in config.audio_ranges.items():
            bounds = config.audio_bounds.get(feature_name)
            if not _range_active(selected_range, bounds):
                continue

            value = track.get(feature_name)
            feature_match = bool(
                value is not None
                and selected_range[0] <= float(value) <= selected_range[1]
            )
            audio_states.append(feature_match)
            group_results.append(feature_match)

            if feature_match:
                reasons.append(f"{feature_name} in range: {float(value):.3f}")
            elif value is None:
                reasons.append(f"{feature_name} unavailable")

        audio_score = 0.0
        if audio_states:
            audio_score = 15.0 * (
                sum(1 for is_match in audio_states if is_match) / len(audio_states)
            )

        if not group_results:
            passes_filters = True
        elif config.mode.upper() == "OR":
            passes_filters = any(group_results)
        else:
            passes_filters = all(group_results)

        if not passes_filters:
            continue

        total_score = min(100.0, genre_score + metadata_score + audio_score)

        if not group_results:
            # No active filters: keep deterministic output ordered by track name.
            total_score = 0.0
            reasons = ["No active filters: sorted alphabetically by track"]

        row = dict(track)
        row["relevance_score"] = round(total_score, 2)
        row["match_reasons"] = "; ".join(reasons) if reasons else "Matched active filters"
        filtered_rows.append(row)

    filtered_rows.sort(
        key=lambda item: (
            -float(item.get("relevance_score") or 0.0),
            item.get("track_name", "").lower(),
            item.get("track_id", ""),
        ),
    )

    return filtered_rows
