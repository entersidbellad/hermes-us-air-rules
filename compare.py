"""Compare two plant screenings from the bundled pack.

The compare calls the same plant triage as usair_plant_triage, then diffs the
profiles and the structured verdicts. It does not fetch eCFR or the Federal
Register, and it does not invent a difference the engine did not return.
"""

from __future__ import annotations

import json
import re
import textwrap
from typing import Any, Dict, List, Optional

from .citations import ecfr_url
from .client import UsairError
from .version import __version__

CITATION_NOTE = (
    "Citations and reasons are from the bundled plant pack. "
    "This compare did not contact eCFR or the Federal Register."
)
VERIFIED_MEANS = (
    "verified on a row is the pack's stored phrase-check flag. "
    "This compare did not re-read the section."
)

_MISSING = object()
_SCREEN_WIDTH = 78
_UNIT_DETAIL_CAP = 8
_UNIT_FIELD = re.compile(r'^units\["(.*)"\]\.(.+)$')
_FACT_LABELS = {
    "burns_solid_waste": "Whether the plant burns solid waste",
    "burns_hazardous_waste": "Whether the plant burns hazardous waste",
}
_VERDICT_WORDS = {
    "check_with_ehs": "check with EHS",
    "skip": "skip",
    "applies": "applies",
    "not_relevant": "outside the screened units",
    "check": "check",
}


def validate_unit_ids(profile: Dict[str, Any], side: str) -> None:
    """Refuse ids that would pair the wrong equipment.

    Triage will invent an id from position when one is missing. Compare does not,
    because inserting a unit would then renumber the others and look like every
    unit changed.
    """
    if not isinstance(profile, dict):
        raise UsairError("bad_argument", f"{side} profile must be a JSON object.",
                         "Start from /usair template plant.")
    units = profile.get("units") or []
    if not isinstance(units, list):
        raise UsairError("bad_argument", f"{side} profile.units must be a list of objects.",
                         "Start from /usair template plant.")
    seen = set()
    for i, raw in enumerate(units):
        if not isinstance(raw, dict):
            raise UsairError("bad_argument", f"{side} profile.units[{i}] must be an object.")
        if "id" not in raw or not isinstance(raw.get("id"), str) or not raw["id"].strip():
            raise UsairError(
                "bad_argument",
                f"{side} profile.units[{i}] has no string id.",
                "Give every unit a stable id such as \"K1\". Compare will not invent one from list position.",
            )
        uid = raw["id"]
        if uid in seen:
            raise UsairError(
                "bad_argument",
                f"{side} profile repeats unit id {uid!r}.",
                "Unit ids must be unique. Compare will not guess which unit is which.",
            )
        seen.add(uid)


def diff_inputs(before: Dict[str, Any], after: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Field-level diff of the profiles as supplied, matched by unit id."""
    changes: List[Dict[str, Any]] = []
    before_fac = {k: v for k, v in before.items() if k != "units"}
    after_fac = {k: v for k, v in after.items() if k != "units"}
    for key in sorted(set(before_fac) | set(after_fac)):
        _diff_value(before_fac.get(key, _MISSING), after_fac.get(key, _MISSING), key, changes)
    before_units = {u["id"]: u for u in before.get("units") or []}
    after_units = {u["id"]: u for u in after.get("units") or []}
    for uid in sorted(set(before_units) | set(after_units), key=str):
        path = "units[" + json.dumps(uid, ensure_ascii=False) + "]"
        if uid not in before_units:
            changes.append({"path": path, "change": "added", "after": after_units[uid], "unit": True})
        elif uid not in after_units:
            changes.append({"path": path, "change": "removed", "before": before_units[uid], "unit": True})
        else:
            _diff_object(before_units[uid], after_units[uid], path, changes, skip={"id"})
    changes.sort(key=lambda item: item["path"])
    return changes


def compare_screenings(before_profile: Dict[str, Any], after_profile: Dict[str, Any],
                       before_result: Dict[str, Any], after_result: Dict[str, Any],
                       pack: Dict[str, Any], rules_filter: Optional[List[str]]) -> Dict[str, Any]:
    """Align pack rows and unit ids. Return the structured diff, without tool metadata."""
    by_id = {entry["id"]: entry for entry in pack["entries"]}
    before_rows = _index(before_result, by_id)
    after_rows = _index(after_result, by_id)
    order = [entry["id"] for entry in pack["entries"]]
    extra = sorted((set(before_rows) | set(after_rows)) - set(order))
    verdict_changes: List[Dict[str, Any]] = []
    explanation_changes: List[Dict[str, Any]] = []
    unit_set_changes: List[Dict[str, Any]] = []
    unchanged: List[str] = []
    rules_added: List[Dict[str, Any]] = []
    rules_removed: List[Dict[str, Any]] = []
    for eid in order + extra:
        if eid not in before_rows and eid not in after_rows:
            continue
        if eid not in before_rows:
            rules_added.append(_side_row(after_rows[eid]))
            continue
        if eid not in after_rows:
            rules_removed.append(_side_row(before_rows[eid]))
            continue
        kind, row = _rule_delta(before_rows[eid], after_rows[eid])
        if kind == "unchanged":
            unchanged.append(eid)
        elif kind == "verdict":
            verdict_changes.append(row)
        elif kind == "explanation":
            explanation_changes.append(row)
        elif kind == "unit_set":
            unit_set_changes.append(row)
        else:
            raise UsairError("internal", f"Unknown compare kind {kind!r}.")
    input_changes = diff_inputs(before_profile, after_profile)
    units_added = [_unit_brief(item["after"]) for item in input_changes if item.get("unit") and item["change"] == "added"]
    units_removed = [_unit_brief(item["before"]) for item in input_changes if item.get("unit") and item["change"] == "removed"]
    compared = (len(verdict_changes) + len(explanation_changes) + len(unit_set_changes)
                + len(unchanged) + len(rules_added) + len(rules_removed))
    return {
        "input_changes": input_changes,
        "verdict_changes": verdict_changes,
        "explanation_changes": explanation_changes,
        "unit_set_changes": unit_set_changes,
        "unchanged_ids": unchanged,
        "rules_added": rules_added,
        "rules_removed": rules_removed,
        "units_added": units_added,
        "units_removed": units_removed,
        "missing_before": _missing(before_rows, order + extra),
        "missing_after": _missing(after_rows, order + extra),
        "rules_filter": list(rules_filter) if rules_filter else None,
        "counts": {
            "input_changes": len(input_changes),
            "verdict_changes": len(verdict_changes),
            "explanation_changes": len(explanation_changes),
            "unit_set_changes": len(unit_set_changes),
            "unchanged": len(unchanged),
            "rules_added": len(rules_added),
            "rules_removed": len(rules_removed),
            "units_added": len(units_added),
            "units_removed": len(units_removed),
            "rules_compared": compared,
        },
    }


def render_compare(result: Dict[str, Any]) -> str:
    """Plain-language screen. The JSON payload keeps the raw verdict enums."""
    before = result.get("before") or {}
    after = result.get("after") or {}
    lines: List[str] = []
    lines.extend(_wrap(_plant_heading(before, after)))
    lines.extend(_wrap(f"us-air-rules {__version__}"))
    lines.extend(_wrap("Offline bundled pack. Not a live eCFR or Federal Register read."))
    if before.get("fixture") or after.get("fixture"):
        lines.extend(_wrap(
            "Profiles: "
            f"{before.get('fixture') or before.get('name') or 'before'} → "
            f"{after.get('fixture') or after.get('name') or 'after'}"
        ))
    if result.get("rules_filter"):
        lines.extend(_wrap("Screen limited to: " + ", ".join(result["rules_filter"]) + "."))
    lines.append("")
    changes = result.get("input_changes") or []
    if not changes:
        lines.extend(_wrap("No input field changed."))
    else:
        lines.extend(_wrap("What changed in the plant:"))
        for item in changes[:4]:
            lines.extend(_wrap(_fact_phrase(item), indent="  "))
        if len(changes) > 4:
            lines.extend(_wrap(
                f"{len(changes) - 4} more field changes are in the tool result.",
                indent="  ",
            ))
    verdicts = result.get("verdict_changes") or []
    explanations = result.get("explanation_changes") or []
    unit_sets = result.get("unit_set_changes") or []
    if not verdicts and not explanations and not unit_sets:
        lines.extend(_wrap("No screened answer changed."))
    shown = 0
    for kind, rows in (
        ("verdict", verdicts),
        ("explanation", explanations),
        ("units", unit_sets),
    ):
        for row in rows:
            if shown >= 3:
                break
            lines.extend(_outcome_blocks(row, kind))
            shown += 1
        if shown >= 3:
            break
    extra = len(verdicts) + len(explanations) + len(unit_sets) - shown
    if extra > 0:
        lines.extend(_wrap(f"{extra} more rule changes are in the tool result."))
    counts = result.get("counts") or {}
    lines.extend(_wrap(f"Unchanged screened rules: {counts.get('unchanged', 0)}."))
    if counts.get("units_added") or counts.get("units_removed"):
        added = ", ".join(u["id"] for u in result.get("units_added") or []) or "none"
        removed = ", ".join(u["id"] for u in result.get("units_removed") or []) or "none"
        lines.extend(_wrap(f"Equipment added: {added}. Equipment removed: {removed}."))
    missing_before = result.get("missing_before") or []
    missing_after = result.get("missing_after") or []
    note = _missing_note(result)
    if note:
        lines.extend(_wrap(note))
    lines.extend(_wrap("Missing before: " + _fmt_missing(missing_before) + "."))
    lines.extend(_wrap("Missing after: " + _fmt_missing(missing_after) + "."))
    for warning in result.get("warnings") or []:
        lines.extend(_wrap("Note: " + warning))
    lines.append("")
    lines.extend(_wrap(result.get("disclaimer") or ""))
    return "\n".join(lines)


def _plant_heading(before: Dict[str, Any], after: Dict[str, Any]) -> str:
    bname = before.get("name") or "Before profile"
    aname = after.get("name") or "After profile"
    if bname == aname:
        state = before.get("state") or ""
        loc = f", {state}" if state else ""
        return f"{bname}{loc} — what changed"
    return f"{bname} → {aname} — what changed"


def _fact_phrase(item: Dict[str, Any]) -> str:
    if item.get("unit") and item["change"] == "added":
        unit = item.get("after") or {}
        return f"Equipment {unit.get('id')} ({unit.get('type') or 'unit'}) was added."
    if item.get("unit") and item["change"] == "removed":
        unit = item.get("before") or {}
        return f"Equipment {unit.get('id')} ({unit.get('type') or 'unit'}) was removed."
    label = _path_words(item["path"])
    if item["change"] == "changed":
        return f"{label} was {_show(item.get('before'))}, now {_show(item.get('after'))}."
    if item["change"] == "added":
        return f"{label} is now {_show(item.get('after'))}."
    return f"{label} was removed (was {_show(item.get('before'))})."


def _path_words(path: str) -> str:
    label = _FACT_LABELS.get(path)
    if label:
        return f"{label} ({path})"
    match = _UNIT_FIELD.match(path)
    if match:
        return f"{match.group(1)} {match.group(2)}"
    return path


def _outcome_blocks(row: Dict[str, Any], _kind: str) -> List[str]:
    """Speak the rule total and each unit from that unit's own before and after.

    The row class is unused here. Added and removed equipment still get that
    unit's verdict, reason, and citation from its own snapshot.
    """
    name = ((row.get("title") or row["id"]).split(":")[0]).strip()
    ident = row["id"]
    units = row.get("unit_changes") or []
    before_v = (row.get("before") or {}).get("verdict")
    after_v = (row.get("after") or {}).get("verdict")
    lines: List[str] = []
    if len(units) == 1 and _unit_matches_rule(units[0], before_v, after_v):
        lines.extend(_wrap(_combined_sentence(units[0], name, ident, before_v, after_v)))
        why = _unit_why(units[0]) or _rule_why(row)
        if why:
            lines.extend(_wrap("Why: " + why))
    else:
        lines.extend(_wrap(_rule_only_sentence(name, ident, before_v, after_v)))
        shown = units[:_UNIT_DETAIL_CAP]
        omitted = len(units) - len(shown)
        details = [(_unit_sentence(unit), _unit_why(unit)) for unit in shown]
        unique = list(dict.fromkeys(why for _, why in details if why))
        if len(unique) <= 1:
            for sentence, _why in details:
                lines.extend(_wrap(sentence))
            if unique:
                lines.extend(_wrap("Why: " + unique[0]))
        else:
            for sentence, why in details:
                lines.extend(_wrap(sentence))
                if why:
                    lines.extend(_wrap("Why: " + why))
        if omitted:
            lines.extend(_wrap(_omitted_equipment(omitted)))
        elif not units:
            why = _rule_why(row)
            if why:
                lines.extend(_wrap("Why: " + why))
    citation = row.get("citation") or ""
    if citation:
        section = row.get("applicability_section")
        extra = f", section {section}" if section else ""
        lines.extend(_wrap(f"Citation: {citation}{extra}. Bundled pack, not a live fetch."))
    lines.extend(_wrap("Evidence: " + _evidence(row)))
    if _moved_to_skip(before_v, after_v, units):
        lines.extend(_wrap("Skip here covers only the screened rule named above."))
    return lines


def _unit_matches_rule(unit: Dict[str, Any], before_v: Optional[str], after_v: Optional[str]) -> bool:
    if unit.get("change") not in ("verdict", "explanation"):
        return False
    before = (unit.get("before") or {}).get("verdict")
    after = (unit.get("after") or {}).get("verdict")
    return before == before_v and after == after_v


def _combined_sentence(unit: Dict[str, Any], name: str, ident: str,
                       before_v: Optional[str], after_v: Optional[str]) -> str:
    lead = f"{_equipment_name(unit)}, "
    if before_v != after_v:
        tail = " for this screened rule" if after_v == "skip" else ""
        return f"{lead}{name} ({ident}): was {_plain(before_v)}, now {_plain(after_v)}{tail}."
    return (
        f"{lead}{name} ({ident}): still {_plain(before_v)}. "
        "The screened answer did not change."
    )


def _rule_only_sentence(name: str, ident: str, before_v: Optional[str], after_v: Optional[str]) -> str:
    if before_v != after_v:
        tail = " for this screened rule" if after_v == "skip" else ""
        return f"{name} ({ident}): the screened rule was {_plain(before_v)}, now {_plain(after_v)}{tail}."
    return f"{name} ({ident}): the screened rule stayed {_plain(after_v)}."


def _unit_sentence(unit: Dict[str, Any]) -> str:
    label = _equipment_name(unit)
    change = unit.get("change")
    if change == "added":
        verdict = (unit.get("after") or {}).get("verdict")
        tail = " for this screened rule" if verdict == "skip" else ""
        return f"{label} was added: {_plain(verdict)}{tail}."
    if change == "removed":
        verdict = (unit.get("before") or {}).get("verdict")
        return f"{label} was removed: was {_plain(verdict)}."
    before = (unit.get("before") or {}).get("verdict")
    after = (unit.get("after") or {}).get("verdict")
    if before != after:
        tail = " for this screened rule" if after == "skip" else ""
        return f"{label}: was {_plain(before)}, now {_plain(after)}{tail}."
    return f"{label}: still {_plain(before)}. The screened answer did not change."


def _equipment_name(unit: Dict[str, Any]) -> str:
    kind = (unit.get("unit_type") or "unit").replace("_", " ")
    return f"{kind.title()} {unit.get('unit_id')}"


def _unit_why(unit: Dict[str, Any]) -> str:
    snap = unit.get("after") or unit.get("before") or {}
    return str(snap.get("why") or "")


def _rule_why(row: Dict[str, Any]) -> str:
    after = row.get("after") or {}
    before = row.get("before") or {}
    return str(after.get("why") or before.get("why") or "")


def _evidence(row: Dict[str, Any]) -> str:
    before = (row.get("before") or {}).get("verdict")
    after = (row.get("after") or {}).get("verdict")
    units = row.get("unit_changes") or []
    if len(units) == 1 and _unit_matches_rule(units[0], before, after):
        return _one_evidence(row["id"], units[0])
    bits = [_rollup_evidence(row["id"], before, after)]
    shown = units[:_UNIT_DETAIL_CAP]
    for unit in shown:
        bits.append(_unit_evidence(unit))
    omitted = len(units) - len(shown)
    if omitted:
        bits.append(_omitted_equipment(omitted).rstrip("."))
    return "; ".join(bits)


def _one_evidence(ident: str, unit: Dict[str, Any]) -> str:
    before = (unit.get("before") or {}).get("verdict")
    after = (unit.get("after") or {}).get("verdict")
    if before == after:
        return f"{ident} {unit.get('unit_id')} {before} (answer unchanged)"
    return f"{ident} {unit.get('unit_id')} {before} → {after}"


def _unit_evidence(unit: Dict[str, Any]) -> str:
    uid = unit.get("unit_id")
    change = unit.get("change")
    if change == "added":
        return f"{uid} added {(unit.get('after') or {}).get('verdict')}"
    if change == "removed":
        return f"{uid} removed was {(unit.get('before') or {}).get('verdict')}"
    before = (unit.get("before") or {}).get("verdict")
    after = (unit.get("after") or {}).get("verdict")
    if before == after:
        return f"{uid} {before} (answer unchanged)"
    return f"{uid} {before} → {after}"


def _rollup_evidence(ident: str, before: Optional[str], after: Optional[str]) -> str:
    if before == after:
        return f"{ident} rollup {before} (answer unchanged)"
    return f"{ident} rollup {before} → {after}"


def _omitted_equipment(omitted: int) -> str:
    noun = "change" if omitted == 1 else "changes"
    verb = "is" if omitted == 1 else "are"
    return f"{omitted} more equipment {noun} {verb} in the tool result."


def _moved_to_skip(before_v: Optional[str], after_v: Optional[str], units: List[Dict[str, Any]]) -> bool:
    if after_v == "skip" and before_v != after_v:
        return True
    for unit in units:
        if unit.get("change") == "removed":
            continue
        after = (unit.get("after") or {}).get("verdict")
        before = (unit.get("before") or {}).get("verdict")
        if after == "skip" and before != after:
            return True
    return False


def _missing_note(result: Dict[str, Any]) -> Optional[str]:
    """Say a fact was filled in only when that same equipment is still screened."""
    before = result.get("missing_before") or []
    after = result.get("missing_after") or []
    if before == after:
        return None
    removed_plant = {unit.get("id") for unit in result.get("units_removed") or []}
    removed_on_rule = set()
    after_verdict: Dict[Any, Any] = {}
    for key in ("verdict_changes", "explanation_changes", "unit_set_changes"):
        for row in result.get(key) or []:
            after_verdict[row.get("id")] = (row.get("after") or {}).get("verdict")
            for unit in row.get("unit_changes") or []:
                if unit.get("change") == "removed":
                    removed_on_rule.add((row.get("id"), unit.get("unit_id")))
    for row in result.get("rules_removed") or []:
        after_verdict[row.get("id")] = "removed"
    after_fields: Dict[Any, set] = {}
    for row in after:
        after_fields.setdefault((row.get("id"), row.get("unit_id")), set()).update(row.get("fields") or [])
    filled = 0
    left_with_equipment = 0
    left_the_screen = 0
    for row in before:
        pair = (row.get("id"), row.get("unit_id"))
        before_fields = set(row.get("fields") or [])
        still = after_fields.get(pair)
        if still is not None and before_fields <= still:
            continue
        if still is not None:
            filled += 1
            continue
        unit_removed = row.get("unit_id") in removed_plant or pair in removed_on_rule
        verdict = after_verdict.get(row.get("id"))
        if unit_removed:
            left_with_equipment += 1
        elif verdict in ("not_relevant", "removed", None):
            left_the_screen += 1
        else:
            filled += 1
    if filled and not left_with_equipment and not left_the_screen and not after:
        return "The missing fact is now filled in." if filled == 1 else "The missing facts are now filled in."
    if left_with_equipment and not filled and not after:
        return "No missing facts remain. The equipment that still needed a fact was removed."
    if left_the_screen and not filled and not left_with_equipment and not after:
        return "No missing facts remain. The rule is outside this screen now."
    parts: List[str] = []
    if filled:
        parts.append("Some missing facts were filled in.")
    if left_with_equipment:
        parts.append("Some missing facts left because that equipment was removed.")
    if left_the_screen:
        parts.append("Some missing facts left because that rule is outside this screen.")
    if after:
        parts.append("Other missing facts remain.")
    if parts:
        return " ".join(parts)
    return "The missing facts are not the same on both sides."


def _plain(verdict: Optional[str]) -> str:
    if verdict in _VERDICT_WORDS:
        return _VERDICT_WORDS[verdict]
    return verdict or "unknown"


def _fmt_missing(rows: List[Dict[str, Any]], limit: int = 6) -> str:
    if not rows:
        return "none"
    shown = []
    for row in rows[:limit]:
        unit = f" {row['unit_id']}" if row.get("unit_id") else ""
        shown.append(f"{row['id']}{unit} {', '.join(row['fields'])}")
    text = "; ".join(shown)
    extra = len(rows) - limit
    if extra > 0:
        text += f" (+{extra} more)"
    return text


def _wrap(text: str, indent: str = "") -> List[str]:
    cleaned = " ".join(str(text).split())
    if not cleaned:
        return []
    return textwrap.wrap(
        cleaned,
        width=_SCREEN_WIDTH,
        initial_indent=indent,
        subsequent_indent=indent + "  ",
        break_long_words=True,
        break_on_hyphens=False,
    )


def _show(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if len(text) > 80:
        return text[:77] + "..."
    return text


def _index(result: Dict[str, Any], by_id: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    rows: Dict[str, Dict[str, Any]] = {}
    for finding in result.get("findings") or []:
        eid = finding.get("id")
        if eid in rows:
            raise UsairError("internal", f"Rule {eid} appears twice in one screening result.")
        rows[eid] = _from_finding(finding, by_id.get(eid))
    for eid in result.get("not_relevant") or []:
        if eid in rows:
            raise UsairError("internal", f"Rule {eid} is both a finding and not relevant.")
        rows[eid] = _from_absent(eid, by_id.get(eid))
    return rows


def _from_finding(finding: Dict[str, Any], entry: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    units = []
    seen = set()
    for unit in finding.get("units") or []:
        uid = unit.get("unit_id")
        if uid in seen:
            raise UsairError(
                "bad_argument",
                f"Rule {finding.get('id')} lists unit {uid!r} more than once.",
                "Compare will not guess which row is which.",
            )
        seen.add(uid)
        units.append({
            "unit_id": uid,
            "unit_type": unit.get("unit_type"),
            "verdict": unit.get("verdict"),
            "why": unit.get("why") or "",
            "missing": list(unit.get("missing") or []),
        })
    row = _pack_cite(finding.get("id"), entry)
    row.update({
        "verdict": finding.get("verdict"),
        "why": finding.get("why") or "",
        "missing": list(finding.get("missing") or []),
        "units": units,
        "verified": bool(finding.get("verified")),
    })
    if finding.get("citation"):
        row["citation"] = finding["citation"]
    return row


def _from_absent(eid: str, entry: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    row = _pack_cite(eid, entry)
    row.update({"verdict": "not_relevant", "why": "", "missing": [], "units": []})
    return row


def _pack_cite(eid: Any, entry: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    entry = entry or {}
    ecfr = entry.get("ecfr") or {}
    ref = {"part": ecfr.get("part"), "subpart": ecfr.get("subpart"),
           "section": ecfr.get("applicability"), "paragraph": None}
    return {
        "id": eid,
        "title": entry.get("title") or "",
        "citation": entry.get("citation") or "",
        "applicability_section": ecfr.get("applicability"),
        "pack_ecfr_url": ecfr_url(ref) if ecfr.get("part") else "",
        "citation_origin": "bundled_pack",
        "verified": bool(entry.get("verified")),
    }


def _side_row(row: Dict[str, Any]) -> Dict[str, Any]:
    kept = _pack_cite(row["id"], None)
    kept.update({
        "title": row.get("title") or "",
        "citation": row.get("citation") or "",
        "applicability_section": row.get("applicability_section"),
        "pack_ecfr_url": row.get("pack_ecfr_url") or "",
        "verified": bool(row.get("verified")),
        "verdict": row.get("verdict"),
        "why": row.get("why") or "",
        "missing": list(row.get("missing") or []),
    })
    return kept


def _rule_delta(before: Dict[str, Any], after: Dict[str, Any]) -> tuple:
    before_units = _unit_map(before)
    after_units = _unit_map(after)
    unit_changes = []
    unchanged_units = 0
    for uid in sorted(set(before_units) | set(after_units), key=lambda item: str(item)):
        if uid not in before_units:
            unit_changes.append({
                "unit_id": uid, "unit_type": after_units[uid].get("unit_type"), "change": "added",
                "after": _unit_snapshot(after_units[uid]),
            })
            continue
        if uid not in after_units:
            unit_changes.append({
                "unit_id": uid, "unit_type": before_units[uid].get("unit_type"), "change": "removed",
                "before": _unit_snapshot(before_units[uid]),
            })
            continue
        left = _unit_snapshot(before_units[uid])
        right = _unit_snapshot(after_units[uid])
        if left == right:
            unchanged_units += 1
            continue
        change = "verdict" if left["verdict"] != right["verdict"] else "explanation"
        unit_changes.append({
            "unit_id": uid,
            "unit_type": after_units[uid].get("unit_type") or before_units[uid].get("unit_type"),
            "change": change,
            "before": left,
            "after": right,
        })
    before_snap = _rule_snapshot(before)
    after_snap = _rule_snapshot(after)
    matched_verdict = any(item["change"] == "verdict" for item in unit_changes)
    matched_explanation = any(item["change"] == "explanation" for item in unit_changes)
    membership = any(item["change"] in ("added", "removed") for item in unit_changes)
    rule_verdict = before_snap["verdict"] != after_snap["verdict"]
    # Unit-scoped why strings name whichever unit the pack ran first. Reordering
    # the profile rewrites that rollup without changing any unit's verdict, reason,
    # or missing fields. That is not a change in the screening.
    rollup_only = (
        not rule_verdict and not matched_verdict and not matched_explanation and not membership
        and before_snap["missing"] == after_snap["missing"]
        and before.get("units") and after.get("units")
        and before_snap["why"] != after_snap["why"])
    if rollup_only:
        return "unchanged", None
    rule_text = before_snap["why"] != after_snap["why"] or before_snap["missing"] != after_snap["missing"]
    if rule_verdict or matched_verdict:
        kind = "verdict"
    elif matched_explanation or (rule_text and not membership):
        kind = "explanation"
    elif membership:
        kind = "unit_set"
    else:
        return "unchanged", None
    row = _pack_cite(before["id"], None)
    row.update({
        "title": after.get("title") or before.get("title") or "",
        "citation": after.get("citation") or before.get("citation") or "",
        "applicability_section": after.get("applicability_section") or before.get("applicability_section"),
        "pack_ecfr_url": after.get("pack_ecfr_url") or before.get("pack_ecfr_url") or "",
        "verified": bool(after.get("verified") and before.get("verified")),
        "kind": kind,
        "before": before_snap,
        "after": after_snap,
        "unit_changes": unit_changes,
        "units_unchanged": unchanged_units,
    })
    if (before.get("citation") or "") != (after.get("citation") or ""):
        row["citation_before"] = before.get("citation") or ""
        row["citation_after"] = after.get("citation") or ""
    return kind, row


def _unit_map(row: Dict[str, Any]) -> Dict[Any, Dict[str, Any]]:
    found: Dict[Any, Dict[str, Any]] = {}
    for unit in row.get("units") or []:
        uid = unit.get("unit_id")
        if uid in found:
            raise UsairError(
                "bad_argument",
                f"Rule {row.get('id')} lists unit {uid!r} more than once.",
                "Compare will not guess which row is which.",
            )
        found[uid] = unit
    return found


def _rule_snapshot(row: Dict[str, Any]) -> Dict[str, Any]:
    return {"verdict": row.get("verdict"), "why": row.get("why") or "", "missing": list(row.get("missing") or [])}


def _unit_snapshot(unit: Dict[str, Any]) -> Dict[str, Any]:
    return {"verdict": unit.get("verdict"), "why": unit.get("why") or "", "missing": list(unit.get("missing") or [])}


def _missing(rows: Dict[str, Dict[str, Any]], order: List[str]) -> List[Dict[str, Any]]:
    found: List[Dict[str, Any]] = []
    for eid in order:
        row = rows.get(eid)
        if not row or row.get("verdict") == "not_relevant":
            continue
        units = row.get("units") or []
        if units:
            for unit in sorted(units, key=lambda item: str(item.get("unit_id"))):
                if unit.get("missing"):
                    found.append({"id": eid, "unit_id": unit.get("unit_id"), "fields": list(unit["missing"])})
        elif row.get("missing"):
            found.append({"id": eid, "unit_id": None, "fields": list(row["missing"])})
    return found


def _unit_brief(unit: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": unit.get("id"), "type": unit.get("type")}


def _diff_object(before: Dict[str, Any], after: Dict[str, Any], prefix: str,
                 changes: List[Dict[str, Any]], skip: Optional[set] = None) -> None:
    skip = skip or set()
    for key in sorted(set(before) | set(after)):
        if key in skip:
            continue
        path = f"{prefix}.{key}" if prefix else key
        _diff_value(before.get(key, _MISSING), after.get(key, _MISSING), path, changes)


def _diff_value(before: Any, after: Any, path: str, changes: List[Dict[str, Any]]) -> None:
    if before is _MISSING and after is _MISSING:
        return
    if before is _MISSING:
        changes.append({"path": path, "change": "added", "after": after})
        return
    if after is _MISSING:
        changes.append({"path": path, "change": "removed", "before": before})
        return
    if _same(before, after):
        return
    if isinstance(before, dict) and isinstance(after, dict):
        _diff_object(before, after, path, changes)
        return
    changes.append({"path": path, "change": "changed", "before": before, "after": after})


def _same(before: Any, after: Any) -> bool:
    if isinstance(before, dict) and isinstance(after, dict):
        return set(before) == set(after) and all(_same(before[key], after[key]) for key in before)
    if isinstance(before, list) and isinstance(after, list):
        return len(before) == len(after) and all(_same(left, right) for left, right in zip(before, after))
    return type(before) is type(after) and before == after
