import unittest

from src.enrichment import enrich_tracks_with_artist_genres


class EnrichmentTests(unittest.TestCase):
    def test_uses_only_main_artist_genres_for_multi_artist_track(self) -> None:
        tracks = [
            {
                "track_id": "t1",
                "artist_ids": ["a1", "a2"],
                "artist_names": ["Artist One", "Artist Two"],
            }
        ]

        genres_by_artist = {
            "a1": ["Hip Hop"],
            "a2": ["French House", "Electro House"],
        }

        enriched = enrich_tracks_with_artist_genres(tracks, genres_by_artist)
        row = enriched[0]

        self.assertAlmostEqual(sum(row["genre_weight_map"].values()), 1.0, places=6)
        self.assertEqual(row["genre_weight_map"]["hip hop"], 1.0)
        self.assertEqual(row["genres"], ["hip hop"])
        self.assertEqual(row["artist_genres_map"], {"a1": ["hip hop"]})


if __name__ == "__main__":
    unittest.main()
