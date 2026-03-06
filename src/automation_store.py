from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.cache_store import utc_now_iso

AUTOMATIONS_VERSION = 1


def _default_state() -> dict[str, Any]:
    now = utc_now_iso()
    return {
        "version": AUTOMATIONS_VERSION,
        "created_at": now,
        "updated_at": now,
        "automations": [],
    }


def _normalize_rule(rule: dict[str, Any]) -> dict[str, Any]:
    now = utc_now_iso()
    normalized = dict(rule)
    normalized.setdefault("id", str(uuid4()))
    normalized.setdefault("name", "Untitled automation")
    normalized.setdefault("source_type", "liked_songs")
    normalized.setdefault("source_playlist_id", None)
    normalized.setdefault("source_label", "Liked Songs")
    normalized.setdefault("target_playlist_id", None)
    normalized.setdefault("target_playlist_name", normalized["name"])
    normalized.setdefault("privacy", "private")
    normalized.setdefault("genres", [])
    normalized.setdefault("genre_mode", "OR")
    normalized.setdefault("order", "added_desc")
    normalized.setdefault("no_match_policy", "confirm_clear")
    normalized.setdefault("created_at", now)
    normalized.setdefault("updated_at", now)
    normalized.setdefault("last_sync_at", None)
    normalized.setdefault(
        "last_sync_result",
        {
            "status": "never",
            "matched_count": 0,
            "written_count": 0,
            "message": "Not synced yet.",
        },
    )

    if normalized["source_type"] == "liked_songs":
        normalized["source_playlist_id"] = None
        normalized["source_label"] = "Liked Songs"

    normalized["genre_mode"] = str(normalized.get("genre_mode", "OR")).upper()
    if normalized["genre_mode"] not in {"OR", "AND"}:
        normalized["genre_mode"] = "OR"
    normalized["genres"] = [
        str(genre).strip()
        for genre in normalized.get("genres", [])
        if str(genre).strip()
    ]
    return normalized


class AutomationStore:
    def __init__(self, root: str | Path = "cache") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "automations.json"
        if not self.path.exists():
            self._write(_default_state())
        else:
            self._migrate_if_needed()

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return _default_state()
        with self.path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _write(self, payload: dict[str, Any]) -> None:
        payload = dict(payload)
        payload["updated_at"] = utc_now_iso()
        temp = self.path.with_suffix(".json.tmp")
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        temp.replace(self.path)

    def _migrate_if_needed(self) -> None:
        state = self._read()
        changed = False

        if state.get("version") != AUTOMATIONS_VERSION:
            state["version"] = AUTOMATIONS_VERSION
            changed = True

        automations = state.get("automations", [])
        normalized_rules: list[dict[str, Any]] = []
        for rule in automations:
            normalized = _normalize_rule(rule if isinstance(rule, dict) else {})
            if normalized != rule:
                changed = True
            normalized_rules.append(normalized)
        state["automations"] = normalized_rules

        if changed:
            self._write(state)

    def list_automations(self) -> list[dict[str, Any]]:
        self._migrate_if_needed()
        state = self._read()
        rules = state.get("automations", [])
        normalized = [_normalize_rule(rule) for rule in rules if isinstance(rule, dict)]
        normalized.sort(key=lambda row: (str(row.get("name", "")).lower(), str(row.get("id", ""))))
        return normalized

    def create_automation(self, payload: dict[str, Any]) -> dict[str, Any]:
        state = self._read()
        rule = _normalize_rule(payload)
        rules = state.get("automations", [])
        rules.append(rule)
        state["automations"] = rules
        self._write(state)
        return rule

    def update_automation(self, automation_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        state = self._read()
        rules = state.get("automations", [])
        found: dict[str, Any] | None = None
        updated_rules: list[dict[str, Any]] = []
        for existing in rules:
            if not isinstance(existing, dict):
                continue
            if str(existing.get("id")) != automation_id:
                updated_rules.append(existing)
                continue
            merged = dict(existing)
            merged.update(updates)
            merged["updated_at"] = utc_now_iso()
            found = _normalize_rule(merged)
            updated_rules.append(found)

        if found is None:
            return None

        state["automations"] = updated_rules
        self._write(state)
        return found

    def delete_automation(self, automation_id: str) -> bool:
        state = self._read()
        rules = state.get("automations", [])
        remaining: list[dict[str, Any]] = []
        deleted = False
        for existing in rules:
            if not isinstance(existing, dict):
                continue
            if str(existing.get("id")) == automation_id:
                deleted = True
                continue
            remaining.append(existing)
        if not deleted:
            return False
        state["automations"] = remaining
        self._write(state)
        return True
