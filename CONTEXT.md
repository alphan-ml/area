# CONTEXT

Working notes for whoever (human or agent) picks this repo up next. Read this
before touching anything. Full spec: `SPEC-area.md` (Drive HQ).

## How to run

```bash
pip install -e ".[dev]"
python3 -m ruff check .
python3 -m pytest -q
```

No secrets or network needed for `ruff`/most of `pytest`. The loader's
idempotency tests need a real scratch Postgres via `TEST_DATABASE_URL`
(see `.env.example`) — unset, they skip cleanly with a stated reason
rather than silently passing. CI sets it against a `postgres:16` service
container.

## CMS Open Payments dataset facts (verified live, 2026-09-12T23:09:30Z)

Discovered from `openpaymentsdata.cms.gov`'s metastore endpoint
(`/api/1/metastore/schemas/dataset/items`) at run time, per spec section
3.2, and re-verified live (dataset identifiers, download URLs, exact file
sizes via a `Range: bytes=0-0` request's `Content-Range` header, and the
real CSV header's column count via a `Range: bytes=0-4095` request)
immediately before this file was written, from a real Mac terminal (see
"Network egress" below for why not from this build environment directly):

| Program year | Dataset identifier | Download URL (download.cms.gov) | Size (bytes) | Size (GB / GiB) | Columns | `modified` |
|---|---|---|---|---|---|---|
| 2021 | `0380bbeb-aea1-58b6-b708-829f92a48202` | `.../openpayments/PGYR2021_P06302026_06032026/OP_DTL_GNRL_PGYR2021_P06302026_06032026.csv` | 6,463,248,098 | 6.46 GB / 6.02 GiB | 91 | 2026-06-30 |
| 2022 | `df01c2f8-dc1f-4e79-96cb-8208beaf143c` | `.../openpayments/PGYR2022_P06302026_06032026/OP_DTL_GNRL_PGYR2022_P06302026_06032026.csv` | 7,439,205,273 | 7.44 GB / 6.93 GiB | 91 | 2026-06-30 |
| 2023 | `fb3a65aa-c901-4a38-a813-b04b00dfa2a9` | `.../openpayments/PGYR2023_P06302026_06032026/OP_DTL_GNRL_PGYR2023_P06302026_06032026.csv` | 8,231,891,293 | 8.23 GB / 7.67 GiB | 91 | 2026-06-30 |
| 2024 | `e6b17c6a-2534-4207-a4a1-6746a14911ff` | `.../openpayments/PGYR2024_P06302026_06032026/OP_DTL_GNRL_PGYR2024_P06302026_06032026.csv` | 8,970,316,799 | 8.97 GB / 8.35 GiB | 91 | 2026-06-30 |
| 2025 | `fb0b1734-1410-429d-92f6-3f4b35218e5e` | `.../openpayments/PGYR2025_P06302026_06032026/OP_DTL_GNRL_PGYR2025_P06302026_06032026.csv` | 9,227,000,332 | 9.23 GB / 8.59 GiB | 91 | 2026-06-30 |

**Total: 40,331,661,795 bytes = 40.33 GB (decimal) = 37.56 GiB, across the
5 years.** These are one-shot bulk CSV downloads with no dataset-level
pagination or server-side product filtering — see D1 below for what that
changed about the pull's design.

Full CSV header (all 91 columns, exact, from the 2021 file — the other 4
years share the identical header) is not reproduced here to keep this
file short; `pull_open_payments.py`'s `process_row()` and `match.py`
document every column name it actually reads. The full column list is
recoverable at any time via the day-1 gate the pull script prints when it
runs (spec section 3.2), or by re-running the same live verification
(a `Range: bytes=0-4095` GET against any `download_url` above).

### Network egress

`openpaymentsdata.cms.gov` and `download.cms.gov` are unreachable (proxy
403) from both this build environment's cloud workspace and its Mac-VM
sandbox shell — confirmed again today (cloud workspace: `curl` to the
metastore endpoint returned `CONNECT tunnel failed, response 403` at
2026-09-12 ~23:08 UTC). They ARE reachable from a real, unrestricted
network connection — the table above was captured live from Leon's actual
Mac terminal (via the `Control_your_Mac` osascript tool, not the sandboxed
Mac-VM `device_bash` shell, which is a separate, also-blocked
environment). Model-bench's own CONTEXT.md previously listed
`openpaymentsdata.cms.gov` as blocked "per Fable's measurement" without
noting this real-Mac path existed; that entry has been corrected there
too (see model-bench's CONTEXT.md).

**Practical consequence**: the real ~37.56 GiB / 5-year pull (§10, task
W-B1) can only run from a machine with genuine network access to these
two hosts — i.e. Leon's real Mac, not this build environment's sandboxes.
It has NOT been run yet — see Open items below.

## Decisions

- D1 — 2026-09-12 — **Disclosed judgment call, spec vs. reality**:
  `SPEC-area.md` §3.2 assumes a paginated/server-side-filterable Open
  Payments query API ("page the full year and filter client-side...the
  pull is paged, resumable (page cursor persisted)...writes raw pages to
  `raw/<year>/page_<n>.json.gz`"). Live verification (this file's dataset
  table above) shows the real "General Payment Data" datasets have no
  such API at all — CMS's platform here is DKAN, not Socrata, and each
  program year is exactly one bulk CSV file, 6.46–9.23 GB, with no
  pagination. `pull_open_payments.py` streams that one file per year
  (never buffering it whole — see its module docstring) and filters
  client-side to the GLP-1 list, matching the spec's fallback path
  ("otherwise page the full year and filter client-side") in substance,
  though there is no literal "page" to persist a cursor for.
- D2 — 2026-09-12 — Following from D1: resumability is YEAR-granular, not
  page/byte-granular. `raw/<year>/manifest.json`'s `"complete": true` is
  the resume marker; an interrupted year is fully re-pulled, not resumed
  mid-file. Reasoning (also in the module docstring): a streaming CSV
  reader is buffered ahead of the row currently being processed, so a
  byte offset recorded "after processing row N" already includes bytes
  for rows N+1, N+2, ... pulled into that buffer — resuming a Range
  request from that offset would silently skip those rows. Full-year
  re-pull (minutes to a couple of hours per year) is slower but cannot
  silently lose rows. Up to 5 in-year retries with backoff (1/2/5/15/30s)
  happen first, before falling back to a full restart.
- D3 — 2026-09-12 — Raw output is `raw/<year>/matched.jsonl.gz` (already
  GLP-1-filtered) rather than the spec's literal `raw/<year>/page_<n>.
  json.gz` (unfiltered raw pages). Storing the full unfiltered ~6.5–9.2 GB
  CSV per year as well would multiply this repo's/S3's storage footprint
  for no real gain: the source file is permanently, publicly
  re-downloadable from the recorded `download_url` + `identifier`, and
  every matched row's fields are kept exactly as CMS returned them
  (unmodified strings) before any type coercion happens in `load_neon.py`.
  `manifest.json` records the dataset identifier, download URL, pull
  timestamp, and per-product/per-manufacturer row counts as the pull's
  provenance trail. Flagging this because §1's "raw pulls are kept
  exactly as received" could be read as requiring the unfiltered bytes
  themselves, not just each kept row's unmodified field values.
- D4 — 2026-09-12 — No S3 bucket exists yet (Gate 1: AWS credentials).
  Raw pulled data will live on local disk only until Gate 1;
  `AREA_RAW_S3_BUCKET` is documented, unset, in `.env.example`. Mirrors
  model-bench's own pre-Gate-1 posture.
- D5 — 2026-09-12 — `.gitignore`: `raw/*/*.jsonl.gz` is ignored (large,
  fully reproducible from the public API); `raw/*/manifest.json` is NOT
  ignored (small, is the audit trail, belongs in git even though the data
  it describes doesn't) — mirrors model-bench's `web/index.html` build-
  output precedent.
- D6 — 2026-09-12 — `sql/001_schema.sql` originally had `CREATE ROLE
  area_reader LOGIN PASSWORD :'area_reader_password'` — psql
  meta-command variable-substitution syntax, which fails under psycopg or
  any non-psql-CLI DDL execution, and also risked reading as a
  hardcoded/templated secret in committed SQL. Fixed to a guarded `CREATE
  ROLE area_reader LOGIN;` (no password) with a comment stating the real
  password is set out-of-band (`ALTER ROLE ... PASSWORD`, run by hand
  against the real Neon database, or via Neon's own role/console API —
  never committed).
- D7 — 2026-09-12 — `payments.quarter` is nullable, not spec section
  3.3's plain `text`. `quarter` is derived from `payment_date` via
  `quarter_of()`, which returns `None` for a blank/unparseable date.
  Section 1's "full data, no sampling" non-negotiable means a row with a
  bad date still belongs in the table — `load_neon.py` must not drop it
  or invent a quarter for it — so the column has to accept `NULL`.
- D8 — 2026-09-12 — `psycopg[binary]` moved into the `dev` extras
  starting in W-B1, not held back for W-B4 as first planned in an early
  `pyproject.toml` comment. Proving `load_neon.py`'s upsert-on-`record_id`
  idempotency for real needs a real `ON CONFLICT DO UPDATE` against a
  real database — a mock can't verify conflict-resolution behavior.
  `load_neon.py` still imports psycopg lazily at call time (same pattern
  as model-bench's boto3, its D10), so this stays a dev/test-time
  dependency, not a hard runtime one, until W-B4 wires the live Neon
  database. `tests/test_load_idempotent.py`'s DB-backed tests read
  `TEST_DATABASE_URL` and skip cleanly, with a stated reason, when unset;
  `.github/workflows/ci.yml` sets it against a `postgres:16` service
  container so these tests run for real on every push, not just locally.
- D9 — 2026-09-12 — `src/area/cli.py` was built in W-B1, ahead of the
  modules its other subcommands need. `pyproject.toml` already declares
  `area = "area.cli:main"` as the installed console script (added when
  the repo was first scaffolded), so leaving `cli.py` unwritten would
  mean the installed `area` command fails to import at all. Wired only
  `pull` and `load` (this task's own deliverables); every other
  subcommand (`tools-test`, `run`, `evals`, `forecast`, `causal`) prints
  a plain "not built yet -- see SPEC-area.md task <X>" message and exits
  1, so the CLI is honest about its own scope rather than silently doing
  nothing or crashing.
- D10 — 2026-09-12 — `data/facts.md`'s 5 SQL queries (spec §3.4) are
  drafted and ready to run, but every value is marked **PENDING**, not a
  placeholder or estimated figure, because no real CMS data has been
  pulled or loaded yet (see Open items). Filling in even an
  order-of-magnitude guess here would violate the BUILD INSTRUCTION's
  "no fake/invented numbers, ever" rule.

- D11 — 2026-09-12 — **Leon's decision: the real ~37.56 GiB / 5-year CMS
  pull waits for Gate 1** (asked directly in chat after the W-B1 report;
  options were "run it now", "run one year first", or "wait until Gate
  1" — Leon chose the third). Reason given by the option itself: once
  S3/Neon credentials exist, the real data can go straight into
  permanent storage instead of sitting on his Mac first. This replaces
  the earlier "needs Leon's go-ahead" open item below with a settled
  plan — no pull runs until Gate 1 is reached, at which point it needs
  its own confirmation to start (not an automatic trigger).

- D12 — 2026-09-13 — **Disclosed judgment call, citation-marker wire
  format (W-B2)**: SPEC-area.md §4.4/§5.3 describe the citation-checker's
  job (verify a claimed number against cited evidence or a stated
  derivation) but don't pin down how a number in the answer text
  connects to a specific `evidence_id`/`derivation_id`. Built: an inline
  marker immediately following the number, e.g. `$1,234.56 [q_ab12cd34]`
  or `24.7% [d_share1]`, looked up first against `evidence`, then
  `derivations`. A number with no marker, or an unmatched marker id,
  fails with a stated reason. `src/area/tools/citation_checker.py`'s own
  module docstring has the full design notes; flag if the agent loop
  (W-B4) needs a different composer-output convention.

- D13 — 2026-09-13 — **Disclosed judgment call, forecast interval
  methodology (W-B2)**: the spec asks for "an 80% interval" for both
  `seasonal_naive` and `ets` without specifying how to compute one. Built:
  a symmetric interval, `point ± 1.2816 × residual_std` (the two-sided
  80% normal critical value), using each model's own in-sample one-step-
  ahead residual standard deviation — so the two models' holdout coverage
  numbers are directly comparable. `src/area/forecast/models.py`.

- D14 — 2026-09-13 — **Disclosed judgment call, `build_series.py` reads
  raw files, not Neon (W-B2)**: the repo layout's own inline comment
  sketches a Neon-backed builder ("Neon -> quarterly series"), but
  task-split §10's actual W-B2 line says the forecast build "runs locally
  on DuckDB or pandas from raw" and gives an explicit reason ("no secrets
  for tests"). No Neon database exists yet regardless (Gate 1, D11), so
  built `build_series.py` to read `raw/<year>/matched.jsonl.gz` directly
  via `load_neon`'s own `iter_matched_rows`/`coerce_row` — the more
  specific, reasoned line, not a blocking spec contradiction. A future
  Neon-backed builder can be added alongside this one without changing
  `holdout.py`'s interface. `src/area/forecast/build_series.py`.

- D15 — 2026-09-13 — **Disclosed judgment call, query-tool guardrail is
  defense-in-depth, not a full SQL semantic analyzer (W-B2)**:
  `validate_and_normalize()` checks tables/views against an exact
  allow-list and flags any other bare identifier that isn't a known
  column/alias/keyword/function — it does not fully resolve per-table
  column scoping (e.g. it would not catch `payments.total_usd`, a column
  that exists on `q_totals` but not `payments`). A real SQL semantic
  analyzer/planner is out of scope for a v0 text-level guardrail whose
  job is to catch the spec's named attack shapes (injection strings,
  UPDATE/DELETE, multi-statement, a genuinely unknown column, missing
  LIMIT) — `sql/001_schema.sql`'s read-only `area_reader` role (SELECT-
  only, 10s `statement_timeout`) is the real backstop for anything a text
  validator gap might miss. Validated empirically against 26 hand-built
  SQL test cases (injection, DDL/DML, multi-statement, CTEs, joins,
  subqueries, comments) before formalizing into pytest.
  `src/area/tools/query_tool.py`.

- D16 — 2026-09-13 — **Real bug found and fixed while testing D15's
  guardrail against real Postgres**: running `data/facts.md`'s own
  query 5 (the "Food and Beverage share" query, spec §3.4) through
  `area tools-test` against a real scratch database, the validator
  rejected it — `FILTER (WHERE ...)` (Postgres's aggregate-filter clause,
  which `sqlparse` tokenizes as a plain, non-keyword `Name` followed by
  `(`, indistinguishable from a function call) and `NULLIF` (simply
  missing from `_ALLOWED_FUNCTIONS`) were both flagged as "not
  allow-listed." Fixed: added `nullif` to `_ALLOWED_FUNCTIONS`, and
  special-cased the literal word `filter` so it's recognized as the
  clause keyword it is rather than requiring it to be an allow-listed
  function — its parenthesized `WHERE` body is still fully checked like
  any other subexpression, so this doesn't loosen the column/table
  allow-list at all. Added
  `test_facts_md_query_5_with_filter_and_nullif_is_not_rejected` and
  `test_filter_clause_still_checks_its_column_reference` to
  `tests/test_query_tool_guardrails.py` so this exact spec-provided query
  never regresses. This was caught by testing against real data, not
  hypothetically — flagging in case any other real query later surfaces
  a similar sqlparse-tokenization gap.

- D17 -- 2026-09-16 -- **Project owner's decision: Gate 1 reached, real pull
  authorized (this session)**. CONTEXT.md's D11 deferred the real
  ~37.56 GiB/5-year CMS pull to Gate 1 (S3 + Neon credentials in place)
  and required a fresh explicit go-ahead once reached. Gate 1 is reached
  (S3 bucket `giggit-area-raw-payments` exists and is reachable; Neon/
  Postgres connection verified earlier the same day against a real
  scratch database, `uv run pytest` passing) and the project owner
  confirmed, in this session, to proceed with the real pull now. Local
  disk on the pull machine is tight (~18-25 GiB free against a ~37.56
  GiB/5-year raw total), so this also authorizes a **streaming** pull/
  load mode rather than the local-disk-buffered `pull_year`/`load_raw_dir`
  path W-B1 shipped: `pull_year_stream` (src/area/pull_open_payments.py)
  tees each year's raw CSV, chunk by chunk as it downloads, to an S3
  multipart upload (`s3://<bucket>/raw/<year>/<file>`) via
  `_S3MultipartTee`, in the same pass as the existing row-by-row filter,
  so the ~6.5-9.2 GB/year raw file is never buffered whole on local disk
  or in memory -- at most one multipart part (8 MiB) is held at a time.
  Matched rows are upserted straight into Postgres in that same pass when
  a database URL is given (`load_neon.coerce_row`/`UPSERT_SQL`, batched),
  reusing the exact idempotent upsert-on-`record_id` path `load_raw_dir`
  already used -- not a second, divergent write path. `matched.jsonl.gz`
  is still written locally per year (small -- matched rows only) as the
  audit trail D3 already established; only the ~6.5-9.2 GB/year *raw*
  CSV is the thing this streams past local disk. `area pull --stream
  --s3-bucket <bucket> [--database-url ...]` and `area load --stream
  --batch-size N [--s3-bucket <bucket>]` (the latter batch-commits
  instead of one end-of-run transaction, and optionally HeadObject-
  verifies each year's S3 mirror before loading) wire this in; the
  original non-streaming `pull_year`/`load_raw_dir` paths are unchanged
  and still the default. See tests/test_pull_open_payments_stream.py
  (offline, fake-S3-client) and the batch-size tests appended to
  tests/test_load_idempotent.py (real scratch Postgres) for proof this
  doesn't change the pull's row-matching logic or the load's idempotency
  guarantee -- only how the bytes move.

## Open items (blocked on Leon)

- **The real ~37.56 GiB / 5-year pull is deferred to Gate 1 (D11).** It
  can only run from a machine with real network access to
  `openpaymentsdata.cms.gov` / `download.cms.gov` (this build
  environment's sandboxes are both blocked — see "Network egress"
  above), so realistically that means Leon's own Mac, for multiple
  hours, using real bandwidth and disk space (peaking at ~9.2 GB for the
  largest single year's raw CSV in flight, streamed and never fully
  buffered). Not blocked on a decision anymore — blocked on reaching
  Gate 1, then needs a fresh explicit go-ahead to actually start.
- Until that pull runs: `data/facts.md`'s 5 headline numbers, the
  Methodology tab's "row counts per product per year" (spec §1's
  non-negotiable), and the manufacturer-of-record sanity check (§3.1 —
  is the non-Novo-Nordisk/non-Eli-Lilly share of matched dollars above or
  below 5%?) all stay PENDING.
- D1–D3 (above): the pull's real design (one bulk CSV per year, no
  pagination; year-granular resume; filtered-only raw storage) differs
  from the spec's literal §3.2 wording — flag if a byte-for-byte
  unfiltered raw archive was specifically wanted despite the storage
  cost, or if page-cursor resumability was meant literally rather than
  functionally.
- D9 (above): `cli.py`'s scope (`pull`/`load` only, others stubbed) — flag
  if a different minimum CLI surface was expected from W-B1.
- No S3 bucket yet (D4, Gate 1) — same open item as model-bench's.
- D12–D15 (above, W-B2): four more disclosed judgment calls (citation-
  marker wire format, forecast interval methodology, `build_series.py`
  reading raw files instead of Neon, and the query guardrail's
  defense-in-depth scope) — flag any of them if a different design was
  specifically wanted.
- Until the real pull/load runs (Gate 1): `area tools-test`'s query_tool
  check and the 5 headline facts stay "NOT AVAILABLE"/"PENDING", and
  `area forecast` has no real series to build yet — all W-B2 code and
  tests are ready and passing against synthetic/fixture data and a real
  scratch Postgres; only real CMS data is missing.

## Not yet built

The agent loop (planner, actor, verifier, loop), `src/area/evals/`,
trace writers (W-B4); `web/` and its Vercel functions (W-B5);
`src/area/causal/` (W-B3, Sunday). The real data pull and load (above)
are also not yet run, independent of code — the code and its tests are
ready for the moment the pull is authorized (Gate 1).

## Reports

<!-- Each task's report (per the BUILD INSTRUCTION format) is appended below. -->

### TASK: W-B1 — 2026-09-12 19:19 ET (commit timestamp 23:19:46 UTC)

TASK: W-B1 — AREA data layer: the GLP-1 product match list and rule
(`data/products.json`, `src/area/match.py`), a streaming/resumable CMS
Open Payments pull verified live against the real public API
(`src/area/pull_open_payments.py`), the `payments` schema + indexes +
read-only role + aggregate views (`sql/001_schema.sql`,
`sql/002_views.sql`), an idempotent Postgres loader
(`src/area/load_neon.py`), the 5 headline-fact SQL queries drafted
(`data/facts.md`), and a minimal CLI (`src/area/cli.py`) wiring `area
pull`/`area load`. Per `SPEC-area.md` section 10's task split.

STATUS: Done.

BUILT:
- `data/products.json` — generic names `semaglutide`/`tirzepatide`;
  brand names `Ozempic`, `Wegovy`, `Rybelsus`, `Mounjaro`, `Zepbound`;
  case-insensitive substring match rule against both
  `Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_N` and
  `Product_Category_or_Therapeutic_Area_N` fields; manufacturer-of-record
  sanity-check config (Novo Nordisk, Eli Lilly; 5% threshold).
- `src/area/match.py` — `ProductMatch` dataclass, `load_products()`,
  `find_matches()`, `canonical_product()` (lowest-slot-wins, brand→generic
  resolution).
- `src/area/pull_open_payments.py` — `discover_year_dataset()` (verified
  live against the real DKAN metastore endpoint), a fully streaming
  scan-and-filter (`io.TextIOWrapper` + `csv.DictReader` over the live
  HTTP response, straight into `gzip.open(...).write()` — never buffers a
  whole year), year-granular resumable `pull_year()`/`pull_all_years()`
  with retry/backoff on transient errors, and a `manifest.json` writer
  that computes the manufacturer-of-record sanity-check share. Every
  HTTP call is injectable (`http_get`/`http_stream`), so the whole module
  is unit-tested with zero network access.
- `sql/001_schema.sql` / `sql/002_views.sql` — the `payments` table (14
  columns per spec §3.3; `quarter` made nullable — D7), 5 indexes, the
  `area_reader` read-only role (`statement_timeout = 10s`), and the
  `q_totals`/`q_by_specialty`/`q_by_state` views.
- `src/area/load_neon.py` — `coerce_row()` (type conversion + required-
  field validation, raising rather than inventing a value for a missing
  NOT-NULL field), `iter_matched_rows()` (skips any year whose pull isn't
  marked complete), `ensure_schema()` (idempotent DDL apply), and
  `load_raw_dir()` (the upsert-on-`record_id` loader itself).
- `data/facts.md` — the 5 headline SQL queries from spec §3.4, drafted
  and ready to run; every value marked **PENDING** (D10 — no invented
  numbers).
- `src/area/cli.py` — `area pull` and `area load` wired for real; every
  other subcommand (`tools-test`/`run`/`evals`/`forecast`/`causal`)
  prints a plain "not built yet" message naming its task (D9).
- `.env.example`, `.gitignore`, `LICENSE`, `pyproject.toml`, `README.md`,
  `CONTEXT.md` (this file).
- 1 commit (`fa28642`) for this task's own scope, plus this report-append
  commit. Also corrected a stale claim in model-bench's own `CONTEXT.md`
  (that CMS's hosts are blocked everywhere — they aren't, from a real
  Mac) in a separate commit there (`ed116d6`).
- Repo synced to `~/Claude/area` on your Mac for the first time (this
  repo didn't exist there before): built fresh in the Mac-VM's own home
  directory via a git bundle transfer, verified there, then `cp -r`'d
  into the new `~/Claude/area` path (no existing directory to move
  aside), and checksum-matched against the cloud workspace copy. Mirrored
  to Drive HQ/giggit/area/ (`area-MANIFEST-2026-09-12-WB1.md`,
  `README.md`, `CONTEXT.md`).

TESTED:
- `python3 -m ruff check .` clean. `python3 -m pytest -q`: **45/45**
  passed in the cloud workspace, including 13 tests run against a REAL
  scratch PostgreSQL 16 database (`TEST_DATABASE_URL`) — idempotent
  upsert-and-rerun-changes-nothing, upsert-updates-changed-values,
  skip-and-report-bad-rows, `ensure_schema` safe to run twice — not
  mocked.
- Re-verified on your Mac (fresh clone into the Mac-VM's own home
  directory, D14 procedure, Python 3.11.15 via `uv`): `ruff check .`
  clean; `pytest -q`: 40 passed / 5 honestly-skipped (stated reason: no
  local `TEST_DATABASE_URL` on that shell, matching model-bench's own
  precedent for this kind of skip); combined `sha256` of all tracked
  files matched the cloud workspace copy exactly
  (`8a7ac3ad2e75d1ddae461f7f0199861cffe5499947402c0c0f8a7e521e39c51d`).
- The installed `area` console script was smoke-tested for real, not
  just unit-tested: `area tools-test` → plain "not built yet" message,
  exit 1; `area load` with no `DATABASE_URL` → clear error, exit 1;
  `area load` against the real scratch Postgres → "loaded 0 rows...",
  exit 0.
- `pull_open_payments.py`'s logic (discovery, streaming filter,
  resumability, retry/backoff, the manufacturer sanity-check math) is
  fully covered offline (`tests/test_pull_open_payments.py`) via a small
  fixture CSV shaped like the real one — but its correctness against the
  REAL CMS API was separately, live-verified (see SPEC CHECK below and
  this file's dataset-facts table).

SPEC CHECK (§3 and relevant §9 items):
- §3.1 product match list: exact generic/brand names, the case-
  insensitive substring rule against both field families, the
  manufacturer-of-record sanity check — all implemented, 11 tests.
- §3.2 pull: dataset ids discovered live from the metastore endpoint and
  recorded above; resumable and retry/backoff-aware on transient errors;
  the day-1 gate (`discover_year_dataset` raises `LookupError` — a loud
  stop — if a program year's dataset title isn't found) is implemented
  and tested. NOT literally "paged" (D1) and NOT literally
  `page_<n>.json.gz` (D3) — the real API has no pagination at all for
  this dataset, so the pull streams one bulk CSV per year and stores
  only the GLP-1-filtered rows plus a full provenance manifest. Both are
  disclosed, reasoned judgment calls — flagged in OPEN below.
- §3.3 load: idempotent upsert on `record_id`, proven on a fixture
  against a REAL Postgres, matching the spec's own wording ("test proves
  it on a fixture") rather than a mock. Read-only `area_reader` role,
  `statement_timeout = 10s`, 5 indexes, all 3 views — all present.
- §3.4 facts.md: all 5 queries drafted; every value PENDING — no
  invented numbers, per the BUILD INSTRUCTION's hardest rule.
- §9 acceptance item 1 ("Neon `payments` loaded with all matching rows...
  raw files in S3 with manifest") is NOT yet met for the whole AREA v0 —
  no real pull/load has run yet, no S3 bucket exists (Gate 1). This is
  §9's bar for the finished v0, not W-B1 alone, and is explicitly gated
  on your go-ahead (see OPEN).
- Full data / no sampling / no fake numbers / secrets only via env /
  tests for every metric-parser-guard: all followed — see BUILT/TESTED
  and D6/D7/D10 above.

OPEN — two things need your call:
1. **The real ~37.56 GiB (40.33 GB) / 5-year CMS Open Payments pull has
   not been run.** It can only run from a machine with real network
   access to `openpaymentsdata.cms.gov`/`download.cms.gov` — this build
   environment's cloud workspace and Mac-VM sandbox are both blocked
   (confirmed again today); your real Mac terminal is reachable and is
   where this file's dataset facts were verified from. The real pull
   would mean multiple hours of wall-clock time and real bandwidth/disk
   use on your Mac (streamed, never fully buffered, but still peaking
   around 9.2 GB in flight for the largest single year). This is a
   separate decision from the general "GO: WB-1" already given, given the
   real resource cost on a personal, possibly-unattended machine — asked
   as a direct, separate question in chat right after this report.
2. **D1/D3 — the pull's actual design versus the spec's literal §3.2
   wording.** The real CMS API has no pagination for this dataset at all
   (one bulk CSV per year), so I built the safest functionally-equivalent
   version (streamed, year-granular resume, filtered-only raw storage
   with a full provenance manifest) rather than a literal but
   non-existent paginated design. Flag if you specifically want the
   unfiltered multi-GB CSV also archived once the real pull runs, despite
   the extra storage cost, or if page-cursor resumability was meant more
   literally than "resumable in substance."

Also open, not spec conflicts, just disclosed choices: D6 (fixed an
invalid psql-only `CREATE ROLE ... PASSWORD :'...'` syntax before it ever
ran against a real database), D7 (nullable `quarter`), D8 (`psycopg`
moved into `dev` extras starting this task, not held for W-B4), D9
(`cli.py`'s `pull`/`load`-only scope) — see Decisions above for each.

NEXT: W-B2 (per the fixed task order). Per the BUILD INSTRUCTION, I have
not started it — waiting for your "go", and separately for your answer
on the real data pull (OPEN item 1 above).

### TASK: W-B2 — 2026-09-12 20:31 ET (commit timestamp 00:31:38 UTC, 2026-09-13)

TASK: W-B2 — AREA tools + forecast pipeline: the vendored model-provider
layer (`src/area/providers/`), the tools registry (`src/area/tools/`)
with all three SPEC-area.md §4 tools (query_tool, forecast_tool,
citation_checker), the forecast pipeline (`src/area/forecast/`:
models, holdout evaluation, series builder), and CLI wiring for `area
tools-test` and `area forecast`. Per `SPEC-area.md` section 10's W-B2
task-split line: "query tool + guardrail tests, forecast build + holdout
on the pulled data (runs locally on DuckDB or pandas from raw), citation
checker + tests, registry."

STATUS: Done.

BUILT:
- `src/area/providers/` — `base.py` (`CallResult`, `Provider`,
  `RetryableError`, `call_with_retries`), `bedrock.py`,
  `anthropic_direct.py`, `openai_compatible.py`, `fake.py`, `__init__.py`
  (`get_provider()`) — vendored from model-bench per the spec's own
  instruction ("copy the package or vendor it; same interface, same
  logging"), only the import namespace changed.
- `src/area/tools/__init__.py` — `Evidence`, `ToolResult`, `Tool`
  dataclasses, and `registry()` (lazily imports each tool module so
  importing `area.tools` never requires every tool's own dependencies).
- `src/area/tools/query_tool.py` — question -> model-generated SQL ->
  `validate_and_normalize()` (single-SELECT only, allow-listed
  tables/columns/functions only, `LIMIT 5000` added if missing,
  multi-statement chains rejected) -> executed read-only -> rows +
  `evidence_id` ("q_<sha8>"). Built on `sqlparse`'s structured token tree
  so CTEs, joins, subqueries, and comma-joined FROM lists are all
  correctly recognized (D15).
- `src/area/tools/forecast_tool.py` — read-only lookup into
  `forecast/latest.json`; never computes a forecast itself; returns a
  clear error (never a fabricated number) if the file or series doesn't
  exist yet.
- `src/area/tools/citation_checker.py` — checks every non-year number in
  drafted answer text against its inline citation marker (D12), matching
  cited evidence within tolerance (0.5% relative or displayed precision)
  or recomputing a stated derivation with a restricted-AST safe
  evaluator (never Python `eval`).
- `src/area/forecast/models.py` — `seasonal_naive` and `ets` (lazy
  pandas/statsmodels import), both with an 80% interval via `point ±
  1.2816×residual_std` (D13).
- `src/area/forecast/holdout.py` — trains on quarters <=2024Q4, tests on
  2025Q1-2025Q4, reports MAE and 80% coverage for both models, picks the
  lower-MAE model as primary, and `run()` (the `area forecast` entry
  point) writes `forecast/latest.json`, recording any series with too
  little history as honestly skipped rather than fabricating a forecast.
- `src/area/forecast/build_series.py` — builds the national and
  top-N-specialty quarterly series directly from raw pulled
  `matched.jsonl.gz` files (D14), reusing `load_neon`'s own
  `iter_matched_rows`/`coerce_row`.
- `src/area/cli.py` — `area tools-test` (smoke-tests all three tools;
  prints the 5 `data/facts.md` headline facts live against a configured
  database, or an honest "NOT AVAILABLE"/"PENDING" line naming why, never
  a fake number) and `area forecast` (runs the pipeline above end-to-end).
- Found and fixed a real guardrail bug while testing against real
  Postgres: `data/facts.md`'s own query 5 (`FILTER (WHERE ...)` +
  `NULLIF`) was being rejected by the validator (D16).
- `pyproject.toml` — added `sqlparse`, `pandas`, `statsmodels` as core
  runtime dependencies (all three run unconditionally, not just once a
  secret exists — see the file's own updated comment); `psycopg` stays a
  dev/test-only dependency (still lazily imported, still only needed once
  a real database is actually queried).
- `README.md` — new "What's built (W-B2)" section; "Not yet built"
  narrowed to W-B3/B4/B5 plus the still-open Gate-1 items.
- `CONTEXT.md` (this file) — decisions D12-D16, updated Open items and
  Not yet built.

TESTED:
- `python3 -m ruff check .` clean. `python3 -m pytest -q`: **128/128**
  passed, including 15 tests run against a REAL scratch PostgreSQL 16
  database (`TEST_DATABASE_URL`): the 13 W-B1 idempotency tests plus 2 new
  W-B2 query_tool execution tests (a valid query returns real rows +
  evidence, a rejected query never reaches the database) — not mocked.
- The installed `area` console script was smoke-tested for real, not just
  unit-tested: `area tools-test` with no database/forecast file ->
  `citation_checker: PASS`, `forecast_tool: NOT AVAILABLE YET`,
  `query_tool: NOT AVAILABLE`, all 5 facts `PENDING` — honest, not faked
  — exit 0. `area tools-test` against the real scratch Postgres (one
  seeded row) -> `query_tool: PASS`, all 5 facts computed for real (e.g.
  `Share of dollars that are Food and Beverage: {'food_and_beverage_share_pct': Decimal('100.00')}`)
  -- exit 0. `area forecast` against a synthetic 5-year/4-specialty raw
  dataset -> wrote `forecast/latest.json`, "4 series computed, 0 skipped"
  -- exit 0; `area tools-test` against that real output file ->
  `forecast_tool: PASS`.
- `tests/test_cli.py` updated: the old "tools-test is not built yet" test
  replaced with a "causal is not built yet" test (still true, W-B3), plus
  4 new tests covering `tools-test` (no-database honesty, and a full
  real-Postgres run) and `forecast` (missing raw-dir, and computed/skipped
  reporting via a monkeypatched `holdout.run`).

SPEC CHECK (§4 and relevant §9/§10 items):
- §4.1 registry: exactly the three tools, each with a plain-English
  description, JSON input schema, and callable — `tests/test_tools_registry.py`.
- §4.2 query tool: single-SELECT enforcement, allow-listed
  tables/columns/functions, `LIMIT 5000` auto-add, multi-statement
  rejection, `evidence_id` "q_<sha8>" — all implemented and tested
  against the named attack shapes (injection, UPDATE/DELETE,
  multi-statement, unknown column, missing LIMIT) plus 2 live-Postgres
  execution tests.
- §4.3 forecast tool + pipeline: `area forecast` computes both models'
  holdout MAE/80%-coverage, picks the primary by lower MAE, writes
  `forecast/latest.json`; `forecast_tool.py` is a pure read-only lookup
  over that file, per the spec's own wording.
- §4.4 citation checker: verifies numbers against cited evidence
  (tolerance-aware) or recomputes stated derivations; years excluded;
  wrong-derivation and no-marker cases both fail with a stated reason —
  `tests/test_citation_checker.py`.
- §9 acceptance criterion 2 ("Three tools pass their tests; `area
  tools-test` runs all three against Neon and prints the 5 facts") is
  **partially met, disclosed honestly**: all three tools pass their own
  tests (128/128), and `area tools-test` runs all three and prints the 5
  facts exactly as the criterion asks — but there is still no real Neon
  database and no real pulled CMS data (both explicitly deferred to Gate
  1 by your own D11 decision), so `query_tool` and the 5 facts report
  "NOT AVAILABLE"/"PENDING" rather than real numbers when run without a
  database configured, and report real numbers (verified against a real
  scratch Postgres) when one is. This is the same honest-disclosure
  pattern as W-B1's own §9 item 1.
- Full data / no sampling / no fake numbers / secrets only via env vars /
  tests for every metric-parser-guard: all followed — see BUILT/TESTED
  and D12-D16 above.

OPEN — same two Gate-1-blocked items as W-B1's report (no change since
D11 settled them): the real ~37.56 GiB/5-year CMS pull, and the real Neon
database, both wait for Gate 1. Nothing new to ask this task — D12-D16
above are disclosed judgment calls, not spec conflicts, flagged for your
awareness rather than blocking on an answer.

NEXT: per the fixed task order, Sync/deploy tooling comes next, then Gate
1 (your credentials), then W-A4/W-M2/W-B4/W-B5/W-B3. Per the BUILD
INSTRUCTION, I have not started Sync/deploy tooling — waiting for your
next "go".
