import unittest
from unittest.mock import Mock

from content_aggregation import aggregate_candidates, compatibility_candidates, failover_sources


class ContentAggregationTests(unittest.TestCase):
    def rows(self):
        return [
            {"title": "Example Film 2024 Hindi 1080p WEB-DL 1.2 GB", "url": "https://one.example/a", "source_id": "one", "source_name": "One"},
            {"title": "Example Film (2024) Hindi 1080p WEB-DL 1.4 GB", "url": "https://two.example/a", "source_id": "two", "source_name": "Two"},
            {"title": "Example Film 2024 Hindi 720p WEB-DL", "url": "https://one.example/b", "source_id": "one", "source_name": "One"},
        ]

    def test_duplicate_content_and_matching_sources_merge_without_size_key(self):
        contents = aggregate_candidates(self.rows())
        self.assertEqual(len(contents), 1)
        self.assertEqual(contents[0].totalSources, 2)
        self.assertEqual(len(contents[0].releaseVariants), 2)
        self.assertEqual(len(contents[0].releaseVariants[0].sources), 2)

    def test_variants_remain_separate_and_source_order_is_preserved(self):
        content = aggregate_candidates(self.rows())[0]
        self.assertEqual([item.quality for item in content.releaseVariants], ["1080P", "720P"])
        self.assertEqual([item.adapterName for item in content.releaseVariants[0].sources], ["one", "two"])

    def test_provider_release_labels_merge_into_one_content_and_legacy_tile(self):
        contents = aggregate_candidates([
            {"title": "Download Iron Man 3 (2013) Dual Audio {Hindi-English} Movie 1080p BluRay ESub", "url": "https://one.example/iron-man-3", "source_id": "one", "source_name": "One"},
            {"title": "Iron Man 3 (2013) Hindi Dubbed", "url": "https://two.example/iron-man-3", "source_id": "two", "source_name": "Two"},
        ])
        self.assertEqual(len(contents), 1)
        self.assertEqual(contents[0].totalSources, 2)
        self.assertEqual(len(contents[0].releaseVariants), 2)
        self.assertEqual([variant.language for variant in contents[0].releaseVariants], ["Hindi/English", "Hindi"])
        self.assertEqual(len(compatibility_candidates(contents)), 1)

    def test_multi_quality_release_page_is_not_labelled_as_its_first_quality(self):
        contents = aggregate_candidates([{
            "title": "Example Film 2024 Hindi 480p | 720p | 1080p | 2160p BluRay",
            "quality": "480p",
            "url": "https://one.example/example-film",
            "source_id": "one",
            "source_name": "One",
        }])
        self.assertEqual(contents[0].releaseVariants[0].quality, "Multiple")

    def test_failover_uses_next_source_after_verification_failure(self):
        variant = aggregate_candidates(self.rows())[0].releaseVariants[0]
        checked = []
        result, source = failover_sources(variant, lambda item: checked.append(item.adapterName) or ("final-link" if item.adapterName == "two" else None))
        self.assertEqual(result, "final-link")
        self.assertEqual(source.adapterName, "two")
        self.assertEqual(checked, ["one", "two"])
        self.assertEqual(variant.sources[0].verificationState, "failed")
        self.assertEqual(variant.sources[1].verificationState, "verified")

    def test_failover_delegates_existing_workflow_verification_unchanged(self):
        variant = aggregate_candidates(self.rows())[0].releaseVariants[0]
        original_candidate = dict(variant.sources[0].workflowMetadata["candidate"])
        verifier = Mock(return_value="verified-by-existing-workflow")
        result, _ = failover_sources(variant, verifier)
        self.assertEqual(result, "verified-by-existing-workflow")
        verifier.assert_called_once_with(variant.sources[0])
        self.assertEqual(variant.sources[0].workflowMetadata["candidate"], original_candidate)


if __name__ == "__main__":
    unittest.main()
