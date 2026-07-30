"""Offline regression coverage for the internal workflow engine."""
from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import workflow_analyzer
from playwright_renderer import RenderedPage


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
    def test_generic_sign_in_copy_is_not_treated_as_a_login_wall(self):
        self.assertIsNone(workflow_analyzer._blocked_html('<div class="login-modal">Sign in</div>'))
        self.assertEqual(
            workflow_analyzer._blocked_html('<p>Authentication required</p>'),
            ("login_required", "Manual verification required"),
        )

    def test_published_file_size_list_maps_to_common_qualities(self):
        html = "<li><strong>File Size:</strong> 450mb 750mb 1.2Gb 2.8Gb 6Gb</li>"
        self.assertEqual(
            workflow_analyzer._quality_sizes_from_movie_page(html),
            {"480P": "450 MB", "720P": "750 MB", "1080P": "1.2 GB", "2160P": "2.8 GB"},
        )

    def test_time_budget_returns_partial_result_without_claiming_a_file(self):
        class Session:
            def __init__(self, *args, **kwargs):
                self.rows = {
                    "https://site.example/": ("<html></html>", 200, None, "text/html"),
                    "https://site.example/movie": ('<a href="https://landing.example/720">720p Download</a>', 200, None, "text/html"),
                    "https://landing.example/720": ("<html></html>", 200, None, "text/html"),
                }
            def fetch_html_once(self, url, referer=""):
                body, status, location, content_type = self.rows[url]
                return body, SimpleNamespace(url=url, status=status, location=location, content_type=content_type, content_length="", headers={}), location

        class Renderer:
            def __init__(self, *args, **kwargs): pass
            def __enter__(self): return self
            def close(self): pass
            def render(self, url):
                body, status, _, content_type = Session().rows[url]
                return RenderedPage(url, body, status, content_type, "", url)

        # The first value establishes the deadline; the third expires just
        # after the queued landing page HTTP response.
        with patch.object(workflow_analyzer, "SafeSession", Session), patch.object(workflow_analyzer, "PlaywrightRenderer", Renderer), patch.object(workflow_analyzer, "validate_public_url", lambda url: url), patch.object(workflow_analyzer.time, "monotonic", side_effect=[0, 0, 100]):
            result = workflow_analyzer.analyze_movie_workflow("https://site.example/", "https://site.example/movie", "720p")
        self.assertEqual(result["status"], "partial")
        self.assertFalse(any(item["is_final_file"] for item in result["results"]))
        self.assertTrue(any("time limit" in item["message"].lower() for item in result["results"]))

    def test_blocked_host_does_not_cancel_later_queued_mirror(self):
        class Session:
            def __init__(self, *args, **kwargs):
                self.rows = {
                    "https://site.example/": ("<html></html>", 200, None, "text/html"),
                    "https://site.example/movie": ('<a href="https://landing.example/1080">1080p Download</a>', 200, None, "text/html"),
                    "https://landing.example/1080": ('<a href="https://gdflix.example/file">GDFLIX DOWNLOAD</a><a href="https://mirror.example/file.mkv">Mirror B</a>', 200, None, "text/html"),
                    "https://gdflix.example/file": ('<div class="cf-turnstile"></div>', 200, None, "text/html"),
                    "https://mirror.example/file.mkv": ("", 200, None, "video/x-matroska"),
                }
            def fetch_html_once(self, url, referer=""):
                body, status, location, content_type = self.rows[url]
                return body, SimpleNamespace(url=url, status=status, location=location, content_type=content_type, content_length="", headers={}), location
        class Renderer:
            def __enter__(self): return self
            def close(self): pass
            def render(self, url):
                body, status, _, content_type = Session().rows[url]
                return RenderedPage(url, body, status, content_type, "", url)

        with patch.object(workflow_analyzer, "SafeSession", Session), patch.object(workflow_analyzer, "PlaywrightRenderer", Renderer), patch.object(workflow_analyzer, "validate_public_url", lambda url: url):
            result = workflow_analyzer.analyze_movie_workflow("https://site.example/", "https://site.example/movie", "1080p")
        self.assertEqual(result["status"], "success")
        self.assertTrue(any(item["blocked_by"] == "cloudflare_turnstile" for item in result["results"]))
        self.assertTrue(any(item["final_url"] == "https://mirror.example/file.mkv" for item in result["results"]))

    def test_host_priority_prefers_direct_then_vcloud_then_gdflix(self):
        actions = [
            workflow_analyzer.Action("https://g.example/file", "GDFLIX DOWNLOAD", "https://page.example", "visible link"),
            workflow_analyzer.Action("https://v.example/file", "VCLOUD DOWNLOAD", "https://page.example", "visible link"),
            workflow_analyzer.Action("https://d.example/file", "DIRECT DOWNLOAD", "https://page.example", "visible link"),
        ]
        self.assertEqual([item.label for item in workflow_analyzer._branch_actions(actions)], ["DIRECT DOWNLOAD", "VCLOUD DOWNLOAD", "GDFLIX DOWNLOAD"])

    def test_quality_root_ignores_navigation_with_inherited_heading(self):
        class Session:
            def __init__(self, *args, **kwargs):
                self.rows = {
                    "https://site.example/": ("<html></html>", 200, None, "text/html"),
                    "https://site.example/movie": ('<h3>1080p</h3><a href="https://site.example/tips">Download Tips</a><a href="https://cdn.example/movie.mkv">1080p Download</a>', 200, None, "text/html"),
                    "https://cdn.example/movie.mkv": ("", 200, None, "video/x-matroska"),
                }
            def fetch_html_once(self, url, referer=""):
                body, status, location, content_type = self.rows[url]
                return body, SimpleNamespace(url=url, status=status, location=location, content_type=content_type, content_length="", headers={}), location
        class Renderer:
            def __enter__(self): return self
            def close(self): pass
            def render(self, url):
                body, status, _, content_type = Session().rows[url]
                return RenderedPage(url, body, status, content_type, "", url)

        with patch.object(workflow_analyzer, "SafeSession", Session), patch.object(workflow_analyzer, "PlaywrightRenderer", Renderer), patch.object(workflow_analyzer, "validate_public_url", lambda url: url):
            result = workflow_analyzer.analyze_movie_workflow("https://site.example/", "https://site.example/movie", "1080p")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["results"][0]["final_url"], "https://cdn.example/movie.mkv")

    def test_graph_traversal_continues_through_rendered_intermediate_pages(self):
        class Session:
            def __init__(self, *args, **kwargs):
                self.rows = {
                    "https://site.example/": ("<html></html>", 200, None, "text/html"),
                    "https://site.example/movie": ('<a href="https://filesdl.example/view/3903">1080p Download</a>', 200, None, "text/html"),
                    "https://filesdl.example/view/3903": ('<button onclick="window.open(\'https://gdflix.example/file-page\')">GDFLIX DOWNLOAD</button>', 200, None, "text/html"),
                    "https://gdflix.example/file-page": ('<a href="https://fast.example/file.mkv">FAST CLOUD / ZIPDISK</a><a href="https://resume.example/file">CLOUD RESUME DOWNLOAD</a>', 200, None, "text/html"),
                    "https://fast.example/file.mkv": ("", 200, None, "video/x-matroska"),
                    "https://resume.example/file": ("<html></html>", 200, None, "text/html"),
                }
            def fetch_html_once(self, url, referer=""):
                body, status, location, content_type = self.rows[url]
                return body, SimpleNamespace(url=url, status=status, location=location, content_type=content_type, content_length="", headers={}), location

        class Renderer:
            def __enter__(self): return self
            def close(self): pass
            def render(self, url):
                body, status, _, content_type = Session().rows[url]
                return RenderedPage(url, body, status, content_type, "", url)

        with patch.object(workflow_analyzer, "SafeSession", Session), patch.object(workflow_analyzer, "PlaywrightRenderer", Renderer), patch.object(workflow_analyzer, "validate_public_url", lambda url: url):
            result = workflow_analyzer.analyze_movie_workflow("https://site.example/", "https://site.example/movie", "1080p")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["results"][0]["final_url"], "https://fast.example/file.mkv")
        labels = [row["link_button_label"] for row in result["execution_log"]]
        self.assertIn("GDFLIX DOWNLOAD", labels)
        self.assertIn("FAST CLOUD / ZIPDISK", labels)
        self.assertTrue(any(row["next_step"] == "All branches queued" for row in result["execution_log"]))

    def test_javascript_rendering_fallback_reuses_one_context_for_workflow(self):
        class Session:
            def __init__(self, *args, **kwargs):
                self.rows = {
                    "https://site.example/": ("<html></html>", 200, None, "text/html"),
                    "https://site.example/movie": ('<div id="root"></div><script>fetch("/api")</script>', 200, None, "text/html"),
                    "https://cdn.example/movie.mkv": ("", 200, None, "video/x-matroska"),
                }
            def fetch_html_once(self, url, referer=""):
                body, status, location, content_type = self.rows[url]
                return body, SimpleNamespace(url=url, status=status, location=location, content_type=content_type, content_length="", headers={}), location

        calls = []
        class Renderer:
            def __enter__(self): calls.append("open"); return self
            def close(self): calls.append("close")
            def render(self, url):
                calls.append(url)
                return RenderedPage(url, '<a href="https://cdn.example/movie.mkv">720p Download</a>', 200, "text/html", "", url)

        with patch.object(workflow_analyzer, "SafeSession", Session), patch.object(workflow_analyzer, "PlaywrightRenderer", Renderer), patch.object(workflow_analyzer, "validate_public_url", lambda url: url):
            result = workflow_analyzer.analyze_movie_workflow("https://site.example/", "https://site.example/movie")
        self.assertEqual(result["status"], "success")
        self.assertEqual(calls, ["open", "https://site.example/movie", "close"])
        self.assertTrue(any(row["reason_followed"] == "JavaScript rendering fallback" for row in result["execution_log"]))

    def test_renderer_stops_at_interactive_verification(self):
        class Session:
            def __init__(self, *args, **kwargs): pass
            def fetch_html_once(self, url, referer=""):
                body = "<div id='root'></div><script>fetch('/api')</script>" if url.endswith("movie") else "<html></html>"
                return body, SimpleNamespace(url=url, status=200, location=None, content_type="text/html", content_length="", headers={}), None
        class Renderer:
            def __enter__(self): return self
            def close(self): pass
            def render(self, url): return RenderedPage(url, '<div class="cf-turnstile"></div>', 200, "text/html", "", url)

        with patch.object(workflow_analyzer, "SafeSession", Session), patch.object(workflow_analyzer, "PlaywrightRenderer", Renderer), patch.object(workflow_analyzer, "validate_public_url", lambda url: url):
            result = workflow_analyzer.analyze_movie_workflow("https://site.example/", "https://site.example/movie")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["message"], "Manual verification required")
        self.assertFalse(any(item["is_final_file"] for item in result["results"]))

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
