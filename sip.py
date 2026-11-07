"""State Implementation Plans as approved by EPA: 40 CFR part 52, one subpart per state.

The map below was read from the eCFR structure of part 52 (2026-11-04). It is checked again by
scripts/verify_packs.py. EPA-approved SIP rules are federally enforceable, but the state's own
current rule can be newer than what part 52 lists, and local air districts can add rules.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .client import UsairError

STATES: Dict[str, Tuple[str, str]] = {
    "AL": ("B", "Alabama"), "AK": ("C", "Alaska"), "AZ": ("D", "Arizona"), "AR": ("E", "Arkansas"),
    "CA": ("F", "California"), "CO": ("G", "Colorado"), "CT": ("H", "Connecticut"), "DE": ("I", "Delaware"),
    "DC": ("J", "District of Columbia"), "FL": ("K", "Florida"), "GA": ("L", "Georgia"), "HI": ("M", "Hawaii"),
    "ID": ("N", "Idaho"), "IL": ("O", "Illinois"), "IN": ("P", "Indiana"), "IA": ("Q", "Iowa"),
    "KS": ("R", "Kansas"), "KY": ("S", "Kentucky"), "LA": ("T", "Louisiana"), "ME": ("U", "Maine"),
    "MD": ("V", "Maryland"), "MA": ("W", "Massachusetts"), "MI": ("X", "Michigan"), "MN": ("Y", "Minnesota"),
    "MS": ("Z", "Mississippi"), "MO": ("AA", "Missouri"), "MT": ("BB", "Montana"), "NE": ("CC", "Nebraska"),
    "NV": ("DD", "Nevada"), "NH": ("EE", "New Hampshire"), "NJ": ("FF", "New Jersey"), "NM": ("GG", "New Mexico"),
    "NY": ("HH", "New York"), "NC": ("II", "North Carolina"), "ND": ("JJ", "North Dakota"), "OH": ("KK", "Ohio"),
    "OK": ("LL", "Oklahoma"), "OR": ("MM", "Oregon"), "PA": ("NN", "Pennsylvania"), "RI": ("OO", "Rhode Island"),
    "SC": ("PP", "South Carolina"), "SD": ("QQ", "South Dakota"), "TN": ("RR", "Tennessee"), "TX": ("SS", "Texas"),
    "UT": ("TT", "Utah"), "VT": ("UU", "Vermont"), "VA": ("VV", "Virginia"), "WA": ("WW", "Washington"),
    "WV": ("XX", "West Virginia"), "WI": ("YY", "Wisconsin"), "WY": ("ZZ", "Wyoming"), "GU": ("AAA", "Guam"),
    "PR": ("BBB", "Puerto Rico"), "VI": ("CCC", "Virgin Islands"), "AS": ("DDD", "American Samoa"),
    "MP": ("FFF", "Commonwealth of the Northern Mariana Islands"),
}

_BY_NAME = {name.lower(): code for code, (_sub, name) in STATES.items()}
_BY_NAME.update({"washington dc": "DC", "washington, d.c.": "DC", "d.c.": "DC", "northern mariana islands": "MP",
                 "us virgin islands": "VI", "u.s. virgin islands": "VI"})

# Topic keys used by the home pack -> eCFR search phrases (tried in order until one has hits).
TOPIC_QUERIES: Dict[str, List[str]] = {
    "outdoor_burning": ['"open burning"', '"outdoor burning"', "burning"],
    "idling": ["idling", '"idle reduction"'],
    "wood_heating": ['"wood heater"', '"wood stove"', '"wood burning"', '"residential wood"'],
    "paint_solvent": ['"architectural coating"', '"consumer products"', "solvent"],
    "spray_coating": ['"surface coating"', '"spray"', '"autobody"', '"motor vehicle refinishing"'],
}
TOPIC_ALIASES = {
    "open_burning": "outdoor_burning", "burning": "outdoor_burning", "backyard_burning": "outdoor_burning",
    "burn_barrel": "outdoor_burning", "anti_idling": "idling", "vehicle_idling": "idling",
    "wood_stove": "wood_heating", "fireplace": "wood_heating", "wood_burning": "wood_heating",
    "paint": "paint_solvent", "solvents": "paint_solvent", "architectural_coatings": "paint_solvent",
    "consumer_products": "paint_solvent", "spray_painting": "spray_coating", "autobody": "spray_coating",
    "surface_coating": "spray_coating",
}


def resolve_state(raw: str) -> Tuple[str, str, str]:
    """Return (code, subpart, name). Raises UsairError for anything not in part 52."""
    s = (raw or "").strip()
    if not s:
        raise UsairError("bad_argument", "No state given.", "Pass a two-letter code like 'TX' or a name like 'Texas'.")
    code = s.upper() if s.upper() in STATES else _BY_NAME.get(s.lower())
    if not code:
        raise UsairError("unknown_state", f"'{raw}' is not a state or territory with a SIP subpart in 40 CFR part 52.",
                         "Use a two-letter postal code (TX, CA, PA, ...) or the full name.")
    sub, name = STATES[code]
    return code, sub, name


def topic_key(raw: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", (raw or "").strip().lower()).strip("_")
    return TOPIC_ALIASES.get(key, key)


def queries_for(topic: str) -> List[str]:
    key = topic_key(topic)
    if key in TOPIC_QUERIES:
        return TOPIC_QUERIES[key]
    return [topic.strip()]


def known_topics() -> List[str]:
    return sorted(TOPIC_QUERIES)


def state_from_profile(profile: Dict) -> Optional[str]:
    raw = (profile or {}).get("state")
    if not raw:
        return None
    try:
        return resolve_state(str(raw))[0]
    except UsairError:
        return None
