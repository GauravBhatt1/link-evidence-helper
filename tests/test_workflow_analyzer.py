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
    def test_same_origin_movie_skips_unneeded_homepage_request(self):
        requested: list[str] = []

        class Session:
            def __init__(self, *args, **kwargs): pass
            def fetch_html_once(self, url, referer=""):
                requested.append(url)
                if url == "https://site.example/":
                    raise AssertionError("same-origin homepage should not be fetched")
                if url == "https://site.example/movie":
                    return ('<a href="https://cdn.example/movie.mkv">1080p Download</a>', SimpleNamespace(url=url, status=200, location=None, content_type="text/html", content_length="", headers={}), None)
                if url == "https://cdn.example/movie.mkv":
                    return ("", SimpleNamespace(url=url, status=200, location=None, content_type="video/x-matroska", content_length="", headers={}), None)
                raise AssertionError(f"Unexpected URL: {url}")

        with patch.object(workflow_analyzer, "SafeSession", Session), patch.object(workflow_analyzer, "validate_public_url", lambda url: url):
            result = workflow_analyzer.analyze_movie_workflow("https://site.example/", "https://site.example/movie", "1080p")
        self.assertEqual(result["status"], "success")
        self.assertEqual(requested, ["https://site.example/movie", "https://cdn.example/movie.mkv"])
        self.assertEqual(result["execution_log"][0]["next_step"], "Same-origin homepage request skipped")

    def test_selected_quality_does_not_fall_back_to_a_different_labelled_branch(self):
        requested: list[str] = []

        class Session:
            def __init__(self, *args, **kwargs): pass

            def fetch_html_once(self, url, referer=""):
                requested.append(url)
                if url == "https://site.example/movie":
                    return (
                        '<a href="https://wrong.example/1080">1080p Download</a>'
                        '<a href="https://right.example/fallback">Download</a>',
                        SimpleNamespace(url=url, status=200, location=None, content_type="text/html", content_length="", headers={}),
                        None,
                    )
                if url == "https://wrong.example/1080":
                    raise AssertionError("A 720p request must not inspect the labelled 1080p branch")
                if url == "https://right.example/fallback":
                    return "", SimpleNamespace(url=url, status=200, location=None, content_type="video/x-matroska", content_length="", headers={}), None
                raise AssertionError(f"Unexpected URL: {url}")

        with patch.object(workflow_analyzer, "SafeSession", Session), patch.object(workflow_analyzer, "validate_public_url", lambda url: url):
            result = workflow_analyzer.analyze_movie_workflow("https://site.example/", "https://site.example/movie", "720p")
        self.assertEqual(result["status"], "success")
        self.assertEqual(requested, ["https://site.example/movie", "https://right.example/fallback"])
        self.assertEqual(result["results"][0]["quality"], "720p")

    def test_generic_sign_in_copy_is_not_treated_as_a_login_wall(self):
        self.assertIsNone(workflow_analyzer._blocked_html('<div class="login-modal">Sign in</div>'))
        self.assertIsNone(workflow_analyzer._blocked_html('<!-- <script src="https://example.test/recaptcha.js"></script> -->'))
        self.assertEqual(
            workflow_analyzer._blocked_html('<p>Authentication required</p>'),
            ("login_required", "Manual verification required"),
        )

    def test_public_direct_download_action_is_verified_without_touching_a_challenge(self):
        class Session:
            def __init__(self, *args, **kwargs): pass
            def fetch_html_once(self, url, referer=""):
                pages = {
                    "https://site.example/": ("<html></html>", 200, "text/html"),
                    "https://site.example/movie": ('<a href="https://host.example/file">1080p Download</a>', 200, "text/html"),
                    "https://host.example/file": ('<button type="button">Direct/Instant Download</button>', 200, "text/html"),
                }
                body, status, content_type = pages[url]
                return body, SimpleNamespace(url=url, status=status, location=None, content_type=content_type, content_length="", headers={}), None
            def inspect(self, url, method="HEAD"):
                if method != "HEAD":
                    raise AssertionError("Direct target must be header-verified")
                return SimpleNamespace(url=url, status=200, location=None, content_type="application/octet-stream", content_length="10000000", headers={})

        class Renderer:
            def __enter__(self): return self
            def close(self): pass
            def render(self, url):
                body = Session().fetch_html_once(url)[0]
                status, content_type = 200, "text/html"
                return RenderedPage(url, body, status, content_type, "", url)
            def direct_download(self, url):
                return "https://cdn.example/file.zip" if url == "https://host.example/file" else ""

        with patch.object(workflow_analyzer, "SafeSession", Session), patch.object(workflow_analyzer, "PlaywrightRenderer", Renderer), patch.object(workflow_analyzer, "validate_public_url", lambda url: url):
            result = workflow_analyzer.analyze_movie_workflow("https://site.example/", "https://site.example/movie", "1080p")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["results"][0]["final_url"], "https://cdn.example/file.zip")
        self.assertEqual(result["results"][0]["source"], "Browser direct download")

    def test_hubcloud_uses_its_generated_r2_delivery_and_skips_other_mirrors(self):
        requested: list[str] = []

        class Session:
            def __init__(self, *args, **kwargs): pass
            def fetch_html_once(self, url, referer=""):
                requested.append(url)
                pages = {
                    "https://site.example/": ("<html></html>", 200, "text/html"),
                    "https://hubcloud.cx/drive/share": (
                        '<h3>1080p</h3><a href="https://tinyurl.example/tutorial">How to Download From HubCloud</a>'
                        '<a href="https://sportverse.cc/hubcloud.php?host=hubcloud">Generate Direct Download Link</a>', 200, "text/html"
                    ),
                    "https://sportverse.cc/hubcloud.php?host=hubcloud": (
                        '<a href="https://ad.example/file.mkv">Download [FSL Server]</a>'
                        '<a href="https://r2.cloudflarestorage.com/hub2/file?signature=temporary">Download [FSL Server]</a>'
                        '<a href="https://pixeldrain.example/u/file">Download [Pixel Server]</a>', 200, "text/html"
                    ),
                    "https://r2.cloudflarestorage.com/hub2/file?signature=temporary": ("", 200, "video/x-matroska"),
                }
                if url in {"https://ad.example/file.mkv", "https://pixeldrain.example/u/file"}:
                    raise AssertionError("HubCloud must not request an unrelated mirror")
                body, status, content_type = pages[url]
                return body, SimpleNamespace(url=url, status=status, location=None, content_type=content_type, content_length="", headers={}), None

        with patch.object(workflow_analyzer, "SafeSession", Session), patch.object(workflow_analyzer, "validate_public_url", lambda url: url):
            result = workflow_analyzer.analyze_movie_workflow("https://site.example/", "https://hubcloud.cx/drive/share")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["results"][0]["final_url"], "https://r2.cloudflarestorage.com/hub2/file?signature=temporary")
        self.assertEqual(requested, [
            "https://site.example/", "https://hubcloud.cx/drive/share",
            "https://sportverse.cc/hubcloud.php?host=hubcloud",
            "https://r2.cloudflarestorage.com/hub2/file?signature=temporary",
        ])

    def test_public_page_http_rejection_can_use_normal_browser_dom(self):
        class Session:
            def __init__(self, *args, **kwargs): pass
            def fetch_html_once(self, url, referer=""):
                pages = {
                    "https://site.example/": ("<html></html>", 200, "text/html"),
                    "https://share.example/drive": ("<html>Request blocked</html>", 403, "text/html"),
                    "https://cdn.example/file.mkv": ("", 200, "video/x-matroska"),
                }
                body, status, content_type = pages[url]
                return body, SimpleNamespace(url=url, status=status, location=None, content_type=content_type, content_length="", headers={}), None

        class Renderer:
            def __enter__(self): return self
            def close(self): pass
            def render(self, url):
                if url != "https://share.example/drive":
                    raise AssertionError(f"Unexpected browser URL: {url}")
                return RenderedPage(url, '<a href="https://cdn.example/file.mkv">1080p Download</a>', 200, "text/html", "", url)

        with patch.object(workflow_analyzer, "SafeSession", Session), patch.object(workflow_analyzer, "PlaywrightRenderer", Renderer), patch.object(workflow_analyzer, "validate_public_url", lambda url: url):
            result = workflow_analyzer.analyze_movie_workflow("https://site.example/", "https://share.example/drive", "1080p")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["results"][0]["final_url"], "https://cdn.example/file.mkv")
        self.assertTrue(any(row["reason_followed"] == "Browser fallback for rejected public page" for row in result["execution_log"]))

    def test_telegram_apk_is_never_accepted_as_a_verified_movie_file(self):
        response = SimpleNamespace(
            status=200,
            content_type="application/octet-stream",
            headers={"content-disposition": 'attachment; filename="Telegram.apk"'},
        )
        self.assertFalse(workflow_analyzer._final_file(
            response,
            "https://cdn4.telesco.pe/file/Telegram.apk?token=temporary",
        ))

    def test_signed_r2_attachment_filename_can_verify_generic_media_response(self):
        response = SimpleNamespace(status=200, content_type="application/octet-stream", headers={})
        self.assertTrue(workflow_analyzer._final_file(
            response,
            "https://bucket.r2.cloudflarestorage.com/hub2/object?X-Amz-Signature=temporary&response-content-disposition=attachment%3B%20filename%3D%22Movie.1080p.mkv%22",
        ))

    def test_final_file_metadata_uses_safe_filename_and_content_length(self):
        response = SimpleNamespace(
            status=200,
            content_type="application/octet-stream",
            content_length="1610612736",
            headers={"content-disposition": 'attachment; filename="Example.Show.S02E03.1080p.mkv"'},
        )
        self.assertEqual(
            workflow_analyzer._final_file_metadata(response, "https://bucket.example/hub2/opaque-object"),
            {
                "file_name": "Example.Show.S02E03.1080p.mkv",
                "content_length": "1610612736",
                "content_type": "application/octet-stream",
            },
        )

    def test_title_case_content_disposition_verifies_hubdrive_style_direct_file(self):
        response = SimpleNamespace(
            status=200,
            content_type="application/octet-stream",
            content_length="9539721175",
            headers={"Content-Disposition": "attachment; filename*=UTF-8''Adarsh.Baal.Vidyalaya.S01.1080p.zip"},
        )
        self.assertTrue(workflow_analyzer._final_file(
            response, "https://pub-example.r2.dev/opaque-object"
        ))
        self.assertEqual(
            workflow_analyzer._final_file_metadata(response, "https://pub-example.r2.dev/opaque-object")["file_name"],
            "Adarsh.Baal.Vidyalaya.S01.1080p.zip",
        )

    def test_generic_binary_requires_media_filename_evidence(self):
        response = SimpleNamespace(status=200, content_type="application/octet-stream", headers={})
        self.assertFalse(workflow_analyzer._final_file(response, "https://cdn.example/download?id=123"))
        self.assertTrue(workflow_analyzer._final_file(
            SimpleNamespace(status=200, content_type="application/octet-stream", headers={"content-disposition": 'attachment; filename="movie.mkv"'}),
            "https://cdn.example/download?id=123",
        ))

    def test_html_page_with_a_mkv_looking_url_is_not_a_final_file(self):
        self.assertFalse(workflow_analyzer._final_file(
            SimpleNamespace(status=200, content_type="text/html;charset=UTF-8", headers={}),
            "https://bdl.example/pretend-movie.mkv",
        ))

    def test_browser_verified_direct_stops_unrelated_queued_mirrors(self):
        requested: list[str] = []

        class Session:
            def __init__(self, *args, **kwargs): pass
            def fetch_html_once(self, url, referer=""):
                requested.append(url)
                pages = {
                    "https://site.example/movie": ('<a href="https://host.example/file">1080p Download</a>', 200, "text/html"),
                    "https://host.example/file": ('<button type="button">Direct Download</button><a href="https://slow.example/chain">Mirror</a>', 200, "text/html"),
                }
                if url == "https://slow.example/chain":
                    raise AssertionError("verified browser Direct result must stop queued mirrors")
                body, status, content_type = pages[url]
                return body, SimpleNamespace(url=url, status=status, location=None, content_type=content_type, content_length="", headers={}), None
            def inspect(self, url, method="HEAD"):
                return SimpleNamespace(url=url, status=200, location=None, content_type="application/octet-stream", content_length="10000000", headers={})

        class Renderer:
            def __enter__(self): return self
            def close(self): pass
            def direct_download(self, url): return "https://cdn.example/file.zip"

        with patch.object(workflow_analyzer, "SafeSession", Session), patch.object(workflow_analyzer, "PlaywrightRenderer", Renderer), patch.object(workflow_analyzer, "validate_public_url", lambda url: url):
            result = workflow_analyzer.analyze_movie_workflow("https://site.example/", "https://site.example/movie", "1080p")
        self.assertEqual(result["status"], "success")
        self.assertNotIn("https://slow.example/chain", requested)

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

    def test_host_priority_prefers_verified_direct_hosts_then_vcloud_then_gdflix(self):
        actions = [
            workflow_analyzer.Action("https://g.example/file", "GDFLIX DOWNLOAD", "https://page.example", "visible link"),
            workflow_analyzer.Action("https://v.example/file", "VCLOUD DOWNLOAD", "https://page.example", "visible link"),
            workflow_analyzer.Action("https://hubdrive.example/file", "Download Now", "https://page.example", "visible link"),
            workflow_analyzer.Action("https://d.example/file", "DIRECT DOWNLOAD", "https://page.example", "visible link"),
        ]
        self.assertEqual([item.label for item in workflow_analyzer._branch_actions(actions)], ["DIRECT DOWNLOAD", "Download Now", "VCLOUD DOWNLOAD", "GDFLIX DOWNLOAD"])

    def test_direct_redirect_completes_before_lower_priority_mirrors(self):
        requested: list[str] = []

        class Session:
            def __init__(self, *args, **kwargs):
                self.rows = {
                    "https://site.example/": ("<html></html>", 200, None, "text/html"),
                    "https://site.example/movie": ('<a href="https://landing.example/1080">1080p Download</a>', 200, None, "text/html"),
                    "https://landing.example/1080": ('<a href="https://direct.example/start">DIRECT DOWNLOAD</a><a href="https://vcloud.example/file">VCLOUD DOWNLOAD</a><a href="https://gdflix.example/file">GDFLIX DOWNLOAD</a>', 200, None, "text/html"),
                    "https://direct.example/start": ("", 302, "https://cdn.example/movie.mkv", "text/html"),
                    "https://cdn.example/movie.mkv": ("", 200, None, "video/x-matroska"),
                }

            def fetch_html_once(self, url, referer=""):
                requested.append(url)
                if url in {"https://vcloud.example/file", "https://gdflix.example/file"}:
                    raise AssertionError("A verified Direct result should stop lower-priority mirrors")
                body, status, location, content_type = self.rows[url]
                return body, SimpleNamespace(url=url, status=status, location=location, content_type=content_type, content_length="", headers={}), location

        with patch.object(workflow_analyzer, "SafeSession", Session), patch.object(workflow_analyzer, "validate_public_url", lambda url: url):
            result = workflow_analyzer.analyze_movie_workflow("https://site.example/", "https://site.example/movie", "1080p")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["results"][-1]["final_url"], "https://cdn.example/movie.mkv")
        self.assertNotIn("https://vcloud.example/file", requested)
        self.assertNotIn("https://gdflix.example/file", requested)

    def test_accepted_intermediate_shell_uses_renderer_then_direct_path(self):
        class Session:
            def __init__(self, *args, **kwargs):
                self.rows = {
                    "https://site.example/": ("<html></html>", 200, None, "text/html"),
                    "https://site.example/movie": ('<a href="https://landing.example/1080">1080p Download</a>', 200, None, "text/html"),
                    "https://landing.example/1080": ("<html></html>", 202, None, "text/html"),
                    "https://direct.example/start": ("", 302, "https://cdn.example/movie.mkv", "text/html"),
                    "https://cdn.example/movie.mkv": ("", 200, None, "video/x-matroska"),
                }

            def fetch_html_once(self, url, referer=""):
                body, status, location, content_type = self.rows[url]
                return body, SimpleNamespace(url=url, status=status, location=location, content_type=content_type, content_length="", headers={}), location

        rendered_urls: list[str] = []

        class Renderer:
            def __enter__(self): return self
            def close(self): pass
            def render(self, url):
                rendered_urls.append(url)
                return RenderedPage(url, '<a href="https://direct.example/start">DIRECT DOWNLOAD</a>', 202, "text/html", "", url)

        with patch.object(workflow_analyzer, "SafeSession", Session), patch.object(workflow_analyzer, "PlaywrightRenderer", Renderer), patch.object(workflow_analyzer, "validate_public_url", lambda url: url):
            result = workflow_analyzer.analyze_movie_workflow("https://site.example/", "https://site.example/movie", "1080p")
        self.assertEqual(result["status"], "success")
        self.assertEqual(rendered_urls, ["https://landing.example/1080"])

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
