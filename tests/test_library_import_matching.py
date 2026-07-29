import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from library_backend import LibraryService, parse_media_file


class LibraryImportMatchingTests(unittest.TestCase):
    def test_manual_match_preview_and_confirmed_rename(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "movies"
            source = root / "Wrong Name 2026"
            source.mkdir(parents=True)
            original = source / "Wrong.Name.2026.1080p.mkv"
            original.write_bytes(b"x")
            database = str(Path(temporary) / "library.db")
            with patch.dict(os.environ, {"MOVIES_PATHS": str(root), "LIBRARY_MIN_FILE_SIZE_MB": "0"}, clear=False):
                service = LibraryService("tmdb-key", database)
                parsed = parse_media_file(original, root, "movie")
                item_id = service._upsert_file("movie", __import__("hashlib").sha1(str(root.resolve()).encode()).hexdigest()[:16], parsed)
                with patch.object(service, "_tmdb", return_value={"id": 99, "title": "Correct Title", "release_date": "2026-01-01"}):
                    service.manual_match(item_id, 99)
                preview = service.rename_preview(item_id)
                self.assertTrue(preview["writable"])
                self.assertEqual(preview["moves"][0]["to"], "Correct Title (2026)")
                self.assertIn("Correct Title (2026)/Correct Title (2026) - 1080p.mkv", [move["to"] for move in preview["moves"]])
                result = service.rename_matched_files(item_id)
                self.assertEqual(result["renamed"], 1)
                renamed = root / "Correct Title (2026)" / "Correct Title (2026) - 1080p.mkv"
                self.assertTrue(renamed.is_file())
                item = service.get_item(item_id)
                self.assertEqual(item["title"], "Correct Title")
                self.assertEqual(item["files"][0]["filename"], renamed.name)
