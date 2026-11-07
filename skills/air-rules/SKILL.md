---
name: air-rules
description: "Does a US EPA air rule apply (plant, kiln, boiler, engine, wood stove, paint, burning)? Use the usair_ tools and quote source, not memory."
---

When the user asks whether a US federal air rule applies to a plant, a kiln, a boiler, an engine, a household, a wood stove, paint, solvents, spray coating, outdoor burning or vehicle idling, use the us-air-rules tools before you answer. If they are not in your tool list, call `tool_search` for "EPA air rule" or "Title 40 CFR", then `tool_call`.

Pick the tool from the question:

- A citation (`40 CFR 63.1340`, `§ 60.62`, `part 60 subpart F`): `usair_cfr_get` with `citation` exactly as written. Quote only the returned `text`, and show `source` and `ecfr_url`.
- A topic inside Title 40 ("which section covers open clinker piles"): `usair_cfr_search`, then `usair_cfr_get` on the hit you use.
- "Anything new on this rule": `usair_fr_recent` with the part and a quoted phrase. A proposed rule is not law. Say so when `type` is Proposed Rule.
- "Does this apply to my plant / kiln / boiler / generator": `usair_plant_triage` with the profile, or `fixture` `cement_plant_demo` / `lime_plant_demo` when they ask for the demo. Report every finding's `verdict`, `citation` and `why`. Do not drop a `check_with_ehs` row.
- "What changed between these two plants or scenarios": `usair_plant_compare` with `before_profile` and `after_profile`, or `before_fixture` and `after_fixture`. For the fictional kiln demo, `before_fixture` is `cement_plant_demo` and `after_fixture` is `cement_ciswi_after` (`/usair demo compare`). Use a fixture only by omitting that side's profile. `{}`, null, or a blank profile is an error and does not load the fixture. Report `input_changes`, each `verdict_changes` row (`verdict`, `why`, `missing`, `unit_changes`), `explanation_changes` when the verdict stayed and the reason or missing facts did not, and the `unchanged` count. `live_fetch` is false. Citations in that result are the bundled pack. Do not describe a difference the tool did not return, and do not call it a live eCFR check.
- "Does this apply to my house / shop / stove / painting": `usair_home_check`. For a `no_federal_rule` or `product_rule` finding, offer `usair_sip_lookup` with the state and the finding's `sip.topic` before you describe state rules.
- A named state and a topic with no federal rule: `usair_sip_lookup`.

Rules you do not get to break:

- If you did not call a tool, say you did not fetch the text. Do not recite a section from memory and do not attribute it to eCFR or the Federal Register.
- A missing fact is `check_with_ehs` or `check`, never `skip`. Do not fill the gap yourself.
- `verified: false` means the bundled rule has not been re-checked against the live section. Still show the citation, and offer to read it with `usair_cfr_get`.
- Put `disclaimer` in the answer, in your own words if you like, but do not drop it: this is a screening aid, not a compliance determination and not legal advice. eCFR is not the official edition.
- If `stale` is true, say the source was down and give the cache date.
- Never compute an emission rate, a modeled concentration, or a permit limit. This plugin does not do that.

The two demo plants (Mesa Gris Cement Works, Copper Hollow Lime) and the two demo households are fictional. Do not treat them as real facilities, and do not look up a real company's permits unless the user asks with public sources.
