import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import web_app


class MediaRootPickerTests(unittest.TestCase):
    def test_lists_only_directories_under_configured_mount(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "media"; root.mkdir()
            (root / "Movies").mkdir(); (root / "readme.txt").write_text("not a folder")
            with patch.dict(os.environ, {"MEDIA_BROWSER_ROOTS": str(root)}, clear=False):
                listing = web_app.media_browser_listing(str(root))
            self.assertEqual(listing["path"], str(root.resolve()))
            self.assertEqual(listing["directories"], [{"name": "Movies", "path": str((root / "Movies").resolve())}])

    def test_rejects_traversal_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "media"; root.mkdir()
            outside = Path(temp) / "outside"; outside.mkdir()
            (root / "escape").symlink_to(outside, target_is_directory=True)
            with patch.dict(os.environ, {"MEDIA_BROWSER_ROOTS": str(root)}, clear=False):
                with self.assertRaises(ValueError):
                    web_app.resolve_media_browser_path(str(root / ".." / "outside"))
                with self.assertRaises(ValueError):
                    web_app.resolve_media_browser_path(str(root / "escape"))

    def test_no_mounts_has_explicit_empty_state(self):
        with patch.dict(os.environ, {"MEDIA_BROWSER_ROOTS": "/does/not/exist"}, clear=False), patch.object(web_app.Path, "is_dir", return_value=False):
            self.assertTrue(web_app.media_browser_listing()["empty"])

