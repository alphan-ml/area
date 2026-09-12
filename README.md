# AREA — Automated Research Evaluation Assistant

v0 instance: GLP-1 manufacturer payments to U.S. clinicians, built from CMS
Open Payments' public "general payments" dataset, program years 2021–2025.
Full spec: `SPEC-area.md` (Drive HQ). This README documents what is
**actually built so far** (task W-B1); see `CONTEXT.md` for the decision
log, open items, and per-task reports.

## How to run

```bash
pip install -e ".[dev]"
python3 -m ruff check .
python3 -m pytest -q
```

Use `python3 -m ruff` / `python3 -m pytest`, not the bare `ruff`/`pytest`
commands, for the same PATH-isolation reason noted in model-bench's
CONTEXT.md.

Most tests need no network or database. The loader's idempotency tests
(`tests/test_load_idempotent.py`) need a real scratch Postgres — set
`TEST_DATABASE_URL` (see `.env.example`) or they skip cleanly with a
stated reason. CI (`.github/workflows/ci.yml`) runs them for real against
a `postgres:16` service container on every push.

## What's built (W-B1)

- `data/products.json` — the GLP-1 product match list (generic names
  `semaglutide`/`tirzepatide`; brand names `Ozempic`, `Wegovy`,
  `Rybelsus`, `Mounjaro`, `Zepbound`) and the case-insensitive substring
  match rule against Open Payments' product name/category fields.
  `src/area/match.py` implements it; `tests/test_products_match.py`
  covers it.
- `src/area/pull_open_payments.py` — pulls each program year's CMS Open
  Payments general-payment data and filters it to the GLP-1 list,
  streaming (never buffering a whole year's multi-GB file), writing
  `raw/<year>/matched.jsonl.gz` + `raw/<year>/manifest.json`. Verified
  against the REAL live public API (no key needed) — see "CMS Open
  Payments dataset facts" below and `CONTEXT.md`'s decision log for how
  the pull's actual design differs from the spec's literal
  paginated-API wording, and why. `tests/test_pull_open_payments.py`
  covers it fully offline via an injectable HTTP layer.
- `sql/001_schema.sql` / `sql/002_views.sql` — the `payments` table, its
  5 indexes, the read-only `area_reader` role, and the `q_totals` /
  `q_by_specialty` / `q_by_state` aggregate views.
- `src/area/load_neon.py` — loads `raw/<year>/matched.jsonl.gz` into the
  `payments` table via upsert on `record_id`; re-running against the same
  raw files changes nothing. `tests/test_load_idempotent.py` proves this
  against a real Postgres, not a mock.
- `data/facts.md` — the 5 headline SQL queries from spec section 3.4,
  drafted and ready to run; every value is marked PENDING until the real
  data pull (below) actually happens — see the note there on why no
  placeholder numbers are written.
- `src/area/cli.py` — `area pull` and `area load` (the two subcommands
  this task's own modules implement); every other subcommand
  (`tools-test`, `run`, `evals`, `forecast`, `causal`) prints a plain
  "not built yet" message naming the task that adds it, instead of
  failing to import.

## CMS Open Payments dataset facts (verified live, 2026-09-12)

Discovered from `openpaymentsdata.cms.gov`'s metastore endpoint at run
time (per spec section 3.2) and re-confirmed live immediately before this
README was written:

| Program year | Dataset identifier | File size | Columns |
|---|---|---|---|
| 2021 | `0380bbeb-aea1-58b6-b708-829f92a48202` | 6.46 GB (6.02 GiB) | 91 |
| 2022 | `df01c2f8-dc1f-4e79-96cb-8208beaf143c` | 7.44 GB (6.93 GiB) | 91 |
| 2023 | `fb3a65aa-c901-4a38-a813-b04b00dfa2a9` | 8.23 GB (7.67 GiB) | 91 |
| 2024 | `e6b17c6a-2534-4207-a4a1-6746a14911ff` | 8.97 GB (8.35 GiB) | 91 |
| 2025 | `fb0b1734-1410-429d-92f6-3f4b35218e5e` | 9.23 GB (8.59 GiB) | 91 |

Total: **40.33 GB (37.56 GiB)** across all 5 years. Each dataset's
`modified` date is 2026-06-30. There is no paginated or server-side
filterable query API for these "general payment" datasets — each program
year is one single bulk CSV download; see `CONTEXT.md`'s decision log for
what this changed about the pull's design versus the spec's literal
wording, and why it still satisfies every non-negotiable in section 1.

**Row counts per product per year** (the spec's own non-negotiable,
section 1) will be published here once the real pull actually runs — see
`CONTEXT.md`'s open items for why that hasn't happened yet (it's a
resource-cost decision, not a code gap).

## Network access note

`openpaymentsdata.cms.gov` and `download.cms.gov` are unreachable from
this build environment's cloud workspace and from its Mac-VM sandbox
shell (both return proxy 403s) but ARE reachable from a real, unrestricted
network connection (verified from Leon's actual Mac). The real pull, when
it runs, needs to run somewhere with real network access to these hosts —
see `CONTEXT.md`.

## Not yet built

Everything past W-B1: the tools registry, query tool, forecast tool,
citation checker (W-B2); the agent loop, golden-set evals, trace writers
(W-B4); the web page and its Vercel functions (W-B5); the causal
estimator simulation (W-B3, Sunday). See `SPEC-area.md` section 10 for
the full task split.
