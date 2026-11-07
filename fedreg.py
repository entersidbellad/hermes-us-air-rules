"""Federal Register (www.federalregister.gov API v1): recent EPA documents that affect a Title 40 part."""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Dict, List, Optional

from .client import Cached, Store, UsairError, build_url, parse_json

HOST = "www.federalregister.gov"
DOCS_PATH = "/api/v1/documents.json"
TTL_S = 6 * 3600
EPA_SLUG = "environmental-protection-agency"

TYPES = {"rule": "RULE", "final": "RULE", "proposed": "PRORULE", "proposed_rule": "PRORULE", "prorule": "PRORULE",
         "notice": "NOTICE"}
FIELDS = ["title", "document_number", "type", "action", "publication_date", "effective_on", "comments_close_on",
          "html_url", "citation", "docket_ids", "regulation_id_numbers", "cfr_references", "abstract"]

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_REL = re.compile(r"^(\d{1,4})\s*([dmy])$", re.I)


def resolve_since(since: Any, today: Optional[_dt.date] = None) -> str:
    today = today or _dt.date.today()
    if since in (None, ""):
        return (today - _dt.timedelta(days=365)).isoformat()
    s = str(since).strip()
    if _DATE.match(s):
        return s
    m = _REL.match(s)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        days = n * {"d": 1, "m": 30, "y": 365}[unit]
        return (today - _dt.timedelta(days=days)).isoformat()
    raise UsairError("bad_argument", f"'{since}' is not a date.", "Use YYYY-MM-DD or a relative window like '90d', '6m', '2y'.")


def resolve_types(types: Any) -> List[str]:
    if types in (None, "", []):
        return ["RULE", "PRORULE"]
    if isinstance(types, str):
        types = [t for t in re.split(r"[,\s]+", types) if t]
    out = []
    for t in types:
        key = str(t).strip().lower()
        code = TYPES.get(key) or (key.upper() if key.upper() in ("RULE", "PRORULE", "NOTICE") else None)
        if not code:
            raise UsairError("bad_argument", f"Unknown document type '{t}'.", "Use rule, proposed or notice.")
        if code not in out:
            out.append(code)
    return out


def _trim(text: Optional[str], n: int = 420) -> str:
    text = " ".join((text or "").split())
    if len(text) <= n:
        return text
    cut = text.rfind(" ", 0, n)
    return text[: cut if cut > 0 else n] + " …"


def normalize(doc: Dict[str, Any]) -> Dict[str, Any]:
    parts = sorted({str(r.get("part")) for r in doc.get("cfr_references") or []
                    if r.get("title") in (40, "40") and r.get("part")}, key=lambda p: (len(p), p))
    return {
        "document_number": doc.get("document_number"),
        "type": doc.get("type"),
        "title": doc.get("title"),
        "action": doc.get("action"),
        "publication_date": doc.get("publication_date"),
        "effective_on": doc.get("effective_on"),
        "comments_close_on": doc.get("comments_close_on"),
        "fr_citation": doc.get("citation"),
        "cfr_parts": parts,
        "docket_ids": doc.get("docket_ids") or [],
        "rin": doc.get("regulation_id_numbers") or [],
        "url": doc.get("html_url"),
        "abstract": _trim(doc.get("abstract")),
    }


class FedReg:
    def __init__(self, store: Store):
        self.store = store

    def documents(self, part: str, term: Optional[str], since: str, types: List[str], per_page: int) -> Cached:
        params: List[tuple] = [
            ("conditions[agencies][]", EPA_SLUG),
            ("conditions[cfr][title]", "40"),
            ("conditions[cfr][part]", str(part)),
            ("conditions[publication_date][gte]", since),
            ("order", "newest"),
            ("per_page", str(per_page)),
        ]
        if term:
            params.append(("conditions[term]", term))
        params += [("conditions[type][]", t) for t in types]
        params += [("fields[]", f) for f in FIELDS]
        url = build_url(HOST, DOCS_PATH, params)
        key = f"fr-{part}-{term or ''}-{since}-{'-'.join(types)}-{per_page}"
        return self.store.fetch(key, url, parse_json, ttl_s=TTL_S)
