"""Parse Title 40 CFR citations written the way people write them, and build eCFR links.

Accepted: '40 CFR 63.1340', '40 C.F.R. § 60.62(a)(1)', '§ 98.2', '63.1340', '40 CFR part 63 subpart LLL',
'part 60 subpart Dc', 'subpart ZZZZ of part 63', 'NSPS subpart IIII', 'NESHAP LLL', 'MACT DDDDD',
'GHGRP subpart H', '63-LLL' (rule-pack id), '40 CFR part 98'.
"""

from __future__ import annotations

import re
from typing import Dict, Optional

from .client import UsairError

ECFR_HUMAN = "https://www.ecfr.gov"

_TITLE = re.compile(r"\b(\d{1,2})\s*C\.?\s*F\.?\s*R\.?", re.I)
_SECTION = re.compile(r"(?<![\d.])(\d{1,3})\.(\d{1,5}[a-z]?(?:-\d{1,3})?)((?:\s*\([A-Za-z0-9]{1,4}\))*)")
_PART = re.compile(r"\bpart\s+(\d{1,3})\b", re.I)
_SUBPART = re.compile(r"\bsubpart\s+([A-Za-z]{1,7})\b", re.I)
_PACK_ID = re.compile(r"^\s*(\d{1,3})\s*-\s*([A-Za-z]{1,7})\s*$")
_PROGRAM = re.compile(r"\b(NSPS|NESHAPS?|MACT|GACT|GHGRP|EG)\b\s*(?:subpart\s+)?([A-Za-z]{1,7})?\b", re.I)
_PROGRAM_PART = {"NSPS": "60", "EG": "60", "NESHAP": "63", "NESHAPS": "63", "MACT": "63", "GACT": "63", "GHGRP": "98"}
_BARE_PART = re.compile(r"^\s*(?:40\s*C\.?F\.?R\.?\s*)?(?:part\s+)?(\d{1,3})\s*$", re.I)


def norm_subpart(raw: str) -> str:
    """'lll' -> 'LLL', 'dc' -> 'Dc', 'ooooa' -> 'OOOOa', 'Kb' stays 'Kb'."""
    s = raw.strip()
    if not s:
        return s
    if s != s.lower() and s != s.upper():
        return s  # mixed case is already deliberate
    low = s.lower()
    if re.fullmatch(r"([a-z])\1*", low):
        return low.upper()
    m = re.fullmatch(r"(([a-z])\2*)([a-f])", low)
    if m and m.group(3) != m.group(2):
        return m.group(1).upper() + m.group(3)
    return s.upper()


def parse_citation(text: str) -> Dict[str, Optional[str]]:
    """Return {title, part, subpart, section, paragraph}. Raises UsairError on anything outside Title 40."""
    raw = (text or "").strip()
    if not raw:
        raise UsairError("bad_argument", "No citation given.",
                         "Pass e.g. '40 CFR 63.1340' or '40 CFR part 60 subpart F'.")
    out: Dict[str, Optional[str]] = {"title": "40", "part": None, "subpart": None, "section": None, "paragraph": None}
    t = _TITLE.search(raw)
    if t and t.group(1) != "40":
        raise UsairError("not_covered", f"Title {t.group(1)} CFR is not covered; this plugin reads Title 40 (environment) only.",
                         "Use a Title 40 citation, or open https://www.ecfr.gov for other titles.")
    work = raw.replace("§", " ").replace("Sec.", " ").replace("sec.", " ")
    pack = _PACK_ID.match(work)
    if pack:
        out["part"], out["subpart"] = pack.group(1), norm_subpart(pack.group(2))
        return out
    work_no_title = _TITLE.sub(" ", work)
    sec = _SECTION.search(work_no_title)
    if sec:
        out["part"] = sec.group(1)
        out["section"] = f"{sec.group(1)}.{sec.group(2)}"
        para = re.sub(r"\s+", "", sec.group(3) or "")
        out["paragraph"] = para or None
        return out
    part = _PART.search(work_no_title)
    sub = _SUBPART.search(work_no_title)
    prog = _PROGRAM.search(work_no_title)
    if part:
        out["part"] = part.group(1)
    elif prog:
        out["part"] = _PROGRAM_PART[prog.group(1).upper()]
    else:
        bare = _BARE_PART.match(work)
        if bare:
            out["part"] = bare.group(1)
    if sub:
        out["subpart"] = norm_subpart(sub.group(1))
    elif prog and prog.group(2) and prog.group(2).lower() not in ("part", "subpart"):
        out["subpart"] = norm_subpart(prog.group(2))
    if not out["part"]:
        raise UsairError("bad_argument", f"Could not read a Title 40 part or section from {raw!r}.",
                         "Write it like '40 CFR 60.4200', '40 CFR part 63 subpart ZZZZ' or 'NSPS subpart IIII'.")
    return out


def label(c: Dict[str, Optional[str]]) -> str:
    if c.get("section"):
        return f"40 CFR {c['section']}{c.get('paragraph') or ''}"
    if c.get("subpart"):
        return f"40 CFR part {c['part']} subpart {c['subpart']}"
    return f"40 CFR part {c['part']}"


def ecfr_url(c: Dict[str, Optional[str]], date: Optional[str] = None) -> str:
    base = f"{ECFR_HUMAN}/on/{date}/title-40" if date else f"{ECFR_HUMAN}/current/title-40"
    if c.get("section"):
        return f"{base}/section-{c['section']}"
    if c.get("subpart"):
        return f"{base}/part-{c['part']}/subpart-{c['subpart']}"
    return f"{base}/part-{c['part']}"
