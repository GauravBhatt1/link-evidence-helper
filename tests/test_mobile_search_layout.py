import unittest

import web_app


class MobileSearchLayoutTests(unittest.TestCase):
    def test_search_workspace_has_an_intentional_idle_state(self):
        self.assertIn('id="welcomePanel"', web_app.HTML)
        self.assertIn('Find the right release, quickly.', web_app.HTML)
        self.assertIn('welcomePanelEl.classList.add("is-hidden")', web_app.HTML)

    def test_library_filters_and_source_actions_have_accessible_labels(self):
        self.assertIn('aria-label="Filter by availability"', web_app.HTML)
        self.assertIn('aria-label="Sort library"', web_app.HTML)

    def test_library_import_exposes_tmdb_match_and_safe_rename_controls(self):
        self.assertIn('id="tmdbSearch"', web_app.HTML)
        self.assertIn('id="renamePreview"', web_app.HTML)
        self.assertIn('id="renameConfirm"', web_app.HTML)
        self.assertIn('/api/admin/library/rename-preview', web_app.HTML)
        self.assertIn('class="source-card"', web_app.HTML)
        self.assertIn('aria-label="Test ${escapeHtml(row.name)}"', web_app.HTML)

    def test_mobile_search_button_spans_the_search_panel_width(self):
        page = web_app.HTML
        mobile_rules = page[page.index("@media (max-width: 650px)"):]

        self.assertIn(
            ".search-panel #searchBtn { grid-column: 1 / -1; width: 100%; }",
            mobile_rules,
        )
