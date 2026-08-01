import threading
import time
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
            rows, diagnostics, failures = web_app.search_all_configured_sources("Ikka")

        self.assertEqual(rows, ["legacy", "custom", "adapter"])
        self.assertEqual(diagnostics, {"custom": {}})
        self.assertEqual(failures, [])

    def test_slow_adapter_does_not_hold_up_all_search_results_and_is_backed_off(self):
        adapter = {
            "id": "slow", "name": "Slow source", "enabled": True,
            "domains": ["slow.example"], "search": {"url_template": "https://slow.example/?q={query}"},
            "health": {"last_test_status": "passed"},
        }

        class SlowAdapter:
            def __init__(self, _config):
                pass

            def search(self, _query):
                time.sleep(0.2)
                return []

        original_backoff = dict(web_app.SEARCH_ADAPTER_FAILURE_UNTIL)
        web_app.SEARCH_ADAPTER_FAILURE_UNTIL.clear()
        try:
            with (
                patch.object(web_app, "enabled_saved_adapters", return_value=[adapter]),
                patch.object(web_app, "SiteAdapter", SlowAdapter),
                patch.object(web_app, "SEARCH_ADAPTER_TIMEOUT_SECONDS", 0.01),
            ):
                started = time.monotonic()
                rows, failures = web_app.saved_adapter_search("test")
                elapsed = time.monotonic() - started
                _, backed_off_failures = web_app.saved_adapter_search("test")
            self.assertEqual(rows, [])
            self.assertLess(elapsed, 0.1)
            self.assertEqual(failures[0]["reason"], "Search timed out")
            self.assertEqual(backed_off_failures[0]["reason"], "Temporarily paused after a timeout")
        finally:
            web_app.SEARCH_ADAPTER_FAILURE_UNTIL.clear()
            web_app.SEARCH_ADAPTER_FAILURE_UNTIL.update(original_backoff)


if __name__ == "__main__":
    unittest.main()
