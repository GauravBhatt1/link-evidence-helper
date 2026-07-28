"""Human-editable adapter schema and validation."""
from __future__ import annotations
import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlparse

QUALITY_PATTERNS = {"480p":[r"\\b480p\\b"],"720p":[r"\\b720p\\b",r"\\bHD\\b"],"1080p":[r"\\b1080p\\b",r"\\bFHD\\b","Full HD"],"2160p":[r"\\b2160p\\b",r"\\b4K\\b",r"\\bUHD\\b"]}

def adapter_id(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", result): raise ValueError("Adapter name must produce a 2–64 character ID")
    return result

def validate_adapter(data: dict[str, Any]) -> dict[str, Any]:
    ident = adapter_id(str(data.get("id") or data.get("name") or "")); domains = data.get("domains") or []
    if not isinstance(domains, list) or not domains: raise ValueError("Adapter needs at least one domain")
    for domain in domains:
        if not isinstance(domain, str) or not re.fullmatch(r"[a-z0-9.-]+", domain.lower()) or "." not in domain: raise ValueError("Invalid adapter domain")
    redirects = data.setdefault("redirects", {})
    redirects["max_hops"] = min(max(int(redirects.get("max_hops", 8)), 1), 10)
    for group in (data.get("search", {}), data.get("content_page", {})):
        for key, values in group.items():
                if key.endswith("selectors") and any(not isinstance(item, str) or len(item) > 200 or "<" in item for item in values): raise ValueError("Unsafe selector value")
    search = data.get("search", {})
    if search.get("mode") == "json_api":
        template = str(search.get("url_template") or "")
        if not template.startswith(("https://", "http://")) or "{query}" not in template:
            raise ValueError("JSON search requires a public URL template containing {query}")
        for key in ("json_results_path", "title_path", "link_path"):
            value = str(search.get(key) or "")
            if not value or not re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*", value):
                raise ValueError("JSON search paths may contain only dot-separated field names")
    data["id"] = ident; return data

def blank_adapter(name: str, main_url: str) -> dict[str, Any]:
    host = urlparse(main_url).hostname or ""
    return {"id":adapter_id(name),"name":name,"enabled":True,"domains":[host],"search":{"mode":"unknown","url_template":"","result_container_selectors":[],"title_selectors":[],"link_selectors":["a[href]"]},"content_page":{"quality_patterns":QUALITY_PATTERNS,"download_terms":["download","direct","instant","drive","server","mirror"],"ignore_terms":["trailer","sample","telegram","advertisement","privacy","contact"],"link_selectors":["a[href]"]},"redirects":{"max_hops":8,"use_head_first":True,"allow_get_html_fallback":True},"final_link_detection":{"host_markers":[],"content_type_prefixes":["video/","application/octet-stream"],"file_extensions":[".mp4",".mkv",".zip"]},"session":{"cookies_required":False,"referer_required":False,"csrf_token_required":False,"javascript_required":False}}
