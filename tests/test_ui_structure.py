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
        qualities = [button for button in self.page.buttons if button.get("data-quality")]
        self.assertEqual(query.get("aria-label"), "Search title")
        self.assertEqual(search["tag"], "button")
        self.assertEqual({button["data-quality"] for button in qualities}, {"480p", "720p", "1080p", "2160p", "all"})

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

    def test_movie_results_prefer_normalized_contents_and_find_by_variant_identity(self):
        # The flat candidate projection remains only as a rolling-deploy
        # fallback; normal movie rendering and Get Link use the aggregate API.
        self.assertIn("state.contents = Array.isArray(body.contents) ? body.contents : []", web_app.HTML)
        self.assertIn("state.candidates = state.contents.length ? [] : (body.candidates || [])", web_app.HTML)
        self.assertIn('data-variant-index="${variantIndex}"', web_app.HTML)
        self.assertIn("contentId: content.contentId, variantId: variant.variantId", web_app.HTML)

    def test_aggregated_cards_keep_the_compact_mobile_poster_rail(self):
        # Content cards have an extra variants section, unlike legacy cards,
        # but they remain compact cards in the same swipeable results rail.
        self.assertIn(".content-card .poster-frame", web_app.HTML)
        self.assertIn("flex: 0 0 136px", web_app.HTML)
        self.assertIn("aspect-ratio: 2 / 3", web_app.HTML)

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
