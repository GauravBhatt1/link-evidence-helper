import threading
import unittest
from unittest.mock import patch

import web_app


class SourceSearchConcurrencyTests(unittest.TestCase):
    def test_all_source_types_start_together_but_keep_display_order(self):
        started = set()
        ready = threading.Event()
        lock = threading.Lock()

        def mark(name, value):
            with lock:
                started.add(name)
                if len(started) == 3:
                    ready.set()
            self.assertTrue(ready.wait(1), f"{name} search did not start in parallel")
            return value

        with (
            patch.object(web_app, "search_movie", side_effect=lambda *_args: mark("legacy", ["legacy"])),
            patch.object(web_app, "custom_authorized_search", side_effect=lambda *_args: mark("custom", (["custom"], {"custom": {}}))),
            patch.object(web_app, "saved_adapter_search", side_effect=lambda *_args: mark("adapter", (["adapter"], []))),
        ):
            rows, diagnostics, failures = web_app.search_all_configured_sources("Ikka", "all")

        self.assertEqual(rows, ["legacy", "custom", "adapter"])
        self.assertEqual(diagnostics, {"custom": {}})
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
