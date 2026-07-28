"""Optional, local-only Ollama assistance for source-adapter diagnosis.

This module deliberately produces advice only.  It never fetches a page, uses
credentials, saves source configuration, or treats a model-generated URL as a
delivery link.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
MAX_HTML_CHARS = 12_000


class LocalAIError(RuntimeError):
    """A user-safe error from the local Ollama helper."""


def _visible_html_excerpt(sample_html: str) -> str:
    cleaned = re.sub(r"<(?:script|style|noscript)\b[^>]*>.*?</(?:script|style|noscript)>", "", sample_html, flags=re.I | re.S)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:MAX_HTML_CHARS]


def review_source_html(sample_html: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Ask a local model for diagnostic advice about supplied HTML only."""
    excerpt = _visible_html_excerpt(sample_html)
    if not excerpt:
        raise LocalAIError("Paste HTML before requesting a local AI review.")
    model = os.environ.get("LINK_FINDER_AI_MODEL", "gemma4:e2b").strip() or "gemma4:e2b"
    current = config if isinstance(config, dict) else {}
    prompt = (
        "You are a cautious Python web-scraping assistant. Review only the supplied HTML and selector configuration. "
        "Do not invent URLs, claim a download/direct link exists, suggest bypassing access controls, or expose credentials. "
        "Keep the complete response under 140 words. Return JSON only with keys: summary (string), pageType (string), likelyIssues (array of strings), "
        "suggestedSelectors (object using only resultContainers,title,link,thumbnail,quality,fileSize,provider,pagination,waitFor; "
        "each value an array of CSS selectors found in the supplied HTML), and nextStep (string). "
        "Suggestions are advisory and must be validated by deterministic parsing.\n\n"
        f"CURRENT CONFIG:\n{json.dumps(current, ensure_ascii=False)[:4000]}\n\n"
        f"HTML EXCERPT:\n{excerpt}"
    )
    # Gemma 4 uses part of its token budget for hidden reasoning unless this is
    # disabled.  This admin helper needs a short, visible diagnostic instead.
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False, "think": False, "options": {"temperature": 0.1, "num_predict": 180}}).encode("utf-8")
    request = Request(OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=120) as response:
            body = json.loads(response.read().decode("utf-8"))
    except TimeoutError as exc:
        raise LocalAIError("Local AI review timed out. Try a smaller HTML sample; this CPU-only model responds slowly.") from exc
    except (URLError, OSError) as exc:
        raise LocalAIError("Local AI is unavailable. Ensure Ollama is running and the configured model is installed.") from exc
    answer = str(body.get("response") or "").strip()
    if not answer:
        raise LocalAIError("Local AI returned no review.")
    try:
        parsed = json.loads(answer.removeprefix("```json").removesuffix("```").strip())
    except json.JSONDecodeError:
        parsed = {"summary": answer[:5000], "pageType": "unknown", "likelyIssues": [], "suggestedSelectors": {}, "nextStep": "Review the response, then validate with the built-in preview."}
    if not isinstance(parsed, dict):
        raise LocalAIError("Local AI returned an invalid review.")
    allowed = {"resultContainers", "title", "link", "thumbnail", "quality", "fileSize", "provider", "pagination", "waitFor"}
    suggested = parsed.get("suggestedSelectors")
    if isinstance(suggested, dict):
        parsed["suggestedSelectors"] = {
            key: [str(item)[:160] for item in value if isinstance(item, str)][:5]
            for key, value in suggested.items() if key in allowed and isinstance(value, list)
        }
    else:
        parsed["suggestedSelectors"] = {}
    parsed["model"] = model
    parsed["advisory"] = True
    return parsed
