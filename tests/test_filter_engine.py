import unittest

from src.filter_engine import FilterConfig, apply_filters


def _sample_tracks() -> list[dict]:
    return [
        {
            "track_id": "t1",
            "track_name": "Hip Hop A",
            "artist_ids": ["a1", "a2"],
            "artist_names": ["Rapper One", "Singer Two"],
            "artist_genres_map": {"a1": ["hip hop"], "a2": ["pop"]},
            "genre_weight_map": {"hip hop": 0.5, "pop": 0.5},
            "genres": ["hip hop", "pop"],
            "release_year": 2020,
            "danceability": 0.8,
            "energy": 0.7,
            "valence": 0.6,
            "tempo": 120.0,
            "acousticness": 0.1,
            "instrumentalness": 0.0,
            "speechiness": 0.3,
            "liveness": 0.2,
        },
        {
            "track_id": "t2",
            "track_name": "Rock B",
            "artist_ids": ["a3"],
            "artist_names": ["Band Three"],
            "artist_genres_map": {"a3": ["rock"]},
            "genre_weight_map": {"rock": 1.0},
            "genres": ["rock"],
            "release_year": 2010,
            "danceability": 0.4,
            "energy": 0.9,
            "valence": 0.2,
            "tempo": 140.0,
            "acousticness": 0.05,
            "instrumentalness": 0.01,
            "speechiness": 0.05,
            "liveness": 0.15,
        },
    ]


class FilterEngineTests(unittest.TestCase):
    def test_and_mode_requires_all_active_groups(self) -> None:
        cfg = FilterConfig(
            mode="AND",
            selected_genres=["hip-hop"],
            selected_artists=["Rapper One"],
            year_range=(2019, 2023),
            year_bounds=(2010, 2023),
            audio_ranges={},
            audio_bounds={},
        )

        rows = apply_filters(_sample_tracks(), cfg)
        self.assertEqual([row["track_id"] for row in rows], ["t1"])

    def test_or_mode_accepts_any_active_group(self) -> None:
        cfg = FilterConfig(
            mode="OR",
            selected_genres=["hip hop"],
            selected_artists=["Band Three"],
            year_range=(2019, 2023),
            year_bounds=(2010, 2023),
            audio_ranges={},
            audio_bounds={},
        )

        rows = apply_filters(_sample_tracks(), cfg)
        self.assertEqual({row["track_id"] for row in rows}, {"t1", "t2"})

    def test_no_active_filters_sorted_by_track_name(self) -> None:
        cfg = FilterConfig(
            mode="AND",
            selected_genres=[],
            selected_artists=[],
            year_range=(2010, 2023),
            year_bounds=(2010, 2023),
            audio_ranges={},
            audio_bounds={},
        )

        rows = apply_filters(_sample_tracks(), cfg)
        self.assertEqual([row["track_name"] for row in rows], ["Hip Hop A", "Rock B"])
        self.assertTrue(
            all(row["match_reasons"] == "No active filters: sorted alphabetically by track" for row in rows)
        )


if __name__ == "__main__":
    unittest.main()
