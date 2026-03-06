import unittest
from unittest.mock import Mock, patch

from src.automation_engine import sync_automation_rule


class AutomationSyncTests(unittest.TestCase):
    def test_sync_returns_confirmation_warning_on_empty_match(self) -> None:
        rule = {
            "id": "r1",
            "name": "Auto Rap",
            "source_type": "liked_songs",
            "genres": ["rap"],
            "genre_mode": "OR",
            "target_playlist_name": "Auto Rap",
        }

        with patch("src.automation_engine._resolve_source_tracks", return_value=([], "Liked Songs")):
            updated_rule, result = sync_automation_rule(
                sp=Mock(),
                cache_store=Mock(),
                rule=rule,
                allow_empty_clear=False,
            )

        self.assertEqual(result["status"], "warning")
        self.assertTrue(result["requires_clear_confirmation"])
        self.assertEqual(updated_rule["last_sync_result"]["status"], "warning")

    def test_sync_writes_playlist_in_replace_then_add_chunks(self) -> None:
        tracks = [
            {
                "track_id": f"track_{index}",
                "track_name": f"T{index}",
                "added_at": "2026-03-01T10:00:00Z",
                "genres": ["rap"],
            }
            for index in range(150)
        ]
        rule = {
            "id": "r2",
            "name": "Auto Many",
            "source_type": "liked_songs",
            "genres": ["rap"],
            "genre_mode": "OR",
            "target_playlist_name": "Auto Many",
        }

        with (
            patch("src.automation_engine._resolve_source_tracks", return_value=(tracks, "Liked Songs")),
            patch("src.automation_engine._ensure_target_playlist", return_value=("pl123", "Auto Many")),
            patch("src.automation_engine.replace_playlist_items") as replace_mock,
            patch("src.automation_engine.add_playlist_items") as add_mock,
        ):
            updated_rule, result = sync_automation_rule(
                sp=Mock(),
                cache_store=Mock(),
                rule=rule,
                allow_empty_clear=False,
            )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["matched_count"], 150)
        replace_mock.assert_called_once()
        add_mock.assert_called_once()
        add_args = add_mock.call_args.kwargs
        self.assertEqual(len(add_args["track_ids"]), 50)
        self.assertEqual(updated_rule["target_playlist_id"], "pl123")

    def test_sync_handles_invalid_playlist_source(self) -> None:
        rule = {
            "id": "r3",
            "name": "Broken Source",
            "source_type": "playlist",
            "source_playlist_id": None,
            "genres": ["rock"],
            "genre_mode": "OR",
            "target_playlist_name": "Broken",
        }
        _, result = sync_automation_rule(
            sp=Mock(),
            cache_store=Mock(),
            rule=rule,
            allow_empty_clear=False,
        )
        self.assertEqual(result["status"], "error")
        self.assertIn("source_playlist_id", result["message"])


if __name__ == "__main__":
    unittest.main()
