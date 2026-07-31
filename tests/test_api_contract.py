import json
import unittest
from http import HTTPStatus

import web_app


class ResponseHandler:
    def __init__(self):
        self.status = None
        self.headers = {}
        self.body = b""
        self.wfile = self

    def send_response(self, status):
        self.status = int(status)

    def send_header(self, name, value):
        self.headers[name.lower()] = value

    def end_headers(self):
        pass

    def write(self, data):
        self.body += data


class ApiContractTests(unittest.TestCase):
    def payload(self, status, value):
        handler = ResponseHandler()
        web_app.response(handler, status, value)
        return handler.status, json.loads(handler.body)

    def test_success_payload_has_a_consistent_envelope(self):
        status, body = self.payload(HTTPStatus.OK, {"items": []})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertTrue(body["success"])
        self.assertEqual(body["code"], "ok")

    def test_application_failure_never_looks_successful(self):
        status, body = self.payload(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Name required"})
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])
        self.assertFalse(body["success"])
        self.assertEqual(body["error"], "Name required")
        self.assertEqual(body["code"], "request_failed")

    def test_failure_gets_a_safe_default_message(self):
        _, body = self.payload(HTTPStatus.BAD_GATEWAY, {"ok": False})
        self.assertEqual(body["error"], "Request failed")


if __name__ == "__main__":
    unittest.main()
