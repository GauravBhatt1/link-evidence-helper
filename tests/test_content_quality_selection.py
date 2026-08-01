"""Focused quality-selection contracts for normalized content lookups."""
from __future__ import annotations

import time
import unittest

import web_app
from content_aggregation import aggregate_candidates


class ContentQualitySelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.content = aggregate_candidates([{
            "title": "Quality Fixture 2024 Hindi 480p | 720p | 1080p | 4K WEB-DL",
            "url": "https://fixture.example/quality-fixture",
            "source_id": "fixture",
            "source_name": "Fixture",
        }])[0]
        self.variant = self.content.releaseVariants[0]
        web_app.CONTENT_VARIANT_CACHE[self.content.contentId] = (time.time(), [self.content])

    def test_identity_lookup_defaults_to_one_detected_quality_not_all(self):
        candidate = web_app.candidate_for_content_variant(self.content.contentId, self.variant.variantId)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["availableQualities"], ["480p", "720p", "1080p", "2160p"])
        self.assertEqual(candidate["selected_quality"], "1080p")
        self.assertEqual(web_app.quality_targets(candidate["selected_quality"]), ("1080p",))

    def test_content_choice_requires_one_available_quality(self):
        candidate = web_app.candidate_for_content_variant(self.content.contentId, self.variant.variantId)
        self.assertEqual(web_app.selected_content_variant_quality("4K", candidate), "2160p")
        with self.assertRaisesRegex(ValueError, "one quality"):
            web_app.selected_content_variant_quality("all", candidate)
        with self.assertRaisesRegex(ValueError, "available for this release"):
            web_app.selected_content_variant_quality("1440p", candidate)

    def test_legacy_all_quality_requests_remain_compatible(self):
        self.assertEqual(web_app.quality_targets("all"), web_app.DEFAULT_QUALITIES)


if __name__ == "__main__":
    unittest.main()
