from __future__ import annotations

import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
CADDYFILE = ROOT / "deploy" / "restructure" / "Caddyfile.preview"


class CaddyPreviewSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = CADDYFILE.read_text(encoding="utf-8")

    def test_binds_only_to_loopback_preview_port(self) -> None:
        self.assertIn("http://127.0.0.1:18880", self.config)
        self.assertNotIn(":8765", self.config)
        self.assertNotRegex(self.config, r"(?m)^https?://0\.0\.0\.0:")
        self.assertNotRegex(self.config, r"(?m)^https?://:\d+")

    def test_disables_stateful_or_remote_admin_features(self) -> None:
        self.assertRegex(self.config, r"(?m)^\s*admin off\s*$")
        self.assertRegex(self.config, r"(?m)^\s*persist_config off\s*$")
        self.assertRegex(self.config, r"(?m)^\s*auto_https off\s*$")

    def test_routes_only_to_internal_future_api(self) -> None:
        upstreams = re.findall(r"reverse_proxy\s+([^\s{]+)", self.config)
        self.assertTrue(upstreams)
        self.assertEqual({"api:8780"}, set(upstreams))
        forbidden = ("localhost", "127.0.0.1:8765", "host.docker.internal", "http://", "https://")
        for upstream in upstreams:
            self.assertFalse(any(value in upstream for value in forbidden), upstream)

    def test_has_bounded_proxy_timeouts_and_security_headers(self) -> None:
        self.assertIn("dial_timeout 3s", self.config)
        self.assertIn("response_header_timeout 30s", self.config)
        self.assertIn('X-Content-Type-Options "nosniff"', self.config)
        self.assertIn('Referrer-Policy "no-referrer"', self.config)
        self.assertIn("frame-ancestors 'none'", self.config)

    def test_contains_no_secret_interpolation_or_tls_material(self) -> None:
        self.assertNotIn("{$", self.config)
        self.assertNotRegex(self.config, r"(?i)(password|secret|token|api[_-]?key)")
        self.assertNotRegex(self.config, r"(?i)(certificate|private_key|tls\s+[^i])")


if __name__ == "__main__":
    unittest.main()
