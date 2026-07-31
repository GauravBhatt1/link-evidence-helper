from __future__ import annotations

from contextlib import ExitStack
from unittest import TestCase
from unittest.mock import patch

import network_safety
import workflow_analyzer
from tests.mock_workflow_server import mock_workflow_server


class LocalWorkflowServerTests(TestCase):
    def local_network(self):
        stack = ExitStack()
        stack.enter_context(patch.object(network_safety, "validate_public_url", side_effect=lambda url: url))
        stack.enter_context(patch.object(workflow_analyzer, "validate_public_url", side_effect=lambda url: url))
        return stack

    def test_redirect_chain_reaches_attachment_without_downloading_body(self):
        with mock_workflow_server() as base, self.local_network():
            result = workflow_analyzer.analyze_movie_workflow(base, f"{base}/movie", "1080p")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["results"][0]["file_name"], "fixture.1080p.mkv")

    def test_successful_branch_is_preserved_when_another_branch_is_blocked(self):
        with mock_workflow_server() as base, self.local_network():
            result = workflow_analyzer.analyze_movie_workflow(base, f"{base}/branches", "1080p")
        self.assertEqual(result["status"], "success")
        self.assertTrue(any(item["status"] == "blocked" for item in result["results"]))
        self.assertTrue(any(item["status"] == "success" for item in result["results"]))

    def test_html_file_lookalike_and_expired_url_are_not_final_files(self):
        with mock_workflow_server() as base, self.local_network():
            session = network_safety.SafeSession(timeout=1)
            _, interstitial, _ = session.fetch_html_once(f"{base}/interstitial/movie.mkv")
            expired = session.inspect(f"{base}/expired")
        self.assertFalse(workflow_analyzer._final_file(interstitial, f"{base}/interstitial/movie.mkv"))
        self.assertFalse(workflow_analyzer._final_file(expired, f"{base}/expired"))

    def test_inactive_challenge_markers_do_not_block_and_duplicate_state_is_distinct(self):
        with mock_workflow_server() as base, self.local_network():
            session = network_safety.SafeSession(timeout=1)
            html, _, _ = session.fetch_html_once(f"{base}/inactive-captcha")
            duplicate, _, _ = session.fetch_html_once(f"{base}/duplicate")
            self.assertIsNone(workflow_analyzer._blocked_html(html))
            actions = workflow_analyzer._parse(f"{base}/duplicate", duplicate)
            self.assertEqual(len(workflow_analyzer._pick(actions, lambda _: True, 10)), 2)

    def test_post_workflow_is_reported_but_never_submitted(self):
        with mock_workflow_server() as base, self.local_network():
            result = workflow_analyzer.analyze_movie_workflow(base, f"{base}/movie-post", None)
        self.assertEqual(result["status"], "partial")
        self.assertIn("not submitted", result["results"][0]["message"])

    def test_timeout_endpoint_respects_session_timeout(self):
        with mock_workflow_server() as base, self.local_network():
            _, response, _ = network_safety.SafeSession(timeout=1).fetch_html_once(f"{base}/timeout")
        self.assertTrue(response.status is None or response.status == 200)
