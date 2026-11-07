#!/usr/bin/env python3
"""Check bundled verify_phrases against live eCFR, and the part 52 state map against the live outline.

This talks to www.ecfr.gov (one request per second). It is not part of the offline tests.
Pack entries ship with verified=false until this script confirms them.

    python scripts/verify_packs.py            # report only
    python scripts/verify_packs.py --mark     # set verified true/false from this run
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Subpart A is general provisions. Subpart EEE is a general approval subpart, not a state.
NON_STATE_SUBPARTS = {"A", "EEE"}


def load_package():
    spec = importlib.util.spec_from_file_location(
        "us_air_rules", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["us_air_rules"] = pkg
    spec.loader.exec_module(pkg)
    return pkg


def main() -> int:
    parser = argparse.ArgumentParser(description="Confirm us-air-rules pack phrases against eCFR.")
    parser.add_argument("--mark", action="store_true", help="Write verified true/false back into the pack files.")
    args = parser.parse_args()
    pkg = load_package()
    from us_air_rules.profiles import load_pack
    from us_air_rules.sip import STATES
    from us_air_rules.tools import Tools

    tools = Tools()
    failures = 0
    marks = {}
    for name in ("plant", "home"):
        pack = load_pack(name)
        print(f"== {name} pack {pack['version']}")
        for entry in pack["entries"]:
            phrases = entry.get("verify_phrases") or []
            sections = []
            applicability = (entry.get("ecfr") or {}).get("applicability")
            if applicability:
                sections.append(applicability)
            sections += entry.get("verify_sections") or []
            if not phrases:
                print(f"  {entry['id']:16} no phrases (stays unverified)")
                marks[(name, entry["id"])] = False
                continue
            if not sections:
                print(f"  {entry['id']:16} FAIL phrases set but no section to read")
                marks[(name, entry["id"])] = False
                failures += 1
                continue
            blob = []
            truncated = False
            ok = True
            for section in sections:
                got = tools.cfr_get({"citation": f"40 CFR {section}", "max_chars": 30000})
                if not got.get("ok"):
                    print(f"  {entry['id']:16} FAIL could not read {section}: {got.get('error')}")
                    ok = False
                    break
                blob.append(got.get("text") or "")
                truncated = truncated or bool(got.get("truncated"))
            if not ok:
                marks[(name, entry["id"])] = False
                failures += 1
                continue
            text = "\n".join(blob)
            missing = [p for p in phrases if p not in text]
            if missing or truncated:
                why = "text truncated at 30000 chars" if truncated and not missing else "missing " + " | ".join(missing)
                print(f"  {entry['id']:16} FAIL {why}")
                marks[(name, entry["id"])] = False
                failures += 1
            else:
                print(f"  {entry['id']:16} ok  ({', '.join(sections)})")
                marks[(name, entry["id"])] = True

    print("== part 52 state map")
    outline = tools.cfr_get({"citation": "40 CFR part 52"})
    if not outline.get("ok"):
        print("  FAIL", outline.get("error"))
        failures += 1
    else:
        live = {s["subpart"]: s["heading"] for s in outline.get("subparts") or []}
        for code, (sub, name) in STATES.items():
            if live.get(sub) != name:
                print(f"  FAIL {code} subpart {sub}: pack says {name!r}, eCFR says {live.get(sub)!r}")
                failures += 1
        extra = sorted(set(live) - {sub for sub, _name in STATES.values()} - NON_STATE_SUBPARTS)
        if extra:
            print("  FAIL subparts not in the map:", ", ".join(extra))
            failures += 1
        if failures == 0:
            print(f"  ok  {len(STATES)} states and territories")

    if args.mark:
        for name in ("plant", "home"):
            path = ROOT / "data" / "packs" / f"{name}.json"
            pack = json.loads(path.read_text(encoding="utf-8"))
            for entry in pack["entries"]:
                entry["verified"] = bool(marks.get((name, entry["id"])))
            path.write_text(json.dumps(pack, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("wrote verified flags")
    print("failures:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
