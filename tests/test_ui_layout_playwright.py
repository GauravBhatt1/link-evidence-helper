"""Rendered layout checks for aggregated result cards.

The Docker image includes Chromium.  Local developer environments without
Playwright skip this module, while the deployment check runs it in-container.
"""
from __future__ import annotations

import unittest

import web_app

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - host test environments need no browser
    sync_playwright = None


class AggregatedContentCardLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if sync_playwright is None:
            raise unittest.SkipTest("Playwright is available in the Docker verification image")
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls) -> None:
        if getattr(cls, "browser", None):
            cls.browser.close()
        if getattr(cls, "playwright", None):
            cls.playwright.stop()

    def render_fixture(self, width: int, height: int):
        page = self.browser.new_page(viewport={"width": width, "height": height})
        page.set_content(web_app.HTML, wait_until="domcontentloaded")
        page.evaluate(
            """
            () => {
              state.contents = Array.from({ length: 12 }, (_, index) => ({
                contentId: `fixture-${index}`,
                title: `Fixture title ${index + 1}`,
                year: "2024",
                mediaType: index % 2 ? "tv" : "movie",
                languages: ["Hindi", "English"],
                totalSources: 3,
                poster: "",
                releaseVariants: [{
                  variantId: `fixture-variant-${index}`,
                  language: "Hindi",
                  releaseType: "WEB-DL",
                  quality: "1080p",
                  sources: [{}, {}, {}],
                }],
              }));
              state.candidates = [];
              state.selectedContent = -1;
              state.selectedVariant = -1;
              state.hasSearched = true;
              renderCandidates();
            }
            """
        )
        return page

    @staticmethod
    def layout(page):
        return page.evaluate(
            """
            () => {
              const results = document.querySelector("#candidates");
              const cards = [...results.querySelectorAll(".content-card")];
              const first = cards[0];
              const poster = first.querySelector(".poster-frame");
              const meta = first.querySelector(".content-meta");
              return {
                resultsDisplay: getComputedStyle(results).display,
                resultsOverflowX: getComputedStyle(results).overflowX,
                resultsWidth: results.getBoundingClientRect().width,
                resultsScrollWidth: results.scrollWidth,
                cardWidths: cards.map((card) => card.getBoundingClientRect().width),
                posterWidth: poster.getBoundingClientRect().width,
                metadataDisplay: getComputedStyle(meta).display,
              };
            }
            """
        )

    def test_desktop_cards_keep_metadata_inline_and_poster_in_its_grid_track(self):
        page = self.render_fixture(1280, 900)
        self.addCleanup(page.close)
        layout = self.layout(page)

        self.assertEqual(layout["resultsDisplay"], "grid")
        self.assertEqual(layout["metadataDisplay"], "flex")
        self.assertAlmostEqual(layout["posterWidth"], 106, delta=1)
        self.assertTrue(all(width >= layout["resultsWidth"] - 2 for width in layout["cardWidths"]))

    def test_mobile_cards_do_not_shrink_into_the_legacy_poster_carousel(self):
        page = self.render_fixture(390, 844)
        self.addCleanup(page.close)
        layout = self.layout(page)

        self.assertEqual(layout["resultsDisplay"], "grid")
        self.assertEqual(layout["resultsOverflowX"], "visible")
        self.assertEqual(layout["metadataDisplay"], "flex")
        self.assertAlmostEqual(layout["posterWidth"], 76, delta=1)
        self.assertLessEqual(layout["resultsScrollWidth"], layout["resultsWidth"] + 1)
        self.assertTrue(all(width >= layout["resultsWidth"] - 2 for width in layout["cardWidths"]))
