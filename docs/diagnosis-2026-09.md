# F1 Diagnosis — AREA Live Eval Canary, 2026-09-27/28

Status at review time (per the design/credibility review): 4 of 8 correct,
1 failed call, release `a010930`. Evidence below is read directly from
the canary's own public ledger (`ledger` branch, `ledger/latest.json` and
`ledger/runs.jsonl`) and from calling the live endpoint
(`https://www.giggitai.com/api/area-ask`) directly with the same 8
questions from `canary/rows.json`. No number in this doc is invented or
assumed — every claim below was reproduced live except where marked
"not reproduced."

## Ledger record used (run at 2026-09-27T21:24:37Z, release a010930)

```
total-dollars OK · top-specialty OK
top-state OK · quarterly-2021-q1 ERR
food-beverage-share MISS · semaglutide-vs-tirzepatide MISS
novo-lilly-share MISS · row-count-2021 OK
correct 4/8 · recorded 8 · tolerance 0 · MISMATCH
```

## Row 1: `quarterly-2021-q1` — HTTP 502 (cause type: execution + SQL meaning)

Reproduced live. The model wrote:

```sql
SELECT SUM(amount_usd) FROM payments WHERE quarter = 1 AND program_year = 2021 LIMIT 5000
```

Postgres rejects this: `operator does not exist: text = integer`. The
`payments.quarter` column is `text` in the form `'2021Q1'` (confirmed in
`sql/001_schema.sql`'s own comment), never a bare integer. The model's SQL
prompt (`SCHEMA_TEXT` in both `web/api/ask.js` and
`src/area/tools/query_tool.py`) never stated the column's format, so the
model guessed `quarter = 1` (treating it as a quarter number 1-4). The
handler correctly returns `{ok:false, error:...}` with HTTP 502 for a
failed query — that part is working as designed — but the SQL itself was
wrong.

**Fix applied:** `SCHEMA_TEXT` in both files now states the exact format
(`quarter` is `text`, e.g. `'2021Q1'`) with a worked example.

## Row 2: `novo-lilly-share` — MISS, wrong number (cause type: SQL meaning)

Reproduced live. The model wrote:

```sql
SELECT SUM(amount_usd) * 100.0 / (SELECT SUM(amount_usd) FROM payments) AS percentage_other_manufacturers
FROM payments WHERE manufacturer NOT IN ('Novo Nordisk', 'Eli Lilly') LIMIT 5000
```

This returned **100%** — every row matched the "not X or Y" filter,
meaning `manufacturer` never exactly equals the bare strings `'Novo
Nordisk'` or `'Eli Lilly'` in the real data (the schema comment for this
column names the real CMS source field, `Applicable_Manufacturer_..._Name`,
which routinely carries suffixes like "Inc." — confirmed by the gold SQL
in `canary/rows.json`, which uses `NOT ILIKE '%Novo Nordisk%'` and gets
**24.53%**, not 100%). This is the same class of bug the review's own
"known lead" already named for `physician_specialty = 'cardiologist'`
(also exact-match against a free-text column) — one shared root cause,
not two.

**Fix applied:** `SCHEMA_TEXT` in both files now instructs case-insensitive
partial matching (`ILIKE '%value%'`) for every free-text column
(`physician_specialty`, `manufacturer`, `physician_state`, `product`,
`product_generic`, `nature_of_payment`), with the exact NOT-ILIKE form
worked out for a "other than X and Y" question.

This fix changes what SQL the model writes going forward; it does not
retroactively confirm the model will always get 24.53% now, since the
model is not deterministic. **Not yet re-verified against a fresh live
call post-fix** — the fix only ships live once this PR is reviewed,
merged, and deployed. Re-run the canary once live and confirm.

## Row 3: `food-beverage-share` — MISS, correct number rejected (cause type: answer checking)

Reproduced live. The model's answer: "34.05% ... [q_web]." — the SQL and
number are correct: the endpoint's own reference check
(`reference.agree`) already confirmed `34.05` matches the gold SQL's own
output exactly. It was marked **MISS** for two independent, stacked
reasons in the citation/verification layer, not because of a data or SQL
problem:

1. **Marker placement.** The composer wrote one citation marker at the
   end of the sentence, not immediately after "34.05%" as the citation
   checker (`web/lib/citation.js`, `src/area/tools/citation_checker.py`)
   required. A single-clause sentence with one number and one trailing
   marker is unambiguous, but the old checker only accepted a marker
   directly adjacent to the number.
2. **Fraction vs. percentage scale.** The SQL's raw result column
   (`food_beverage_share`) holds a 0–1 fraction (`0.3405...`); the answer
   correctly states it as a percentage (`34.05%`). The old checker
   compared the claimed percentage directly against the raw fraction —
   off by 100x — and never accounted for the unit conversion a percentage
   question requires.

**Fix applied (both `citation.js` and `citation_checker.py`, kept in
parity per the Python file's own "source of truth" role):**
- A number with no marker immediately following it is now also checked
  against the next marker later in the text, but **only** when it is the
  single unclaimed number before that marker. Two or more unclaimed
  numbers before one marker stay unverified (ambiguous is never guessed).
- A claimed percentage is now also matched against a cited evidence leaf
  scaled by 100 in either direction, so it works whether the SQL already
  multiplied by 100 or left a raw fraction.
- `citation.js`'s numeric-leaf extraction was also missing what
  `citation_checker.py` already does: treating a numeric-looking evidence
  *string* (e.g. Postgres NUMERIC values, which the driver returns as
  strings to avoid float precision loss) the same as a native number.
  This was a silent JS/Python parity gap, fixed for defense-in-depth even
  though it was not the specific cause of this row's MISS (this row's
  evidence value did parse as a plain numeric string either way, once the
  marker was attributed).

All three changes are covered by new tests reproducing the exact real
answer/evidence text above, plus a new ambiguous-case test proving the
fallback does not guess when it shouldn't. 17/17 Python tests pass
(13 pre-existing + 4 new), 10/10 relevant JS tests pass (7 pre-existing +
3 new), full suite: 190 passed / 11 skipped (Python, pre-existing
Postgres-only skips, unrelated), 28/28 JS tests across the whole `web/`
package.

## Row 4: `semaglutide-vs-tirzepatide` — MISS in the ledger, **not reproduced live** (cause type: unresolved — likely answer checking, same as Row 3)

Calling the live endpoint just now with the exact same question returned
`accepted: true`, `reference.agree: true`, and both numbers
($17,478,278.97 and $53,703,508.24) correct. Applying the *old* citation
checker code to this exact answer/evidence text also passes it — so this
row's MISS was not reproduced by any of the causes found above.

Two honest possibilities, not confirmed:
- **Timing/data drift.** The canary computes its own gold numbers by
  running `gold_sql` against the live database at roughly the same
  moment it calls the endpoint. If the CMS pull was still loading rows
  during that specific run, the two numbers could disagree by more than
  the 0.5% tolerance purely from data changing between the two queries,
  with no code bug at all.
- **The same citation-formatting flakiness as Row 3, but this specific
  run's phrasing happened to already include correctly-placed markers.**
  The composer's phrasing is not fully deterministic; a different run
  could plausibly omit or misplace a marker the way it did on Row 3.

I am not marking a fix for this row. Recommend re-running the canary a
few times post-deploy and watching whether this row ever misses again
before treating it as resolved — reporting it fixed without re-observing
it would be a guess, not a verified result.

## Known SQL bug not causing today's MISS, worth fixing anyway

The model's SQL for `semaglutide-vs-tirzepatide` was:

```sql
SELECT SUM(amount_usd) AS semaglutide_total, SUM(amount_usd) AS tirzepatide_total
FROM payments WHERE product_generic = 'semaglutide'
UNION ALL
SELECT SUM(amount_usd) AS semaglutide_total, SUM(amount_usd) AS tirzepatide_total
FROM payments WHERE product_generic = 'tirzepatide' LIMIT 5000
```

Both output columns repeat the same `SUM(amount_usd)` per branch, so each
result row has `semaglutide_total == tirzepatide_total` (both equal to
whichever branch's own filtered sum) — the column labels are misleading
even though, in this run, the model still stated both real numbers
correctly by reading the two rows positionally. This is exactly the kind
of ambiguous SQL that `product_generic = 'semaglutide'` (exact match) or
a mislabeled `UNION ALL` could silently mis-map on a future run. Not
fixed here (out of scope for F1's failing-row diagnosis, since it isn't
currently causing a wrong answer) — flagging for a follow-up SQL-prompt
improvement: teach the model to write `GROUP BY product_generic`
(matching the canary's own gold SQL shape) instead of a same-column
`UNION ALL`.

## Summary table

| Row | Cause type (F1 taxonomy) | Reproduced live? | Fixed in this PR? |
|---|---|---|---|
| quarterly-2021-q1 (502) | (a) SQL meaning + (c) execution | Yes | Yes — schema now documents `quarter` format |
| novo-lilly-share (MISS) | (a) SQL meaning | Yes | Yes — schema now requires ILIKE on free-text columns |
| food-beverage-share (MISS) | (d) answer checking | Yes | Yes — marker fallback + percent/fraction scaling |
| semaglutide-vs-tirzepatide (MISS) | unresolved (likely (d) or data timing) | No | No — needs re-observation post-deploy |

## What is NOT done (per F1's "Done means")

- The SQL-prompt fix (Row 1, Row 2) has not been re-verified against a
  fresh live canary run, because it is not deployed yet — this PR must
  ship first.
- Per section 0.1's review gate: this PR must be reviewed and merged
  (site/API deploy) before a canary re-run can confirm 8/8, or a smaller
  remaining miss count with a written cause for each.
- Row 4 is an open item, not a fix — see above.
