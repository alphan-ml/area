# AREA v0 — headline facts (SPEC-area.md §3.4)

Five numbers, each computed by the hand-written SQL pasted below, run
against the real `payments` table. These are the golden anchors used to
build `data/golden.jsonl` (task B4) and to sanity-check the agent's
answers later.

**Status: PENDING.** The SQL below is drafted and ready to run (per task
W-B1's scope: "facts.md SQL drafted"), but no value is filled in yet
because the real CMS Open Payments data has not been pulled and loaded
into Postgres yet — that is a separate, explicit decision (see
`CONTEXT.md`'s open items; it needs Leon's go-ahead because the real pull
is ~37.56 GB across 5 years and can only run from a machine with network
access to `download.cms.gov`). Per the BUILD INSTRUCTION's non-negotiable
rule against fake or invented numbers, no placeholder figures are written
here — each value below is filled in for real, from a real query run
against real loaded data, with the run date noted, once that pull and load
have happened.

## 1. Total dollars, 2021–2025

```sql
SELECT SUM(amount_usd) AS total_usd
FROM payments
WHERE program_year BETWEEN 2021 AND 2025;
```

**Value:** PENDING — awaiting real data load.

## 2. Top specialty by dollars

```sql
SELECT physician_specialty, SUM(amount_usd) AS total_usd
FROM payments
WHERE physician_specialty IS NOT NULL
GROUP BY physician_specialty
ORDER BY total_usd DESC
LIMIT 1;
```

**Value:** PENDING — awaiting real data load.

## 3. Top state by dollars

```sql
SELECT physician_state, SUM(amount_usd) AS total_usd
FROM payments
WHERE physician_state IS NOT NULL
GROUP BY physician_state
ORDER BY total_usd DESC
LIMIT 1;
```

**Value:** PENDING — awaiting real data load.

## 4. Dollars per quarter (the series)

```sql
SELECT quarter, SUM(amount_usd) AS total_usd
FROM payments
WHERE quarter IS NOT NULL
GROUP BY quarter
ORDER BY quarter;
```

**Value:** PENDING — awaiting real data load. (This is the series
`forecast/build_series.py` will read from, once task B2 builds it.)

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

**Value:** PENDING — awaiting real data load.

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
