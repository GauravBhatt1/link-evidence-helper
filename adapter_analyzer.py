"""Automatic, bounded adapter discovery using public server-rendered HTML."""
from __future__ import annotations
import re
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote_plus, urljoin, urlparse
from adapter_models import blank_adapter
from network_safety import SafeSession, redact_url, validate_public_url

DOWNLOAD = ("download", "direct", "instant", "drive", "server", "mirror", "file")
IGNORE = ("trailer", "sample", "telegram", "advertisement", "privacy", "contact", "login")
QUALITY = ("480p", "720p", "1080p", "2160p", "4k", "uhd", "fhd")


def _looks_like_javascript_search(html: str) -> bool:
    """Return True for a search shell whose results are fetched client-side.

    This is detection only.  The adapter maker deliberately does not execute
    site JavaScript or call undocumented search backends.
    """
    lowered = html.lower()
    return ("fetch(" in lowered or "xmlhttprequest" in lowered or "axios." in lowered) and any(
        marker in lowered for marker in ("results-grid", "search results", "documents/search", "graphql")
    )


def _matching_search_results(html: str, page_url: str, query: str) -> list[Element]:
    """Find server-rendered result links that actually match the submitted query."""
    doc = PageParser(page_url)
    doc.feed(html)
    terms = [term for term in re.sub(r"[^a-z0-9]+", " ", query.lower()).split() if term]
    return [
        item for item in doc.elements
        if item.href and item.tag == "a" and item.text
        and (not terms or all(term in (item.text + " " + item.href).lower() for term in terms))
    ]

@dataclass
class Element:
    tag: str; attrs: dict[str,str]; text: str; href: str; section: str

class PageParser(HTMLParser):
    def __init__(self, base: str):
        super().__init__(convert_charrefs=True); self.base=base; self.elements=[]; self._stack=[]; self._heading=[]; self.section=""; self.title=""; self.forms=[]
    def handle_starttag(self, tag, attrs):
        data={k:(v or "") for k,v in attrs}; item={"tag":tag,"attrs":data,"text":[]}; self._stack.append(item)
        if tag == "form": self.forms.append(data)
        if tag in {"h1","h2","h3","h4","h5","h6","title"}: self._heading.append(item)
    def handle_data(self, data):
        for item in self._stack: item["text"].append(data)
    def handle_endtag(self, tag):
        if not self._stack: return
        item=self._stack.pop(); text=re.sub(r"\s+", " ", "".join(item["text"])).strip()
        if tag in {"h1","h2","h3","h4","h5","h6"} and text: self.section=text
        if tag == "title" and text: self.title=text
        if tag in {"a","button","input"}:
            href=item["attrs"].get("href", "") or item["attrs"].get("data-href", "")
            self.elements.append(Element(tag,item["attrs"],text,urljoin(self.base,href) if href else "",self.section))

def _selector(element: Element) -> list[str]:
    values=[]; cls=element.attrs.get("class","").split(); ident=element.attrs.get("id","")
    if ident: values.append(f"#{ident}")
    values += [f".{value}" for value in cls if re.fullmatch(r"[A-Za-z_-][\w-]*", value)]
    if cls: values.append(element.tag+"."+cls[0])
    values.append(element.tag); return values

def _score(element: Element, quality: str = "") -> tuple[int,list[str]]:
    text=" ".join([element.text,element.href,element.section," ".join(element.attrs.values())]).lower(); score=0; why=[]
    if quality and quality.lower() in text: score+=5; why.append("requested quality")
    if any(term in text for term in DOWNLOAD): score+=4; why.append("download-related text")
    if any(term in element.href.lower() for term in ("download","file","server","drive")): score+=3; why.append("download-like URL")
    if re.search(r"\.(mp4|mkv|zip)(?:$|[?#])", element.href.lower()): score+=2; why.append("file extension")
    if any(term in text for term in IGNORE): score-=5; why.append("ignored term")
    return score,why

def _candidate_report(elements: list[Element], quality: str, session: SafeSession) -> list[dict[str,Any]]:
    rows=[]
    for item in elements:
        if not item.href.startswith(("http://","https://")): continue
        score, reasons = _score(item,quality)
        if score < 0: rows.append({"url":redact_url(item.href),"score":score,"decision":"rejected","reasons":reasons}); continue
        if score < 3: continue
        chain=session.redirect_chain(item.href, 8); final=chain[-1] if chain else None
        ctype=(final.content_type if final else "").lower(); length=(final.content_length if final else "")
        if ctype.startswith(("video/","application/octet-stream")): score+=3; reasons.append("file content type")
        if length.isdigit() and int(length)>10_000_000: score+=2; reasons.append("large content length")
        rows.append({"url":redact_url(item.href),"text":item.text,"section":item.section,"score":score,"decision":"accepted" if score>=4 else "weak","reasons":reasons,"chain":[asdict(hop) for hop in chain]})
    return sorted(rows,key=lambda row: row["score"],reverse=True)[:12]

def analyze(payload: dict[str,Any]) -> dict[str,Any]:
    name=str(payload.get("siteName") or "New Site").strip(); main=validate_public_url(str(payload.get("mainSiteUrl") or "")); example=validate_public_url(str(payload.get("examplePageUrl") or "")); query=str(payload.get("searchQuery") or "example").strip(); quality=str(payload.get("expectedQuality") or "").strip(); expected=str(payload.get("exampleFinalUrl") or "").strip()
    session=SafeSession(); main_html, main_meta=session.fetch_html(main); example_html, example_meta=session.fetch_html(example,main)
    if main_meta.error: raise ValueError(f"Main page fetch failed: {main_meta.error}")
    if example_meta.error: raise ValueError(f"Example page fetch failed: {example_meta.error}")
    main_doc, page_doc=PageParser(main),PageParser(example); main_doc.feed(main_html); page_doc.feed(example_html)
    adapter=blank_adapter(name,main); adapter["session"]["cookies_required"]=bool(session.cookies)
    # Keep enough non-secret context for a later retest and for the Saved
    # Adapters screen.  This is not part of the runtime matching logic.
    adapter["maker"] = {"example_page_url": example, "search_query": query, "expected_quality": quality}
    forms=[form for form in main_doc.forms if any(key in form for key in ("action","method"))]
    search=""; search_html=""; search_url=""; search_meta=None
    if forms:
        action=urljoin(main,forms[0].get("action") or "/"); search=action+("&" if "?" in action else "?")+"s={query}"; adapter["search"]["mode"]="form_query"; adapter["search"]["url_template"]=search
        search_url=search.replace("{query}", quote_plus(query)); search_html, search_meta=session.fetch_html(search_url, main)
    else:
        for template in ("?s={query}","?q={query}","/search/{query}","/search?q={query}"):
            trial=urljoin(main,template.replace("{query}",quote_plus(query))); html,meta=session.fetch_html(trial,main)
            if not meta.error and len(html)>100: search=urljoin(main,template); adapter["search"]["mode"]="query_url"; adapter["search"]["url_template"]=search; break
        if search:
            search_url=search.replace("{query}", quote_plus(query)); search_html, search_meta=session.fetch_html(search_url, main)
    links=[item for item in page_doc.elements if item.href]
    selector_scores=[]
    for selector in sorted({candidate for item in links for candidate in _selector(item)}):
        matches=[item for item in links if selector == item.tag or selector in _selector(item)]
        if not matches: continue
        unique=len({item.href for item in matches}); confidence=min(100, int(20+min(len(matches),8)*8+(unique/len(matches))*25))
        selector_scores.append({"selector":selector,"matches":len(matches),"title_found":any(item.text for item in matches),"url_found":any(item.href for item in matches),"duplicate_percentage":round(100*(1-unique/len(matches))),"confidence":confidence})
    selector_scores.sort(key=lambda item:item["confidence"],reverse=True)
    best=[item["selector"] for item in selector_scores[:4]]
    adapter["search"]["result_container_selectors"]=best; adapter["search"]["title_selectors"]=best; adapter["search"]["link_selectors"]=["a[href]"]+best
    candidates=_candidate_report(links,quality,session)
    texts=" ".join(item.text for item in page_doc.elements).lower()
    search_results = _matching_search_results(search_html, search_url, query) if search_html else []
    search_js_required = bool(search_html) and not search_results and _looks_like_javascript_search(search_html)
    js_required=(bool(re.search(r"enable javascript|__next_data__|challenge-platform",example_html,re.I)) and not bool(links)) or search_js_required
    adapter["session"].update({"csrf_token_required":bool(re.search(r"csrf|_token",example_html,re.I)),"javascript_required":js_required})
    hosts=sorted({urlparse(row["chain"][-1]["url"]).hostname for row in candidates if row.get("chain") and urlparse(row["chain"][-1]["url"]).hostname})
    adapter["final_link_detection"]["host_markers"]=hosts[:5]
    quality_detected=any(term in texts for term in QUALITY)
    download_detected=bool(candidates)
    redirects_working=any(bool(row.get("chain")) for row in candidates)
    final_detected=bool(hosts)
    # A high heuristic score alone must never make an adapter saveable.  The
    # user-facing readiness decision is intentionally conservative.
    checks={
        "main_site_reachable": True,
        "search_working": bool(search_results),
        "example_page_valid": True,
        "quality_detected": quality_detected,
        "download_button_detected": download_detected,
        "redirect_chain_working": redirects_working,
        "final_link_detected": final_detected,
    }
    ready=all(checks.values()) and not js_required
    adapter["maker"].update({
        "ready_to_save": ready,
        "last_analysis_summary": (
            "Ready: search, page and final-link checks passed."
            if ready else
            "Not ready: the page/search may work, but the final-link flow was not verified."
        ),
    })
    confidence=min(100, 20+sum(11 for value in checks.values() if value)+(3 if not js_required else 0))
    report={"main_page_fetched":True,"example_page_fetched":True,"search_form_detected":bool(forms),"search_test_successful":bool(search_results),"result_selector_detected":bool(best),"quality_section_detected":quality_detected,"download_candidates_found":len(candidates),"redirect_chains_inspected":sum(bool(row.get("chain")) for row in candidates),"probable_final_host_detected":final_detected,"javascript_required":js_required,"cookie_session_required":bool(session.cookies),"adapter_confidence":confidence,"status":"Manual browser adapter required" if search_js_required else ("Ready to save" if ready else "Not ready"),"ready_to_save":ready,"simple_checks":checks}
    return {"adapter":adapter,"report":report,"selector_candidates":selector_scores[:12],"debug":{"main":asdict(main_meta),"example":asdict(example_meta),"search":asdict(search_meta) if search_meta else {},"search_result_count":len(search_results),"search_requires_javascript":search_js_required,"page_title":page_doc.title,"headings":list(dict.fromkeys(item.section for item in page_doc.elements if item.section))[:20],"forms":forms,"candidates":candidates,"expected_final_url_supplied":bool(expected)}}
