"""Detection-only helpers for visible CAPTCHA and login blockers."""
from __future__ import annotations

import re


def visible_document_text(html: str) -> str:
    """Remove inactive markup that must not block a public workflow.

    This is deliberately conservative: real visible challenge widgets remain,
    while comments, scripts, styles, templates, ``hidden`` nodes, ARIA-hidden
    nodes, and inline ``display:none``/``visibility:hidden`` fragments do not.
    """
    value = html or ""
    value = re.sub(r"<!--.*?-->", "", value, flags=re.S)
    value = re.sub(r"<(?:script|style|template)\b[^>]*>.*?</(?:script|style|template)\s*>", "", value, flags=re.I | re.S)
    hidden = r"<(\w+)\b[^>]*(?:\bhidden\b|aria-hidden\s*=\s*['\"]?true|style\s*=\s*['\"][^'\"]*(?:display\s*:\s*none|visibility\s*:\s*hidden)[^'\"]*)[^>]*>.*?</\1\s*>"
    previous = None
    while previous != value:
        previous, value = value, re.sub(hidden, "", value, flags=re.I | re.S)
    return value
