import unittest

import web_app


class DeliveryMetadataTests(unittest.TestCase):
    def test_verified_response_metadata_uses_header_size_filename_and_episode(self):
        item = web_app.enrich_delivery_link({
            "quality": "1080p",
            "size": "verified",
            "url": "https://cdn.example/delivery",
            "filename": "Example.Show.S02E03.1080p.mkv",
            "content_length": str(1610612736),
        })
        self.assertEqual(item["size"], "1.50 GB")
        self.assertEqual(item["season"], "Season 2")
        self.assertEqual(item["kind"], "Episode 03")
        self.assertEqual(item["filename"], "Example.Show.S02E03.1080p.mkv")

    def test_archive_and_unknown_size_are_still_explicitly_labelled(self):
        item = web_app.enrich_delivery_link({
            "quality": "720p",
            "size": "",
            "url": "https://cdn.example/Show.Season.2.Complete.zip",
        })
        self.assertEqual(item["size"], "Size unavailable")
        self.assertEqual(item["season"], "Season 2")
        self.assertEqual(item["kind"], "Season Zip")
