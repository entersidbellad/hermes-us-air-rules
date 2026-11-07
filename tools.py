"""Tool handlers. Every handler returns a JSON string and never raises.

A failure is {"ok": false, "error", "error_type", "next_step"}. A success carries source[]
and the disclaimer. Verdicts from the rule packs are applicability screening, not a reading
of the live text, unless a source entry says the text was fetched.
"""

from __future__ import annotations

import copy
import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Set

from .citations import ecfr_url, label, norm_subpart, parse_citation
from .client import Cached, Store, UsairError, build_url
from .compare import CITATION_NOTE, VERIFIED_MEANS, compare_screenings, render_compare, validate_unit_ids
from .ecfr import HOST as ECFR_HOST, SEARCH_PAGE_CAP, SEARCH_PER_PAGE, TITLES_PATH, Ecfr, current_hits
from .engine import run_pack
from .fedreg import FedReg, normalize, resolve_since, resolve_types
from .profiles import (
    fixture_names,
    load_fixture,
    load_pack,
    load_template,
    normalize_home,
    normalize_plant,
    resolve_profile,
    template_names,
)
from .sip import known_topics, queries_for, resolve_state, topic_key

logger = logging.getLogger(__name__)

DISCLAIMER = (
    "Screening aid, not a compliance determination and not legal advice. "
    "eCFR is an editorial compilation; the Federal Register and the Code of Federal Regulations "
    "published by the Government Publishing Office are the official versions. State and local rules, "
    "permits and consent decrees can be stricter and are not decided here. "
    "Confirm with environmental staff or counsel before relying on a verdict."
)
SIP_CAVEAT = (
    "40 CFR part 52 records the state rules EPA has approved into the State Implementation Plan. "
    "The state's own current rule can be newer, and city or county rules do not appear here."
)

_PARA = re.compile(r"^\(([A-Za-z0-9]{1,4})\)")


def _dump(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _error(kind: str, message: str, next_step: str = "", details: Optional[dict] = None) -> str:
    payload: Dict[str, Any] = {"ok": False, "error": message, "error_type": kind}
    for key, value in (details or {}).items():
        if value not in (None, "", [], {}):
            payload[key] = value
    if next_step:
        payload["next_step"] = next_step
    return _dump(payload)


def _int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, num))


def _flag(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y", "on")
    return bool(value)


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)) if ts else ""


def _cut(text: str, max_chars: int) -> Dict[str, Any]:
    if len(text) <= max_chars:
        return {"text": text, "truncated": False}
    cut = text.rfind("\n", max_chars // 2, max_chars)
    cut = cut if cut > 0 else max_chars
    return {"text": text[:cut].rstrip(), "truncated": True, "total_chars": len(text)}


def _pinpoint(text: str, paragraph: Optional[str]) -> Dict[str, Any]:
    """Cut a section down to one lettered or numbered block such as '(a)'. Nested markers stay."""
    if not paragraph:
        return {"text": text}
    top = _PARA.match(paragraph.strip())
    if not top:
        return {"text": text, "paragraph_note": f"Could not read {paragraph!r}; the whole section is returned."}
    marker = "(" + top.group(1) + ")"
    lines = text.split("\n")
    start = next((i for i, line in enumerate(lines) if line.startswith(marker)), None)
    if start is None:
        return {"text": text, "paragraph_note": f"{marker} is not a block in this section; the whole section is returned."}
    alpha = top.group(1).isalpha()
    end = len(lines)
    for j in range(start + 1, len(lines)):
        m = _PARA.match(lines[j])
        if m and m.group(1).isalpha() == alpha:
            end = j
            break
    out = {"text": "\n".join(lines[start:end]).strip(), "paragraph": marker}
    if paragraph.strip() != marker:
        out["paragraph_note"] = f"Returned {marker}. The nested pinpoint {paragraph.strip()} is inside it and was not cut further."
    return out


def _strings(value: Any) -> List[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, str):
        return [p.strip() for p in value.split(",") if p.strip()]
    if isinstance(value, list):
        return [str(p).strip() for p in value if str(p).strip()]
    return []


class Tools:
    def __init__(self, store: Optional[Store] = None):
        self.store = store or Store()
        self.ecfr = Ecfr(self.store)
        self.fr = FedReg(self.store)

    def _setting_int(self, key: str, default: int, lo: int, hi: int) -> int:
        return _int(self.store.setting(key, default), default, lo, hi)

    def _source(self, name: str, url: str, cached: Optional[Cached], **extra: Any) -> Dict[str, Any]:
        src: Dict[str, Any] = {"name": name, "url": url}
        if cached is not None:
            src["retrieved_at"] = _iso(cached.fetched_at)
            src["cache"] = cached.state
            if cached.state == "stale":
                src["stale"] = True
                src["note"] = "The source could not be reached; this is the last cached copy. Say so when you quote it."
        src.update({k: v for k, v in extra.items() if v not in (None, "", [], {})})
        return src

    def _finish(self, payload: Dict[str, Any], sources: List[Dict[str, Any]]) -> Dict[str, Any]:
        payload["ok"] = True
        payload["source"] = sources
        payload["disclaimer"] = DISCLAIMER
        if any(s.get("stale") for s in sources):
            payload["stale"] = True
        return payload

    # ------------------------------------------------------------- usair_cfr_get
    def cfr_get(self, args: Dict[str, Any]) -> Dict[str, Any]:
        citation = parse_citation(str(args.get("citation") or ""))
        date, title = self.ecfr.resolve_date(args.get("date"))
        max_chars = _int(args.get("max_chars"), 6000, 500, 30000)
        if citation.get("section"):
            payload, sources = self._cfr_section(citation, date, max_chars)
        elif citation.get("subpart"):
            payload, sources = self._cfr_subpart(citation, date, max_chars)
        else:
            payload, sources = self._cfr_part(citation, date)
        return self._with_title(payload, sources, title, date)

    def _cfr_section(self, citation: Dict[str, Optional[str]], date: str, max_chars: int) -> Dict[str, Any]:
        cached = self.ecfr.full(date, citation["part"], section=citation["section"])
        sections = cached.data.get("sections") or []
        hit = next((s for s in sections if s.get("section") == citation["section"]), None)
        if hit is None:
            raise UsairError("not_found", f"eCFR returned no section {citation['section']} in Title 40 on {date}.",
                             "Check the section number, or search with usair_cfr_search.")
        pinned = _pinpoint(hit.get("text") or "", citation.get("paragraph"))
        cut = _cut(pinned["text"], max_chars)
        payload: Dict[str, Any] = {
            "citation": label(citation),
            "heading": hit.get("heading") or "",
            "text": cut["text"],
            "truncated": cut["truncated"],
            "amended": hit.get("amended") or "",
            "ecfr_date": date,
            "ecfr_url": ecfr_url(citation, date),
        }
        if pinned.get("paragraph"):
            payload["paragraph"] = pinned["paragraph"]
        if pinned.get("paragraph_note"):
            payload["paragraph_note"] = pinned["paragraph_note"]
        if cut.get("total_chars"):
            payload["total_chars"] = cut["total_chars"]
        if cut["truncated"]:
            payload["next_step"] = "The section was cut. Call again with a larger max_chars (up to 30000)."
        return payload, [self._source(
            "eCFR (National Archives, Office of the Federal Register)", payload["ecfr_url"], cached,
            ecfr_date=date, official=False,
            attribution="Editorial compilation from www.ecfr.gov. Not the official legal edition.")]

    def _cfr_subpart(self, citation: Dict[str, Optional[str]], date: str, max_chars: int) -> Dict[str, Any]:
        cached = self.ecfr.full(date, citation["part"], subpart=citation["subpart"])
        sections = cached.data.get("sections") or []
        if not sections:
            raise UsairError("not_found", f"eCFR returned no text for {label(citation)} on {date}.",
                             "Check the subpart. Subpart letters are case-sensitive in the API (Dc, not DC).")
        blocks = []
        for s in sections:
            heading = s.get("heading") or s.get("section") or ""
            body = s.get("text") or ""
            blocks.append(f"{heading}\n{body}".strip() if body else heading)
        cut = _cut("\n\n".join(blocks), max_chars)
        payload = {
            "citation": label(citation),
            "heading": cached.data.get("heading") or "",
            "section_count": len(sections),
            "sections": [{"section": s.get("section"), "heading": s.get("heading")} for s in sections],
            "text": cut["text"],
            "truncated": cut["truncated"],
            "ecfr_date": date,
            "ecfr_url": ecfr_url(citation, date),
        }
        if cut["truncated"]:
            payload["next_step"] = "Text was cut. Read one section with usair_cfr_get and its full citation."
        return payload, [self._source(
            "eCFR (National Archives, Office of the Federal Register)", payload["ecfr_url"], cached,
            ecfr_date=date, official=False)]

    def _cfr_part(self, citation: Dict[str, Optional[str]], date: str) -> tuple:
        cached = self.ecfr.structure(date, citation["part"])
        data = cached.data
        payload = {
            "citation": label(citation),
            "heading": data.get("heading") or "",
            "subparts": data.get("subparts") or [],
            "sections_outside_subparts": data.get("sections_outside_subparts") or 0,
            "ecfr_date": date,
            "ecfr_url": ecfr_url(citation, date),
            "next_step": "Ask for a subpart or a section to read the text. A whole part is not downloaded.",
        }
        return payload, [self._source(
            "eCFR (National Archives, Office of the Federal Register)", payload["ecfr_url"], cached,
            ecfr_date=date, official=False)]

    # ---------------------------------------------------------- usair_cfr_search
    def cfr_search(self, args: Dict[str, Any]) -> Dict[str, Any]:
        query = str(args.get("query") or "").strip()
        if len(query) < 2:
            raise UsairError("bad_argument", "Give a search query of at least two characters.",
                             "Example: query='open clinker storage', part='63'.")
        part = _part_number(args.get("part"))
        subpart = norm_subpart(str(args["subpart"])) if args.get("subpart") else None
        if subpart and not part:
            raise UsairError("bad_argument", "A subpart needs a part number too.", "Pass part='63' with subpart='LLL'.")
        limit = _int(args.get("max_results"), 8, 1, 20)
        date, title = self.ecfr.resolve_date(None)
        hits, pages, exhausted = self._search_current(query, part, subpart, date, limit)
        shown = hits[:limit]
        for hit in shown:
            ref = {"part": hit.get("part"), "subpart": None, "section": hit.get("section"), "paragraph": None}
            hit["citation"] = label(ref) if hit.get("section") else label({"part": hit.get("part"), "subpart": hit.get("subpart"), "section": None, "paragraph": None})
            hit["url"] = ecfr_url(ref, date) if hit.get("section") else ecfr_url(
                {"part": hit.get("part"), "subpart": hit.get("subpart"), "section": None, "paragraph": None}, date)
        payload: Dict[str, Any] = {
            "query": query, "part": part, "subpart": subpart, "ecfr_date": date,
            "total_current": len(hits), "results": shown,
            "pages_fetched": len(pages), "search_complete": exhausted,
        }
        if not shown and not exhausted:
            payload["message"] = ("The search stopped before the result list was exhausted, so this is not "
                                  "a finding that no current section matches.")
        elif not shown:
            payload["message"] = "No current Title 40 section matched."
        else:
            payload["next_step"] = "Read one hit with usair_cfr_get before quoting it."
            if not exhausted:
                payload["search_note"] = ("Returned the current sections found within the page cap. "
                                          "Later pages were not read.")
        scope = {"part": part or "40", "subpart": subpart, "section": None, "paragraph": None}
        sources = [self._source("eCFR search (National Archives)", ecfr_url(scope, date), page, ecfr_date=date)
                   for page in pages]
        return self._with_title(payload, sources, title, date)

    # ----------------------------------------------------------- usair_fr_recent
    def fr_recent(self, args: Dict[str, Any]) -> Dict[str, Any]:
        part = _part_number(args.get("part"))
        if not part:
            raise UsairError("bad_argument", "Give a Title 40 part number.", "Example: part='63', term='\"portland cement manufacturing\"', since='2y'.")
        term = str(args.get("term") or "").strip() or None
        since = resolve_since(args.get("since"))
        types = resolve_types(args.get("types"))
        limit = _int(args.get("max_results"), 8, 1, 20)
        cached = self.fr.documents(part, term, since, types, limit)
        data = cached.data
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise UsairError("bad_response", "The Federal Register returned an unexpected document list.", "Retry once.")
        docs = [normalize(d) for d in data["results"]]
        payload: Dict[str, Any] = {
            "part": part, "term": term, "since": since, "types": types,
            "total": data.get("count"), "documents": docs,
            "note": "Full-text search matches loosely, and a proposed rule is not law. Read the abstract.",
        }
        if not docs:
            payload["message"] = "No EPA document in the Federal Register matched."
        else:
            payload["next_step"] = "Confirm current text with usair_cfr_get. Open html_url for the official document."
        return self._finish(payload, [self._source(
            "Federal Register (Office of the Federal Register, National Archives)",
            "https://www.federalregister.gov/", cached,
            query_url=cached.meta.get("url"), official=True)])

    # -------------------------------------------------------- usair_plant_triage
    def plant_triage(self, args: Dict[str, Any]) -> Dict[str, Any]:
        profile, fixture = resolve_profile(args, "plant")
        facility, units, warnings = normalize_plant(profile)
        pack = load_pack("plant")
        only = self._rule_filter(pack, _strings(args.get("rules")))
        ran = run_pack(pack, {"facility": facility}, units, only)
        findings = [self._finding(item) for item in ran["evaluated"]]
        fr_notes: List[str] = []
        fr_sources: List[Dict[str, Any]] = []
        if _flag(args.get("include_fr")):
            fr_notes = self._attach_fr(findings, fr_sources)
        counts: Dict[str, int] = {}
        for f in findings:
            counts[f["verdict"]] = counts.get(f["verdict"], 0) + 1
        unverified = [f["id"] for f in findings if not f["verified"]]
        payload: Dict[str, Any] = {
            "layer": "plant",
            "fixture": fixture,
            "facility": {"name": facility.get("name"), "state": facility.get("state"),
                         "naics": facility.get("naics"), "units": len(units)},
            "pack": {"name": pack["name"], "version": pack["version"]},
            "counts": counts,
            "findings": findings,
            "not_relevant": ran["not_relevant"],
            "warnings": warnings,
            "unverified_rules": unverified,
            "next_step": "Quote a rule only after usair_cfr_get on its citation. Recent proposals: usair_fr_recent.",
        }
        if fr_notes:
            payload["fr_notes"] = fr_notes
        sources = [self._pack_source(pack)]
        sources.extend(fr_sources)
        return self._finish(payload, sources)

    # ------------------------------------------------------- usair_plant_compare
    def plant_compare(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Rerun plant triage on two profiles and diff the structured result. No network."""
        if _flag(args.get("include_fr")):
            raise UsairError(
                "bad_argument",
                "plant compare does not fetch Federal Register documents.",
                "Run usair_fr_recent for a part, or usair_cfr_get for a section. Compare only reruns the bundled pack.",
            )
        rules = _strings(args.get("rules"))
        warnings: List[str] = []
        before_profile, before_fixture = self._compare_side(args, "before", warnings)
        after_profile, after_fixture = self._compare_side(args, "after", warnings)
        before_profile = copy.deepcopy(before_profile)
        after_profile = copy.deepcopy(after_profile)
        validate_unit_ids(before_profile, "before")
        validate_unit_ids(after_profile, "after")
        triage_args: Dict[str, Any] = {}
        if rules:
            triage_args["rules"] = rules
        before_run = self.plant_triage(dict(triage_args, profile=before_profile))
        after_run = self.plant_triage(dict(triage_args, profile=after_profile))
        pack = load_pack("plant")
        diff = compare_screenings(before_profile, after_profile, before_run, after_run, pack, rules or None)
        for side, run in (("before", before_run), ("after", after_run)):
            for warning in run.get("warnings") or []:
                warnings.append(f"{side}: {warning}")
        payload: Dict[str, Any] = {
            "layer": "plant",
            "offline": True,
            "live_fetch": False,
            "text_origin": "bundled_pack",
            "citation_note": CITATION_NOTE,
            "verified_means": VERIFIED_MEANS,
            "before": _compare_label(before_run, before_fixture),
            "after": _compare_label(after_run, after_fixture),
            "pack": before_run.get("pack"),
            "warnings": warnings,
        }
        payload.update(diff)
        source = self._pack_source(pack)
        source["text_origin"] = "bundled_pack"
        source["live_fetch"] = False
        finished = self._finish(payload, [source])
        finished["summary"] = render_compare(finished)
        return finished

    def _compare_side(self, args: Dict[str, Any], side: str, warnings: List[str]) -> tuple:
        profile = args.get(f"{side}_profile")
        fixture = args.get(f"{side}_fixture")
        if profile and fixture:
            warnings.append(f"{side}_profile was used; {side}_fixture was ignored.")
        return resolve_profile({"profile": profile, "fixture": fixture}, "plant")

    # --------------------------------------------------------- usair_home_check
    def home_check(self, args: Dict[str, Any]) -> Dict[str, Any]:
        profile, fixture = resolve_profile(args, "home")
        if _strings(args.get("topics")):
            profile = dict(profile, topics=_strings(args.get("topics")))
        home, topics, warnings = normalize_home(profile)
        if not topics:
            raise UsairError("bad_argument", "No topic to check.",
                             "Pass topics or fill in burning, wood_heater or coatings. Topics: " + ", ".join(known_topics()) + ".")
        pack = load_pack("home")
        only = {e["id"] for e in pack["entries"] if e.get("topic") in topics}
        ran = run_pack(pack, {"home": home}, [], only)
        findings = [self._finding(item) for item in ran["evaluated"]]
        state = home.get("state")
        for finding, item in zip(findings, ran["evaluated"]):
            sip_topic = item["entry"].get("sip_topic")
            if sip_topic and finding["verdict"] in ("no_federal_rule", "check", "product_rule") and state:
                try:
                    code, sub, name = resolve_state(str(state))
                except UsairError:
                    warnings.append(f"state {state!r} is not a part 52 SIP subpart")
                else:
                    finding["sip"] = {"state": code, "name": name, "citation": f"40 CFR part 52 subpart {sub}",
                                      "topic": sip_topic,
                                      "next_step": f"usair_sip_lookup(state='{code}', topic='{sip_topic}')"}
        counts: Dict[str, int] = {}
        for f in findings:
            counts[f["verdict"]] = counts.get(f["verdict"], 0) + 1
        payload = {
            "layer": "home",
            "fixture": fixture,
            "who": {"role": home.get("role"), "state": state, "name": home.get("name")},
            "topics": topics,
            "pack": {"name": pack["name"], "version": pack["version"]},
            "counts": counts,
            "findings": findings,
            "warnings": warnings,
            "unverified_rules": [f["id"] for f in findings if not f["verified"]],
            "next_step": "Where sip is set, call usair_sip_lookup before describing state rules.",
        }
        return self._finish(payload, [self._pack_source(pack)])

    # --------------------------------------------------------- usair_sip_lookup
    def sip_lookup(self, args: Dict[str, Any]) -> Dict[str, Any]:
        code, sub, name = resolve_state(str(args.get("state") or ""))
        topic = str(args.get("topic") or "").strip()
        if len(topic) < 2:
            raise UsairError("bad_argument", "Give a topic.",
                             "Example: state='TX', topic='outdoor_burning'. Known: " + ", ".join(known_topics()) + ".")
        limit = _int(args.get("max_results"), 8, 1, 20)
        date, title = self.ecfr.resolve_date(None)
        queries = queries_for(topic_key(topic) if topic_key(topic) in set(known_topics()) else topic)
        hits: List[Dict[str, Any]] = []
        pages: List[Cached] = []
        used = queries[0]
        exhausted = True
        incomplete = False
        for query in queries:
            hits, pages, exhausted = self._search_current(query, "52", sub, date, limit)
            used = query
            if hits:
                break
            if not exhausted:
                incomplete = True
                break
        shown = hits[:limit]
        for hit in shown:
            ref = {"part": "52", "subpart": None, "section": hit.get("section"), "paragraph": None}
            hit["citation"] = label(ref) if hit.get("section") else ""
            hit["url"] = ecfr_url(ref, date) if hit.get("section") else ""
        citation = {"part": "52", "subpart": sub, "section": None, "paragraph": None}
        payload: Dict[str, Any] = {
            "state": {"code": code, "name": name},
            "citation": label(citation),
            "topic": topic,
            "query": used,
            "ecfr_date": date,
            "ecfr_url": ecfr_url(citation, date),
            "results": shown,
            "pages_fetched": len(pages),
            "search_complete": (bool(hits) and exhausted) or (not hits and not incomplete),
            "caveat": SIP_CAVEAT,
        }
        if not shown and incomplete:
            payload["search_complete"] = False
            payload["message"] = (f"Search of part 52 subpart {sub} for {topic!r} stopped before the result list "
                                  "was exhausted, so this is not a finding that no current text matches.")
            payload["next_step"] = "Try a narrower query, or open the subpart URL. Do not treat this as no match."
        elif not shown:
            payload["message"] = f"No current part 52 text in subpart {sub} matched {topic!r}."
            payload["next_step"] = "Try different words, or open the subpart URL. The rule may be state-only and not yet approved."
        else:
            payload["next_step"] = "Read one section with usair_cfr_get before quoting it. This is the EPA-approved text."
            if not exhausted:
                payload["search_note"] = "Returned the current sections found within the page cap. Later pages were not read."
        sources = [self._source("eCFR, 40 CFR part 52 (EPA-approved SIP)", payload["ecfr_url"], page, ecfr_date=date)
                   for page in pages]
        return self._with_title(payload, sources, title, date)

    # ------------------------------------------------------------------- internals
    def _rule_filter(self, pack: Dict[str, Any], rules: List[str]) -> Optional[Set[str]]:
        if not rules:
            return None
        known = {e["id"] for e in pack["entries"]}
        bad = [r for r in rules if r not in known]
        if bad:
            raise UsairError("bad_argument", f"Unknown rule id(s): {', '.join(bad)}.",
                             "Use ids from a full triage, such as '63-LLL' or '60-IIII'.")
        return set(rules)

    def _finding(self, item: Dict[str, Any]) -> Dict[str, Any]:
        entry = item["entry"]
        ecfr = entry.get("ecfr") or {}
        ref = {"part": ecfr.get("part"), "subpart": ecfr.get("subpart"),
               "section": ecfr.get("applicability"), "paragraph": None}
        finding: Dict[str, Any] = {
            "id": entry["id"],
            "title": entry.get("title"),
            "program": entry.get("program"),
            "citation": entry.get("citation"),
            "applicability_section": ecfr.get("applicability"),
            "ecfr_url": ecfr_url(ref) if ecfr.get("part") else "",
            "verdict": item["verdict"],
            "why": item.get("why") or "",
            "missing": item.get("missing") or [],
            "verified": bool(entry.get("verified")),
        }
        if entry.get("notes"):
            finding["notes"] = entry["notes"]
        if item.get("units"):
            finding["units"] = [{k: u.get(k) for k in ("unit_id", "unit_type", "verdict", "why", "missing")}
                                for u in item["units"]]
            if not finding["why"]:
                finding["why"] = _unit_why(item["units"])
        return finding

    def _attach_fr(self, findings: List[Dict[str, Any]], sources: List[Dict[str, Any]]) -> List[str]:
        """Best-effort Federal Register context. Each attempt counts toward the cap, including
        failures, so an outage cannot walk every eligible finding. Source details, including an
        empty or stale result, are kept on the finding and on the response."""
        cap = self._setting_int("fr_on_triage_max", 4, 0, 12)
        notes: List[str] = []
        attempts = 0
        deferred = False
        pack = {e["id"]: e for e in load_pack("plant")["entries"]}
        for finding in findings:
            if finding["verdict"] not in ("applies", "check_with_ehs", "check"):
                continue
            fr = (pack.get(finding["id"]) or {}).get("fr") or {}
            if not fr.get("part"):
                continue
            if attempts >= cap:
                deferred = True
                continue
            attempts += 1
            try:
                result = self.fr_recent({"part": fr["part"], "term": fr.get("term"), "since": "2y", "max_results": 3})
            except UsairError as err:
                finding["fr_error"] = err.message
                notes.append(f"{finding['id']}: Federal Register not checked ({err.kind}).")
                continue
            finding["fr_recent"] = result.get("documents") or []
            finding["fr_source"] = result.get("source") or []
            if result.get("message"):
                finding["fr_message"] = result["message"]
            if result.get("stale"):
                finding["fr_stale"] = True
            sources.extend(finding["fr_source"])
        if deferred:
            notes.append(f"Stopped after {cap} Federal Register attempt(s); later rules were not checked.")
        elif attempts == 0 and not notes:
            notes.append("include_fr was set, but no matching rule had a Federal Register query within the cap.")
        return notes

    def _search_current(self, query: str, part: Optional[str], subpart: Optional[str], date: str,
                        limit: int) -> tuple:
        """Page until `limit` current hits, the result list ends, or SEARCH_PAGE_CAP.

        Returns (current_hits, page_caches, exhausted). An empty filtered page is not treated as
        the end of the search.
        """
        raw_hits: List[Dict[str, Any]] = []
        pages: List[Cached] = []
        exhausted = False
        for page_no in range(1, SEARCH_PAGE_CAP + 1):
            cached = self.ecfr.search(query, part, subpart, per_page=SEARCH_PER_PAGE, page=page_no)
            pages.append(cached)
            data = cached.data if isinstance(cached.data, dict) else {}
            raw = data.get("results")
            if not isinstance(raw, list):
                raise UsairError("bad_response", "eCFR search returned no result list.", "Retry once.")
            raw_hits.extend(raw)
            meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
            try:
                last_page = meta.get("total_pages") is not None and page_no >= int(meta["total_pages"])
            except (TypeError, ValueError):
                last_page = False
            if last_page or len(raw) < SEARCH_PER_PAGE:
                exhausted = True
                break
            if len(current_hits(raw_hits, date)) >= limit:
                break
        else:
            exhausted = False
        return current_hits(raw_hits, date), pages, exhausted

    def _with_title(self, payload: Dict[str, Any], sources: List[Dict[str, Any]],
                    title: Optional[Cached], date: str) -> Dict[str, Any]:
        if title is not None:
            sources = list(sources) + [self._source(
                "eCFR Title 40 currency date",
                build_url(ECFR_HOST, TITLES_PATH),
                title,
                ecfr_date=date,
            )]
        return self._finish(payload, sources)

    @staticmethod
    def _pack_source(pack: Dict[str, Any]) -> Dict[str, Any]:
        verified = sum(1 for e in pack["entries"] if e.get("verified"))
        return {
            "name": f"us-air-rules {pack['name']} pack",
            "version": pack["version"],
            "entries": len(pack["entries"]),
            "verified_entries": verified,
            "note": ("Verdicts are computed from the bundled applicability pack, not from a fresh reading "
                     "of eCFR. Entries with verified=false have not been confirmed by scripts/verify_packs.py."),
        }


def _unit_why(units: List[Dict[str, Any]]) -> str:
    parts = []
    for u in units:
        why = u.get("why") or ""
        if why:
            parts.append(f"{u.get('unit_id')}: {why}")
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return parts[0] + f" (+{len(parts) - 1} more units; see units[].why)"


def _part_number(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if re.fullmatch(r"\d{1,3}", text):
        return text
    parsed = parse_citation(text)
    if parsed.get("section"):
        raise UsairError("bad_argument", f"{text!r} is a section, not a part.",
                         "Pass the part number, e.g. '63'. Use usair_cfr_get to read a section.")
    return parsed.get("part")


def _as_handler(fn: Callable[[Dict[str, Any]], Dict[str, Any]], name: str) -> Callable:
    def handler(args: Optional[dict] = None, **_kwargs) -> str:
        try:
            if not isinstance(args, dict):
                args = {}
            return _dump(fn(dict(args)))
        except UsairError as err:
            return _error(err.kind, err.message, err.next_step, err.details)
        except Exception as err:
            logger.exception("us-air-rules tool %s failed", name)
            return _error("internal", f"Unexpected error in us-air-rules: {type(err).__name__}: {err}",
                          "Retry once. If it repeats, open the eCFR or Federal Register link yourself.")
    handler.__name__ = name
    return handler


def make_handlers(tools: Optional[Tools] = None) -> Dict[str, Callable]:
    t = tools or Tools()
    return {
        "usair_cfr_get": _as_handler(t.cfr_get, "usair_cfr_get"),
        "usair_cfr_search": _as_handler(t.cfr_search, "usair_cfr_search"),
        "usair_fr_recent": _as_handler(t.fr_recent, "usair_fr_recent"),
        "usair_plant_triage": _as_handler(t.plant_triage, "usair_plant_triage"),
        "usair_plant_compare": _as_handler(t.plant_compare, "usair_plant_compare"),
        "usair_home_check": _as_handler(t.home_check, "usair_home_check"),
        "usair_sip_lookup": _as_handler(t.sip_lookup, "usair_sip_lookup"),
    }


# ---------------------------------------------------------------- slash command

def slash_usair(raw: str, tools: Optional[Tools] = None) -> str:
    """/usair demo|compare|template|fixtures. Demo and compare profiles make no network call."""
    parts = (raw or "").strip().split()
    cmd = parts[0].lower() if parts else "help"
    arg = parts[1].lower() if len(parts) > 1 else ""
    tools = tools or Tools()
    if cmd in ("help", ""):
        return ("Usage: /usair demo [plant|lime|home|shop|compare] | "
                "/usair compare <before_fixture> <after_fixture> | "
                "/usair template [plant|home] | /usair fixtures\n"
                "demo and compare run fictional plant profiles offline. template prints a blank profile. "
                "Live text is usair_cfr_get / usair_cfr_search / usair_fr_recent / usair_sip_lookup.")
    if cmd == "compare":
        if len(parts) != 3:
            return "Usage: /usair compare <before_fixture> <after_fixture>"
        return _compare_text(tools, {"before_fixture": parts[1], "after_fixture": parts[2]})
    if cmd == "fixtures":
        lines = ["Fictional fixtures (no real facility):"]
        for name in fixture_names():
            data = load_fixture(name)
            lines.append(f"- {name} ({data.get('layer')}): {data.get('note', '')}")
        return "\n".join(lines)
    if cmd == "template":
        name = "home" if arg in ("home", "homeowner", "shop") else "plant"
        if arg and arg not in ("plant", "home", "homeowner", "shop"):
            return f"Unknown template {arg!r}. Use plant or home."
        return json.dumps(load_template(name), indent=2)
    if cmd == "demo":
        return _demo(arg or "plant", tools)
    return f"Unknown /usair command {cmd!r}. Try /usair help."


def _demo(which: str, tools: Tools) -> str:
    if which in ("home", "homeowner"):
        result = tools.home_check({"fixture": "homeowner_demo"})
        title = "Homeowner demo (fictional, Pennsylvania)"
    elif which in ("shop", "body", "bodyshop"):
        result = tools.home_check({"fixture": "body_shop_demo"})
        title = "Body shop demo (fictional)"
    elif which in ("lime",):
        result = tools.plant_triage({"fixture": "lime_plant_demo"})
        title = "Lime plant demo (fictional, Copper Hollow)"
    elif which in ("plant", "cement"):
        result = tools.plant_triage({"fixture": "cement_plant_demo"})
        title = "Cement plant demo (fictional, Mesa Gris)"
    elif which in ("compare", "diff"):
        return _compare_text(tools, {
            "before_fixture": "cement_plant_demo",
            "after_fixture": "cement_ciswi_after",
        })
    else:
        return f"Unknown demo {which!r}. Use plant, lime, home, shop or compare."
    return title + "\n\n" + _render(result)


def _render(result: Dict[str, Any]) -> str:
    lines = []
    counts = result.get("counts") or {}
    if counts:
        lines.append("Counts: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    for f in result.get("findings") or []:
        missing = f" [missing: {', '.join(f['missing'])}]" if f.get("missing") else ""
        lines.append(f"- {f['verdict']:16} {f['id']:18} {f.get('citation') or ''}{missing}")
        if f.get("why"):
            lines.append(f"    {f['why']}")
    if result.get("not_relevant"):
        lines.append("Not in play: " + ", ".join(result["not_relevant"]))
    lines.append("")
    lines.append(result.get("disclaimer") or DISCLAIMER)
    return "\n".join(lines)


def _compare_text(tools: Tools, args: Dict[str, Any]) -> str:
    try:
        result = tools.plant_compare(args)
    except UsairError as err:
        return err.message
    return result.get("summary") or ""


def _compare_label(run: Dict[str, Any], fixture: Optional[str]) -> Dict[str, Any]:
    facility = run.get("facility") or {}
    return {
        "fixture": fixture,
        "name": facility.get("name"),
        "state": facility.get("state"),
        "units": facility.get("units"),
    }


def cli_warm(tools: Optional[Tools] = None) -> str:
    """One cheap request: learn the date Title 40 is current through, so later reads hit a warm cache."""
    tools = tools or Tools()
    cached = tools.ecfr.title40_status()
    data = cached.data
    return (f"Title 40 is current through {data.get('up_to_date_as_of')} "
            f"(last amended {data.get('latest_amended_on')}, cache {cached.state}). "
            f"Section text is fetched on demand and then kept.")
