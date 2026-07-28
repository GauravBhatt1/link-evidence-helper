import unittest
from unittest.mock import patch

from custom_authorized_source import (LiveFetchError, auto_setup_from_site_url, detect_selectors, fetch_live_page, guided_source_setup, inspect_example_result,
                                      live_auto_detect, live_preview, parse_html,
                                      preview_selectors, resolve_authorized_result, validate_config, validate_live_page_url)


CONFIG = {
    "allowedDomain": "catalog.example.com",
    "searchUrlTemplate": "https://catalog.example.com/search?q={query}",
    "resultContainers": [".result", "[data-result]"],
    "title": [".title", "h2"],
    "link": ["a.download", "a[href]"],
    "quality": [".quality"],
    "fileSize": [".size"],
    "provider": [".provider"],
    "requestTimeout": 12,
}


class AuthorizedParserTests(unittest.TestCase):
    def test_direct_file_example_is_inspected_by_headers_without_download(self):
        class Reply:
            status = 200
            headers = {"content-type": "video/x-matroska", "content-length": "123456", "content-disposition": 'attachment; filename="movie.mkv"'}
            def read(self, _limit): raise AssertionError("direct file body must not be downloaded")
            def getcode(self): return self.status
        class Opener:
            def open(self, request, timeout):
                self.method = request.get_method()
                return Reply()
        opener = Opener(); dns = [(None, None, None, None, ("93.184.216.34", 443))]
        with patch("custom_authorized_source.socket.getaddrinfo", return_value=dns), patch("custom_authorized_source.build_opener", return_value=opener):
            metadata = inspect_example_result("https://files.example.com/download/movie-name.mkv?token=secret")
        self.assertEqual(opener.method, "HEAD")
        self.assertEqual(metadata["kind"], "direct-file")
        self.assertEqual(metadata["contentLength"], 123456)
        self.assertEqual(metadata["fileName"], "movie.mkv")
        self.assertEqual(metadata["pathPattern"], "https://files.example.com/download/*.mkv")

    def test_guided_setup_learns_result_route_and_repeating_cards(self):
        search_html = '''<nav><a href="/post/login">Login</a></nav><section><article class="result"><img class="poster" src="/one.jpg"><h2 class="title">One</h2><a class="result-link" href="/post/one">Open</a><span class="quality">1080p</span><span class="size">1 GB</span><span class="provider">Catalog</span></article><article class="result"><img class="poster" src="/two.jpg"><h2 class="title">Two</h2><a class="result-link" href="/post/two">Open</a><span class="quality">720p</span><span class="size">900 MB</span><span class="provider">Catalog</span></article></section>'''
        pages = {
            "https://catalog.example.com/search?q=x": {"url": "https://catalog.example.com/search?q=x", "httpStatus": 200, "contentType": "text/html", "body": search_html, "rendered": False},
        }
        example = {"success": True, "kind": "result-page", "originalUrl": "https://catalog.example.com/post/one", "finalUrl": "https://catalog.example.com/post/one", "hostname": "catalog.example.com", "pathPattern": "https://catalog.example.com/post/*", "pathPrefix": "/post/", "fileExtension": "", "contentType": "text/html", "contentLength": None, "fileName": "one", "redirectChain": ["https://catalog.example.com/post/one"], "httpStatus": 200}
        with patch("custom_authorized_source.fetch_live_page", side_effect=lambda url, **_kwargs: pages[str(url)]), patch("custom_authorized_source.inspect_example_result", return_value=example):
            result = guided_source_setup("https://catalog.example.com/search?q=x", "https://catalog.example.com/post/one", render_mode="static")
        self.assertTrue(result["success"])
        self.assertEqual(result["detectionStatus"], "working")
        self.assertEqual(result["urlPattern"], "https://catalog.example.com/post/*")
        self.assertEqual(result["diagnostics"]["matchingLinks"], 2)
        self.assertEqual(result["suggestions"]["resultContainers"][0]["selector"], "article.result")
        self.assertEqual(result["suggestions"]["thumbnail"][0]["selector"], "img.poster")
        self.assertEqual(len(result["cards"]), 2)
        self.assertEqual(result["diagnostics"]["validResults"], 2)
        self.assertEqual(result["config"]["resultContainers"][0], "article.result")
        self.assertEqual(result["config"]["searchUrlTemplate"], "https://catalog.example.com/search?q={query}")

    def test_guided_setup_reports_url_pattern_mismatch_as_successful_fetch(self):
        pages = {
            "https://catalog.example.com/search": {"url": "https://catalog.example.com/search", "httpStatus": 200, "contentType": "text/html", "body": '<a href="/category/action">Action</a>', "rendered": False},
        }
        example = {"success": True, "kind": "result-page", "originalUrl": "https://catalog.example.com/post/one", "finalUrl": "https://catalog.example.com/post/one", "hostname": "catalog.example.com", "pathPattern": "https://catalog.example.com/post/*", "pathPrefix": "/post/", "fileExtension": "", "contentType": "text/html", "contentLength": None, "fileName": "one", "redirectChain": ["https://catalog.example.com/post/one"], "httpStatus": 200}
        with patch("custom_authorized_source.fetch_live_page", side_effect=lambda url, **_kwargs: pages[str(url)]), patch("custom_authorized_source.inspect_example_result", return_value=example):
            result = guided_source_setup("https://catalog.example.com/search", "https://catalog.example.com/post/one", render_mode="static")
        self.assertTrue(result["success"])
        self.assertEqual(result["status"], 200)
        self.assertEqual(result["detectionStatus"], "url-pattern-mismatch")
        self.assertEqual(result["diagnostics"]["errorStage"], "no-matching-result-links")

    def test_direct_file_guided_setup_uses_repeating_detail_cards_and_verifies_delivery(self):
        html = '''<article class="card"><h2 class="title">One</h2><a class="open" href="/detail/one">Open</a></article><article class="card"><h2 class="title">Two</h2><a class="open" href="/detail/two">Open</a></article>'''
        search = {"url": "https://catalog.example.com/search?q=x", "httpStatus": 200, "contentType": "text/html", "body": html, "rendered": False}
        example = {"success": True, "kind": "direct-file", "finalUrl": "https://cdn.example.com/files/one.mkv", "hostname": "cdn.example.com", "pathPattern": "https://cdn.example.com/files/*.mkv", "pathPrefix": "/files/", "fileExtension": ".mkv", "contentType": "video/x-matroska", "httpStatus": 200}
        with patch("custom_authorized_source.fetch_live_page", return_value=search), patch("custom_authorized_source.inspect_example_result", return_value=example), patch("custom_authorized_source.discover_authorized_delivery", return_value={"found": True, "url": "https://cdn.example.com/files/one.mkv", "trail": [], "kind": "direct-file"}):
            result = guided_source_setup(search["url"], example["finalUrl"], render_mode="static")
        self.assertEqual(result["detectionStatus"], "working")
        self.assertEqual(result["config"]["deliveryPattern"]["hostname"], "cdn.example.com")
        self.assertTrue(result["cards"][0]["deliveryUrl"].endswith(".mkv"))

    def test_resolve_authorized_result_uses_learned_delivery_pattern(self):
        source = {"config": {**CONFIG, "deliveryPattern": {"hostname": "cdn.example.com", "pathPrefix": "/files/", "fileExtension": ".mkv", "maxDepth": 2}}}
        with patch("custom_authorized_source.discover_authorized_delivery", return_value={"found": True, "url": "https://cdn.example.com/files/one.mkv", "trail": ["https://catalog.example.com/detail/one"]}):
            result = resolve_authorized_result(source, "https://catalog.example.com/detail/one")
        self.assertTrue(result["success"])
        self.assertEqual(result["url"], "https://cdn.example.com/files/one.mkv")

    def test_delivery_resolution_accepts_a_fresh_signed_url_without_saving_its_token(self):
        from custom_authorized_source import _delivery_pattern_matches
        pattern = {"hostname": "cdn.example.com", "pathPrefix": "/files/", "fileExtension": ".mkv", "maxDepth": 2}
        self.assertTrue(_delivery_pattern_matches("https://cdn.example.com/files/movie.mkv?token=fresh&expires=999", pattern))
        self.assertFalse(_delivery_pattern_matches("https://cdn.example.com/other/movie.mkv?token=fresh", pattern))

    def test_one_url_setup_discovers_the_site_get_search_form(self):
        landing = {"url": "https://catalog.example.com/", "httpStatus": 200, "contentType": "text/html", "body": '<form action="/search" method="get"><input type="search" name="q"></form>', "rendered": False}
        search = {"url": "https://catalog.example.com/search?q=a", "httpStatus": 200, "contentType": "text/html", "body": '<article class="card"><h2>One</h2><a href="/post/one">Open</a></article><article class="card"><h2>Two</h2><a href="/post/two">Open</a></article>', "rendered": False}
        guided = {"success": True, "detectionStatus": "working", "config": CONFIG, "diagnostics": {"validResults": 2}}
        with patch("custom_authorized_source.fetch_live_page", side_effect=[landing, search]), patch("custom_authorized_source.guided_source_setup", return_value=guided) as setup:
            result = auto_setup_from_site_url("https://catalog.example.com", render_mode="static")
        self.assertEqual(result["discoveredSearchTemplate"], "https://catalog.example.com/search?q={query}")
        self.assertEqual(setup.call_args.args[0], "https://catalog.example.com/search?q=a")
    def test_live_url_blocks_localhost_unless_explicitly_enabled(self):
        with patch("custom_authorized_source.socket.getaddrinfo", return_value=[(None, None, None, None, ("127.0.0.1", 80))]):
            with self.assertRaises(LiveFetchError) as error:
                validate_live_page_url("http://localhost/catalog")
            self.assertEqual(error.exception.status, "access-restricted")
            self.assertEqual(validate_live_page_url("http://localhost/catalog", True), "http://localhost/catalog")

    def test_live_fetch_follows_safe_redirect_without_leaking_auth_to_other_origin(self):
        class Reply:
            def __init__(self, status, headers, body=b""):
                self.status, self.headers, self._body = status, headers, body
            def read(self, _limit): return self._body
            def getcode(self): return self.status
        requests = []
        class Opener:
            def open(self, request, timeout):
                requests.append(dict(request.header_items()))
                if len(requests) == 1:
                    return Reply(302, {"location": "https://redirect.example.com/results"})
                return Reply(200, {"content-type": "text/html"}, b"<main>OK</main>")
        dns = [(None, None, None, None, ("93.184.216.34", 443))]
        with patch("custom_authorized_source.socket.getaddrinfo", return_value=dns), patch("custom_authorized_source.build_opener", return_value=Opener()):
            fetched = fetch_live_page("https://catalog.example.com", auth={"bearerToken": "not-to-log", "headers": {"X-Private": "yes"}})
        self.assertEqual(fetched["url"], "https://redirect.example.com/results")
        self.assertIn("Authorization", requests[0])
        self.assertNotIn("Authorization", requests[1])
        self.assertNotIn("X-private", requests[1])

    def test_live_detect_returns_consistent_success_schema_and_preview(self):
        html = b'<article class="result movie-card"><h2 class="title">One</h2><a class="download" href="/one">Open</a><span class="quality">1080p</span><span class="size">1 GB</span><span class="provider">Catalog</span></article><article class="result movie-card"><h2 class="title">Two</h2><a class="download" href="/two">Open</a></article>'
        class Reply:
            status = 200
            headers = {"content-type": "text/html"}
            def read(self, _limit): return html
            def getcode(self): return self.status
        class Opener:
            def open(self, request, timeout): return Reply()
        dns = [(None, None, None, None, ("93.184.216.34", 443))]
        with patch("custom_authorized_source.socket.getaddrinfo", return_value=dns), patch("custom_authorized_source.build_opener", return_value=Opener()):
            detected = live_auto_detect("https://catalog.example.com/results", render_mode="static")
            preview = live_preview("https://catalog.example.com/results", CONFIG, "Custom", render_mode="static")
        self.assertTrue(detected["reliable"])
        self.assertTrue(detected["success"])
        self.assertEqual(detected["status"], 200)
        self.assertEqual(detected["contentType"], "text/html")
        self.assertEqual(detected["finalUrl"], "https://catalog.example.com/results")
        self.assertIn("movie-card", detected["html"])
        self.assertTrue(detected["diagnostics"]["fetchCompleted"])
        self.assertTrue(detected["diagnostics"]["domParsingCompleted"])
        self.assertEqual(preview["diagnostics"]["validResults"], 2)
        self.assertEqual(preview["preview"]["containersMatched"], 2)

    def test_http_200_without_selectors_is_a_successful_fetch(self):
        html = b"<main>Empty search page</main>"
        class Reply:
            status = 200
            headers = {"content-type": "text/html"}
            def read(self, _limit): return html
            def getcode(self): return self.status
        class Opener:
            def open(self, request, timeout): return Reply()
        dns = [(None, None, None, None, ("93.184.216.34", 443))]
        with patch("custom_authorized_source.socket.getaddrinfo", return_value=dns), patch("custom_authorized_source.build_opener", return_value=Opener()):
            detected = live_auto_detect("https://catalog.example.com/empty", render_mode="static")
        self.assertTrue(detected["success"])
        self.assertTrue(detected["ok"])
        self.assertEqual(detected["status"], 200)
        self.assertEqual(detected["detectionStatus"], "no-selectors-detected")
        self.assertEqual(detected["diagnostics"]["errorStage"], "no-selectors-detected")
        self.assertIn("Page fetched successfully", detected["message"])
    def test_valid_result_normalizes_to_contract(self):
        html = '<article class="result"><h2 class="title">Example Title</h2><a class="download" href="/public/file">Open</a><span class="quality">1080p</span><span class="size">1.4 GB</span><span class="provider">Example Provider</span></article>'
        rows, diagnostics = parse_html(html, CONFIG, "source-1", "Custom Authorized Source", "https://catalog.example.com/search?q=x")
        self.assertEqual(diagnostics["validResults"], 1)
        self.assertEqual(rows[0], {"sourceId": "source-1", "title": "Example Title", "quality": "1080p", "filename": "Example Title", "fileSize": "1.4 GB", "providerName": "Example Provider", "pageUrl": "https://catalog.example.com/search?q=x", "publicUrl": "https://catalog.example.com/public/file", "sourceBadge": "Custom Authorized Source", "confidence": "high"})

    def test_invalid_records_report_exact_rejection_reason(self):
        html = '<div class="result"><span class="title">Missing link</span></div><div class="result"><a href="javascript:bad">Missing title</a></div>'
        rows, diagnostics = parse_html(html, CONFIG, "source-1", "Custom", "https://catalog.example.com/search?q=x")
        self.assertEqual(rows, [])
        self.assertEqual([entry["reason"] for entry in diagnostics["rejectedResults"]], ["Missing URL", "Missing title"])

    def test_duplicate_urls_are_rejected(self):
        html = '<div class="result"><span class="title">One</span><a href="/same">A</a></div><div class="result"><span class="title">Two</span><a href="/same">B</a></div>'
        rows, diagnostics = parse_html(html, CONFIG, "source-1", "Custom", "https://catalog.example.com/search?q=x")
        self.assertEqual(len(rows), 1)
        self.assertIn("Duplicate result", [entry["reason"] for entry in diagnostics["rejectedResults"]])

    def test_selector_mismatch_is_explicit(self):
        rows, diagnostics = parse_html("<main>No results</main>", CONFIG, "source-1", "Custom", "https://catalog.example.com/search?q=x")
        self.assertEqual(rows, [])
        self.assertEqual(diagnostics["parserStatus"], "parser-mismatch")
        self.assertEqual(diagnostics["rejectedResults"][0]["reason"], "Parser selector mismatch")

    def test_config_rejects_other_domain(self):
        invalid = dict(CONFIG, searchUrlTemplate="https://other.example.com/search?q={query}")
        with self.assertRaises(ValueError):
            validate_config(invalid)

    def test_auto_detects_ranked_selectors_from_repeating_html(self):
        html = '''<section><article class="movie-card"><h2 class="title">One</h2><a class="download" href="/one">Open</a><span class="quality">1080p</span><span class="size">1 GB</span><span class="provider">Catalog</span></article><article class="movie-card"><h2 class="title">Two</h2><a class="download" href="/two">Open</a><span class="quality">720p</span><span class="size">900 MB</span><span class="provider">Catalog</span></article></section>'''
        detected = detect_selectors(html)
        self.assertTrue(detected["reliable"])
        self.assertEqual(detected["suggestions"]["resultContainers"][0]["selector"], "article.movie-card")
        self.assertEqual(detected["suggestions"]["title"][0]["selector"], "h2.title")
        self.assertEqual(detected["suggestions"]["link"][0]["selector"], "a.download")
        self.assertLessEqual(len(detected["suggestions"]["quality"]), 5)

    def test_auto_detect_supplies_editable_low_confidence_fallbacks_for_every_field(self):
        detected = detect_selectors("<main>Rendered page shell</main>")
        self.assertFalse(detected["reliable"])
        for field in ("resultContainers", "title", "link", "quality", "fileSize", "provider", "pagination", "waitFor"):
            self.assertTrue(detected["suggestions"][field], field)
            self.assertLess(detected["suggestions"][field][0]["confidence"], 50)

    def test_preview_reports_found_and_missing_fields_per_container(self):
        html = '<article class="result"><h2 class="title">One</h2><a href="/one">Open</a></article>'
        preview = preview_selectors(html, CONFIG)
        self.assertEqual(preview["containersMatched"], 1)
        self.assertTrue(preview["containers"][0]["found"]["title"])
        self.assertIn("Missing Size", preview["containers"][0]["missing"])

    def test_isolated_source_failure_does_not_discard_other_source(self):
        import web_app
        sources = [
            {"id": "bad", "name": "Bad", "base_url": "https://bad.example.com", "enabled": True, "parser_type": "custom-authorized", "config": CONFIG},
            {"id": "good", "name": "Good", "base_url": "https://catalog.example.com", "enabled": True, "parser_type": "custom-authorized", "config": CONFIG},
        ]
        class Library:
            def list_sources(self): return sources
        def fake_search(source, query):
            if source["id"] == "bad": raise RuntimeError("broken parser")
            return ([{"sourceId": "good", "title": "Good result", "quality": "720p", "filename": "good", "fileSize": "1 GB", "providerName": "Good", "pageUrl": "https://catalog.example.com/search?q=x", "publicUrl": "https://catalog.example.com/file", "sourceBadge": "Good", "confidence": "high"}], {"parserStatus": "working", "finalRenderedResults": 1})
        with patch.object(web_app, "LIBRARY", Library()), patch.object(web_app, "search_authorized_source", fake_search):
            rows, diagnostics = web_app.custom_authorized_search("x")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Good result")
        self.assertEqual(rows[0]["url"], "https://catalog.example.com/file")
        self.assertEqual(diagnostics["bad"]["errorStage"], "configuration")


if __name__ == "__main__":
    unittest.main()
