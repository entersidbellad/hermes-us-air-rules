# us-air-rules — build plan

Hermes Agent plugin for the Nous Research plugin hackathon. Submissions are due 5 PM EST on 11 November 2026: a catalog pull request, a demo on X, and the hackathon form. See [DEMO_SCRIPT.md](DEMO_SCRIPT.md) and [CATALOG_PR.md](CATALOG_PR.md).

This file replaces the Claude draft (`uploads/PLAN_FROM_CLAUDE_766a.md`). Where they disagree, this file is the one to follow.

## Corrections to the Claude draft

- There is **no** "pinned SHA must be two weeks old" rule in the catalog admission policy (checked in `plugin-catalog/README.md` on 6 November 2026). A catalog PR may pin a fresh commit. `hermes plugins install owner/repo --enable` works for a demo either way.
- Catalog name is **`us-air-rules`**. `plant-rules` reads as gardening.
- Intended public repo: `entersidbellad/hermes-us-air-rules`. This checkout is the working tree for that plugin.

## Identity

| | |
|---|---|
| Catalog name | `us-air-rules` |
| Tool prefix | `usair_` |
| Category | `tools` |
| License | MIT |
| Dependencies | Python standard library only |
| Hermes floor | `>=0.22.2` (same surface as de-law: `register_tool`, `register_skill`, `register_command`, `register_cli_command`, `get_config`) |
| Pattern | de-law: tools never raise; JSON `ok` / `error` / `error_type` / `next_step`; `source` + disclaimer on success; fail closed |

One line: facility or household in, triage out (applies / check with EHS / skip), from live eCFR Title 40 and the Federal Register, with citations.

## What shipped

Native plugin at the repo root:

- `plugin.yaml` and `register(ctx)` in `__init__.py`
- Skill `us-air-rules:air-rules` (`skills/air-rules/SKILL.md`)
- `/usair demo|template|fixtures` and `hermes us-air-rules warm`
- Six tools: `usair_cfr_get`, `usair_cfr_search`, `usair_fr_recent`, `usair_plant_triage`, `usair_home_check`, `usair_sip_lookup`
- One engine (`engine.py`) and two packs (`data/packs/plant.json`, `data/packs/home.json`)
- Fictional fixtures only. No Holcim, Carmeuse, Hatch, or any other real plant.
- `scripts/verify_packs.py`. On 6 November 2026 it matched every phrase against the current eCFR section and the part 52 state map. `--mark` set `verified: true` on those entries. Outdoor burning and idling stay unverified: there is no federal section to quote.

## Sources and pace

- `www.ecfr.gov` — versioner (titles, full XML, structure) and search. Full text needs `Accept-Encoding: gzip` or eCFR returns 406.
- `www.federalregister.gov` — API v1 document search, EPA agency filter, one Title 40 part.
- HTTPS GET only. Other hosts refused. Redirects followed by hand, three at most, and only back to these two hosts.
- One request per second per host, 20 second timeout, 10 MB cap.
- Cache under `<HERMES_HOME>/plugin-data/us-air-rules/cache/`, atomic writes, oldest dropped past `cache_max_mb` (default 100).
- A profile never leaves the machine. Search words and the citation do.

Search results include historical versions of a section. The plugin keeps the version in force on the Title 40 "current through" date.

Federal Register full-text search is loose (a cement phrase can match a document that only brushes the word). Tools say so. Quoted phrases in the pack's `fr.term` keep it tighter. A proposed rule is labeled as one.

## Engine

Conditions are yes / no / unknown. Unknown means a profile field is missing, or a partial date straddles a threshold (`1971` against 17 August 1971).

Rules run in order. The first true rule wins. The first unknown rule stops that entry and returns the pack fallback plus `missing`. A default verdict inside an entry cannot be `skip` (`never_skip_without_evidence`). The `flagged` operator is the exception: an optional claim (an existing title V permit, "I am buying a new stove") treats a missing value as false, because the user did not assert it.

Unit-scoped entries run once per matching unit. The row's verdict is the highest-priority unit verdict (`applies` before `check_with_ehs` before `skip`). Later entries can read earlier verdicts as `results.<id>`, which is how title V and CAM see subpart LLL.

## Pack scope (seed)

**Plant.** Subparts F, LLL, EEE, CCCC (check only), OOO, Y, ZZZZ, IIII, JJJJ, DDDDD, JJJJJJ, Db, Dc, AAAAA, HH, GHGRP H / S / C, part 70, part 64. Cement NAICS 327310 and lime 327410 drive the category gates.

**Home.** No federal rule for backyard burning or idling (SIP pointer). Wood heaters AAA and QQQQ. Architectural coatings, consumer products, and aerosol coatings as product rules. Subpart HHHHHH for spray coating, with the hobby exclusion.

**SIP.** Part 52 subpart per state and territory, read from the eCFR outline (Northern Mariana Islands is FFF, not EEE; EEE is a general approval subpart; A is general provisions). Checked by the verify script.

## Explicitly not built

AERMOD or any dispersion model. RegRadar or any UI. Emission calculations or permit-limit math. State-law scraping. ECHO. A model call inside a tool. Hooks, cron, telemetry, self-update.

## Checks already run

- `python -m unittest discover -s tests -v` — 42 tests, no network.
- `hermes plugins validate .` — all checks ok, security scan `safe`, no warnings.
- `python scripts/verify_packs.py` — phrases and the part 52 map matched live eCFR on 6 November 2026.

## Still to do before the deadline

1. Publish the tree as a public GitHub repo `entersidbellad/hermes-us-air-rules`.
2. Open the catalog PR with the commit SHA. Steps and the entry body are in [CATALOG_PR.md](CATALOG_PR.md).
3. Record the demo in [DEMO_SCRIPT.md](DEMO_SCRIPT.md) (under 90 seconds), post it on X, tag @NousResearch.
4. Submit the PR link and the post link through the hackathon form.
