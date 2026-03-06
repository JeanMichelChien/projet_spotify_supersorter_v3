from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CACHE_VERSION = 1


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class CacheStore:
    def __init__(self, root: str | Path = "cache") -> None:
        self.root = Path(root)
        self.playlists_dir = self.root / "playlists"
        self.artists_dir = self.root / "artists"
        self.audio_dir = self.root / "audio"
        self.index_path = self.root / "index.json"

        self.playlists_dir.mkdir(parents=True, exist_ok=True)
        self.artists_dir.mkdir(parents=True, exist_ok=True)
        self.audio_dir.mkdir(parents=True, exist_ok=True)

        if not self.index_path.exists():
            self._write_json(
                self.index_path,
                {
                    "version": CACHE_VERSION,
                    "created_at": utc_now_iso(),
                    "updated_at": utc_now_iso(),
                    "last_global_refresh": None,
                    "playlists": {},
                },
            )

    def _read_json(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        temp_path.replace(path)

    def get_index(self) -> dict[str, Any]:
        index = self._read_json(self.index_path)
        if index is None:
            return {
                "version": CACHE_VERSION,
                "created_at": utc_now_iso(),
                "updated_at": utc_now_iso(),
                "last_global_refresh": None,
                "playlists": {},
            }
        return index

    def touch_global_refresh(self) -> None:
        index = self.get_index()
        index["updated_at"] = utc_now_iso()
        index["last_global_refresh"] = utc_now_iso()
        self._write_json(self.index_path, index)

    def get_playlist_cache(self, playlist_id: str) -> dict[str, Any] | None:
        return self._read_json(self.playlists_dir / f"{playlist_id}.json")

    def set_playlist_cache(self, playlist_id: str, payload: dict[str, Any]) -> None:
        payload = dict(payload)
        payload.setdefault("fetched_at", utc_now_iso())
        self._write_json(self.playlists_dir / f"{playlist_id}.json", payload)

        index = self.get_index()
        index.setdefault("playlists", {})
        index["playlists"][playlist_id] = payload["fetched_at"]
        index["updated_at"] = utc_now_iso()
        self._write_json(self.index_path, index)

    def get_artist_cache(self, artist_id: str) -> dict[str, Any] | None:
        return self._read_json(self.artists_dir / f"{artist_id}.json")

    def set_artist_cache(self, artist_id: str, genres: list[str]) -> None:
        self._write_json(
            self.artists_dir / f"{artist_id}.json",
            {
                "artist_id": artist_id,
                "genres": genres,
                "fetched_at": utc_now_iso(),
            },
        )

    def get_audio_cache(self, track_id: str) -> dict[str, Any] | None:
        return self._read_json(self.audio_dir / f"{track_id}.json")

    def set_audio_cache(self, track_id: str, features: dict[str, Any]) -> None:
        self._write_json(
            self.audio_dir / f"{track_id}.json",
            {
                "track_id": track_id,
                "features": features,
                "fetched_at": utc_now_iso(),
            },
        )

    @staticmethod
    def format_age(iso_timestamp: str | None) -> str:
        if not iso_timestamp:
            return "never"

        try:
            ts = datetime.fromisoformat(iso_timestamp)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            delta = datetime.now(timezone.utc) - ts
        except ValueError:
            return "unknown"

        total_seconds = int(delta.total_seconds())
        if total_seconds < 60:
            return f"{total_seconds}s ago"
        if total_seconds < 3600:
            return f"{total_seconds // 60}m ago"
        if total_seconds < 86400:
            return f"{total_seconds // 3600}h ago"
        return f"{total_seconds // 86400}d ago"
