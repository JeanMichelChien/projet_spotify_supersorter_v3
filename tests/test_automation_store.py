import json
import tempfile
import unittest
from pathlib import Path

from src.automation_store import AutomationStore


class AutomationStoreTests(unittest.TestCase):
    def test_create_update_delete_rule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = AutomationStore(tmp_dir)
            created = store.create_automation(
                {
                    "name": "My Auto",
                    "source_type": "liked_songs",
                    "genres": ["hip hop"],
                    "genre_mode": "OR",
                    "target_playlist_name": "My Auto Playlist",
                }
            )
            self.assertTrue(created.get("id"))
            self.assertEqual(created["source_type"], "liked_songs")

            updated = store.update_automation(
                created["id"],
                {
                    "source_type": "playlist",
                    "source_playlist_id": "playlist123",
                    "source_label": "Playlist 123",
                    "genre_mode": "AND",
                },
            )
            self.assertIsNotNone(updated)
            self.assertEqual(updated["source_type"], "playlist")
            self.assertEqual(updated["source_playlist_id"], "playlist123")
            self.assertEqual(updated["genre_mode"], "AND")

            deleted = store.delete_automation(created["id"])
            self.assertTrue(deleted)
            self.assertEqual(store.list_automations(), [])

    def test_migrates_legacy_rule_without_source_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache_path = Path(tmp_dir) / "automations.json"
            legacy = {
                "version": 0,
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "automations": [
                    {
                        "id": "legacy1",
                        "name": "Legacy",
                        "genres": ["rap"],
                    }
                ],
            }
            with cache_path.open("w", encoding="utf-8") as handle:
                json.dump(legacy, handle)

            store = AutomationStore(tmp_dir)
            rules = store.list_automations()
            self.assertEqual(len(rules), 1)
            self.assertEqual(rules[0]["id"], "legacy1")
            self.assertEqual(rules[0]["source_type"], "liked_songs")
            self.assertIsNone(rules[0]["source_playlist_id"])
            self.assertEqual(rules[0]["source_label"], "Liked Songs")


if __name__ == "__main__":
    unittest.main()
