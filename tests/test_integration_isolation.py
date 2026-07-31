import unittest
from unittest.mock import patch

import web_app


class IntegrationIsolationTests(unittest.TestCase):
    def setUp(self):
        self.jellyfin_url = web_app.JELLYFIN_URL
        self.jellyfin_key = web_app.JELLYFIN_API_KEY
        web_app.JELLYFIN_URL = "https://jellyfin.invalid"
        web_app.JELLYFIN_API_KEY = "never-log-this-key"
        web_app.JELLYFIN_SHOW_DETAIL_CACHE.clear()

    def tearDown(self):
        web_app.JELLYFIN_URL = self.jellyfin_url
        web_app.JELLYFIN_API_KEY = self.jellyfin_key
        web_app.JELLYFIN_SHOW_DETAIL_CACHE.clear()

    def test_jellyfin_outage_returns_a_generic_detail_error(self):
        with patch.object(web_app, "urlopen", side_effect=OSError("https://jellyfin.invalid/Items?api_key=leak")):
            result = web_app.jellyfin_show_availability("show-id")
        self.assertEqual(result["error"], "Jellyfin episode details are temporarily unavailable.")
        self.assertNotIn("leak", result["error"])

    def test_jellyfin_refresh_does_not_return_exception_or_key(self):
        with patch.object(web_app, "urlopen", side_effect=OSError("request token=leak")):
            result = web_app.trigger_jellyfin_library_refresh()
        self.assertFalse(result["started"])
        self.assertEqual(result["message"], "Jellyfin refresh is temporarily unavailable.")
        self.assertNotIn("leak", result["message"])

    def test_http_access_log_redacts_query_parameters(self):
        handler = type("Handler", (), {"address_string": lambda self: "127.0.0.1"})()
        with patch.object(web_app.LOG, "info") as info:
            web_app.AppHandler.log_message(handler, '"GET /api/find?token=secret HTTP/1.1" %s', "200")
        logged = " ".join(str(value) for value in info.call_args.args)
        self.assertNotIn("secret", logged)
        self.assertIn("[redacted]", logged)
