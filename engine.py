"""The rule engine shared by plant triage and home checks.

Conditions use three-valued logic: True, False, or None (unknown, because a profile field is
missing). An entry's rules are tried in order; the first rule whose condition is True gives the
verdict. The first rule whose condition is unknown stops evaluation and gives the pack's fallback
verdict ("check with EHS" / "check") plus the fields that were missing. So a missing fact can
never turn into "skip": the engine fails closed.

Condition forms:
  {"field": "unit.hp", "op": "gte", "value": 500}
  {"all": [cond, ...]}   {"any": [cond, ...]}   {"not": cond}   {"always": true}
Ops: eq ne in not_in any_in startswith gt gte lt lte after on_or_after before on_or_before
     is_true is_false present missing flagged
"flagged" is the one op that treats a missing value as False: use it only for optional claims
(an existing permit, a test-cell engine) whose absence is not a missing fact.
Dates may be YYYY, YYYY-MM or YYYY-MM-DD; a partial date that straddles the threshold is unknown.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

UNKNOWN_STRINGS = {"", "unknown", "unk", "n/a", "na", "?", "not sure", "dont_know", "don't know", "tbd"}
TRUE_STRINGS = {"true", "yes", "y", "1"}
FALSE_STRINGS = {"false", "no", "n", "0", "none"}

OPS = {"eq", "ne", "in", "not_in", "any_in", "startswith", "gt", "gte", "lt", "lte", "after", "on_or_after",
       "before", "on_or_before", "is_true", "is_false", "present", "missing", "flagged"}
_NO_VALUE_OPS = {"is_true", "is_false", "present", "missing", "flagged"}

Tri = Optional[bool]


class PackError(ValueError):
    """A rule pack that does not follow the condition grammar."""


def is_unknown(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip().lower() in UNKNOWN_STRINGS:
        return True
    if isinstance(value, (list, tuple)) and len(value) == 0:
        return True
    return False


def as_bool(value: Any) -> Tri:
    if isinstance(value, bool):
        return value
    if is_unknown(value):
        return None
    if isinstance(value, (int, float)):
        return bool(value)
    s = str(value).strip().lower()
    if s in TRUE_STRINGS:
        return True
    if s in FALSE_STRINGS:
        return False
    return None


def as_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or is_unknown(value):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


_DATE_RX = re.compile(r"^(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?$")


def date_range(value: Any) -> Optional[Tuple[_dt.date, _dt.date]]:
    """'2006' -> (2006-01-01, 2006-12-31); '2006-04' -> April; full dates -> (d, d).

    A dict {"start", "end"} is an explicit bound, used when the latest known event is not a
    single calendar day, month, or year.
    """
    if isinstance(value, _dt.date):
        return value, value
    if isinstance(value, dict) and "start" in value and "end" in value:
        lo = date_range(value.get("start"))
        hi = date_range(value.get("end"))
        if lo is None or hi is None or hi[1] < lo[0]:
            return None
        return lo[0], hi[1]
    if is_unknown(value):
        return None
    m = _DATE_RX.match(str(value).strip())
    if not m:
        return None
    y = int(m.group(1))
    try:
        if m.group(3):
            d = _dt.date(y, int(m.group(2)), int(m.group(3)))
            return d, d
        if m.group(2):
            mo = int(m.group(2))
            last = (_dt.date(y + (mo == 12), mo % 12 + 1, 1) - _dt.timedelta(days=1))
            return _dt.date(y, mo, 1), last
        return _dt.date(y, 1, 1), _dt.date(y, 12, 31)
    except ValueError:
        return None


def _norm(v: Any) -> Any:
    return v.strip().lower() if isinstance(v, str) else v


def get_path(ctx: Dict[str, Any], path: str) -> Any:
    root, _, rest = path.partition(".")
    node: Any = ctx.get(root)
    if root == "results":
        return (node or {}).get(rest)
    for part in rest.split(".") if rest else []:
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _compare(op: str, value: Any, target: Any) -> Tri:
    if op == "flagged":
        return as_bool(value) is True
    if op == "present":
        return not is_unknown(value)
    if op == "missing":
        return is_unknown(value)
    if op in ("is_true", "is_false"):
        b = as_bool(value)
        if b is None:
            return None
        return b if op == "is_true" else not b
    if is_unknown(value):
        return None
    if op in ("eq", "ne"):
        if isinstance(target, bool):
            b = as_bool(value)
            if b is None:
                return None
            same = b == target
        else:
            same = _norm(value) == _norm(target)
        return same if op == "eq" else not same
    if op in ("in", "not_in"):
        hit = _norm(value) in {_norm(t) for t in target}
        return hit if op == "in" else not hit
    if op == "any_in":
        values = value if isinstance(value, (list, tuple, set)) else [value]
        return any(_norm(v) in {_norm(t) for t in target} for v in values)
    if op == "startswith":
        values = value if isinstance(value, (list, tuple, set)) else [value]
        prefixes = target if isinstance(target, (list, tuple)) else [target]
        return any(str(v).strip().startswith(str(p)) for v in values for p in prefixes)
    if op in ("gt", "gte", "lt", "lte"):
        num = as_number(value)
        if num is None:
            return None
        t = float(target)
        return {"gt": num > t, "gte": num >= t, "lt": num < t, "lte": num <= t}[op]
    if op in ("after", "on_or_after", "before", "on_or_before"):
        rng = date_range(value)
        t = date_range(target)
        if rng is None or t is None:
            return None
        lo, hi = rng
        tgt = t[0]
        if op == "after":
            return True if lo > tgt else False if hi <= tgt else None
        if op == "on_or_after":
            return True if lo >= tgt else False if hi < tgt else None
        if op == "before":
            return True if hi < tgt else False if lo >= tgt else None
        return True if hi <= tgt else False if lo > tgt else None
    raise PackError(f"unknown op {op!r}")


def evaluate(cond: Dict[str, Any], ctx: Dict[str, Any]) -> Tuple[Tri, Set[str]]:
    """Return (result, missing_fields)."""
    if "always" in cond:
        return bool(cond["always"]), set()
    if "all" in cond:
        missing: Set[str] = set()
        unknown = False
        for sub in cond["all"]:
            r, m = evaluate(sub, ctx)
            if r is False:
                return False, set()
            if r is None:
                unknown = True
                missing |= m
        return (None, missing) if unknown else (True, set())
    if "any" in cond:
        missing = set()
        unknown = False
        for sub in cond["any"]:
            r, m = evaluate(sub, ctx)
            if r is True:
                return True, set()
            if r is None:
                unknown = True
                missing |= m
        return (None, missing) if unknown else (False, set())
    if "not" in cond:
        r, m = evaluate(cond["not"], ctx)
        return (None if r is None else not r), m
    field = cond.get("field")
    op = cond.get("op")
    if not field or op not in OPS:
        raise PackError(f"bad condition {cond!r}")
    value = get_path(ctx, field)
    r = _compare(op, value, cond.get("value"))
    return r, ({field} if r is None else set())


def validate_condition(cond: Any, where: str) -> List[str]:
    problems: List[str] = []
    if not isinstance(cond, dict):
        return [f"{where}: condition must be an object"]
    keys = set(cond)
    if keys & {"all", "any"}:
        key = "all" if "all" in cond else "any"
        if not isinstance(cond[key], list) or not cond[key]:
            problems.append(f"{where}: '{key}' needs a non-empty list")
        else:
            for i, sub in enumerate(cond[key]):
                problems += validate_condition(sub, f"{where}.{key}[{i}]")
    elif "not" in keys:
        problems += validate_condition(cond["not"], f"{where}.not")
    elif "always" in keys:
        pass
    else:
        if not isinstance(cond.get("field"), str):
            problems.append(f"{where}: missing 'field'")
        if cond.get("op") not in OPS:
            problems.append(f"{where}: unknown op {cond.get('op')!r}")
        elif cond["op"] not in _NO_VALUE_OPS and "value" not in cond:
            problems.append(f"{where}: op {cond['op']!r} needs a 'value'")
    return problems


def evaluate_rules(entry: Dict[str, Any], ctx: Dict[str, Any], fallback: str) -> Dict[str, Any]:
    for rule in entry.get("rules") or []:
        r, missing = evaluate(rule["when"], ctx)
        if r is True:
            return {"verdict": rule["verdict"], "why": rule.get("why", ""), "missing": []}
        if r is None:
            return {"verdict": fallback,
                    "why": entry.get("unknown_why") or "The profile is missing facts this rule depends on.",
                    "missing": sorted(missing)}
    default = entry.get("default") or {}
    return {"verdict": default.get("verdict", fallback), "why": default.get("why", ""), "missing": []}


def worst(verdicts: Iterable[str], order: List[str]) -> str:
    rank = {v: i for i, v in enumerate(order)}
    vs = list(verdicts)
    return min(vs, key=lambda v: rank.get(v, -1)) if vs else order[-1]


def _result_refs(node: Any) -> Set[str]:
    """Ids read as results.<id> anywhere in an entry's conditions."""
    found: Set[str] = set()
    if isinstance(node, dict):
        field = node.get("field")
        if isinstance(field, str) and field.startswith("results."):
            found.add(field.split(".", 1)[1])
        for value in node.values():
            found |= _result_refs(value)
    elif isinstance(node, list):
        for value in node:
            found |= _result_refs(value)
    return found


def entries_to_evaluate(entries: List[Dict[str, Any]], only: Optional[Set[str]]) -> Optional[Set[str]]:
    """Ids that must run so filtered rows still see earlier verdicts. None means every entry."""
    if only is None:
        return None
    by_id = {entry["id"]: entry for entry in entries}
    needed = {eid for eid in only if eid in by_id}
    changed = True
    while changed:
        changed = False
        for eid in list(needed):
            for dep in _result_refs(by_id[eid]):
                if dep in by_id and dep not in needed:
                    needed.add(dep)
                    changed = True
    return needed


def run_pack(pack: Dict[str, Any], ctx_base: Dict[str, Any], units: List[Dict[str, Any]],
             only: Optional[Set[str]] = None) -> Dict[str, Any]:
    """Evaluate every entry the answer depends on. Unit-scoped entries run once per matching unit;
    the entry's verdict is the highest-priority unit verdict. Later entries can read earlier
    verdicts as results.<id>. A filter limits the rows returned, not the earlier entries those
    rows read: a missing result must not stop a later rule before a known fact is checked."""
    order: List[str] = pack["verdicts"]
    fallback: str = pack["fallback_verdict"]
    needed = entries_to_evaluate(pack["entries"], only)
    results: Dict[str, str] = {}
    evaluated: List[Dict[str, Any]] = []
    not_relevant: List[str] = []
    for entry in pack["entries"]:
        eid = entry["id"]
        if needed is not None and eid not in needed:
            continue
        visible = only is None or eid in only
        ctx = dict(ctx_base, results=results)
        rel = entry.get("relevant_if")
        rel_r, rel_missing = evaluate(rel, ctx) if rel else (True, set())
        if rel_r is False:
            results[eid] = "not_relevant"
            if visible:
                not_relevant.append(eid)
            continue
        if rel_r is None:
            out = {"verdict": fallback, "why": entry.get("relevance_unknown_why")
                   or "Could not tell whether this source category is present.", "missing": sorted(rel_missing)}
            results[eid] = fallback
            if visible:
                evaluated.append({"entry": entry, **out, "units": []})
            continue
        if entry.get("scope") == "unit":
            types = set(entry.get("unit_types") or [])
            matching = [u for u in units if u.get("type") in types]
            if not matching:
                results[eid] = "not_relevant"
                if visible:
                    not_relevant.append(eid)
                continue
            unit_results = []
            for u in matching:
                r = evaluate_rules(entry, dict(ctx, unit=u), fallback)
                unit_results.append({"unit_id": u.get("id"), "unit_type": u.get("type"), **r})
            verdict = worst((r["verdict"] for r in unit_results), order)
            missing = sorted({m for r in unit_results for m in r["missing"]})
            results[eid] = verdict
            if visible:
                evaluated.append({"entry": entry, "verdict": verdict, "why": "", "missing": missing,
                                  "units": unit_results})
        else:
            r = evaluate_rules(entry, ctx, fallback)
            results[eid] = r["verdict"]
            if visible:
                evaluated.append({"entry": entry, **r, "units": []})
    rank = {v: i for i, v in enumerate(order)}
    evaluated.sort(key=lambda e: rank.get(e["verdict"], len(order)))
    return {"evaluated": evaluated, "not_relevant": not_relevant, "results": results}
