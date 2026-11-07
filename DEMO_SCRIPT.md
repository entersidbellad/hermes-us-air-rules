# Demo script

Under 90 seconds, per the hackathon call. Show the plugin working. The two plants and the household are fictional; say so once.

Record a Hermes CLI or desktop session with the plugin enabled. Suggested cuts:

## 0:00 — the question

On screen, one sentence:

> Does subpart LLL apply to this cement plant, and what else should EHS look at?

## 0:08 — triage, no network

Prompt:

> Run usair_plant_triage on the fixture cement_plant_demo. Group the rows by verdict. Do not quote rule text you did not fetch.

What you should see (Mesa Gris Cement Works, fictional, Texas):

- **applies** — 60-F, 63-LLL, 60-OOO, 63-ZZZZ, 60-IIII, 63-DDDDD, 60-Dc, 98-H, 98-C, part 70
- **check with EHS** — 60-CCCC (kiln solid-waste flag is unknown), 60-Y (coal over 200 tons/day; the unit-level dates still matter), part 64 (title V source with control devices)
- **skip** — 63-EEE (kiln does not burn hazardous waste), 60-JJJJ (the engines are diesel), 63-JJJJJJ and 60-Db (the boiler is gas, 12 MMBtu/hr, at a major source), 60-HH (not a lime kiln)
- The old finish mill (1968, never modified) is **skip** under subpart F and **applies** under subpart LLL. Point at that row. It is the whole product: the date test and the NESHAP are not the same answer.

Say the disclaimer in one line: screening aid, not a compliance determination, eCFR is not the official edition.

## 0:40 — it actually reads the section

Prompt:

> Read 40 CFR 63.1340(a) with usair_cfr_get and quote it.

The tool returns the eCFR sentence that subpart LLL applies to each new and existing portland cement plant that is a major source or an area source, plus the eCFR URL and the date of the text. Read one clause out loud. Do not paraphrase from memory before the tool returns.

## 1:05 — the household, then stop

Prompt:

> usair_home_check fixture homeowner_demo.

What you should see:

- Wood stove, buying new: **applies**, subpart AAA. Buy a certified model, burn only what the manual allows.
- Spray-painting one car a year at home, unpaid: **exempt** from subpart HHHHHH.
- Backyard leaves: **no federal rule**. The row points at Pennsylvania, 40 CFR part 52 subpart NN, and tells the model to call `usair_sip_lookup` before describing a state rule.

End on that handoff. Do not open a state website. That is the boundary of the plugin.

## If you have ten more seconds

> What has EPA proposed that mentions "portland cement manufacturing" since 2024? Use usair_fr_recent on part 63.

Say "proposed" out loud when the type is Proposed Rule.

## What not to show

A real company's name, a real permit, an emission number, a modeled concentration, or a claim that a row is a legal opinion. `verified: true` means the bundled phrase was found in the section on 6 November 2026, not that counsel signed it.

## After the recording

1. Post the video on X. Tag @NousResearch. One line of copy is enough: "us-air-rules: plant or household in, Title 40 applicability out, with the eCFR citation. Fictional cement plant in the demo."
2. Open the catalog PR (see [CATALOG_PR.md](CATALOG_PR.md)).
3. Submit both links through the hackathon form: [https://form.typeform.com/to/FwJLvOln](https://form.typeform.com/to/FwJLvOln). The steps are the ones in the Nous Research hackathon announcement. You can submit the form as soon as the pull request is open; catalog checks start when a maintainer runs them.

Sources:

- [Nous Research](https://nousresearch.com/) (hackathon announcement, retrieved 6 November 2026)
- [Catalog submission guide](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/catalog-submission)
