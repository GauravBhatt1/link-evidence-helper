import unittest

import web_app


class MobileSearchLayoutTests(unittest.TestCase):
    def test_search_workspace_has_an_intentional_idle_state(self):
        self.assertIn('id="welcomePanel"', web_app.HTML)
        self.assertIn('Find the right release, quickly.', web_app.HTML)
        self.assertIn('welcomePanelEl.classList.add("is-hidden")', web_app.HTML)

    def test_mobile_search_button_spans_the_search_panel_width(self):
        page = web_app.HTML
        mobile_rules = page[page.index("@media (max-width: 650px)"):]

        self.assertIn(
            ".search-panel #searchBtn { grid-column: 1 / -1; width: 100%; }",
            mobile_rules,
        )
