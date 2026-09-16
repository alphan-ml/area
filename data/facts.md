# AREA v0 — headline facts (SPEC-area.md §3.4)

Five numbers, each computed by the hand-written SQL pasted below, run
against the real `payments` table. These are the golden anchors used to
build `data/golden.jsonl` (task B4) and to sanity-check the agent's
answers later.

**Status: PENDING (real, live-queried, but the table is empty).** Gate 1 is reached and the real pull was authorized (D17), but as of this update it has NOT loaded any rows: the pull crashed before its first real database write, and this session traced why in CONTEXT.md's W-B4/W-B3 reports — in short, running the test suite against this environment's `TEST_DATABASE_URL` drops the `payments` table, because `TEST_DATABASE_URL` and `DATABASE_URL` point at the SAME real database in this environment, not a separate scratch one (flagged as a real environment-configuration finding, not fixed here per this task's own no-config-file-edits rule).

The values below are real, live query results against the real (currently 0-row) `payments` table (`area tools-test`, 2026-09-16, this session) — not placeholders, and not the earlier same-day "relation does not exist" error CONTEXT.md's Open items describes. **Year coverage: none loaded (0 of the 5 program years 2021-2025).** Per the BUILD INSTRUCTION's non-negotiable rule against fake or invented numbers, no estimated figure is written here -- each value below is the literal, real SQL result (NULL/no rows) against the currently-empty table, and will be replaced with real non-NULL numbers, with year coverage stated, the moment the pull actually loads rows (command is re-runnable as-is: `area pull --stream --s3-bucket giggit-area-raw-payments`; see CONTEXT.md's Open items for why it needs to be started by hand).

## 1. Total dollars, 2021–2025

```sql
SELECT SUM(amount_usd) AS total_usd
FROM payments
WHERE program_year BETWEEN 2021 AND 2025;
```

**Value:** NULL (0 rows loaded; real, live query result, not a placeholder) -- year coverage: none of 2021-2025 loaded yet.

## 2. Top specialty by dollars

```sql
SELECT physician_specialty, SUM(amount_usd) AS total_usd
FROM payments
WHERE physician_specialty IS NOT NULL
GROUP BY physician_specialty
ORDER BY total_usd DESC
LIMIT 1;
```

**Value:** (no rows) -- 0 rows loaded, year coverage: none.

## 3. Top state by dollars

```sql
SELECT physician_state, SUM(amount_usd) AS total_usd
FROM payments
WHERE physician_state IS NOT NULL
GROUP BY physician_state
ORDER BY total_usd DESC
LIMIT 1;
```

**Value:** (no rows) -- 0 rows loaded, year coverage: none.

## 4. Dollars per quarter (the series)

```sql
SELECT quarter, SUM(amount_usd) AS total_usd
FROM payments
WHERE quarter IS NOT NULL
GROUP BY quarter
ORDER BY quarter;
```

**Value:** (no rows) -- 0 rows loaded, year coverage: none. (This is the series `forecast/build_series.py` reads from -- built in W-B2, still no real rows to read yet.)

## 5. Share of dollars that are "Food and Beverage"

```sql
SELECT
    ROUND(
        100.0 * SUM(amount_usd) FILTER (WHERE nature_of_payment = 'Food and Beverage')
        / NULLIF(SUM(amount_usd), 0),
        2
    ) AS food_and_beverage_share_pct
FROM payments;
```

**Value:** NULL (0 rows loaded; the NULLIF divide-by-zero guard is exercised for real right now, not hypothetically) -- year coverage: none.

## Notes on these queries

- All five read directly from the `payments` table (`sql/001_schema.sql`),
  the same table the query tool (task B2) will query through the
  read-only `area_reader` role.
- Queries 2–4 exclude `NULL` specialty/state/quarter rows explicitly
  rather than silently including them in a catch-all group, so the
  numbers stay traceable to real, well-formed rows; the `payments` table
  itself still keeps every matched row regardless (§1's "no sampling"
  non-negotiable — see `CONTEXT.md`'s note on nullable `quarter`).
- Query 5 uses `NULLIF(SUM(amount_usd), 0)` only as divide-by-zero
  protection for the (currently true) empty-table case; once real data is
  loaded the denominator will never be zero.
