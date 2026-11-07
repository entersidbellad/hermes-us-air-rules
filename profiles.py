"""Profiles (what the user tells us), fixtures (fictional demo profiles) and rule packs (bundled JSON).

Normalisation adds derived fields the packs read, for example unit.nsps_date (the latest of the
construction, modification and reconstruction dates) or unit.ignition (CI/SI from the fuel). It never
invents a fact: if the inputs are missing, the derived field stays missing and the engine says so.
"""

from __future__ import annotations

import copy
import datetime as _dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .client import UsairError
from .engine import PackError, as_bool, as_number, date_range, is_unknown, validate_condition

DATA = Path(__file__).resolve().parent / "data"
PACKS = DATA / "packs"
FIXTURES = DATA / "fixtures"
TEMPLATES = DATA / "templates"

UNIT_ALIASES = {
    "cement_kiln": ("kiln", {"product": "cement"}), "lime_kiln": ("kiln", {"product": "lime"}),
    "rotary_kiln": ("kiln", {"kiln_type": "rotary"}), "preheater_kiln": ("kiln", {}), "precalciner_kiln": ("kiln", {}),
    "cooler": ("clinker_cooler", {}), "raw_mill_system": ("raw_mill", {}), "finish_mill_system": ("finish_mill", {}),
    "dryer": ("raw_material_dryer", {}), "raw_mill_dryer": ("raw_material_dryer", {}),
    "silo": ("storage", {}), "storage_bin": ("storage", {}), "clinker_storage": ("storage", {}),
    "transfer_point": ("conveyor_transfer", {}), "conveyor": ("conveyor_transfer", {}),
    "bagging": ("bagging_loading", {}), "loadout": ("bagging_loading", {}), "bulk_loading": ("bagging_loading", {}),
    "clinker_pile": ("open_clinker_pile", {}), "primary_crusher": ("crusher", {}), "screening": ("screen", {}),
    "engine": ("stationary_rice", {}), "rice": ("stationary_rice", {}), "generator": ("stationary_rice", {}),
    "emergency_generator": ("stationary_rice", {"use": "emergency"}), "fire_pump": ("stationary_rice", {"use": "fire_pump"}),
    "steam_generator": ("boiler", {}), "heater": ("process_heater", {}),
}
UNIT_TYPES = {"kiln", "clinker_cooler", "raw_mill", "finish_mill", "raw_material_dryer", "storage", "conveyor_transfer",
              "bagging_loading", "open_clinker_pile", "crusher", "screen", "coal_mill", "stationary_rice", "boiler",
              "process_heater", "other"}

CI_FUELS = {"diesel", "distillate", "fuel_oil", "no2_oil", "biodiesel", "kerosene", "jet_fuel"}
SI_FUELS = {"natural_gas", "propane", "lpg", "gasoline", "landfill_gas", "digester_gas", "biogas", "field_gas"}
SOLID_FUELS = {"coal", "petcoke", "pet_coke", "coke", "biomass", "wood", "tires", "tire_derived_fuel", "tdf",
               "solid_waste", "sludge", "bagasse"}
LIQUID_FUELS = {"diesel", "distillate", "fuel_oil", "no2_oil", "no6_oil", "residual_oil", "used_oil", "waste_oil",
                "biodiesel", "kerosene"}
GAS_FUELS = {"natural_gas", "propane", "lpg", "landfill_gas", "digester_gas", "biogas", "refinery_gas",
             "process_gas", "hydrogen", "field_gas"}

HOME_TOPICS = ["outdoor_burning", "idling", "wood_heating", "paint_solvent", "spray_coating"]
_TOPIC_HINTS = {"burning": "outdoor_burning", "vehicles": "idling", "wood_heater": "wood_heating",
                "painting": "paint_solvent", "coatings": "spray_coating"}


def _key(s: Any) -> str:
    return "_".join(str(s).strip().lower().replace("-", " ").replace("/", " ").split())


def _fuels(raw: Any) -> List[str]:
    if is_unknown(raw):
        return []
    items = raw if isinstance(raw, list) else str(raw).replace(";", ",").split(",")
    return [_key(f) for f in items if not is_unknown(f)]


def _format_span(lo: _dt.date, hi: _dt.date) -> Any:
    """Collapse a bound to YYYY, YYYY-MM, or YYYY-MM-DD when that string means the same range."""
    if lo == hi:
        return lo.isoformat()
    if (lo.year == hi.year and lo.month == 1 and lo.day == 1 and hi.month == 12 and hi.day == 31):
        return f"{lo.year:04d}"
    if lo.year == hi.year and lo.month == hi.month and lo.day == 1:
        last = (_dt.date(lo.year + (lo.month == 12), lo.month % 12 + 1, 1) - _dt.timedelta(days=1))
        if hi == last:
            return f"{lo.year:04d}-{lo.month:02d}"
    return {"start": lo.isoformat(), "end": hi.isoformat()}


def _latest_date(*values: Any) -> Any:
    """Latest known construction, modification, or reconstruction, keeping partial-date bounds.

    The lower bound is the latest start among known events, and the upper bound is the latest
    end. A known modification on 1971-09-01 is not hidden by a construction year of 1971, and a
    modification date is kept when construction was never reported.
    """
    ranges = [rng for rng in (date_range(v) for v in values) if rng]
    if not ranges:
        return None
    lo = max(start for start, _end in ranges)
    hi = max(end for _start, end in ranges)
    return _format_span(lo, hi)


# ------------------------------------------------------------------ plant

def normalize_plant(profile: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[str]]:
    """Return (facility, units, warnings)."""
    if not isinstance(profile, dict):
        raise UsairError("bad_argument", "profile must be a JSON object.", "Start from /usair template plant.")
    fac = copy.deepcopy({k: v for k, v in profile.items() if k != "units"})
    warnings: List[str] = []
    raw_units = profile.get("units") or []
    if not isinstance(raw_units, list):
        raise UsairError("bad_argument", "profile.units must be a list of objects.", "Start from /usair template plant.")

    naics = fac.get("naics")
    fac["naics"] = [str(n).strip() for n in (naics if isinstance(naics, list) else [naics]) if not is_unknown(n)]
    hap = _key(fac.get("hap_status", "")) if not is_unknown(fac.get("hap_status")) else ""
    major = as_bool(fac.get("major_source_hap"))
    if major is None and hap in ("major", "major_source"):
        major = True
    elif major is None and hap in ("area", "area_source", "minor", "synthetic_minor"):
        major = False
    fac["major_hap"] = major
    fac["area_hap"] = None if major is None else not major
    for flag in ("burns_hazardous_waste", "burns_solid_waste", "title_v_permit", "criteria_pte_major",
                 "uses_control_devices"):
        fac[flag] = as_bool(fac.get(flag))

    units: List[Dict[str, Any]] = []
    for i, raw in enumerate(raw_units):
        if not isinstance(raw, dict):
            raise UsairError("bad_argument", f"profile.units[{i}] must be an object.")
        u = copy.deepcopy(raw)
        t = _key(u.get("type", ""))
        if t in UNIT_ALIASES:
            t, extra = UNIT_ALIASES[t]
            for k, v in extra.items():
                u.setdefault(k, v)
        if t not in UNIT_TYPES:
            warnings.append(f"unit {u.get('id') or i}: unknown type {u.get('type')!r}; no pack entry reads it")
            t = t or "other"
        u["type"] = t
        u.setdefault("id", f"{t}-{i + 1}")
        fuels = _fuels(u.get("fuel"))
        u["fuel"] = fuels
        if fuels:
            if any(f in SOLID_FUELS for f in fuels):
                u["fuel_class"] = "solid"
            elif any(f in LIQUID_FUELS for f in fuels):
                u["fuel_class"] = "liquid"
            elif all(f in GAS_FUELS for f in fuels):
                u["fuel_class"] = "gas"
        if is_unknown(u.get("ignition")) and fuels:
            if all(f in CI_FUELS for f in fuels):
                u["ignition"] = "CI"
            elif all(f in SI_FUELS for f in fuels):
                u["ignition"] = "SI"
        elif not is_unknown(u.get("ignition")):
            u["ignition"] = str(u["ignition"]).strip().upper()
        if is_unknown(u.get("hp")) and as_number(u.get("kw")) is not None:
            u["hp"] = round(as_number(u["kw"]) * 1.341, 1)
        for flag in ("modified_or_reconstructed", "lean_burn", "test_cell", "follows_subpart_f", "subject_to_ooo"):
            if flag in u:
                u[flag] = as_bool(u[flag])
        if u.get("modified_or_reconstructed") is None and (u.get("modified_date") or u.get("reconstructed_date")):
            u["modified_or_reconstructed"] = True
        nsps = _latest_date(u.get("construction_date"), u.get("modified_date"), u.get("reconstructed_date"))
        if nsps is not None:
            u["nsps_date"] = nsps
        if t == "kiln":
            for flag in ("burns_hazardous_waste", "burns_solid_waste"):
                u[flag] = as_bool(u[flag]) if flag in u else fac.get(flag)
        if not is_unknown(u.get("use")):
            u["use"] = _key(u["use"])
        units.append(u)

    def kiln_products() -> List[Any]:
        return [u.get("product") for u in units if u["type"] == "kiln"]

    products = [_key(p) for p in kiln_products() if not is_unknown(p)]
    fac["is_cement_plant"] = _category(fac["naics"], "327310", "cement", products, kiln_products())
    fac["is_lime_plant"] = _category(fac["naics"], "327410", "lime", products, kiln_products())
    if fac["is_cement_plant"] and not fac["is_lime_plant"]:
        for u in units:
            if u["type"] == "kiln" and is_unknown(u.get("product")):
                u["product"] = "cement"
    # A unit list that never mentions coal does not prove the plant handles none. Reported
    # throughput is evidence either way, including zero. Unknown stays unknown so subpart Y
    # cannot disappear before the 200 ton/day test.
    coal_units = any(
        u["type"] == "coal_mill" or {"coal", "petcoke", "pet_coke"} & set(u.get("fuel") or []) for u in units)
    if coal_units or as_number(fac.get("coal_processed_tpd")) is not None:
        fac["handles_coal"] = True
    else:
        fac["handles_coal"] = None
    cement = fac["is_cement_plant"]
    lime = fac["is_lime_plant"]
    if cement is True or lime is True:
        fac["ghg_all_in_category"] = True
    elif cement is False and lime is False:
        fac["ghg_all_in_category"] = False
    else:
        fac["ghg_all_in_category"] = None
    fac["combustion_heat_at_least_30"] = _combustion_heat_at_least_30(units)
    return fac, units, warnings


# Boilers, process heaters, engines, and kilns are the stationary fuel combustion units this pack
# can see. Horsepower is not converted to MMBtu/hr. A known sum of at least 30 is enough, because
# unlisted capacity cannot make the aggregate smaller. A sum below 30 is False only when every
# listed combustion unit reported a heat input. Otherwise the aggregate stays unknown.
_COMBUSTION_TYPES = {"boiler", "process_heater", "stationary_rice", "kiln"}


def _combustion_heat_at_least_30(units: List[Dict[str, Any]]) -> Optional[bool]:
    combustion = [u for u in units if u.get("type") in _COMBUSTION_TYPES]
    if not combustion:
        return None
    known: List[float] = []
    missing = False
    for unit in combustion:
        rate = as_number(unit.get("mmbtu_hr"))
        if rate is None:
            missing = True
        else:
            known.append(rate)
    if sum(known) >= 30:
        return True
    if missing:
        return None
    return False


def _category(naics: List[str], code: str, product: str, products: List[str], raw_products: List[Any]) -> Optional[bool]:
    if any(n.startswith(code) for n in naics) or product in products:
        return True
    if naics or (raw_products and all(not is_unknown(p) for p in raw_products)):
        return False
    return None


# ------------------------------------------------------------------ home

def normalize_home(profile: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str], List[str]]:
    """Return (home, topics, warnings). Topics come from profile.topics or from the sections present."""
    if not isinstance(profile, dict):
        raise UsairError("bad_argument", "profile must be a JSON object.", "Start from /usair template home.")
    home = copy.deepcopy(profile)
    warnings: List[str] = []
    role = _key(home.get("role", "")) if not is_unknown(home.get("role")) else None
    if role in ("home", "household", "resident", "hobbyist", "individual"):
        role = "homeowner"
    if role in ("shop", "business", "body_shop", "autobody_shop", "contractor", "small_business"):
        role = "small_shop"
    home["role"] = role
    raw_topics = home.get("topics")
    topics: List[str] = []
    if isinstance(raw_topics, list) and raw_topics:
        topics = [_key(t) for t in raw_topics]
    else:
        topics = [topic for key, topic in _TOPIC_HINTS.items() if isinstance(home.get(key), dict)]
    unknown = [t for t in topics if t not in HOME_TOPICS]
    if unknown:
        warnings.append(f"unknown topic(s) {unknown}; known: {HOME_TOPICS}")
    topics = [t for t in topics if t in HOME_TOPICS]
    home["topics"] = topics
    wh = home.get("wood_heater")
    if isinstance(wh, dict) and not is_unknown(wh.get("kind")):
        wh["kind"] = _key(wh["kind"])
    co = home.get("coatings")
    if isinstance(co, dict) and isinstance(co.get("substrates"), list):
        co["substrates"] = [_key(s) for s in co["substrates"]]
    burning = home.get("burning")
    if isinstance(burning, dict) and isinstance(burning.get("what"), list):
        burning["what"] = [_key(s) for s in burning["what"]]
    return home, topics, warnings


# ------------------------------------------------------------------ packs, fixtures, templates

def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_pack(name: str) -> Dict[str, Any]:
    path = PACKS / f"{name}.json"
    try:
        pack = _read_json(path)
    except (OSError, ValueError) as err:
        raise UsairError("internal", f"Rule pack {name!r} could not be read: {err}") from err
    problems = validate_pack(pack)
    if problems:
        raise UsairError("internal", f"Rule pack {name!r} is invalid: {problems[:5]}")
    return pack


def validate_pack(pack: Dict[str, Any]) -> List[str]:
    problems: List[str] = []
    for key in ("name", "version", "verdicts", "fallback_verdict", "entries"):
        if key not in pack:
            problems.append(f"pack: missing {key}")
    if problems:
        return problems
    verdicts = set(pack["verdicts"])
    if pack["fallback_verdict"] not in verdicts:
        problems.append("pack: fallback_verdict not in verdicts")
    ids = set()
    for i, e in enumerate(pack["entries"]):
        where = f"entries[{i}]({e.get('id')})"
        for key in ("id", "title", "citation", "rules", "verified"):
            if key not in e:
                problems.append(f"{where}: missing {key}")
        if e.get("id") in ids:
            problems.append(f"{where}: duplicate id")
        ids.add(e.get("id"))
        if e.get("scope") == "unit" and not e.get("unit_types"):
            problems.append(f"{where}: unit scope needs unit_types")
        if e.get("relevant_if"):
            problems += validate_condition(e["relevant_if"], f"{where}.relevant_if")
        for j, rule in enumerate(e.get("rules") or []):
            if rule.get("verdict") not in verdicts:
                problems.append(f"{where}.rules[{j}]: verdict {rule.get('verdict')!r} not in pack verdicts")
            problems += validate_condition(rule.get("when"), f"{where}.rules[{j}].when")
        dv = (e.get("default") or {}).get("verdict")
        if dv is not None and dv not in verdicts:
            problems.append(f"{where}.default: verdict {dv!r} not in pack verdicts")
        if pack.get("never_skip_without_evidence") and dv == pack.get("skip_verdict"):
            problems.append(f"{where}.default: may not default to {dv!r}")
    return problems


def fixture_names() -> List[str]:
    return sorted(p.stem for p in FIXTURES.glob("*.json"))


def load_fixture(name: str) -> Dict[str, Any]:
    key = _key(name)
    path = FIXTURES / f"{key}.json"
    if not path.is_file():
        raise UsairError("unknown_fixture", f"No fixture named {name!r}.", f"Use one of: {', '.join(fixture_names())}.")
    return _read_json(path)


def template_names() -> List[str]:
    return sorted(p.stem for p in TEMPLATES.glob("*.json"))


def load_template(name: str) -> Dict[str, Any]:
    path = TEMPLATES / f"{_key(name)}.json"
    if not path.is_file():
        raise UsairError("unknown_template", f"No template named {name!r}.", f"Use one of: {', '.join(template_names())}.")
    return _read_json(path)


def resolve_profile(args: Dict[str, Any], kind: str) -> Tuple[Dict[str, Any], Optional[str]]:
    profile = args.get("profile")
    fixture = args.get("fixture")
    if isinstance(profile, str) and profile.strip():
        try:
            profile = json.loads(profile)
        except ValueError as err:
            raise UsairError("bad_argument", f"profile is not valid JSON: {err}", f"Pass an object; see /usair template {kind}.") from err
    if profile:
        return profile, None
    if fixture:
        data = load_fixture(str(fixture))
        if data.get("layer") != kind:
            raise UsairError("bad_argument", f"Fixture {fixture!r} is a {data.get('layer')} profile, not {kind}.",
                             f"Use a {kind} fixture: {', '.join(n for n in fixture_names() if load_fixture(n).get('layer') == kind)}.")
        return data["profile"], str(fixture)
    raise UsairError("bad_argument", "Pass a profile object or a fixture name.",
                     f"Try fixture='{'cement_plant_demo' if kind == 'plant' else 'homeowner_demo'}', or /usair template {kind}.")


__all__ = ["PackError", "normalize_plant", "normalize_home", "load_pack", "validate_pack", "load_fixture",
           "fixture_names", "load_template", "template_names", "resolve_profile", "HOME_TOPICS"]
