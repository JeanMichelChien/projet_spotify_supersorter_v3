from __future__ import annotations

from datetime import datetime
from typing import Any


def build_playlist_select_options(playlists: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """
    Return playlist options sorted alphabetically by playlist name (case-insensitive).

    Output format: [(playlist_id, "Playlist Name (N tracks)"), ...]
    """
    sortable_rows: list[tuple[str, str, str]] = []
    for playlist in playlists:
        playlist_id = playlist.get("id")
        if not playlist_id:
            continue

        raw_name = playlist.get("name")
        name = raw_name.strip() if isinstance(raw_name, str) else ""
        if not name:
            name = "Untitled playlist"

        track_total = ((playlist.get("tracks") or {}).get("total")) or 0
        label = f"{name} ({track_total} tracks)"
        sortable_rows.append((playlist_id, label, name.lower()))

    sortable_rows.sort(key=lambda row: (row[2], row[0]))
    return [(playlist_id, label) for playlist_id, label, _ in sortable_rows]


def build_genre_count_rows(
    tracks: list[dict[str, Any]],
    total_tracks: int | None = None,
    top_n: int | None = None,
) -> list[dict[str, Any]]:
    """
    Build sorted genre counts from tracks.

    Counting rule: each track contributes +1 to each unique genre in that track.
    Sort order: descending track_count, ascending genre.
    """
    denominator = total_tracks if isinstance(total_tracks, int) and total_tracks > 0 else len(tracks)
    counts: dict[str, int] = {}
    for track in tracks:
        unique_genres = {
            genre.strip()
            for genre in track.get("genres", [])
            if isinstance(genre, str) and genre.strip()
        }
        for genre in unique_genres:
            counts[genre] = counts.get(genre, 0) + 1

    rows = [
        {
            "genre": genre,
            "track_count": count,
            "share_pct": round((count / denominator) * 100.0, 2) if denominator > 0 else 0.0,
        }
        for genre, count in counts.items()
    ]
    rows.sort(key=lambda row: (-int(row["track_count"]), str(row["genre"])))
    if isinstance(top_n, int) and top_n > 0:
        return rows[:top_n]
    return rows


def _parse_added_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def build_genre_timeline_rows(
    tracks: list[dict[str, Any]],
    top_n: int = 5,
) -> list[dict[str, Any]]:
    """
    Build quarterly genre-share timeline rows from tracks.

    - Period: quarter derived from track `added_at`
    - Counting: each track contributes +1 to each unique genre for its quarter
    - Share: count(genre in quarter) / count(all tracks added in quarter) * 100
    - Genre selection: top N genres *within each quarter*
    - Output rows: period, year, quarter, period_index, genre, track_count, share_pct
    """
    period_genre_counts: dict[tuple[int, int], dict[str, int]] = {}
    period_totals: dict[tuple[int, int], int] = {}

    for track in tracks:
        dt = _parse_added_datetime(track.get("added_at"))
        if dt is None:
            continue
        year = int(dt.year)
        quarter = ((int(dt.month) - 1) // 3) + 1
        period_key = (year, quarter)
        period_totals[period_key] = period_totals.get(period_key, 0) + 1

        unique_genres = {
            genre.strip()
            for genre in track.get("genres", [])
            if isinstance(genre, str) and genre.strip()
        }
        if not unique_genres:
            continue

        if period_key not in period_genre_counts:
            period_genre_counts[period_key] = {}

        for genre in unique_genres:
            period_genre_counts[period_key][genre] = period_genre_counts[period_key].get(genre, 0) + 1

    if not period_genre_counts:
        return []

    rows: list[dict[str, Any]] = []
    for year, quarter in sorted(period_genre_counts.keys()):
        total_for_period = period_totals.get((year, quarter), 0)
        if total_for_period <= 0:
            continue
        period_label = f"{year}-Q{quarter}"
        period_index = (year * 4) + (quarter - 1)
        period_top_genres = sorted(
            period_genre_counts[(year, quarter)].items(),
            key=lambda item: (-item[1], item[0]),
        )[: max(1, int(top_n))]

        for rank, (genre, count) in enumerate(period_top_genres, start=1):
            rows.append(
                {
                    "period": period_label,
                    "year": year,
                    "quarter": quarter,
                    "period_index": period_index,
                    "rank": rank,
                    "genre": genre,
                    "track_count": count,
                    "share_pct": round((count / total_for_period) * 100.0, 2),
                }
            )

    return rows


def summarize_genre_evolution(timeline_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Summarize evolution between first and last period.

    Returns:
    - start_period, end_period
    - latest_top_genre, latest_top_share_pct
    - fastest_rising_genre (+delta share pct)
    - fastest_declining_genre (delta share pct, negative)
    """
    if not timeline_rows:
        return {}

    period_indices = sorted({int(row["period_index"]) for row in timeline_rows})
    if len(period_indices) < 2:
        only_row = min(timeline_rows, key=lambda row: int(row["period_index"]))
        return {
            "start_period": str(only_row["period"]),
            "end_period": str(only_row["period"]),
            "latest_top_genre": None,
            "latest_top_share_pct": None,
            "fastest_rising_genre": None,
            "fastest_rising_delta_pct": None,
            "fastest_declining_genre": None,
            "fastest_declining_delta_pct": None,
        }

    start_period_index = period_indices[0]
    end_period_index = period_indices[-1]

    start_map: dict[str, float] = {}
    end_map: dict[str, float] = {}
    start_period_label: str | None = None
    end_period_label: str | None = None
    for row in timeline_rows:
        genre = str(row["genre"])
        share = float(row["share_pct"])
        period_index = int(row["period_index"])
        if period_index == start_period_index:
            start_map[genre] = share
            start_period_label = str(row["period"])
        if period_index == end_period_index:
            end_map[genre] = share
            end_period_label = str(row["period"])

    if not end_map:
        return {}

    all_genres = sorted(set(start_map) | set(end_map))
    deltas = [
        (
            genre,
            round(end_map.get(genre, 0.0) - start_map.get(genre, 0.0), 2),
        )
        for genre in all_genres
    ]

    rising = max(deltas, key=lambda item: (item[1], item[0]))
    declining = min(deltas, key=lambda item: (item[1], item[0]))
    latest_top = max(end_map.items(), key=lambda item: (item[1], item[0]))

    return {
        "start_period": start_period_label,
        "end_period": end_period_label,
        "latest_top_genre": latest_top[0],
        "latest_top_share_pct": round(latest_top[1], 2),
        "fastest_rising_genre": rising[0],
        "fastest_rising_delta_pct": rising[1],
        "fastest_declining_genre": declining[0],
        "fastest_declining_delta_pct": declining[1],
    }
