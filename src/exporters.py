from __future__ import annotations

from typing import Any

import pandas as pd


def filtered_tracks_to_dataframe(tracks: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for track in tracks:
        rows.append(
            {
                "track_name": track.get("track_name", ""),
                "artists": ", ".join(track.get("artist_names", [])),
                "genres": ", ".join(track.get("genres", [])),
                "release_year": track.get("release_year"),
                "relevance_score": track.get("relevance_score"),
                "match_reasons": track.get("match_reasons", ""),
                "spotify_url": track.get("spotify_url", ""),
            }
        )

    return pd.DataFrame(rows)


def filtered_tracks_to_csv_bytes(tracks: list[dict[str, Any]]) -> bytes:
    df = filtered_tracks_to_dataframe(tracks)
    return df.to_csv(index=False).encode("utf-8")
