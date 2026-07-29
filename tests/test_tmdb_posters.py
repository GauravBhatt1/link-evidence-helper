import unittest
from unittest.mock import patch

import web_app


class TmdbPosterFallbackTests(unittest.TestCase):
    def test_query_fallback_is_used_for_matching_noisy_release_title(self):
        with patch.object(web_app, "tmdb_poster_url", side_effect=["", "https://image.tmdb.org/t/p/w154/ikka.jpg"]) as lookup:
            poster = web_app.tmdb_poster_for_candidate(
                "Ikka 2026 Bollywood Hindi Full Movie HD", "Ikka"
            )
        self.assertEqual(poster, "https://image.tmdb.org/t/p/w154/ikka.jpg")
        self.assertEqual(lookup.call_args_list[0].args[0], "Ikka 2026 Bollywood Hindi Full Movie HD")
        self.assertEqual(lookup.call_args_list[1].args[0], "Ikka")

    def test_query_fallback_never_applies_to_partial_word_match(self):
        with patch.object(web_app, "tmdb_poster_url", return_value="") as lookup:
            poster = web_app.tmdb_poster_for_candidate("Nikka Zaildar 4", "Ikka")
        self.assertEqual(poster, "")
        self.assertEqual(lookup.call_count, 1)

    def test_clean_title_removes_distribution_labels(self):
        title, year, _ = web_app.clean_movie_title_for_tmdb("Ikka 2026 Bollywood Hindi Full Movie HD")
        self.assertEqual(title, "Ikka")
        self.assertEqual(year, "2026")


if __name__ == "__main__":
    unittest.main()
