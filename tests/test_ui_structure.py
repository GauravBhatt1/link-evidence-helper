"""Fast semantic UI checks; rendered viewport behaviour is tested separately."""
import unittest
from html.parser import HTMLParser

import web_app


class _PageIndex(HTMLParser):
    def __init__(self):
        super().__init__()
        self.by_id, self.buttons = {}, []

    def handle_starttag(self, tag, attrs):
        node = {"tag": tag, **dict(attrs)}
        if node.get("id"):
            self.by_id[node["id"]] = node
        if tag == "button":
            self.buttons.append(node)


class UiStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = _PageIndex()
        cls.page.feed(web_app.HTML)

    def test_search_controls_have_labels_and_usable_targets(self):
        query = self.page.by_id["query"]
        search = self.page.by_id["searchBtn"]
        self.assertEqual(query.get("aria-label"), "Search title")
        self.assertEqual(search["tag"], "button")

    def test_navigation_and_mobile_menu_are_connected_accessibly(self):
        mobile_menu = self.page.by_id["mobileMoreMenu"]
        more = self.page.by_id["mobileMoreBtn"]
        desktop_views = {button["data-view"] for button in self.page.buttons if button.get("data-view")}
        self.assertEqual(more.get("aria-controls"), mobile_menu.get("id"))
        self.assertEqual(more.get("aria-expanded"), "false")
        self.assertTrue({"search", "movies", "tv", "missing", "recent", "admin"} <= desktop_views)
        self.assertNotIn("sources", desktop_views)

    def test_dynamic_regions_are_announced_and_modal_is_a_dialog(self):
        self.assertEqual(self.page.by_id["toast"].get("role"), "status")
        self.assertEqual(self.page.by_id["progressWrap"].get("aria-live"), "polite")
        detail = self.page.by_id["libraryDetail"]
        self.assertEqual(detail.get("role"), "dialog")
        self.assertEqual(detail.get("aria-modal"), "true")

    def test_movie_results_use_the_content_card_as_the_only_workspace(self):
        self.assertIn("state.contents = Array.isArray(body.contents) ? body.contents : []", web_app.HTML)
        self.assertIn("state.candidates = state.contents.length ? [] : (body.candidates || [])", web_app.HTML)
        self.assertIn('data-variant-index="${variantIndex}"', web_app.HTML)
        self.assertIn("contentId: content.contentId, variantId: variant.variantId, quality: selectedQuality", web_app.HTML)
        self.assertIn('body: JSON.stringify({ contentId: content.contentId, variantId: variant.variantId, quality: selectedQuality })', web_app.HTML)
        self.assertIn("Select a release to continue.", web_app.HTML)
        self.assertIn("Select a quality to continue.", web_app.HTML)
        self.assertNotIn('id="selectedResult"', web_app.HTML)
        self.assertNotIn("Selected result ${content", web_app.HTML)

    def test_multi_quality_releases_offer_one_inline_quality_choice(self):
        self.assertIn("availableQualities", web_app.HTML)
        self.assertIn("selectedVariantQuality", web_app.HTML)
        self.assertIn("variant-quality-picker", web_app.HTML)
        self.assertIn("variant-quality-choice", web_app.HTML)
        self.assertIn("const findDisabled = state.busy || !variant || !selectedQuality", web_app.HTML)

    def test_aggregated_cards_progressively_disclose_releases_on_mobile(self):
        self.assertIn(".content-card .poster-frame", web_app.HTML)
        self.assertIn(".release-workspace", web_app.HTML)
        self.assertIn('aria-expanded="${contentIndex === state.selectedContent ? "true" : "false"}"', web_app.HTML)
        self.assertIn("min-height: 44px", web_app.HTML)

    def test_aggregated_cards_do_not_inherit_the_legacy_poster_carousel(self):
        self.assertIn('candidatesEl.classList.toggle("content-results", hasContents)', web_app.HTML)
        self.assertIn(".results.content-results", web_app.HTML)
        self.assertIn("grid-template-columns: minmax(0, 1fr);", web_app.HTML)
        self.assertIn(".content-card .poster-frame { width: 106px; min-width: 0; min-height: 0; aspect-ratio: auto;", web_app.HTML)
        self.assertIn(".poster-card span", web_app.HTML)
        self.assertNotIn(".candidate span", web_app.HTML)

    def test_admin_lock_does_not_persist_plain_password_and_uses_existing_header(self):
        self.assertIn("let adminPassword = \"\"", web_app.HTML)
        self.assertNotIn('sessionStorage.getItem("adminPassword")', web_app.HTML)
        self.assertNotIn('sessionStorage.setItem("adminPassword"', web_app.HTML)
        self.assertIn("Incorrect password", web_app.HTML)
        self.assertIn('"x-admin-password": adminPassword', web_app.HTML)

    def test_admin_contains_root_folder_picker_and_sources_are_not_public(self):
        self.assertIn("Add Root Folder", web_app.HTML)
        self.assertIn("Use This Folder", web_app.HTML)
        self.assertIn("Remove from library configuration", web_app.HTML)
        self.assertNotIn('<button data-view="sources">Sources</button>', web_app.HTML)
