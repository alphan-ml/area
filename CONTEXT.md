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

## Open items (blocked on Leon)

- **The real ~37.56 GiB / 5-year pull has not been run.** It can only run
  from a machine with real network access to `openpaymentsdata.cms.gov` /
  `download.cms.gov` (this build environment's sandboxes are both
  blocked — see "Network egress" above), so realistically that means
  Leon's own Mac, for multiple hours, using real bandwidth and disk
  space (peaking at ~9.2 GB for the largest single year's raw CSV
  in flight, streamed and never fully buffered, though the OS/network
  stack still moves that many bytes across the wire). This needs an
  explicit go-ahead separate from the general "GO: WB-1" already given,
  because of that real resource cost on a personal, possibly-unattended
  machine — see the chat message asking this directly.
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

## Not yet built

Everything past W-B1: `src/area/tools/` (registry, query tool, forecast
tool, citation checker — W-B2); `src/area/agent/` (planner, actor,
verifier, loop), `src/area/evals/`, trace writers (W-B4); `web/` and its
Vercel functions (W-B5); `src/area/causal/` (W-B3, Sunday). The real data
pull and load (above) are also not yet run, independent of code — the
code and its tests are ready for the moment the pull is authorized.

## Reports

<!-- Each task's report (per the BUILD INSTRUCTION format) is appended below. -->
