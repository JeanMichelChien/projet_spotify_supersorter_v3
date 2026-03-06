import unittest
from unittest.mock import ANY, Mock, patch

from src.automation_engine import (
    _ensure_target_playlist,
    filter_tracks_for_automation,
    ordered_unique_track_ids,
)


def _sample_tracks() -> list[dict]:
    return [
        {
            "track_id": "t1",
            "track_name": "A",
            "added_at": "2026-02-01T10:00:00Z",
            "genres": ["hip hop", "rap"],
        },
        {
            "track_id": "t2",
            "track_name": "B",
            "added_at": "2026-03-01T10:00:00Z",
            "genres": ["rap"],
        },
        {
            "track_id": "t3",
            "track_name": "C",
            "added_at": "2026-01-01T10:00:00Z",
            "genres": ["rock"],
        },
    ]


class AutomationEngineTests(unittest.TestCase):
    def test_filter_tracks_or_mode(self) -> None:
        rows = filter_tracks_for_automation(_sample_tracks(), ["hip hop", "rock"], "OR")
        self.assertEqual([row["track_id"] for row in rows], ["t1", "t3"])

    def test_filter_tracks_and_mode(self) -> None:
        rows = filter_tracks_for_automation(_sample_tracks(), ["hip hop", "rap"], "AND")
        self.assertEqual([row["track_id"] for row in rows], ["t1"])

    def test_ordered_unique_track_ids_respects_order_and_dedupes(self) -> None:
        rows = [
            {"track_id": "a"},
            {"track_id": "b"},
            {"track_id": "a"},
            {"track_id": "c"},
        ]
        self.assertEqual(ordered_unique_track_ids(rows), ["a", "b", "c"])

    def test_ensure_target_playlist_reuses_existing_target_without_metadata_call(self) -> None:
        rule = {
            "target_playlist_id": "pl_existing",
            "target_playlist_name": "Auto",
            "privacy": "private",
        }
        with patch("src.automation_engine.ensure_playlist_private") as ensure_private_mock:
            playlist_id, playlist_name = _ensure_target_playlist(Mock(), rule)

        self.assertEqual(playlist_id, "pl_existing")
        self.assertEqual(playlist_name, "Auto")
        ensure_private_mock.assert_not_called()

    def test_ensure_target_playlist_enforces_private_on_created_playlist(self) -> None:
        rule = {
            "name": "Auto New",
            "target_playlist_name": "Auto New",
            "privacy": "private",
        }
        with (
            patch("src.automation_engine.get_current_user_profile", return_value={"id": "user_1"}),
            patch(
                "src.automation_engine.create_private_playlist",
                return_value={"id": "pl_new", "name": "Auto New"},
            ),
            patch("src.automation_engine.ensure_playlist_private") as ensure_private_mock,
        ):
            playlist_id, playlist_name = _ensure_target_playlist(Mock(), rule)

        self.assertEqual(playlist_id, "pl_new")
        self.assertEqual(playlist_name, "Auto New")
        ensure_private_mock.assert_called_once_with(sp=ANY, playlist_id="pl_new")


if __name__ == "__main__":
    unittest.main()
