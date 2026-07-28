"""Offline regression coverage for the internal workflow engine."""
from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import workflow_analyzer


class _FakeSession:
    def __init__(self, *args, **kwargs):
        self.responses = {
            "https://site.example/": ("<html></html>", 200),
            "https://site.example/movie": ('<h3>720p x265</h3><a href="https://landing.example/720">Download 720p</a>', 200),
            "https://landing.example/720": ('<a href="https://branch.example/file">G-Direct Instant</a>', 200),
            "https://branch.example/file": ('<div class="cf-turnstile"></div>Please verify that you are human.', 200),
        }

    def fetch_html_once(self, url, referer=""):
        body, status = self.responses[url]
        return body, SimpleNamespace(url=url, status=status, location=None, content_type="text/html", content_length="", headers={}), None


class WorkflowAnalyzerTests(TestCase):
    def test_turnstile_is_blocked_and_never_becomes_a_final_link(self):
        with patch.object(workflow_analyzer, "SafeSession", _FakeSession), patch.object(workflow_analyzer, "validate_public_url", lambda url: url):
            result = workflow_analyzer.analyze_movie_workflow("https://site.example/", "https://site.example/movie", "720p")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["results"][0]["blocked_by"], "cloudflare_turnstile")
        self.assertIsNone(result["results"][0]["final_url"])
        self.assertFalse(any(row["redirect_location"] for row in result["execution_log"]))

    def test_redirect_meta_and_static_javascript_are_followed_to_a_file(self):
        class Session:
            def __init__(self, *args, **kwargs):
                self.rows = {
                    "https://site.example/": ("<html></html>", 200, None, "text/html"),
                    "https://site.example/movie": ('<a href="https://landing.example/720">720p x265</a>', 200, None, "text/html"),
                    "https://landing.example/720": ("", 302, "https://step.example/one", "text/html"),
                    "https://step.example/one": ('<meta http-equiv="refresh" content="0; url=https://step.example/two">', 200, None, "text/html"),
                    "https://step.example/two": ("<script>window.location.replace('https://cdn.example/movie.mkv')</script>", 200, None, "text/html"),
                    "https://cdn.example/movie.mkv": ("", 200, None, "video/x-matroska"),
                }

            def fetch_html_once(self, url, referer=""):
                body, status, location, content_type = self.rows[url]
                return body, SimpleNamespace(url=url, status=status, location=location, content_type=content_type, content_length="", headers={}), location

        with patch.object(workflow_analyzer, "SafeSession", Session), patch.object(workflow_analyzer, "validate_public_url", lambda url: url):
            result = workflow_analyzer.analyze_movie_workflow("https://site.example/", "https://site.example/movie")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["results"][0]["final_url"], "https://cdn.example/movie.mkv")
        self.assertTrue(any(row["redirect_location"] for row in result["execution_log"]))
        self.assertIn({"label": "Final file", "state": "passed"}, result["workflow_steps"])
