# AREA — Automated Research Evaluation Assistant

v0 instance: GLP-1 manufacturer payments to U.S. clinicians, built from CMS
Open Payments' public "general payments" dataset, program years 2021–2025.
Full spec: `SPEC-area.md` (Drive HQ). This README documents what is
**actually built so far** (tasks W-B1, W-B2); see `CONTEXT.md` for the
decision log, open items, and per-task reports.

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
(`tests/test_load_idempotent.py`) and the query tool's real-execution
tests (`tests/test_query_tool_guardrails.py`) need a real scratch
Postgres — set `TEST_DATABASE_URL` (see `.env.example`) or they skip
cleanly with a stated reason. CI (`.github/workflows/ci.yml`) runs them
for real against a `postgres:16` service container on every push.

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

## What's built (W-B2)

- `src/area/providers/` — the same model-call provider layer as
  model-bench (bedrock/anthropic_direct/openai_compatible/fake), vendored
  with only the import namespace changed (per SPEC-area.md's own
  instruction to copy or vendor it). `tests/test_providers.py` mirrors
  model-bench's own provider tests: every network boundary is faked, never
  real.
- `src/area/tools/` — the three tools SPEC-area.md section 4 defines,
  each a plain `(name, description, input_schema, fn)` `Tool` the later
  agent loop (W-B4) will call, plus `registry()` (`tests/test_tools_registry.py`):
  - `query_tool.py` — question → SQL (model-generated, injectable for
    tests) → validated → executed read-only → rows + `evidence_id`. The
    validator (`validate_and_normalize`) rejects anything that isn't
    exactly one `SELECT`, any reference outside the allow-listed
    tables/columns/functions, and multi-statement chains, and adds
    `LIMIT 5000` when missing — defense-in-depth on top of
    `sql/001_schema.sql`'s read-only `area_reader` role and its 10s
    statement timeout (`CONTEXT.md` decision D-query-1).
    `tests/test_query_tool_guardrails.py` covers the validator with zero
    secrets/network, plus two tests against a real scratch Postgres.
  - `forecast_tool.py` — a read-only lookup into `forecast/latest.json`;
    it never computes a forecast itself. Returns a clear error (never a
    fabricated number) if that file or the requested series doesn't
    exist yet. `tests/test_forecast_tool.py`.
  - `citation_checker.py` — checks every non-year number in a drafted
    answer against its inline `[evidence_id]`/`[derivation_id]` marker,
    verifying it against the cited evidence payload (0.5% relative or
    displayed-precision tolerance) or recomputing a stated derivation
    expression with a restricted AST evaluator (never Python `eval`).
    Wire format documented as `CONTEXT.md` decision D12.
    `tests/test_citation_checker.py`.
- `src/area/forecast/` — the forecast pipeline `area forecast` runs:
  `models.py` (`seasonal_naive`, `ets`, both with an 80% interval —
  `point ± 1.2816×residual_std`, `CONTEXT.md` decision D-forecast-1),
  `holdout.py` (train ≤2024Q4 / test 2025Q1–Q4, MAE + 80% coverage for
  both models, picks the lower-MAE model as primary, writes
  `forecast/latest.json`), and `build_series.py` (builds the national and
  top-N-specialty quarterly series from raw pulled `matched.jsonl.gz`
  files directly — not from Neon — since no database exists yet before
  Gate 1; `CONTEXT.md` decision D-forecast-2).
  `tests/test_forecast_holdout.py`, `tests/test_build_series.py`.
- `src/area/cli.py` additions — `area tools-test` (smoke-tests all three
  tools and prints the 5 headline facts from `data/facts.md`, live
  against a configured database, or an honest "NOT AVAILABLE"/"PENDING"
  when one isn't configured — never a fake number) and `area forecast`
  (runs the pipeline above end-to-end and writes `forecast/latest.json`).

**Full-repo test status: 128/128 passed** (`python3 -m ruff check .` and
`python3 -m pytest -q`), including real-Postgres tests for both the
loader (W-B1) and the query tool (W-B2). See `CONTEXT.md`'s W-B2 report
for the exact commands and honest disclosure of what still can't run for
real (Gate 1: no database, no pulled data yet).

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

The agent loop (planner/actor/verifier), golden-set evals, trace writers
(W-B4); the web page and its Vercel functions (W-B5); the causal
estimator simulation (W-B3, Sunday). See `SPEC-area.md` section 10 for
the full task split. Also still open regardless of task: the real CMS
Open Payments pull, and the real Neon database — both explicitly
deferred to Gate 1 (`CONTEXT.md` decision D11), so `area tools-test`'s
query_tool/facts and `area forecast`'s series are honestly "NOT
AVAILABLE"/"PENDING" until then, not fabricated.
