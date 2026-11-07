# us-air-rules

Does this EPA air rule apply to me? Give Hermes a plant or a household, and it screens Title 40 of the Code of Federal Regulations: **applies**, **check with EHS** (or **check**), or **skip**, with the citation beside every row.

Two layers share one engine:

- **Plant.** Cement and lime plants, plus the boilers, engines, and crushers that sit next to them. NESHAP, NSPS, GHG reporting, title V, and CAM. CISWI is check-only: the plugin flags a kiln that burns solid waste and stops there.
- **Home and small shop.** Outdoor burning, vehicle idling, wood stoves and outdoor boilers, house paint and solvents, and spray coating. Where no federal rule reaches the person, the result says so and points at that state's SIP subpart in 40 CFR part 52.

Sources are [eCFR](https://www.ecfr.gov) and the [Federal Register](https://www.federalregister.gov). No API key. Python standard library only.

> **Screening aid, not a compliance determination, and not legal advice.** eCFR is an editorial compilation. The Federal Register and the Code of Federal Regulations published by the Government Publishing Office are the official versions. State and local rules, permits, and consent decrees can be stricter. Confirm with environmental staff or counsel before you rely on a verdict.

The demo facilities (Mesa Gris Cement Works, Copper Hollow Lime) and the demo household and body shop are fictional. Nothing in this repo is a real plant's data.

## Install

Requires Hermes Agent 0.22.2 or newer.

Once this repo is on GitHub as `entersidbellad/hermes-us-air-rules`:

```bash
hermes plugins install entersidbellad/hermes-us-air-rules --enable
```

Until then, copy this directory into the plugins folder (the directory name can be anything; the manifest name is `us-air-rules`):

```bash
mkdir -p ~/.hermes/plugins
rm -rf ~/.hermes/plugins/us-air-rules
cp -a . ~/.hermes/plugins/us-air-rules
hermes plugins enable us-air-rules
```

In a chat: `/usair demo plant`, `/usair template home`, `/usair fixtures`. From a shell, `hermes us-air-rules warm` fetches the date Title 40 is current through, so the next read is one request cheaper.

## Tools

| Tool | What it does |
|---|---|
| `usair_cfr_get` | One Title 40 section, subpart, or part outline, from eCFR. A paragraph such as `(a)` is cut out when the section numbers its blocks. |
| `usair_cfr_search` | Search current Title 40 text. Older versions of the same section are folded into the one in force. |
| `usair_fr_recent` | Recent EPA rules, proposed rules, and notices that name one Title 40 part. A proposed rule is not law. |
| `usair_plant_triage` | Screen a facility profile, or the fixture `cement_plant_demo` / `lime_plant_demo`. |
| `usair_home_check` | Screen a household or small shop, or the fixture `homeowner_demo` / `body_shop_demo`. |
| `usair_sip_lookup` | Search one state's EPA-approved SIP rules in 40 CFR part 52. |

The bundled skill `us-air-rules:air-rules` tells the model to call these before it quotes a rule from memory.

A missing fact never becomes `skip`. The engine uses three-valued logic: yes, no, or unknown. Unknown stops the rule and returns the pack's fallback (`check_with_ehs` or `check`) plus the field names that were missing.

`include_fr: true` on a plant triage attaches recent Federal Register documents to the rows that apply or need a check. The screening result still stands if the Register is down; the row says the Register was not checked.

### Try it

- "Read 40 CFR 63.1340 and tell me what subpart LLL covers. Quote the text."
- "Run the fictional cement plant and list only the rules I would have to check with EHS."
- "I spray-paint my own car in the driveway, maybe once a year, in Pennsylvania. Does the autobody rule apply?"
- "What has EPA proposed on portland cement manufacturing in the last two years?"
- "Does Texas have an EPA-approved rule on outdoor burning?"

## What comes back

Success is JSON with `ok: true`, a `source` list, and `disclaimer`. Failure is `ok: false`, `error`, `error_type`, and usually `next_step`. Tools do not raise.

`source.cache` is `fresh` (just downloaded), `cached` (local copy still inside its lifetime), or `stale` (the source was down, so the last copy is returned and `stale` is true). Section text is pinned to the eCFR date it was fetched for, so a cached section does not go stale: that date's text does not change. The title's "current through" date is rechecked every six hours. Search results and Federal Register queries are rechecked every six or twenty-four hours.

`verified: true` on a finding means `scripts/verify_packs.py` found that entry's phrases in the live applicability section on 6 November 2026. It does not mean a lawyer signed off. The two "no federal rule" home topics (outdoor burning, idling) stay `verified: false` because there is no federal section to quote.

## Rule packs

`data/packs/plant.json` (20 entries, all phrase-checked) and `data/packs/home.json` (8 entries, 6 phrase-checked). Each entry cites its applicability section. Thresholds the pack actually uses:

- NSPS subpart F (cement): construction or modification after 17 August 1971.
- NESHAP subpart LLL: portland cement plants, major or area source. Crushers are out. Hazardous-waste kilns go to subpart EEE. Storage, transfer points, and bagging are major-source only.
- CISWI (subparts CCCC / DDDD): check only, when a kiln burns solid waste.
- NSPS subpart OOO: crushers and screens after 31 August 1983, unless they follow a subpart F unit in the process.
- Stationary engines: NESHAP ZZZZ for any stationary RICE; NSPS IIII and JJJJ by order date and manufacture date.
- Boilers: NESHAP DDDDD at major sources, JJJJJJ at area sources (gas-fired boilers excluded), NSPS Db above 100 MMBtu/hr, Dc from 10 to 100.
- Lime: NESHAP AAAAA at major sources, NSPS HH for rotary kilns after 3 May 1977.
- GHG reporting: cement (subpart H) and lime (subpart S) report regardless of amount (§ 98.2(a)(1)). The combustion-only path needs both 25,000 metric tons CO2e and an aggregate heat input of at least 30 MMBtu/hr (§ 98.2(a)(3)). A missing category or heat input is check with EHS. Below 25,000 is not a skip: other Table A-3 categories are outside this pack.
- Wood heaters: subpart AAA applies to heaters manufactured or sold on or after 15 May 2015 (§ 60.530(a)(2)), and to EPA-certified heaters manufactured on or after 1 July 1988 and before that date (§ 60.530(a)(1)). A certificate with no manufacture date stays check. Subpart QQQQ covers hydronic heaters and forced-air furnaces from 15 May 2015.
- Autobody and spray coating: subpart HHHHHH. Coating your own vehicles, at most two a year, without pay, is the hobby exclusion.
- Paint in a can: part 59 binds the manufacturer or importer, not the person painting. The verdict is `product_rule`.

Blank profiles are in `data/templates/`. `/usair template plant` prints one.

## Network, data, and privacy

- **Two hosts, HTTPS GET only:** `www.ecfr.gov` and `www.federalregister.gov`. A redirect to any other host is refused. An `http://` URL on these hosts is upgraded to HTTPS.
- **What is sent:** the citation or part you asked for, the search words, and (for the Register) the document type and the date window. A facility profile is evaluated on the machine and is not sent. `usair_plant_triage` and `usair_home_check` make no request unless `include_fr` is set.
- **What is stored:** public regulatory text and search responses, as JSON, under `<HERMES_HOME>/plugin-data/us-air-rules/cache/`. No questions and no profiles. The cap is `cache_max_mb` (default 100). Delete the folder any time.
- **What is read:** only this plugin's own settings, through Hermes's plugin settings API.
- **Pace and size:** at most one request per second per host, a 20 second timeout, and a 10 MB response cap. eCFR's full-text endpoint requires `Accept-Encoding: gzip`; the plugin sends it.
- **No credentials, no hooks, no cron, no background process, no shell, no telemetry, no self-update.** `urllib` honors `HTTPS_PROXY`.

## Settings

Under `plugins.entries.us-air-rules.settings` in `config.yaml`:

| Key | Default | Meaning |
|---|---|---|
| `cache_max_mb` | `100` | Drop the oldest cached responses past this size. |
| `fr_on_triage_max` | `4` | How many Federal Register requests one `include_fr` triage may attempt. A failed request counts. |

## Development

```bash
python -m unittest discover -s tests -v          # 51 offline tests, no network
python scripts/verify_packs.py                   # live eCFR phrase check; add --mark to rewrite verified flags
hermes plugins validate .                        # the check the catalog runs
```

`hermes plugins validate .` on this tree (Hermes Agent vgit, 6 November 2026) passed every check, including the security scan at `safe`, with no warnings.

The offline tests use a fake transport. They cover citation parsing, the three-valued engine, both fictional fixtures, eCFR XML, search dedupe to the version in force, Federal Register normalization, host allowlisting, redirects, gzip, the size cap, stale cache, and that `register()` matches `plugin.yaml`.

## What this plugin does not do

No dispersion modeling, no emission calculations, no permit-limit math, no ECHO or facility lookup, no state-law scraper, no model call inside a tool. Part 52 is the EPA-approved SIP text, which can lag the state's own rule, and city or county rules are not in it. The plant pack is built around cement and lime; a refinery or a power plant will mostly see the engine, boiler, reporting, and title V rows.

## Publishing

See [CATALOG_PR.md](CATALOG_PR.md) and [catalog-entry.yaml.template](catalog-entry.yaml.template). The demo you record is in [DEMO_SCRIPT.md](DEMO_SCRIPT.md). The build notes are in [PLAN.md](PLAN.md).

## License

MIT. See [LICENSE](LICENSE).
