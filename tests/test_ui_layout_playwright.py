"""Rendered layout checks for aggregated result cards and delivery-link placement.

The Docker image includes Chromium.  Local developer environments without
Playwright skip this module, while the deployment check runs it in-container.
"""
from __future__ import annotations

import json
import unittest
from urllib.parse import urlparse

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

    def test_delivery_links_open_inside_a_later_active_card_on_desktop_and_mobile(self):
        for width, height in ((1280, 900), (390, 844)):
            with self.subTest(viewport=f"{width}x{height}"):
                page = self.browser.new_page(viewport={"width": width, "height": height})
                self.addCleanup(page.close)
                find_requests = []

                def route_request(route):
                    path = urlparse(route.request.url).path
                    if path == "/index/":
                        route.fulfill(status=200, content_type="text/html", body=web_app.HTML)
                        return
                    if path == "/index/api/wallpapers":
                        route.fulfill(
                            status=200,
                            content_type="application/json",
                            body=json.dumps({"ok": True, "images": []}),
                        )
                        return
                    if path == "/index/api/find":
                        find_requests.append(json.loads(route.request.post_data or "{}"))
                        route.fulfill(
                            status=200,
                            content_type="application/json",
                            body=json.dumps({
                                "ok": True,
                                "links": [{
                                    "quality": "1080p",
                                    "size": "1.2 GB",
                                    "url": "https://files.example/fixture.mkv",
                                    "source_name": "Fixture source",
                                    "kind": "Video file",
                                }],
                            }),
                        )
                        return
                    route.fulfill(status=404, content_type="text/plain", body="Not found")

                page.route("**/*", route_request)
                page.goto("http://fixture.test/index/", wait_until="domcontentloaded")
                page.evaluate(
                    """
                    () => {
                      state.contents = Array.from({ length: 15 }, (_, index) => ({
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
                      state.links = [];
                      state.showLinks = false;
                      state.selectedContent = 14;
                      state.selectedVariant = 0;
                      state.hasSearched = true;
                      renderCandidates();
                      window.scrollTo(0, 0);
                    }
                    """
                )
                self.assertTrue(page.evaluate(
                    """() => {
                      const panel = document.querySelector("#linksPanel");
                      return panel.classList.contains("is-hidden")
                        && panel.parentElement?.id === "linksPanelHome";
                    }"""
                ))

                page.locator(".find-release").click()
                page.wait_for_selector("#linksPanel:not(.is-hidden) .link-card")
                page.wait_for_function(
                    """() => {
                      const link = document.querySelector("#linksPanel .link-card");
                      if (!link) return false;
                      const rect = link.getBoundingClientRect();
                      return rect.top >= 0 && rect.bottom <= window.innerHeight;
                    }"""
                )
                layout = page.evaluate(
                    """() => {
                      const panel = document.querySelector("#linksPanel");
                      const active = document.querySelector(".content-card.active");
                      const find = active.querySelector(".find-release");
                      const next = active.nextElementSibling;
                      const panelRect = panel.getBoundingClientRect();
                      const findRect = find.getBoundingClientRect();
                      const linkRect = panel.querySelector(".link-card").getBoundingClientRect();
                      return {
                        visible: !panel.classList.contains("is-hidden"),
                        inActiveCard: active.contains(panel),
                        inDeliverySlot: panel.parentElement?.matches("[data-delivery-links-slot]"),
                        linkCount: document.querySelectorAll(".link-card").length,
                        linkTop: linkRect.top,
                        linkBottom: linkRect.bottom,
                        viewportHeight: window.innerHeight,
                        followsFindButton: panelRect.top >= findRect.bottom - 1,
                        beforeNextCard: !next || Boolean(
                          panel.compareDocumentPosition(next) & Node.DOCUMENT_POSITION_FOLLOWING
                        ),
                      };
                    }"""
                )
                self.assertEqual(find_requests, [{"contentId": "fixture-14", "variantId": "fixture-variant-14"}])
                self.assertTrue(layout["visible"])
                self.assertTrue(layout["inActiveCard"])
                self.assertTrue(layout["inDeliverySlot"])
                self.assertEqual(layout["linkCount"], 1)
                self.assertGreaterEqual(layout["linkTop"], 0)
                self.assertLessEqual(layout["linkBottom"], layout["viewportHeight"])
                self.assertTrue(layout["followsFindButton"])
                self.assertTrue(layout["beforeNextCard"])
