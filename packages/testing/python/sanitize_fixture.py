"""Deterministic fixture sanitization with no production-data access."""
from __future__ import annotations

from copy import deepcopy
from typing import Any
from urllib.parse import urlsplit, urlunsplit

FORBIDDEN_KEYS = {
    "authorization", "cookie", "cookies", "password", "apikey", "api_key",
    "token", "signedquery", "workflowmetadata", "selector", "selectors",
}


def _safe_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme in {"http", "https"}:
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    return value


def sanitize(value: Any) -> Any:
    """Remove internal/secret fields and signed query values recursively."""
    if isinstance(value, dict):
        clean = {}
        for key in sorted(value):
            normalized = key.replace("-", "").replace("_", "").lower()
            if normalized in FORBIDDEN_KEYS:
                continue
            clean[key] = sanitize(value[key])
        return clean
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return _safe_url(value)
    return deepcopy(value)
