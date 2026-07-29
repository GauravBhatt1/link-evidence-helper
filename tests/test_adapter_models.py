import unittest
from adapter_models import blank_adapter, enable_workflow_fallback_for_verified_onboarding, validate_adapter
from adapter_analyzer import PageParser, _score
from adapter_runtime import SiteAdapter, normalized_title
from adapter_analyzer import _looks_like_javascript_search

class AdapterModelTests(unittest.TestCase):
    def test_blank_adapter_is_editable_and_valid(self):
        data=blank_adapter("Example Site", "https://example.com")
        self.assertEqual(validate_adapter(data)["id"], "example_site")
    def test_parser_and_download_score(self):
        parser=PageParser("https://example.com")
        with open("tests/fixtures/site.html", encoding="utf-8") as fixture:
            parser.feed(fixture.read())
        link=next(item for item in parser.elements if item.href)
        score, reasons=_score(link,"1080p")
        self.assertGreaterEqual(score, 10)
        self.assertIn("requested quality", reasons)
    def test_title_normalization_merges_quality_variants(self):
        self.assertEqual(normalized_title("Governor (2026) 1080p"), normalized_title("Governor 2026"))

    def test_dynamic_search_shell_is_not_treated_as_working_results(self):
        html = '<div id="results-grid"></div><script>fetch("/documents/search")</script>'
        self.assertTrue(_looks_like_javascript_search(html))

    def test_runtime_ignores_share_and_non_http_actions(self):
        adapter = SiteAdapter(blank_adapter("Example Site", "https://example.com"))
        html = '''
            <a href="whatsapp://send?text=share">Share on WhatsApp</a>
            <a href="javascript:void(0)">JavaScript action</a>
            <a href="mailto:help@example.com">Contact</a>
            <a href="https://files.example/movie.mkv">1080p Download</a>
        '''
        self.assertEqual(adapter.extract_candidates(html, "https://example.com/movie"), [{"title": "1080p Download", "url": "https://files.example/movie.mkv"}])
        self.assertEqual([row["url"] for row in adapter.extract_quality_links(html, "https://example.com/movie", "1080p")], ["https://files.example/movie.mkv"])

    def test_search_rejects_current_page_navigation_even_when_query_is_in_url(self):
        adapter = SiteAdapter(blank_adapter("Example Site", "https://example.com"))
        search_url = "https://example.com/?s=Ikka"
        navigation = {"title": "Skip to content", "url": "https://example.com/?s=Ikka#main"}
        movie = {"title": "Ikka 2026 Hindi Full Movie", "url": "https://example.com/ikka-2026/"}
        unrelated = {"title": "Another Movie", "url": "https://example.com/another-movie/"}
        terms = ["ikka"]
        self.assertFalse(adapter._is_matching_search_result(navigation, search_url, terms))
        self.assertTrue(adapter._is_matching_search_result(movie, search_url, terms))
        self.assertFalse(adapter._is_matching_search_result(unrelated, search_url, terms))

    def test_verified_onboarding_enables_workflow_fallback_only(self):
        verified = blank_adapter("Verified Site", "https://example.com")
        verified["maker"] = {"workflow_verified": True}
        self.assertTrue(enable_workflow_fallback_for_verified_onboarding(verified))
        self.assertTrue(verified["workflow_analyzer"]["enabled"])
        unverified = blank_adapter("Unverified Site", "https://example.net")
        self.assertFalse(enable_workflow_fallback_for_verified_onboarding(unverified))
        self.assertNotIn("workflow_analyzer", unverified)

if __name__ == "__main__": unittest.main()
