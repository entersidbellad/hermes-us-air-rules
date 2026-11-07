# Catalog pull request

Open this against `NousResearch/hermes-agent`, adding one file: `plugin-catalog/us-air-rules.yaml`. Copy [catalog-entry.yaml.template](catalog-entry.yaml.template), replace the SHA, and use the text below as the PR body.

There is no rule that the pinned commit be two weeks old. Pin the commit you are actually submitting. Admission policy: [plugin catalog README](https://github.com/NousResearch/hermes-agent/blob/main/plugin-catalog/README.md) and the [submission guide](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/catalog-submission).

## Before you open it

1. The public repo is `entersidbellad/hermes-us-air-rules`. Pin the full 40-character commit you are submitting (`git rev-parse HEAD` on that commit), not an older one.
2. Fork `NousResearch/hermes-agent`, branch from `main`, add `plugin-catalog/us-air-rules.yaml` with that SHA. `name` in the entry and `name` in `plugin.yaml` are both `us-air-rules`.
3. Confirm `capabilities` matches `plugin.yaml`: the seven `usair_` tools, including `usair_plant_compare`, no hooks, no middleware, no env vars.
4. `hermes plugins validate` at that SHA should be clean. The 0.1.0 tree passed on 6 November 2026 (security scan `safe`, no warnings). Run it again on the commit you pin.

Install for the demo, before or after the catalog PR:

```bash
hermes plugins install entersidbellad/hermes-us-air-rules --enable
```

## PR title

`us-air-rules: Title 40 applicability screening from eCFR and the Federal Register`

## PR body

```markdown
## What this plugin does

Asks "does this EPA air rule apply to me?" A plant profile or a household profile goes in. Each bundled Title 40 rule comes back applies, check with EHS, or skip, with the citation and the facts that were missing. A missing fact never becomes skip.

Seven read-only tools: read one Title 40 section from eCFR, search current Title 40 text, list recent EPA Federal Register documents for one part, triage a plant, compare two plant scenarios with the bundled pack, check a household or small shop, and search a state's EPA-approved SIP rules in 40 CFR part 52. The compare tool reruns the same screening engine and does not contact the network.

The seed pack is cement and lime (kilns, mills, engines, boilers, GHG reporting, title V, CAM) plus the federal rules a household actually runs into (wood heaters, paint, spray coating). Outdoor burning and vehicle idling have no federal rule; the tool says so and names the state's part 52 subpart. CISWI is check-only. No emission math and no dispersion model.

Demo fixtures are fictional. The repo contains no real facility's data.

## Disclosure

- Network: HTTPS GET only, to `www.ecfr.gov` (title currency, section and subpart XML, part structure, search) and `www.federalregister.gov` (document search: agency EPA, Title 40 part, optional term, date window, document type). Redirects stay on those hosts. No API key.
- What is sent: the citation, the part, and the search words. A facility or household profile is evaluated locally and is not sent. Plant and home triage make no request unless `include_fr` is set. `usair_plant_compare` makes no request and does not store the profiles.
- What is stored: public regulatory responses as JSON under `HERMES_HOME/plugin-data/us-air-rules/cache`, capped by `cache_max_mb` (default 100). No profiles, no questions, no telemetry.
- Pace: at most one request per second per host, 20 s timeout, 10 MB cap.
- No hooks, no cron, no background process, no shell, no credential read, no self-update. Standard library only.
- Not a compliance determination and not legal advice. eCFR is an editorial compilation; the Federal Register and the GPO edition of the CFR are the official texts. `verified: true` means a phrase check against the live section on 6 November 2026 (`scripts/verify_packs.py`), not a legal review. Outdoor burning and idling stay unverified because there is no federal section to quote.

## Checklist

- [x] Owner submission (repo `entersidbellad/hermes-us-air-rules`)
- [x] Exact 40-character SHA
- [x] `capabilities` matches what `register()` registers
- [x] `requires_hermes: ">=0.22.2"` in the entry and in `plugin.yaml`
- [x] No self-updater in this tree
- [ ] `hermes plugins validate` clean at the pinned commit (re-run on the SHA you submit; 0.1.0 was clean on 6 November 2026)
- [x] README states the network behavior above
```

## After it is open

Post the demo ([DEMO_SCRIPT.md](DEMO_SCRIPT.md)) on X, tag @NousResearch, and send both links through [the hackathon form](https://form.typeform.com/to/FwJLvOln). The form can go in as soon as the PR exists. Catalog CI runs when a maintainer starts it. Reviewers re-review the diff when you later bump the SHA.
