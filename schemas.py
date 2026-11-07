"""Tool schemas: what the model reads to decide which tool to call.

The first sentence of each description is the short line Hermes shows in Tool Search,
so it names the domain and the action.
"""

CFR_GET = {
    "name": "usair_cfr_get",
    "description": (
        "US EPA rules: read one Title 40 CFR section, subpart or part from eCFR. "
        "Read-only. Returns the current text (or the text on a date you give) of a citation such as "
        "'40 CFR 63.1340', '40 CFR 60.62(a)', '40 CFR part 60 subpart F' or 'NSPS subpart IIII', "
        "with the eCFR link and the date of the text. A whole part comes back as a list of subparts, "
        "not the full text. eCFR is an editorial compilation, not the official legal edition. "
        "If you did not call this tool, do not quote regulatory text from memory."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "citation": {
                "type": "string",
                "description": "Title 40 citation: '40 CFR 63.1340', '§ 60.62(a)(1)', 'part 63 subpart LLL', '63-LLL', 'GHGRP subpart H'.",
            },
            "date": {
                "type": "string",
                "description": "Optional text date, YYYY-MM-DD. Leave empty for the current eCFR text.",
            },
            "max_chars": {
                "type": "integer", "minimum": 500, "maximum": 30000, "default": 6000,
                "description": "Cut the returned text after this many characters (default 6000).",
            },
        },
        "required": ["citation"],
    },
}

CFR_SEARCH = {
    "name": "usair_cfr_search",
    "description": (
        "US EPA rules: search current Title 40 text on eCFR. "
        "Read-only. Finds sections whose current text matches the query, optionally inside one part "
        "or subpart (example: query 'open burning', part '52', subpart 'SS'). Historical versions of "
        "the same section are folded into the version in force. Returns headings, a short excerpt and "
        "an eCFR link. Read a hit with usair_cfr_get before quoting it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Words to find. Quotes keep a phrase together."},
            "part": {"type": "string", "description": "Optional Title 40 part, e.g. '63' or 'part 60'."},
            "subpart": {"type": "string", "description": "Optional subpart, e.g. 'LLL' or 'Dc'."},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
        },
        "required": ["query"],
    },
}

FR_RECENT = {
    "name": "usair_fr_recent",
    "description": (
        "US EPA rules: recent Federal Register documents for one Title 40 part. "
        "Read-only. Lists EPA rules, proposed rules and notices published since a date that affect "
        "one part (example: part '63', term '\"portland cement manufacturing\"', since '2y'). "
        "A proposed rule is not law. Full-text search matches loosely: read the abstract, and confirm "
        "the current text with usair_cfr_get."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "part": {"type": "string", "description": "Title 40 part number, e.g. '63'."},
            "term": {"type": "string", "description": "Optional full-text words. Use quotes for a phrase."},
            "since": {"type": "string", "description": "YYYY-MM-DD, or a window like '90d', '6m', '2y'. Default: one year ago."},
            "types": {
                "type": "array",
                "items": {"type": "string", "enum": ["rule", "proposed", "notice"]},
                "description": "Document types (default: rule and proposed).",
            },
            "max_results": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
        },
        "required": ["part"],
    },
}

PLANT_TRIAGE = {
    "name": "usair_plant_triage",
    "description": (
        "US air rules: does this federal rule apply to this plant? "
        "Screen a cement, lime or other industrial profile against the bundled Title 40 applicability "
        "pack (NESHAP, NSPS, GHG reporting, title V, CAM). Each rule comes back applies, check_with_ehs "
        "or skip, with the citation and the facts that were missing. A missing fact never becomes skip. "
        "This is a screening aid, not a compliance determination. Pass a profile object or a fixture name "
        "('cement_plant_demo', 'lime_plant_demo'). Set include_fr true to attach recent Federal Register "
        "documents for the rules that apply."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "profile": {"type": "object", "description": "Facility profile. See /usair template plant."},
            "fixture": {"type": "string", "description": "Fictional demo profile name, used when profile is omitted."},
            "rules": {
                "type": "array", "items": {"type": "string"},
                "description": "Optional pack entry ids to run, e.g. ['63-LLL', '60-F']. Default: all.",
            },
            "include_fr": {
                "type": "boolean", "default": False,
                "description": "Also fetch recent Federal Register documents for rules that apply or need a check.",
            },
        },
        "required": [],
    },
}

PLANT_COMPARE = {
    "name": "usair_plant_compare",
    "description": (
        "US air rules: what changed between two plant scenarios? "
        "Run the same bundled applicability pack on two cement or lime profiles and return the "
        "input fields that differ, the rule and unit verdicts that differ, reason or missing-fact "
        "changes, and how many rules stayed the same. Pass before_profile/after_profile or "
        "before_fixture/after_fixture (cement_plant_demo, cement_ciswi_after, lime_plant_demo). "
        "This tool does not fetch eCFR or the Federal Register. Citations are the bundled pack. "
        "A missing fact stays check_with_ehs or check. Not a compliance determination."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "before_profile": {"type": "object", "description": "Plant profile before the change. See /usair template plant."},
            "before_fixture": {"type": "string", "description": "Fictional plant fixture, used when before_profile is omitted."},
            "after_profile": {"type": "object", "description": "Plant profile after the change."},
            "after_fixture": {"type": "string", "description": "Fictional plant fixture, used when after_profile is omitted."},
            "rules": {
                "type": "array", "items": {"type": "string"},
                "description": "Optional pack entry ids to compare, e.g. ['60-CCCC']. Default: every plant rule.",
            },
        },
        "required": [],
    },
}

HOME_CHECK = {
    "name": "usair_home_check",
    "description": (
        "US air rules: what applies to this household or small shop? "
        "Screen outdoor burning, vehicle idling, wood heating, paint and solvents, and spray coating "
        "against the federal rules that actually reach a person (wood-heater NSPS, area-source autobody "
        "NESHAP, and product rules that bind manufacturers). Where no federal rule applies, the result "
        "says so and names the state's SIP subpart. Pass a profile or a fixture "
        "('homeowner_demo', 'body_shop_demo'). Not a compliance determination."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "profile": {"type": "object", "description": "Household or shop profile. See /usair template home."},
            "fixture": {"type": "string", "description": "Fictional demo profile, used when profile is omitted."},
            "topics": {
                "type": "array", "items": {"type": "string"},
                "description": "Optional subset: outdoor_burning, idling, wood_heating, paint_solvent, spray_coating.",
            },
        },
        "required": [],
    },
}

SIP_LOOKUP = {
    "name": "usair_sip_lookup",
    "description": (
        "US air rules: find a state's EPA-approved SIP rules in 40 CFR part 52. "
        "Read-only. Searches one state's part 52 subpart for a topic such as outdoor burning, idling, "
        "wood heating, paint or spray coating, and returns the matching sections with excerpts. "
        "Part 52 is what EPA has approved, which can lag the state's own current rule, and local "
        "ordinances are not in it. Read a hit with usair_cfr_get before quoting it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "state": {"type": "string", "description": "State or territory: 'TX', 'Texas', 'DC', 'Puerto Rico'."},
            "topic": {"type": "string", "description": "outdoor_burning, idling, wood_heating, paint_solvent, spray_coating, or your own words."},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
        },
        "required": ["state", "topic"],
    },
}

ALL_SCHEMAS = [CFR_GET, CFR_SEARCH, FR_RECENT, PLANT_TRIAGE, PLANT_COMPARE, HOME_CHECK, SIP_LOOKUP]
