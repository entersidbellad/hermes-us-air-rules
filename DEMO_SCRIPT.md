# Demo script

Under 90 seconds. The plants and the household are fictional; say so once.

Record a Hermes CLI or desktop session with the plugin enabled. The compare cut below is the one to record. The longer plant-then-household tour is still underneath if you want it later.

## Record this: what changed

One fictional cement kiln. The solid-waste fact starts unknown, so CISWI is check with EHS. The same fact, supplied as false, moves that one row to skip. The other rows stay. This cut does not use the network.

### 0:00

On screen:

> What changes if this kiln does not burn solid waste?

### 0:08

In Hermes:

```
/usair demo compare
```

The same run as a tool call, if you would rather show the agent:

> Compare cement_plant_demo with cement_ciswi_after using usair_plant_compare. Do not fetch eCFR.

What you should see:

```
Mesa Gris Cement Works (fictional), TX — what changed
us-air-rules 0.2.1
Offline bundled pack. Not a live eCFR or Federal Register read.
Profiles: cement_plant_demo → cement_ciswi_after

What changed in the plant:
  Whether the plant burns solid waste (burns_solid_waste) was unknown, now
    false.
Kiln K1, CISWI (60-CCCC): was check with EHS, now skip for this screened rule.
Why: The kiln does not combust solid waste (non-hazardous secondary materials
  that are not solid waste do not trigger CISWI).
Citation: 40 CFR part 60 subparts CCCC and DDDD, section 60.2015. Bundled
  pack, not a live fetch.
Evidence: 60-CCCC K1 check_with_ehs → skip
Skip here covers only the screened rule named above.
Unchanged screened rules: 19.
The missing fact is now filled in.
Missing before: 60-CCCC K1 unit.burns_solid_waste.
Missing after: none.
```

Then the screening disclaimer.

Say this, and stop:

> The profile did not say whether kiln K1 burns solid waste, so CISWI stayed check with EHS. The same plant, with that fact set to false, is skip for this screened rule. The reason is the pack's own sentence. Nineteen other screened rules did not move. That citation was the bundled pack, not a live eCFR read. Skip is not a finding that the plant is unregulated.

`burns_solid_waste: true` stays check with EHS. The pack flags a waste-burning kiln; it does not decide the 40 CFR part 241 solid-waste question. Do not say the row became "applies."

### If you have twenty more seconds

> Read 40 CFR 60.2015 with usair_cfr_get.

That second call is the live text. Say so. The compare above did not do it.

## Longer tour

Suggested cuts:

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
