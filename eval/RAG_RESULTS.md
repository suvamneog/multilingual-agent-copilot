# Production RAG on 48 wordings — test results

Branch `feat/production-rag-48`, run on 2026-11-11 against the server on port 8001
(Ollama `qwen2.5:7b`, BGE-M3 + BM25 hybrid retrieval, answer cache off so every answer is live).

## What changed

Policy terms now come from the wording, not from the fact table in `web/data/catalogue.json`:

- `wording.py` — for every product and field: hybrid retrieval over that product's clauses only, then a
  parser reads the retrieved pages in rank order and returns the stating clause with its quote, PDF page,
  printed page and rule number. Not stated means "not stated", with the reason. Cached by index fingerprint,
  so a re-ingested wording is re-read on the next start.
- `rank.py` — need-first ranking on the server. The need is parsed first (age, relation, conditions,
  negation as a filter, maternity and its date, planned surgery and its date, budget / low cost), then ranked
  on that need: PED wait for a condition, maternity wait for a baby, the joint-replacement wait (plus PED for
  an existing knee problem) for a planned surgery, the 30-day wait and entry age for a healthy person.
  Cover dates are compared with the needed date; "no plan covers it in time" is said plainly.
  A low-budget need shows "Option 1 / 2", never "Best". Watch candidates are deterministic.
- `answer.py` — verification after generation: the cited clauses must mention the question's topic, every
  figure must sit in the clause the sentence cites, a PED or listed-illness wait must equal the one this
  product's wording states, "covered from day 1" needs a clause that says so. A topic the wording never
  mentions is "not written" without calling the model. A waiting-list item is answered as a wait, never as
  "not covered". Add-on-only cover keeps the template from the pitch branch.
- `web/chat.js` / `web/agent-core.js` — the chat loads facts from `/api/wording-facts`, ranks with
  `/api/recommend`, keeps the 30-day wait in "Say this", does not duplicate the close kit, keeps the
  conversation's language for short English commands, reads negation ("diabetes nahi hai").
- `products.py` — names, insurer, UIN, sale status and replacement only (product master, not policy terms).

## Coverage of fields read from the 48 wordings

| Field | Stated in the wording | Notes |
|---|---|---|
| PED wait | 46 / 48 | agrees with the old fact table on all 48 |
| Specified-disease wait | 41 / 48 | 5 more than the old fact table |
| 30-day initial wait | 41 / 48 | |
| Maximum entry age | 6 / 48 | the rest do not state it in the wording (it is in the prospectus) |
| Room rent | 18 / 48 | the rest leave it to the Policy Schedule |
| Maternity | 44 / 48 | excluded / add-on only / covered after N months |
| Joint replacement in waiting list | 35 / 48 | |
| Sum insured options | 14 / 48 | |
| AYUSH / cataract / robotic surgery | 21 / 38 / 30 | |
| Coded exclusions | 43 / 48 | |

## Seven cases (live, browser, end to end)

| # | Case | Result | Best | Cover starts | Page |
|---|---|---|---|---|---|
| 1 | Cancer, father 58, treated 2 years ago | pass | Generali Central Health Absolute (PED 24 m) | 11 Nov 2028 | PDF p23 |
| 2 | Diabetes, mother 55, long Hinglish line | pass | Option 1 Generali (24 m); Option 2 Optima Secure V08 (36 m) | 11 Nov 2028 / 11 Nov 2029 | p23 / p30 |
| 3 | Heart, mother 62, bypass + BP | pass | Generali (24 m), "not from day 1" | 11 Nov 2028 | p23 |
| 4 | Senior 68, healthy, no diabetes / BP | pass | Optima Secure V08 (no max entry age, 30-day wait) | — | p32, p44 |
| 5 | Maternity, wife 28, baby next year | pass | Care Joy (24 m); second Magma (24 m); no plan in time | 11 Nov 2028 | p11 / p26 |
| 6 | Young 30, healthy, cheap | pass | Option 1 IFFCO Tokio, Option 2 ICICI CHI; money not compared | — | p13 |
| 7 | Knee replacement in 6 months, mother 60 | pass | Aditya Birla Activ One (24 m); no plan in time | 11 Nov 2028 | p47 |

Mother story, one chat (long line → Optima → pick V08 → premium → cataract → robotic → AYUSH → Close the
sale twice → National Mediclaim): pass. Premium refusal exact; cataract Code-Excl02, PDF page 31, 24 months;
AYUSH 1.4, PDF page 12; robotic "not written" in 73 ms; 30-day wait PDF page 32 in "Say this"; one close-kit
panel after two taps; National Mediclaim stopped 1 Apr 2024, renewals only.

Bug cases: negation, heart surgery (no day-1 claim, no 48-month PED), waiting list vs exclusion (joint
replacement 24 months, PDF page 32), hard question on first ask after a pick, stable "Also check": all pass.

## Differs from the expected values in rag.md.txt

The brief expected the mother case to rank Optima Secure V08 first and Liberty HealthPrime Connect second.
Read from the wordings, Generali Central Health Absolute ranks first: its PED wait is 24 months (clause
3.1.1.1, PDF page 23) and its benefit table states "Maximum Entry Age … Adult – No limit" (PDF page 50).
The old fact table had no entry age for Generali, so it was never ranked. Optima Secure V08 is second at 36
months (PDF page 30); Liberty ties at 36 and sorts after it. Because the case also asks for a low premium,
the two are shown as Option 1 / Option 2, not Best.

## Other suites

- `web/story-cases.js` (41 cases, expectations updated to the wording ranking and the Option labels): 41 / 41.
- Node tests: need, router 21/21, agent, quotes-readable, catalogue — all passing, 0 failing.
- `scripts/story_five_features.mjs` against port 8001: 5 / 5 features.

## Known limits

- Entry age is in only 6 wordings; plans without it are ranked after age-confirmed plans for customers 45+.
- Some rule labels read from table rows are coarse ("Clause 3.1 Exclusions applicable for all Benefits").
- Open questions outside the parsed fields still depend on the 7B model; verification drops unsupported
  figures, which can leave a short answer (heart surgery: "subject to the waiting period rules for
  pre-existing diseases").
- The index was not rebuilt; it already held all 48 wordings clause by clause. Two-column pages whose index
  text is interleaved fall back to the pdftotext page text for parsing.
