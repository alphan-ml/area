# AREA — Automated Research Evaluation Assistant

v0 instance: GLP-1 manufacturer payments to U.S. clinicians, built from CMS
Open Payments' public "general payments" dataset, program years 2021–2025.
Full spec: `SPEC-area.md` (Drive HQ). This README documents what is
**actually built** in the tree today (data layer, tools, forecast, causal
estimator, agent loop, evals, CLI, and the web app -- every task in the
original v0 split, W-B1 through W-B5); see `CONTEXT.md` for the decision
log, open items, and per-task reports, and "Evals status" below for why
"built" still doesn't mean "verified against real data."

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

## What's built

Rewritten 2026-09-17 (task A1 part 2) directly from the tree, not from
task history — each line was verified by reading the module it names.
See `CONTEXT.md`'s per-task reports (W-B1 through W-B5) for how it got
built, and "Evals status" below for the one thing that's built but not
yet verified against real data.

**Data layer**
- `data/products.json` / `src/area/match.py` — the GLP-1 product match
  list (generic names `semaglutide`/`tirzepatide`; brand names
  `Ozempic`, `Wegovy`, `Rybelsus`, `Mounjaro`, `Zepbound`) and the
  case-insensitive substring match rule against Open Payments' product
  name/category fields.
- `src/area/pull_open_payments.py` — streaming, resumable pull of each
  CMS Open Payments program year, filtered to the GLP-1 list; verified
  live against the real public API (see "CMS Open Payments dataset
  facts" below). `pull_year_stream`/`_S3MultipartTee` additionally tee
  the raw CSV to S3 while filtering it, so a year's raw multi-GB file is
  never buffered whole on local disk (D17).
- `sql/001_schema.sql` / `sql/002_views.sql` — the `payments` table, its
  5 indexes, the read-only `area_reader` role (10s statement timeout),
  and the `q_totals`/`q_by_specialty`/`q_by_state` aggregate views.
- `src/area/load_neon.py` — idempotent upsert-on-`record_id` loader from
  `raw/<year>/matched.jsonl.gz` into `payments`; re-running it against
  the same raw files changes nothing.
- `data/facts.md` — the 5 headline SQL queries from spec section 3.4;
  every value is still marked PENDING (no real pull has loaded rows —
  see "Evals status" and CONTEXT.md's Open items).

**Tools** (`src/area/tools/`, called by the agent loop below; registry in
`src/area/tools/__init__.py`)
- `query_tool.py` — question → model-generated SQL → validated
  (`validate_and_normalize`: single `SELECT` only, allow-listed
  tables/columns/functions only, `LIMIT 5000` enforced, multi-statement
  chains rejected) → executed read-only → rows + `evidence_id`.
- `forecast_tool.py` — a read-only lookup into `forecast/latest.json`;
  never computes a forecast itself, and returns a clear error rather
  than a fabricated number if the file or series doesn't exist yet.
- `citation_checker.py` — checks every non-year number in a drafted
  answer against its inline `[evidence_id]`/`[derivation_id]` marker,
  matching cited evidence within tolerance (0.5% relative or displayed
  precision) or recomputing a stated derivation with a restricted-AST
  evaluator (never Python `eval`). This is `AgentTrace.accepted`'s hard
  gate — see the agent loop below.

**Forecast** (`src/area/forecast/`)
- `models.py` — `seasonal_naive` and `ets`, each with an 80% interval
  (`point ± 1.2816×residual_std`).
- `holdout.py` — trains through 2024Q4, tests 2025Q1–2025Q4, reports MAE
  + 80% coverage for both models, picks the lower-MAE model as primary
  per series, and writes `forecast/latest.json` (this is `area
  forecast`'s entry point).
- `build_series.py` — builds the national and top-N-specialty quarterly
  series directly from raw pulled `matched.jsonl.gz` files (not from
  Neon — no database exists to build from yet, D14).

**Causal** (`src/area/causal/diff_in_diff.py`) — a standard 2-group/
2-period difference-in-differences estimator: splits real matched rows
into a treated group (default: the manufacturers of record) and a
control group by quarter, and computes `(treated_after - treated_before)
- (control_after - control_before)` over a given or auto-median event
quarter. States its own parallel-trends caveat in its output
(`parallel_trend_note`) rather than assuming it away, and does not claim
to have found a real-world policy effect — the payments data alone has
no outcome variable to make that claim about (see the module's own
docstring). This is `area causal`'s entry point.

**Agent loop** (`src/area/agent/loop.py`) — `run_agent(question, ...)`: a
bounded (`MAX_STEPS=4`) planner/actor/composer/verifier loop over the
real tool registry. Every model call goes through `area.providers`; the
composed answer is only marked `AgentTrace.accepted` after
`citation_checker.check()` verifies it — per spec section 1's hard gate,
an answer with an unverified number is never shown as trustworthy. This
is `area run "<question>"`'s entry point.

**Evals** (`src/area/evals/`)
- `cases.py` — the 8 fixed real research questions (mirrors
  `data/facts.md`'s 5 headline queries plus 3 more); each now also
  carries a `gold` value (or `None` while unverified — see "Evals
  status"), `expect_abstain`, `status`, and `parts` scoring metadata
  (task A1).
- `scoring.py` — the four independent per-question scores
  (`correct`/`complete`/`retrieval_ok`/`abstained`) and the combined
  `passed` rule (task A1) — see its own module docstring for the exact
  rule and the three-valued logic behind "not scored."
- `runner.py` — `run_evals()` (runs `CASES` through the real agent loop,
  scores each with `scoring.score_case`, writes one trace file per case)
  and `score_from_traces()` (re-scores already-written trace files with
  no agent loop, model, or database call at all — `area evals
  --from-traces`).
- `traces.py` — `write_trace`/`read_trace`: one JSON file per run under
  `evals/traces/<case_id>.json`.

**Providers** (`src/area/providers/`) — the same model-call provider
layer as model-bench (bedrock/anthropic_direct/openai_compatible/fake)
behind one `Provider` interface, vendored with only the import namespace
changed; every real network call goes through `call_with_retries`.

**CLI** (`src/area/cli.py`) — `area pull|load|tools-test|forecast|run
"<question>"|evals|causal`. Every subcommand in the original task split
is wired; none prints a "not built yet" stub anymore.

**Web** (`web/`) — an independent JS implementation of the read-only
query + citation-check + facts logic (per model-bench's own
`web/package.json` rule, "Python never deploys here" — this is not a
Python subprocess): `lib/guardrail.js` and `lib/citation.js` port
`query_tool.py`'s validator and `citation_checker.py`'s check;
`lib/bedrock.js`/`lib/db.js` wrap Bedrock Converse and `pg`;
`api/health.js`, `api/facts.js`, `api/ask.js` are the three Vercel
functions; `index.html` is a plain-JS front page with no build step.
`vercel build` passes locally; **not deployed** (see CONTEXT.md's W-B5
report for the exact deploy command and a real finding about a stale
auto-linked Vercel project that must not be deployed to as-is).

**Full-repo test status (this session, 2026-09-17): 173 passed, 11
skipped** (`uv run ruff check .` clean; `uv run pytest -q`) — up from
128/128 at the last time this README was updated (W-B2). The 11 skips
are the loader/query-tool tests that need a real scratch Postgres
(`TEST_DATABASE_URL`, unset in this environment) and skip cleanly with a
stated reason rather than silently passing; CI runs them for real
against a `postgres:16` service container on every push.

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
network connection (verified from the owner's actual Mac). The real pull, when
it runs, needs to run somewhere with real network access to these hosts —
see `CONTEXT.md`.

## Not yet built

Rewritten 2026-09-17 (task A1 part 2): nothing from the original v0 task
split (W-B1 through W-B5 — agent loop, evals, trace writers, the web
page and its Vercel functions, the causal estimator) remains unbuilt in
code; see "What's built" above for what each one actually does. What's
still outstanding is data and deploy, not code:

- The real ~37.56 GiB/5-year CMS Open Payments pull has not successfully
  loaded any rows into Postgres (`CONTEXT.md`'s Open items) — `payments`
  has 0 real rows, so `area tools-test`'s query_tool/facts, `area
  forecast`'s series, `area causal`'s estimate, and every `area
  evals`/`area run` answer above are all honestly "NOT AVAILABLE"/
  "PENDING"/"no data available yet" rather than fabricated.
- `web/` has never been `vercel deploy`'d (see its own "What's built"
  entry above for the documented, not-yet-run deploy command).
- Verified `gold` values for the 8 fixed eval questions in
  `src/area/evals/cases.py` — see "Evals status" below.

## Evals status

`evals/summary.json` was regenerated this session (`area evals
--from-traces`, task A1 part 1) under the new correctness/completeness/
retrieval/abstention scoring in `src/area/evals/scoring.py`. It reports
**0 passed, 8 not scored** — every one of the 8 fixed questions still has
`gold: null` (no verified true value exists yet, because the real data
pull above hasn't loaded rows), so none can be scored a pass under the
new rule, even though every answer is an honest, citation-clean
abstention. This summary **predates the real data load and counts as not
passed** — it is not evidence the agent works, only that it fails safely
with no data. The old summary this replaced reported "8/8 passed" under
a rule that only checked for citation-clean text, which is exactly the
gap this task closed.

## Live Eval canary

giggitai.com's Live Eval tab is a public ledger of scheduled evaluation
runs against systems' live endpoints, replayed on the site as a
terminal. `canary/canary.workflow.yml.txt` is this repo's contribution
to that: once moved to `.github/workflows/canary.yml` (see that file's
own note on why it isn't there yet), it runs `area canary`
(`src/area/canary.py`) on a schedule (`17 */6 * * *`, every 6 hours) and
on demand (`workflow_dispatch`).

What it does, each run:

- Reads the 8 fixed questions in `canary/rows.json`. These are the same
  8 questions as `src/area/evals/cases.py`'s question set — the
  gold-blind set written before any real data was loaded and never used
  to prompt-engineer `web/api/ask.js`'s SQL-writing model call, so it is
  this system's held-out eval split. `tests/test_canary.py` proves the
  two id sets match exactly.
- For each question, runs that row's own committed gold SQL against the
  real, currently-loaded database (`DATABASE_URL`) to get the real gold
  number(s) **at run time** — never a stored or guessed number.
- Calls the live endpoint, `POST https://giggitai.com/api/area-ask`,
  with the same question text.
- Scores the answer: a row is `correct` if every number the answer
  states is within 0.5% of one of that row's gold numbers, and the
  citation check passed (`accepted: true`, `unverified: []`). A row
  whose gold SQL finds no data (its years aren't loaded yet) is instead
  `correct` if the agent honestly abstains — an abstention always counts
  as correct for that row.
- Prints one JSON record — `ts`, `release` (short git sha of `main`),
  `metric: "correct"`, `recorded`, `observed`, `tolerance`, `match`,
  `p50_ms`/`p95_ms`, `errors`, `duration_s`, up to 8 terminal `lines`,
  and an `extra.rows` breakdown of `cited`/`retrieval_ok`/`abstained`/
  `latency_ms` per question — then appends it to `ledger/runs.jsonl` and
  overwrites `ledger/latest.json` on this repo's own orphan `ledger`
  branch (created empty the first time this workflow runs). Every commit
  to that branch is authored and committed as
  `Alpha N <45754668+alphan-ml@users.noreply.github.com>`.

Where the ledger is: the `ledger` branch of this repo —
`ledger/latest.json` (the most recent run) and `ledger/runs.jsonl` (one
JSON object per line, oldest first). giggitai.com reads both files
straight from `raw.githubusercontent.com`.

**`recorded` is a definition, not a measured value.** All 8 canary
questions are specified to be answerable, or correctly abstainable,
every time — so 8 out of 8 is the passing bar the system is held to, not
a number read back from a training run. The metric's own `tolerance` is
0: `match` is only `true` when `observed` is exactly 8. (A separate,
per-number tolerance of 0.5% is what decides whether one answer's stated
number matches that row's gold number — see above.)

How to read `match`: `true` means the live endpoint answered every one
of the 8 canary questions correctly (or correctly abstained) against the
real database, right now. `false` means at least one row was wrong,
uncited, or errored — check that run's `extra.rows` and `lines` for
which one and why. An endpoint or database error is always recorded as
an error for that row, never papered over with a fallback number.

Run it by hand: `area canary` (reads `DATABASE_URL` from the
environment, calls the live endpoint, prints the record to stdout, and
exits 0 only when `match` is `true`).
