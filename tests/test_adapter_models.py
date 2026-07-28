import unittest
from adapter_models import blank_adapter, validate_adapter
from adapter_analyzer import PageParser, _score
from adapter_runtime import normalized_title
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

if __name__ == "__main__": unittest.main()
