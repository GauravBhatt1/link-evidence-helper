import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from library_backend import LibraryService, parse_media_file


class LibraryImportMatchingTests(unittest.TestCase):
    def _service_with_item(self, temporary: str, filenames: list[str]) -> tuple[LibraryService, Path, str]:
        root = Path(temporary) / "movies"
        source = root / "Wrong Name 2026"
        source.mkdir(parents=True)
        database = str(Path(temporary) / "library.db")
        service = LibraryService("tmdb-key", database)
        root_key = __import__("hashlib").sha1(str(root.resolve()).encode()).hexdigest()[:16]
        item_id = ""
        for name in filenames:
            original = source / name
            original.write_bytes(b"x")
            parsed = parse_media_file(original, root, "movie")
            item_id = service._upsert_file("movie", root_key, parsed)
        with patch.object(service, "_tmdb", return_value={"id": 99, "title": "Correct Title", "release_date": "2026-01-01"}):
            service.manual_match(item_id, 99)
        return service, root, item_id

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

    def test_rename_rejects_symlinked_media_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "movies"
            outside = Path(temporary) / "outside"
            outside.mkdir()
            original = outside / "Wrong.Name.2026.1080p.mkv"
            original.write_bytes(b"x")
            root.mkdir()
            link = root / "Wrong Name 2026"
            link.symlink_to(outside, target_is_directory=True)
            database = str(Path(temporary) / "library.db")
            with patch.dict(os.environ, {"MOVIES_PATHS": str(root), "LIBRARY_MIN_FILE_SIZE_MB": "0"}, clear=False):
                service = LibraryService("tmdb-key", database)
                parsed = parse_media_file(link / original.name, root, "movie")
                item_id = service._upsert_file("movie", __import__("hashlib").sha1(str(root.resolve()).encode()).hexdigest()[:16], parsed)
                with patch.object(service, "_tmdb", return_value={"id": 99, "title": "Correct Title", "release_date": "2026-01-01"}):
                    service.manual_match(item_id, 99)
                with self.assertRaisesRegex(ValueError, "symlinked media folder"):
                    service.rename_matched_files(item_id)
            self.assertTrue(original.exists())

    def test_rename_rolls_back_when_a_later_file_move_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(os.environ, {"MOVIES_PATHS": str(Path(temporary) / "movies"), "LIBRARY_MIN_FILE_SIZE_MB": "0"}, clear=False):
                service, root, item_id = self._service_with_item(temporary, ["one.mkv", "two.mkv"])
                original_rename = Path.rename

                def fail_second_file(path: Path, target: str | Path):
                    if Path(target).name.endswith(" - 2.mkv"):
                        raise OSError("simulated destination failure")
                    return original_rename(path, target)

                with patch.object(Path, "rename", fail_second_file):
                    with self.assertRaisesRegex(OSError, "simulated destination failure"):
                        service.rename_matched_files(item_id)
                self.assertTrue((root / "Wrong Name 2026" / "one.mkv").is_file())
                self.assertTrue((root / "Wrong Name 2026" / "two.mkv").is_file())
                self.assertFalse((root / "Correct Title (2026)").exists())

    def test_admin_event_redacts_signed_url_parameters(self):
        with tempfile.TemporaryDirectory() as temporary:
            service = LibraryService("", str(Path(temporary) / "library.db"))
            service.log_admin_event(
                "resolver_failure", "Resolver failed", "https://files.example/video.mkv?token=secret", "redirect https://files.example/x?signature=hidden",
            )
            event = service.admin_dashboard()["events"][0]
            self.assertNotIn("secret", event["source_url"] + event["detail"])
            self.assertNotIn("hidden", event["source_url"] + event["detail"])
            self.assertIn("[redacted]", event["source_url"])
