import unittest

import web_app


class MobileSearchLayoutTests(unittest.TestCase):
    def test_mobile_search_button_spans_the_search_panel_width(self):
        page = web_app.HTML
        mobile_rules = page[page.index("@media (max-width: 650px)"):]

        self.assertIn(
            ".search-panel #searchBtn { grid-column: 1 / -1; width: 100%; }",
            mobile_rules,
        )
