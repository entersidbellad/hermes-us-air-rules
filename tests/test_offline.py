"""Offline tests for us-air-rules. No network: HTTP goes through a fake transport.

Run from the plugin directory:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import datetime as dt
import gzip
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "us_air_rules", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
pkg = importlib.util.module_from_spec(_spec)
sys.modules["us_air_rules"] = pkg
_spec.loader.exec_module(pkg)

from us_air_rules import citations, client, engine, fedreg, profiles, schemas, tools  # noqa: E402
from us_air_rules.client import Cache, Http, Response, Store, UsairError  # noqa: E402

TITLES = {
    "titles": [{
        "number": 40,
        "up_to_date_as_of": "2026-11-04",
        "latest_amended_on": "2026-11-03",
        "latest_issue_date": "2026-11-03",
    }],
}

SECTION_XML = """<?xml version="1.0"?>
<DIV8 N="63.1340" TYPE="SECTION">
<HEAD>§ 63.1340 What parts of my plant does this subpart cover?</HEAD>
<P>(a) The provisions of this subpart apply to each new and existing portland cement plant which is a major source or an area source as defined in § 63.2.</P>
<P>(b) The affected sources subject to this subpart are:</P>
<P>(1) Each kiln including alkali bypasses and inline coal mills.</P>
<CITA TYPE="N">[75 FR 55051, Sept. 9, 2010]</CITA>
</DIV8>
"""

SUBPART_XML = """<?xml version="1.0"?>
<DIV6 N="F" TYPE="SUBPART">
<HEAD>Subpart F—Standards of Performance for Portland Cement Plants</HEAD>
<DIV8 N="60.60" TYPE="SECTION"><HEAD>§ 60.60 Applicability and designation of affected facility.</HEAD>
<P>(a) Kiln, clinker cooler, raw mill system, finish mill system.</P>
<P>(b) Any facility that commences construction or modification after August 17, 1971, is subject to the requirements of this subpart.</P>
</DIV8>
<DIV8 N="60.62" TYPE="SECTION"><HEAD>§ 60.62 Standards.</HEAD>
<P>(a) Particulate matter limit. FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER FILLER.</P>
</DIV8>
</DIV6>
"""

STRUCTURE = {
    "type": "title", "identifier": "40",
    "children": [{
        "type": "part", "identifier": "98", "label_description": "Mandatory Greenhouse Gas Reporting",
        "children": [
            {"type": "subpart", "identifier": "H", "label_description": "Cement Production", "reserved": False,
             "children": [{"type": "section", "identifier": "98.80"}]},
            {"type": "subpart", "identifier": "B", "label_description": "Subpart B [Reserved]", "reserved": True,
             "children": []},
        ],
    }],
}

SEARCH = {
    "results": [
        {"starts_on": "2016-09-08", "ends_on": "2018-07-24", "removed": False, "reserved": False,
         "hierarchy": {"title": "40", "part": "63", "subpart": "LLL", "section": "63.1343"},
         "headings": {"section": "Old heading", "subpart": "Subpart LLL"},
         "full_text_excerpt": "old text", "score": 10},
        {"starts_on": "2018-07-25", "ends_on": None, "removed": False, "reserved": False,
         "hierarchy": {"title": "40", "part": "63", "subpart": "LLL", "section": "63.1343"},
         "headings": {"section": "§ 63.1343 What standards apply?", "subpart": "Subpart LLL"},
         "full_text_excerpt": "open <strong>clinker</strong> storage pile", "score": 40},
        {"starts_on": "2020-01-01", "ends_on": "2024-01-01", "removed": False,
         "hierarchy": {"part": "63", "section": "63.9999"},
         "headings": {"section": "Gone"}, "full_text_excerpt": "ended"},
    ],
}

SIP_SEARCH = {
    "results": [{
        "starts_on": "2020-01-01", "ends_on": None, "removed": False,
        "hierarchy": {"part": "52", "subpart": "SS", "section": "52.2270"},
        "headings": {"section": "Identification of plan.", "subpart": "Texas"},
        "full_text_excerpt": "Outdoor <strong>burning</strong>",
    }],
}

FR_DOCS = {
    "count": 1,
    "results": [{
        "title": "Test rule about portland cement",
        "document_number": "2026-00001",
        "type": "Proposed Rule",
        "action": "Proposed rule.",
        "publication_date": "2026-09-01",
        "effective_on": None,
        "comments_close_on": "2026-11-12",
        "html_url": "https://www.federalregister.gov/documents/2026/09/01/2026-00001/test",
        "citation": "91 FR 1",
        "docket_ids": ["EPA-HQ-OAR-2099-0001"],
        "regulation_id_numbers": [],
        "cfr_references": [{"title": 40, "part": "63"}, {"title": 40, "part": "60"}],
        "abstract": "A short abstract about portland cement manufacturing that is long enough to keep.",
    }],
}


def _json(body) -> bytes:
    return json.dumps(body).encode()


class Router:
    def __init__(self, routes):
        self.routes = list(routes)
        self.urls = []
        self.hosts = []

    def __call__(self, url, headers):
        self.urls.append(url)
        host = url.split("/")[2]
        self.hosts.append(host)
        if "Accept-Encoding" not in headers:
            raise AssertionError("gzip accept-encoding missing")
        for needle, status, body, hdrs in self.routes:
            if needle in url:
                raw = body if isinstance(body, bytes) else body.encode()
                return Response(status, raw, dict(hdrs or {}), url)
        return Response(404, b"no fixture", {}, url)


def make_tools(routes, root: Path) -> tools.Tools:
    http = Http(Router(routes), min_interval=0)
    return tools.Tools(Store(http=http, cache=Cache(root)))


def standard_routes():
    return [
        ("/titles.json", 200, _json(TITLES), {}),
        ("section=63.1340", 200, SECTION_XML, {}),
        ("subpart=F", 200, SUBPART_XML, {}),
        ("structure/", 200, _json(STRUCTURE), {}),
        ("/api/search/", 200, _json(SEARCH), {}),
        ("/documents.json", 200, _json(FR_DOCS), {}),
    ]


class Citations(unittest.TestCase):
    def test_section_forms(self):
        for text in ("40 CFR 63.1340", "40 C.F.R. § 63.1340", "§ 63.1340", "63.1340"):
            got = citations.parse_citation(text)
            self.assertEqual(got["section"], "63.1340", text)
            self.assertEqual(got["part"], "63")

    def test_paragraph(self):
        got = citations.parse_citation("40 C.F.R. § 60.62(a)(1)")
        self.assertEqual(got["section"], "60.62")
        self.assertEqual(got["paragraph"], "(a)(1)")

    def test_subpart_forms(self):
        for text, sub in (("40 CFR part 63 subpart LLL", "LLL"), ("subpart ZZZZ of part 63", "ZZZZ"),
                          ("63-LLL", "LLL"), ("NSPS subpart IIII", "IIII"), ("NESHAP LLL", "LLL"),
                          ("GHGRP subpart H", "H")):
            got = citations.parse_citation(text)
            self.assertEqual(got["subpart"], sub, text)

    def test_subpart_case(self):
        self.assertEqual(citations.norm_subpart("dc"), "Dc")
        self.assertEqual(citations.norm_subpart("lll"), "LLL")
        self.assertEqual(citations.norm_subpart("ooooa"), "OOOOa")
        self.assertEqual(citations.parse_citation("part 60 subpart Dc")["subpart"], "Dc")

    def test_part_only(self):
        self.assertEqual(citations.parse_citation("40 CFR part 98")["part"], "98")
        self.assertIsNone(citations.parse_citation("part 98")["section"])

    def test_other_title_rejected(self):
        with self.assertRaises(UsairError) as caught:
            citations.parse_citation("42 CFR 84.1")
        self.assertEqual(caught.exception.kind, "not_covered")

    def test_empty_and_garbage(self):
        with self.assertRaises(UsairError):
            citations.parse_citation("   ")
        with self.assertRaises(UsairError):
            citations.parse_citation("hello there")

    def test_links(self):
        c = citations.parse_citation("40 CFR 63.1340")
        self.assertEqual(citations.ecfr_url(c), "https://www.ecfr.gov/current/title-40/section-63.1340")
        self.assertIn("/on/2026-11-04/", citations.ecfr_url(c, "2026-11-04"))


class Engine(unittest.TestCase):
    def test_missing_fact_is_unknown_not_false(self):
        result, missing = engine.evaluate({"field": "unit.hp", "op": "gte", "value": 500}, {"unit": {}})
        self.assertIsNone(result)
        self.assertEqual(missing, {"unit.hp"})

    def test_flagged_missing_is_false(self):
        result, missing = engine.evaluate({"field": "unit.test_cell", "op": "flagged"}, {"unit": {}})
        self.assertIs(result, False)
        self.assertEqual(missing, set())

    def test_year_straddling_a_threshold_is_unknown(self):
        result, _ = engine.evaluate(
            {"field": "unit.nsps_date", "op": "after", "value": "1971-08-17"},
            {"unit": {"nsps_date": "1971"}})
        self.assertIsNone(result)
        later, _ = engine.evaluate(
            {"field": "unit.nsps_date", "op": "after", "value": "1971-08-17"},
            {"unit": {"nsps_date": "1972"}})
        self.assertIs(later, True)

    def test_all_fails_closed_on_unknown(self):
        cond = {"all": [{"field": "unit.a", "op": "is_true"}, {"field": "unit.b", "op": "is_true"}]}
        result, missing = engine.evaluate(cond, {"unit": {"a": True}})
        self.assertIsNone(result)
        self.assertEqual(missing, {"unit.b"})
        no, _ = engine.evaluate(cond, {"unit": {"a": False}})
        self.assertIs(no, False)

    def test_any_true_short_circuit(self):
        cond = {"any": [{"field": "unit.a", "op": "is_true"}, {"field": "unit.b", "op": "is_true"}]}
        result, missing = engine.evaluate(cond, {"unit": {"a": True}})
        self.assertIs(result, True)
        self.assertEqual(missing, set())

    def test_pack_default_cannot_be_skip(self):
        pack = profiles.load_pack("plant")
        self.assertEqual(profiles.validate_pack(pack), [])
        self.assertTrue(pack["never_skip_without_evidence"])
        for entry in pack["entries"]:
            self.assertNotEqual((entry.get("default") or {}).get("verdict"), "skip", entry["id"])
            self.assertIsInstance(entry["verified"], bool)
        self.assertTrue(next(e for e in pack["entries"] if e["id"] == "63-LLL")["verified"])

    def test_home_pack_valid(self):
        pack = profiles.load_pack("home")
        self.assertEqual(profiles.validate_pack(pack), [])
        topics = {e["topic"] for e in pack["entries"]}
        self.assertEqual(topics, set(profiles.HOME_TOPICS))


class Profiles(unittest.TestCase):
    def test_cement_fixture_is_fictional_and_normalized(self):
        raw = (ROOT / "data" / "fixtures" / "cement_plant_demo.json").read_text()
        for banned in ("Holcim", "Carmeuse", "Hatch", "Lafarge"):
            self.assertNotIn(banned, raw)
        data = profiles.load_fixture("cement_plant_demo")
        self.assertTrue(data["fictional"])
        facility, units, warnings = profiles.normalize_plant(data["profile"])
        self.assertEqual(warnings, [])
        self.assertIs(facility["is_cement_plant"], True)
        self.assertIs(facility["is_lime_plant"], False)
        gen = next(u for u in units if u["id"] == "EG1")
        self.assertEqual(gen["type"], "stationary_rice")
        self.assertEqual(gen["ignition"], "CI")
        self.assertEqual(gen["use"], "emergency")
        kiln = next(u for u in units if u["id"] == "K1")
        self.assertEqual(kiln["nsps_date"], "2008-10-01")

    def test_kw_becomes_hp_and_unknown_fixture(self):
        facility, units, _ = profiles.normalize_plant(
            {"naics": "327310", "units": [{"type": "generator", "fuel": "natural_gas", "kw": 100}]})
        self.assertEqual(units[0]["ignition"], "SI")
        self.assertAlmostEqual(units[0]["hp"], 134.1)
        with self.assertRaises(UsairError) as caught:
            profiles.load_fixture("holcim")
        self.assertEqual(caught.exception.kind, "unknown_fixture")
        self.assertIsNotNone(facility)

    def test_home_topics_inferred(self):
        home, topics, _ = profiles.normalize_home(
            {"role": "hobbyist", "state": "pa", "burning": {"what": ["leaves"]}, "wood_heater": {"kind": "Wood Stove"}})
        self.assertEqual(home["role"], "homeowner")
        self.assertEqual(home["wood_heater"]["kind"], "wood_stove")
        self.assertEqual(topics, ["outdoor_burning", "wood_heating"])


class HttpAndCache(unittest.TestCase):
    def test_refuses_other_hosts_and_schemes(self):
        http = Http(lambda url, headers: Response(200, b"{}", {}, url), min_interval=0)
        with self.assertRaises(UsairError):
            http.get("https://example.com/api")
        with self.assertRaises(UsairError):
            http.get("ftp://www.ecfr.gov/x")

    def test_upgrades_http_and_blocks_bad_redirect(self):
        seen = []

        def transport(url, headers):
            seen.append(url)
            return Response(302, b"", {"location": "https://evil.example/steal"}, url)

        http = Http(transport, min_interval=0)
        with self.assertRaises(UsairError) as caught:
            http.get("http://www.ecfr.gov/api/versioner/v1/titles.json")
        self.assertEqual(caught.exception.kind, "network")
        self.assertTrue(seen[0].startswith("https://www.ecfr.gov/"))

    def test_gunzip_and_size_cap(self):
        payload = b'{"ok": true, "n": 1}'

        def transport(url, headers):
            return Response(200, gzip.compress(payload), {"content-encoding": "gzip"}, url)

        http = Http(transport, min_interval=0)
        self.assertEqual(http.get("https://www.ecfr.gov/x").body, payload)
        old = client.MAX_BYTES
        try:
            client.MAX_BYTES = 8
            with self.assertRaises(UsairError) as caught:
                http.get("https://www.ecfr.gov/x")
            self.assertEqual(caught.exception.kind, "too_large")
        finally:
            client.MAX_BYTES = old

    def test_stale_cache_when_the_source_is_down(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        cache = Cache(root)
        cache.write("ecfr-titles", TITLES["titles"][0], {"fetched_at": 0, "url": "https://www.ecfr.gov/api/versioner/v1/titles.json"})

        def down(url, headers):
            raise urllib.error.URLError("down")

        store = Store(http=Http(down, min_interval=0), cache=cache)
        got = tools.Tools(store).ecfr.title40_status()
        self.assertEqual(got.state, "stale")
        self.assertEqual(got.data["up_to_date_as_of"], "2026-11-04")

    def test_cache_prune_keeps_newest(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        cache = Cache(root)
        cache.write("old-key", {"n": 1}, {"fetched_at": 1})
        cache.write("new-key", {"n": 2}, {"fetched_at": 2})
        # Shrink by truncating nothing: prune to a tiny budget drops the older file first.
        removed = cache.prune(1, keep="new-key")
        self.assertGreaterEqual(removed, 1)
        self.assertIsNotNone(cache.read("new-key")[0])


class ToolsLiveShaped(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.tools = make_tools(standard_routes(), self.root)
        self.handlers = tools.make_handlers(self.tools)

    def test_cfr_get_section_paragraph_and_source(self):
        out = self.tools.cfr_get({"citation": "40 CFR 63.1340(a)"})
        self.assertTrue(out["ok"])
        self.assertIn("portland cement plant", out["text"])
        self.assertNotIn("(b)", out["text"])
        self.assertEqual(out["paragraph"], "(a)")
        self.assertEqual(out["ecfr_date"], "2026-11-04")
        self.assertTrue(out["source"])
        self.assertIn("disclaimer", out)
        self.assertIn("75 FR 55051", out["amended"])

    def test_cfr_get_subpart_and_part_outline(self):
        sub = self.tools.cfr_get({"citation": "40 CFR part 60 subpart F", "max_chars": 500})
        self.assertTrue(sub["truncated"])
        self.assertEqual(sub["section_count"], 2)
        self.assertIn("next_step", sub)
        part = self.tools.cfr_get({"citation": "40 CFR part 98"})
        ids = [s["subpart"] for s in part["subparts"]]
        self.assertEqual(ids, ["H", "B"])
        self.assertTrue(part["subparts"][1]["reserved"])

    def test_cfr_get_errors_are_json_and_typed(self):
        missing = json.loads(self.handlers["usair_cfr_get"]({"citation": "40 CFR 63.9999"}))
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["error_type"], "not_found")
        self.assertIn("next_step", missing)
        other = json.loads(self.handlers["usair_cfr_get"]({"citation": "21 CFR 101.1"}))
        self.assertEqual(other["error_type"], "not_covered")
        bad_date = json.loads(self.handlers["usair_cfr_get"]({"citation": "40 CFR 63.1340", "date": "yesterday"}))
        self.assertEqual(bad_date["error_type"], "bad_argument")

    def test_cfr_search_keeps_the_version_in_force(self):
        out = self.tools.cfr_search({"query": "clinker storage", "part": "63", "max_results": 5})
        self.assertEqual(len(out["results"]), 1)
        self.assertEqual(out["results"][0]["section"], "63.1343")
        self.assertEqual(out["results"][0]["version_starts_on"], "2018-07-25")
        self.assertNotIn("<strong>", out["results"][0]["excerpt"])
        self.assertIn("section-63.1343", out["results"][0]["url"])

    def test_cfr_search_needs_a_query_and_a_part_with_subpart(self):
        empty = json.loads(self.handlers["usair_cfr_search"]({"query": " "}))
        self.assertEqual(empty["error_type"], "bad_argument")
        lone = json.loads(self.handlers["usair_cfr_search"]({"query": "burning", "subpart": "SS"}))
        self.assertEqual(lone["error_type"], "bad_argument")

    def test_fr_recent_normalizes_and_warns(self):
        out = self.tools.fr_recent({"part": "63", "term": '"portland cement"', "since": "2y", "types": ["proposed"]})
        self.assertEqual(out["documents"][0]["document_number"], "2026-00001")
        self.assertEqual(out["documents"][0]["cfr_parts"], ["60", "63"])
        self.assertIn("proposed rule is not law", out["note"])
        self.assertTrue(out["source"][0]["official"])
        bad = json.loads(self.handlers["usair_fr_recent"]({"part": "63", "since": "next week"}))
        self.assertEqual(bad["error_type"], "bad_argument")

    def test_plant_triage_cement_fixture(self):
        out = self.tools.plant_triage({"fixture": "cement_plant_demo"})
        by_id = {f["id"]: f for f in out["findings"]}
        self.assertEqual(by_id["63-LLL"]["verdict"], "applies")
        self.assertEqual(by_id["63-EEE"]["verdict"], "skip")
        self.assertEqual(by_id["60-CCCC"]["verdict"], "check_with_ehs")
        self.assertIn("unit.burns_solid_waste", by_id["60-CCCC"]["missing"])
        self.assertEqual(by_id["60-Y"]["verdict"], "check_with_ehs")
        units = {u["unit_id"]: u["verdict"] for u in by_id["60-F"]["units"]}
        self.assertEqual(units["FM1"], "skip")
        self.assertEqual(units["FM2"], "applies")
        self.assertIn("63-AAAAA", out["not_relevant"])
        self.assertTrue(by_id["63-LLL"]["verified"])
        self.assertEqual(out["unverified_rules"], [])
        self.assertEqual(out["source"][0]["name"].split()[0], "us-air-rules")
        self.assertEqual(self.tools.store.http.requests, 0)

    def test_plant_triage_lime_and_rule_filter(self):
        out = self.tools.plant_triage({"fixture": "lime_plant_demo", "rules": ["60-HH", "63-AAAAA"]})
        by_id = {f["id"]: f for f in out["findings"]}
        self.assertEqual(set(by_id), {"60-HH", "63-AAAAA"})
        self.assertEqual(by_id["63-AAAAA"]["verdict"], "skip")
        hh = {u["unit_id"]: u["verdict"] for u in by_id["60-HH"]["units"]}
        self.assertEqual(hh["LK1"], "skip")
        self.assertEqual(hh["LK2"], "applies")
        bad = json.loads(self.handlers["usair_plant_triage"]({"fixture": "lime_plant_demo", "rules": ["nope"]}))
        self.assertEqual(bad["error_type"], "bad_argument")

    def test_missing_boiler_facts_do_not_skip(self):
        out = self.tools.plant_triage({"profile": {
            "name": "Bare boiler (fictional)", "naics": "327310",
            "units": [{"id": "B", "type": "boiler"}],
        }})
        by_id = {f["id"]: f for f in out["findings"]}
        for rule in ("63-DDDDD", "63-JJJJJJ", "60-Db", "60-Dc"):
            self.assertNotEqual(by_id[rule]["verdict"], "skip", rule)
            self.assertTrue(by_id[rule]["missing"], rule)

    def test_include_fr_attaches_documents(self):
        out = self.tools.plant_triage({"fixture": "cement_plant_demo", "rules": ["63-LLL"], "include_fr": True})
        finding = out["findings"][0]
        self.assertEqual(finding["fr_recent"][0]["type"], "Proposed Rule")
        self.assertTrue(any("Federal Register" in s["name"] for s in out["source"]))
        self.assertGreater(self.tools.store.http.requests, 0)

    def test_home_owner_and_body_shop(self):
        home = self.tools.home_check({"fixture": "homeowner_demo"})
        by_id = {f["id"]: f for f in home["findings"]}
        self.assertEqual(by_id["63-HHHHHH"]["verdict"], "exempt")
        self.assertEqual(by_id["60-AAA"]["verdict"], "applies")
        self.assertEqual(by_id["home-outdoor-burning"]["verdict"], "no_federal_rule")
        self.assertFalse(by_id["home-idling"]["verified"])
        self.assertTrue(by_id["60-AAA"]["verified"])
        self.assertEqual(by_id["home-idling"]["sip"]["state"], "PA")
        shop = self.tools.home_check({"fixture": "body_shop_demo"})
        shop_ids = {f["id"]: f for f in shop["findings"]}
        self.assertEqual(shop_ids["63-HHHHHH"]["verdict"], "applies")
        self.assertEqual(shop_ids["home-outdoor-burning"]["verdict"], "check")
        self.assertNotIn("60-AAA", shop_ids)
        self.assertNotIn("home-idling", shop_ids)

    def test_home_topic_filter_and_unknown_fixture_layer(self):
        out = self.tools.home_check({"fixture": "homeowner_demo", "topics": ["idling"]})
        self.assertEqual([f["id"] for f in out["findings"]], ["home-idling"])
        wrong = json.loads(self.handlers["usair_home_check"]({"fixture": "cement_plant_demo"}))
        self.assertEqual(wrong["error_type"], "bad_argument")

    def test_sip_lookup(self):
        # The default router answers every search with SEARCH (part 63). Swap in the SIP fixture.
        self.tools = make_tools([
            ("/titles.json", 200, _json(TITLES), {}),
            ("/api/search/", 200, _json(SIP_SEARCH), {}),
        ], self.root)
        out = self.tools.sip_lookup({"state": "Texas", "topic": "outdoor burning"})
        self.assertEqual(out["state"]["code"], "TX")
        self.assertEqual(out["citation"], "40 CFR part 52 subpart SS")
        self.assertEqual(out["results"][0]["section"], "52.2270")
        self.assertIn("caveat", out)
        self.assertIn("disclaimer", out)
        bad = json.loads(tools.make_handlers(self.tools)["usair_sip_lookup"]({"state": "Atlantis", "topic": "burning"}))
        self.assertEqual(bad["error_type"], "unknown_state")

    def test_handlers_never_raise(self):
        for name, handler in self.handlers.items():
            for args in (None, "nope", [], {"citation": None, "part": None, "profile": 5}):
                raw = handler(args)
                parsed = json.loads(raw)
                self.assertIn("ok", parsed, name)
                self.assertIsInstance(raw, str)

    def test_second_read_is_cached(self):
        self.tools.cfr_get({"citation": "40 CFR 63.1340"})
        first = self.tools.store.http.requests
        again = self.tools.cfr_get({"citation": "63.1340"})
        self.assertEqual(again["source"][0]["cache"], "cached")
        self.assertEqual(self.tools.store.http.requests, first)


class Registration(unittest.TestCase):
    def test_register_matches_the_manifest(self):
        class Ctx:
            def __init__(self):
                self.tools = []
                self.commands = []
                self.cli = []
                self.skills = []

            def register_tool(self, name, **kwargs):
                self.tools.append(name)
                self.last_schema = kwargs["schema"]

            def register_command(self, name, handler, description=""):
                self.commands.append(name)

            def register_cli_command(self, name, help, setup_fn, handler_fn):
                self.cli.append(name)

            def register_skill(self, name, path, description=""):
                self.skills.append((name, Path(path).is_file()))

            def get_config(self, key, default=None):
                return default

        ctx = Ctx()
        pkg.register(ctx)
        declared = []
        grab = False
        for line in (ROOT / "plugin.yaml").read_text().splitlines():
            if line.startswith("provides_tools:"):
                grab = True
                continue
            if grab:
                if line.startswith("  - "):
                    declared.append(line.split("- ", 1)[1].strip())
                elif line.strip():
                    grab = False
        self.assertEqual(ctx.tools, declared)
        self.assertEqual(ctx.tools, [s["name"] for s in schemas.ALL_SCHEMAS])
        self.assertEqual(ctx.commands, ["usair"])
        self.assertEqual(ctx.cli, ["us-air-rules"])
        self.assertEqual(ctx.skills, [("air-rules", True)])
        self.assertTrue(ctx.last_schema["description"])

    def test_slash_demo_is_offline(self):
        text = tools.slash_usair("demo plant", self._quiet_tools())
        self.assertIn("63-LLL", text)
        self.assertIn("applies", text)
        self.assertIn("Screening aid", text)
        self.assertIn("cement_plant_demo", tools.slash_usair("fixtures"))
        template = json.loads(tools.slash_usair("template home"))
        self.assertEqual(template["layer"], "home")
        self.assertIn("demo", tools.slash_usair("").lower())

    def _quiet_tools(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        return make_tools([], root)

    def test_templates_exist(self):
        self.assertEqual(set(profiles.template_names()), {"home", "plant"})
        plant = profiles.load_template("plant")
        self.assertIn("units", plant["profile"])


class FedRegDates(unittest.TestCase):
    def test_since_and_types(self):
        self.assertEqual(fedreg.resolve_since("2024-01-02"), "2024-01-02")
        self.assertEqual(fedreg.resolve_since("10d", today=dt.date(2026, 11, 6)), "2026-10-27")
        self.assertEqual(fedreg.resolve_types(["rule", "notice"]), ["RULE", "NOTICE"])
        self.assertEqual(fedreg.resolve_types(None), ["RULE", "PRORULE"])
        with self.assertRaises(UsairError):
            fedreg.resolve_types(["poem"])


def _hit(section: str, *, start: str, end: Optional[str]) -> dict:
    return {
        "starts_on": start, "ends_on": end, "removed": False, "reserved": False,
        "hierarchy": {"title": "40", "part": section.split(".")[0], "section": section},
        "headings": {"section": section},
        "full_text_excerpt": "excerpt",
    }


class ReviewRegressions(unittest.TestCase):
    def test_filter_still_evaluates_rules_the_answer_reads(self):
        profile = {
            "name": "Major boiler, no permit, no controls (fictional)",
            "hap_status": "major",
            "title_v_permit": False,
            "uses_control_devices": False,
            "units": [{"id": "B", "type": "boiler", "fuel": "natural_gas", "mmbtu_hr": 50}],
        }
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        tool = make_tools([], root)
        part70 = tool.plant_triage({"profile": profile, "rules": ["70"]})
        self.assertEqual([f["id"] for f in part70["findings"]], ["70"])
        self.assertEqual(part70["findings"][0]["verdict"], "applies")
        self.assertNotIn("63-LLL", part70["not_relevant"])
        cam = tool.plant_triage({"profile": profile, "rules": ["64"]})
        self.assertEqual([f["id"] for f in cam["findings"]], ["64"])
        self.assertEqual(cam["findings"][0]["verdict"], "skip")
        self.assertIn("control device", cam["findings"][0]["why"])

    def test_coal_throughput_is_evidence_and_a_unit_list_is_not_absence(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        tool = make_tools([], root)

        def row(extra):
            profile = {"name": "Boiler house (fictional)", "units": [
                {"id": "B", "type": "boiler", "fuel": "natural_gas", "mmbtu_hr": 40}]}
            profile.update(extra)
            out = tool.plant_triage({"profile": profile, "rules": ["60-Y"]})
            self.assertNotIn("60-Y", out["not_relevant"])
            return out["findings"][0]

        over = row({"coal_processed_tpd": 450})
        self.assertEqual(over["verdict"], "check_with_ehs")
        under = row({"coal_processed_tpd": 50})
        self.assertEqual(under["verdict"], "skip")
        unknown = row({})
        self.assertEqual(unknown["verdict"], "check_with_ehs")
        self.assertIn("facility.handles_coal", unknown["missing"])

    def test_ghg_does_not_skip_or_apply_without_the_cited_facts(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        tool = make_tools([], root)

        def verdict(profile):
            out = tool.plant_triage({"profile": profile, "rules": ["98-C"]})
            return out["findings"][0]

        low_unknown = verdict({
            "name": "No category (fictional)", "ghg_co2e_tpy": 10000,
            "units": [{"id": "B", "type": "boiler", "fuel": "natural_gas", "mmbtu_hr": 10}],
        })
        self.assertEqual(low_unknown["verdict"], "check_with_ehs")
        self.assertNotEqual(low_unknown["verdict"], "skip")

        low_other = verdict({
            "name": "Not cement or lime (fictional)", "naics": "221112", "ghg_co2e_tpy": 10000,
            "units": [{"id": "B", "type": "boiler", "fuel": "natural_gas", "mmbtu_hr": 10}],
        })
        self.assertEqual(low_other["verdict"], "check_with_ehs")
        self.assertIn("not a skip", low_other["why"])

        high_small_boiler = verdict({
            "name": "Small boiler (fictional)", "naics": "221112", "ghg_co2e_tpy": 30000,
            "units": [{"id": "B", "type": "boiler", "fuel": "natural_gas", "mmbtu_hr": 12}],
        })
        self.assertEqual(high_small_boiler["verdict"], "check_with_ehs")

        high_missing_heat = verdict({
            "name": "Unrated boiler (fictional)", "naics": "221112", "ghg_co2e_tpy": 30000,
            "units": [{"id": "B", "type": "boiler", "fuel": "natural_gas"}],
        })
        self.assertEqual(high_missing_heat["verdict"], "check_with_ehs")
        self.assertIn("facility.combustion_heat_at_least_30", high_missing_heat["missing"])

        high_enough = verdict({
            "name": "Large boiler (fictional)", "naics": "221112", "ghg_co2e_tpy": 30000,
            "units": [{"id": "B", "type": "boiler", "fuel": "natural_gas", "mmbtu_hr": 40}],
        })
        self.assertEqual(high_enough["verdict"], "applies")
        self.assertIn("30", high_enough["why"])

        cement = verdict({
            "name": "Cement (fictional)", "naics": "327310", "ghg_co2e_tpy": 10000,
            "units": [{"id": "K", "type": "kiln", "product": "cement"}],
        })
        self.assertEqual(cement["verdict"], "applies")

    def test_known_modification_survives_a_missing_or_partial_construction_date(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        tool = make_tools([], root)

        def subpart_f(unit):
            out = tool.plant_triage({"profile": {
                "name": "Kiln (fictional)", "naics": "327310", "units": [unit]}, "rules": ["60-F"]})
            return out["findings"][0]["verdict"]

        self.assertEqual(subpart_f({
            "id": "K", "type": "kiln", "product": "cement", "modified_date": "1971-09-01",
        }), "applies")
        self.assertEqual(subpart_f({
            "id": "K", "type": "kiln", "product": "cement",
            "construction_date": "1971", "modified_date": "1971-09-01",
        }), "applies")
        _fac, units, _warnings = profiles.normalize_plant({"naics": "327310", "units": [{
            "id": "K", "type": "kiln", "product": "cement",
            "construction_date": "1971", "modified_date": "1971-09-01",
        }]})
        self.assertEqual(units[0]["nsps_date"], {"start": "1971-09-01", "end": "1971-12-31"})
        self.assertEqual(subpart_f({
            "id": "K", "type": "kiln", "product": "cement", "construction_date": "1971",
        }), "check_with_ehs")

    def test_stale_title_date_is_not_hidden_by_a_pinned_section(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        cache = Cache(root)
        cache.write("ecfr-titles", {
            "up_to_date_as_of": "2020-01-15",
            "latest_amended_on": "2020-01-14",
            "latest_issue_date": "2020-01-14",
        }, {"fetched_at": 0, "url": "https://www.ecfr.gov/api/versioner/v1/titles.json"})

        def transport(url, headers):
            if "titles.json" in url:
                raise urllib.error.URLError("down")
            if "section=63.1340" in url and "/2020-01-15/" in url:
                return Response(200, SECTION_XML.encode(), {}, url)
            return Response(404, b"no", {}, url)

        tool = tools.Tools(Store(http=Http(transport, min_interval=0), cache=cache))
        out = tool.cfr_get({"citation": "40 CFR 63.1340"})
        self.assertEqual(out["ecfr_date"], "2020-01-15")
        self.assertTrue(out["stale"])
        self.assertEqual(out["source"][0]["cache"], "fresh")
        title = next(s for s in out["source"] if s["name"] == "eCFR Title 40 currency date")
        self.assertTrue(title["stale"])
        self.assertIn("last cached copy", title["note"])

    def test_search_reads_a_later_page_when_the_first_is_only_old_versions(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        urls = []

        def page(url):
            if "page=2" in url:
                return {"results": [_hit("63.1343", start="2018-07-25", end=None)],
                        "meta": {"current_page": 2, "total_pages": 2}}
            return {"results": [_hit(f"63.{i}", start="2010-01-01", end="2011-01-01") for i in range(20)],
                    "meta": {"current_page": 1, "total_pages": 2}}

        def transport(url, headers):
            urls.append(url)
            if "titles.json" in url:
                return Response(200, _json(TITLES), {}, url)
            if "/api/search/" in url:
                return Response(200, _json(page(url)), {}, url)
            return Response(404, b"no", {}, url)

        tool = tools.Tools(Store(http=Http(transport, min_interval=0), cache=Cache(root)))
        out = tool.cfr_search({"query": "clinker", "part": "63", "max_results": 5})
        self.assertEqual(out["results"][0]["section"], "63.1343")
        self.assertTrue(out["search_complete"])
        self.assertNotEqual(out.get("message"), "No current Title 40 section matched.")
        self.assertTrue(any("page=2" in url for url in urls))

        sip = tool.sip_lookup({"state": "TX", "topic": "zz kiln piles"})
        self.assertEqual(sip["results"][0]["section"], "63.1343")
        self.assertNotIn("No current part 52 text", sip.get("message", ""))

    def test_search_page_cap_is_not_reported_as_no_match(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        searches = []

        def transport(url, headers):
            if "titles.json" in url:
                return Response(200, _json(TITLES), {}, url)
            if "/api/search/" in url:
                searches.append(url)
                page_no = 1
                marker = "page="
                if marker in url:
                    page_no = int(url.split(marker, 1)[1].split("&", 1)[0])
                body = {
                    "results": [_hit(f"63.{page_no}{i:02d}", start="2010-01-01", end="2011-01-01") for i in range(20)],
                    "meta": {"current_page": page_no, "total_pages": 99},
                }
                return Response(200, _json(body), {}, url)
            return Response(404, b"no", {}, url)

        tool = tools.Tools(Store(http=Http(transport, min_interval=0), cache=Cache(root)))
        out = tool.cfr_search({"query": "expired only", "part": "63"})
        self.assertEqual(out["results"], [])
        self.assertFalse(out["search_complete"])
        self.assertNotIn("No current Title 40 section matched.", out["message"])
        self.assertEqual(len(searches), 5)
        sip = tool.sip_lookup({"state": "TX", "topic": "zz nothing current"})
        self.assertEqual(sip["results"], [])
        self.assertFalse(sip["search_complete"])
        self.assertNotIn("No current part 52 text", sip["message"])

    def test_fr_cap_counts_failures_and_keeps_stale_or_empty_sources(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        calls = {"n": 0}

        def down(url, headers):
            calls["n"] += 1
            raise urllib.error.URLError("down")

        store = Store(http=Http(down, min_interval=0), cache=Cache(root),
                      settings=lambda key, default=None: 1 if key == "fr_on_triage_max" else default)
        out = tools.Tools(store).plant_triage({"fixture": "cement_plant_demo", "include_fr": True})
        self.assertEqual(calls["n"], 1)
        self.assertTrue(any("not checked" in note for note in out["fr_notes"]))
        self.assertTrue(any(note.startswith("Stopped after 1") for note in out["fr_notes"]))

        since = fedreg.resolve_since("2y")
        key = f'fr-63-"portland cement manufacturing"-{since}-RULE-PRORULE-3'
        cache = Cache(root)
        cache.write(key, {"count": 0, "results": []},
                    {"fetched_at": 1, "url": "https://www.federalregister.gov/api/v1/documents.json"})
        stale = tools.Tools(Store(http=Http(down, min_interval=0), cache=cache)).plant_triage(
            {"fixture": "cement_plant_demo", "rules": ["63-LLL"], "include_fr": True})
        finding = stale["findings"][0]
        self.assertEqual(finding["fr_recent"], [])
        self.assertTrue(finding["fr_stale"])
        self.assertTrue(finding["fr_source"][0]["stale"])
        self.assertTrue(stale["stale"])
        self.assertTrue(any(s.get("stale") for s in stale["source"]))

        cache.write(key, FR_DOCS, {"fetched_at": 1, "url": "https://www.federalregister.gov/api/v1/documents.json"})
        with_docs = tools.Tools(Store(http=Http(down, min_interval=0), cache=cache)).plant_triage(
            {"fixture": "cement_plant_demo", "rules": ["63-LLL"], "include_fr": True})
        self.assertEqual(with_docs["findings"][0]["fr_recent"][0]["document_number"], "2026-00001")
        self.assertTrue(with_docs["stale"])

    def test_wood_certificate_follows_the_manufacture_date_rules(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        tool = make_tools([], root)

        def heater(fields):
            out = tool.home_check({"profile": {
                "role": "homeowner", "state": "TX", "topics": ["wood_heating"],
                "wood_heater": dict({"kind": "wood_stove"}, **fields),
            }})
            return next(f for f in out["findings"] if f["id"] == "60-AAA")

        certified_old = heater({"manufacture_date": "1995-06-01", "epa_certified": True})
        self.assertEqual(certified_old["verdict"], "applies")
        self.assertIn("60.530(a)(1)", certified_old["why"])
        post_2015 = heater({"manufacture_date": "2016-01-01"})
        self.assertEqual(post_2015["verdict"], "applies")
        certificate_only = heater({"epa_certified": True})
        self.assertEqual(certificate_only["verdict"], "check")
        self.assertIn("home.wood_heater.manufacture_date", certificate_only["missing"])
        antique = heater({"manufacture_date": "1980-01-01", "epa_certified": False})
        self.assertEqual(antique["verdict"], "no_federal_rule")


if __name__ == "__main__":
    unittest.main()
