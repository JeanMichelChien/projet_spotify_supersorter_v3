import unittest

from src.ui_helpers import (
    build_genre_count_rows,
    build_genre_timeline_rows,
    build_playlist_select_options,
    summarize_genre_evolution,
)


class UIHelpersTests(unittest.TestCase):
    def test_playlists_are_sorted_alphabetically_case_insensitive(self) -> None:
        playlists = [
            {"id": "3", "name": "zeta vibes", "tracks": {"total": 5}},
            {"id": "1", "name": "Alpha set", "tracks": {"total": 10}},
            {"id": "2", "name": "beta mix", "tracks": {"total": 8}},
        ]

        options = build_playlist_select_options(playlists)
        self.assertEqual(
            options,
            [
                ("1", "Alpha set (10 tracks)"),
                ("2", "beta mix (8 tracks)"),
                ("3", "zeta vibes (5 tracks)"),
            ],
        )

    def test_genre_counts_add_one_per_track_per_genre(self) -> None:
        tracks = [
            {"track_id": "t1", "genres": ["hip hop", "rap", "hip hop"]},
            {"track_id": "t2", "genres": ["rap"]},
            {"track_id": "t3", "genres": ["rock"]},
            {"track_id": "t4", "genres": ["hip hop", "rock"]},
        ]

        rows = build_genre_count_rows(tracks, total_tracks=4, top_n=10)
        self.assertEqual(
            rows,
            [
                {"genre": "hip hop", "track_count": 2, "share_pct": 50.0},
                {"genre": "rap", "track_count": 2, "share_pct": 50.0},
                {"genre": "rock", "track_count": 2, "share_pct": 50.0},
            ],
        )

    def test_genre_counts_top_n_limit_is_applied(self) -> None:
        tracks = [{"genres": [f"genre-{index}"]} for index in range(15)]
        rows = build_genre_count_rows(tracks, total_tracks=15, top_n=10)
        self.assertEqual(len(rows), 10)

    def test_genre_timeline_rows_build_quarterly_shares(self) -> None:
        tracks = [
            {"added_at": "2023-01-10T00:00:00Z", "genres": ["hip hop", "rap"]},
            {"added_at": "2023-02-10T00:00:00Z", "genres": []},
            {"added_at": "2023-06-10T00:00:00Z", "genres": ["rap"]},
            {"added_at": "2024-01-10T00:00:00Z", "genres": ["hip hop"]},
            {"added_at": "2024-03-10T00:00:00Z", "genres": ["rock"]},
        ]
        rows = build_genre_timeline_rows(tracks, top_n=2)
        self.assertEqual(sorted({row["period"] for row in rows}), ["2023-Q1", "2023-Q2", "2024-Q1"])

        # 2023-Q1 has 2 tracks total, but only one has genres.
        rap_q1 = next(row for row in rows if row["period"] == "2023-Q1" and row["genre"] == "rap")
        hiphop_q1 = next(
            row for row in rows if row["period"] == "2023-Q1" and row["genre"] == "hip hop"
        )
        self.assertEqual(rap_q1["track_count"], 1)
        self.assertEqual(hiphop_q1["track_count"], 1)
        self.assertAlmostEqual(float(rap_q1["share_pct"]), 50.0, places=2)

    def test_genre_timeline_uses_top_n_per_quarter_not_global(self) -> None:
        tracks = [
            {"added_at": "2023-01-10T00:00:00Z", "genres": ["rap"]},
            {"added_at": "2023-02-10T00:00:00Z", "genres": ["hip hop"]},
            {"added_at": "2023-04-10T00:00:00Z", "genres": ["rock"]},
        ]
        rows = build_genre_timeline_rows(tracks, top_n=1)
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["period"] for row in rows}, {"2023-Q1", "2023-Q2"})

    def test_summarize_genre_evolution_reports_rise_and_decline(self) -> None:
        rows = [
            {
                "period": "2023-Q1",
                "period_index": 8092,
                "genre": "hip hop",
                "track_count": 1,
                "share_pct": 25.0,
            },
            {
                "period": "2023-Q1",
                "period_index": 8092,
                "genre": "rap",
                "track_count": 3,
                "share_pct": 75.0,
            },
            {
                "period": "2024-Q1",
                "period_index": 8096,
                "genre": "hip hop",
                "track_count": 3,
                "share_pct": 75.0,
            },
            {
                "period": "2024-Q1",
                "period_index": 8096,
                "genre": "rap",
                "track_count": 1,
                "share_pct": 25.0,
            },
        ]
        summary = summarize_genre_evolution(rows)
        self.assertEqual(summary["start_period"], "2023-Q1")
        self.assertEqual(summary["end_period"], "2024-Q1")
        self.assertEqual(summary["latest_top_genre"], "hip hop")
        self.assertEqual(summary["fastest_rising_genre"], "hip hop")
        self.assertEqual(summary["fastest_declining_genre"], "rap")


if __name__ == "__main__":
    unittest.main()
