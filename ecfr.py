"""eCFR (www.ecfr.gov): Title 40 currency date, full text of a section/subpart/part, and search.

Full text comes from the versioner API, which needs a date and gzip. The date defaults to the
"up to date as of" day eCFR reports for Title 40, so a text fetched for one date never changes and
is cached without expiry. Search results are deduplicated to the version in force on that date.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from .client import Cached, Store, UsairError, build_url, parse_json

HOST = "www.ecfr.gov"
TITLES_PATH = "/api/versioner/v1/titles.json"
FULL_PATH = "/api/versioner/v1/full/{date}/title-40.xml"
SEARCH_PATH = "/api/search/v1/results"
STRUCTURE_PATH = "/api/versioner/v1/structure/{date}/title-40.json"
TITLES_TTL_S = 6 * 3600
SEARCH_TTL_S = 24 * 3600

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_BLOCKS = {"P", "FP", "HD1", "HD2", "HD3", "NOTE", "EXTRACT", "CITA", "AUTH", "SOURCE", "EDNOTE", "FTNT", "SECAUTH"}
_TAG = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def strip_html(text: str) -> str:
    return _clean(html.unescape(_TAG.sub("", text or "")))


def _table_text(table: ET.Element) -> List[str]:
    rows = []
    for row in table.iter():
        if row.tag in ("ROW", "BOXHD", "tr"):
            cells = [_clean("".join(c.itertext())) for c in row if c.tag in ("ENT", "CHED", "td", "th")]
            cells = [c for c in cells if c]
            if cells:
                rows.append(" | ".join(cells))
    return rows


def _block_lines(div: ET.Element) -> List[str]:
    lines: List[str] = []
    for child in div:
        tag = child.tag
        if tag == "HEAD" or tag.startswith("DIV"):
            continue
        if tag in ("GPOTABLE", "TABLE", "table"):
            lines.extend(_table_text(child))
        elif tag in _BLOCKS or tag.isupper():
            text = _clean("".join(child.itertext()))
            if text:
                lines.append(text)
    return lines


def parse_full_xml(body: bytes) -> Dict[str, Any]:
    """{'heading', 'type', 'sections': [{'section', 'heading', 'text', 'amended'}]} for DIV8/DIV9 units."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError as err:
        raise UsairError("bad_response", f"eCFR sent XML that does not parse: {err}",
                         "Retry once; if it repeats, open the eCFR link instead.") from err
    sections: List[Dict[str, str]] = []
    for div in root.iter():
        if div.tag not in ("DIV8", "DIV9"):
            continue
        lines = _block_lines(div)
        cita = next((l for l in lines if l.startswith("[") and " FR " in l), "")
        body_lines = [l for l in lines if l != cita]
        sections.append({
            "section": div.attrib.get("N", ""),
            "type": div.attrib.get("TYPE", "").lower(),
            "heading": _clean(div.findtext("HEAD") or ""),
            "text": "\n".join(body_lines),
            "amended": cita,
        })
    return {"heading": _clean(root.findtext("HEAD") or ""), "type": root.attrib.get("TYPE", "").lower(),
            "number": root.attrib.get("N", ""), "sections": sections}


class Ecfr:
    def __init__(self, store: Store):
        self.store = store

    def title40_status(self) -> Cached:
        url = build_url(HOST, TITLES_PATH)

        def parse(body: bytes) -> Dict[str, Any]:
            data = parse_json(body)
            for t in data.get("titles") or []:
                if t.get("number") == 40:
                    return {k: t.get(k) for k in ("up_to_date_as_of", "latest_amended_on", "latest_issue_date")}
            raise UsairError("bad_response", "eCFR's title list has no Title 40 entry.")

        return self.store.fetch("ecfr-titles", url, parse, ttl_s=TITLES_TTL_S)

    def resolve_date(self, date: Optional[str]) -> str:
        if date:
            date = str(date).strip()
            if not _DATE.match(date):
                raise UsairError("bad_argument", f"'{date}' is not a date.", "Use YYYY-MM-DD, or leave date out for current text.")
            return date
        status = self.title40_status().data
        current = status.get("up_to_date_as_of") or status.get("latest_issue_date")
        if not current:
            raise UsairError("bad_response", "eCFR did not report a current date for Title 40.")
        return current

    def full(self, date: str, part: str, subpart: Optional[str] = None, section: Optional[str] = None) -> Cached:
        params: List[tuple] = [("part", part)]
        if section:
            params = [("section", section)]
        elif subpart:
            params.append(("subpart", subpart))
        url = build_url(HOST, FULL_PATH.format(date=date), params)
        key = f"ecfr-full-{date}-{part}-{subpart or ''}-{section or ''}"
        return self.store.fetch(key, url, parse_full_xml, ttl_s=-1)

    def structure(self, date: str, part: str) -> Cached:
        url = build_url(HOST, STRUCTURE_PATH.format(date=date), [("part", part)])
        return self.store.fetch(f"ecfr-structure-{date}-{part}", url,
                                lambda body: parse_structure(parse_json(body), part), ttl_s=-1)

    def search(self, query: str, part: Optional[str] = None, subpart: Optional[str] = None,
               per_page: int = 20, date: Optional[str] = None) -> Cached:
        params: List[tuple] = [("query", query), ("hierarchy[title]", "40"), ("per_page", str(per_page))]
        if part:
            params.append(("hierarchy[part]", part))
        if subpart:
            params.append(("hierarchy[subpart]", subpart))
        url = build_url(HOST, SEARCH_PATH, params)
        key = f"ecfr-search-{query}-{part or ''}-{subpart or ''}-{per_page}"
        return self.store.fetch(key, url, parse_json, ttl_s=SEARCH_TTL_S)


def parse_structure(tree: Dict[str, Any], part: str) -> Dict[str, Any]:
    """{'part', 'heading', 'subparts': [{'subpart', 'heading', 'reserved', 'sections'}]} for one part."""
    node = None
    stack = [tree]
    while stack:
        n = stack.pop()
        if n.get("type") == "part" and str(n.get("identifier")) == str(part):
            node = n
            break
        stack.extend(n.get("children") or [])
    if node is None:
        raise UsairError("not_found", f"Title 40 has no part {part}.", "Check the part number.")
    subparts = []
    loose = 0
    for child in node.get("children") or []:
        if child.get("type") == "subpart":
            count = 0
            todo = list(child.get("children") or [])
            while todo:
                c = todo.pop()
                if c.get("type") == "section":
                    count += 1
                todo.extend(c.get("children") or [])
            subparts.append({"subpart": child.get("identifier"),
                             "heading": _clean(child.get("label_description") or ""),
                             "reserved": bool(child.get("reserved")), "sections": count})
        elif child.get("type") == "section":
            loose += 1
    return {"part": str(part), "heading": _clean(node.get("label_description") or ""),
            "subparts": subparts, "sections_outside_subparts": loose}


def current_hits(results: List[Dict[str, Any]], on_date: str) -> List[Dict[str, Any]]:
    """Keep one hit per section: the version in force on ``on_date`` (newest start date wins)."""
    best: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for r in results or []:
        if r.get("removed") or r.get("reserved"):
            continue
        h = r.get("hierarchy") or {}
        key = h.get("section") or h.get("appendix") or h.get("subpart") or ""
        starts, ends = r.get("starts_on") or "", r.get("ends_on")
        if starts and starts > on_date:
            continue
        if ends and ends < on_date:
            continue
        if key not in best:
            order.append(key)
            best[key] = r
        elif starts > (best[key].get("starts_on") or ""):
            best[key] = r
    out = []
    for key in order:
        r = best[key]
        h = r.get("hierarchy") or {}
        heads = r.get("headings") or {}
        out.append({
            "section": h.get("section"),
            "part": h.get("part"),
            "subpart": h.get("subpart"),
            "appendix": h.get("appendix"),
            "heading": strip_html(heads.get("section") or heads.get("appendix") or ""),
            "subpart_heading": strip_html(heads.get("subpart") or ""),
            "excerpt": strip_html(r.get("full_text_excerpt") or "")[:600],
            "version_starts_on": r.get("starts_on"),
            "score": r.get("score"),
        })
    return out
