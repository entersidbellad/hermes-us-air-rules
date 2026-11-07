"""Compare two plant screenings from the bundled pack.

The compare calls the same plant triage as usair_plant_triage, then diffs the
profiles and the structured verdicts. It does not fetch eCFR or the Federal
Register, and it does not invent a difference the engine did not return.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .citations import ecfr_url
from .client import UsairError

CITATION_NOTE = (
    "Citations and reasons are from the bundled plant pack. "
    "This compare did not contact eCFR or the Federal Register."
)
VERIFIED_MEANS = (
    "verified on a row is the pack's stored phrase-check flag. "
    "This compare did not re-read the section."
)

_MISSING = object()


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
    """One screen for the terminal. The JSON payload keeps every row."""
    lines = [
        "What changed",
        f"  before: {_side_label(result.get('before') or {})}",
        f"  after:  {_side_label(result.get('after') or {})}",
        "Compared offline. Citations are the bundled pack, not a live eCFR or Federal Register read.",
    ]
    if result.get("rules_filter"):
        lines.append("Rules compared: " + ", ".join(result["rules_filter"]))
    changes = result.get("input_changes") or []
    lines.append(f"Input changes ({len(changes)}):")
    if not changes:
        lines.append("  (none)")
    for item in changes[:8]:
        if item["change"] == "changed":
            lines.append(f"  {item['path']}: {_show(item.get('before'))} → {_show(item.get('after'))}")
        elif item["change"] == "added" and item.get("unit"):
            lines.append(f"  {item['path']}: added")
        elif item["change"] == "added":
            lines.append(f"  {item['path']}: added {_show(item.get('after'))}")
        elif item.get("unit"):
            lines.append(f"  {item['path']}: removed")
        else:
            lines.append(f"  {item['path']}: removed {_show(item.get('before'))}")
    if len(changes) > 8:
        lines.append(f"  … {len(changes) - 8} more in input_changes")
    verdicts = result.get("verdict_changes") or []
    lines.append(f"Verdict changes ({len(verdicts)}):")
    if not verdicts:
        lines.append("  (none)")
    for row in verdicts:
        lines.extend(_rule_lines(row))
    for heading, key in (
        ("Same verdict, reason or missing facts changed", "explanation_changes"),
        ("Same verdict, units added or removed", "unit_set_changes"),
    ):
        rows = result.get(key) or []
        if not rows:
            continue
        lines.append(f"{heading} ({len(rows)}):")
        for row in rows:
            lines.extend(_rule_lines(row))
    counts = result.get("counts") or {}
    lines.append(f"Unchanged rules: {counts.get('unchanged', 0)}")
    if counts.get("units_added") or counts.get("units_removed"):
        added = ", ".join(u["id"] for u in result.get("units_added") or []) or "none"
        removed = ", ".join(u["id"] for u in result.get("units_removed") or []) or "none"
        lines.append(f"Units added: {added}. Units removed: {removed}.")
    lines.append("Missing before: " + _fmt_missing(result.get("missing_before") or []))
    lines.append("Missing after: " + _fmt_missing(result.get("missing_after") or []))
    warnings = result.get("warnings") or []
    if warnings:
        lines.append("Warnings:")
        for warning in warnings:
            lines.append(f"  {warning}")
    lines.append("")
    lines.append(result.get("disclaimer") or "")
    return "\n".join(lines)


def _rule_lines(row: Dict[str, Any]) -> List[str]:
    before = row.get("before") or {}
    after = row.get("after") or {}
    if before.get("verdict") == after.get("verdict"):
        lines = [f"  {row['id']}  {before.get('verdict')} (verdict unchanged)"]
    else:
        lines = [f"  {row['id']}  {before.get('verdict')} → {after.get('verdict')}"]
    citation = row.get("citation") or ""
    if citation:
        lines.append(f"    {citation}  (bundled pack, not fetched)")
    for unit in row.get("unit_changes") or []:
        lines.append("    " + _unit_line(unit))
    why = after.get("why") or before.get("why") or ""
    if why and not row.get("unit_changes"):
        lines.append(f"    why: {why}")
    return lines


def _unit_line(unit: Dict[str, Any]) -> str:
    change = unit["change"]
    uid = unit.get("unit_id")
    if change == "added":
        return f"{uid}: added, {unit['after']['verdict']}"
    if change == "removed":
        return f"{uid}: removed, was {unit['before']['verdict']}"
    before = unit["before"]
    after = unit["after"]
    text = f"{uid}: {before['verdict']} → {after['verdict']}"
    if before.get("missing") != after.get("missing"):
        text += f"; missing {_fields(before.get('missing'))} → {_fields(after.get('missing'))}"
    why = after.get("why") or before.get("why") or ""
    if why:
        text += f"; {why}"
    return text


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


def _side_label(side: Dict[str, Any]) -> str:
    name = side.get("name") or "profile"
    fixture = side.get("fixture")
    if fixture:
        return f"{fixture} ({name})"
    return str(name)


def _fields(values: Any) -> str:
    if not values:
        return "none"
    return ", ".join(str(item) for item in values)


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
