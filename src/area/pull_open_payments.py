"""Pulls CMS Open Payments general-payment data and filters it to the
GLP-1 product list (SPEC-area.md sections 3.1-3.2).

Real, live network calls: this reads directly from the public,
unauthenticated CMS Open Payments DKAN API (no API key, no login) --
`openpaymentsdata.cms.gov`'s metastore for dataset discovery, then a
direct CSV download from `download.cms.gov` for each program year. Every
call is injectable (`http_get`/`http_stream`), so all the logic below --
matching, quarter parsing, manifest bookkeeping, retry/backoff -- is
covered by tests/test_pull_open_payments.py with zero network access,
using a small fixture CSV. See CONTEXT.md's decision log for how this
was verified against the REAL live API (dataset discovery, file sizes,
real column names) before this file was written.

Resumability is at YEAR granularity, not byte/row granularity -- see
CONTEXT.md's decision on this. `raw/<year>/manifest.json` records
`"complete": true` once a year finishes; `pull_all_years()` skips any
year already complete unless `force=True`. A year interrupted partway
through (network drop, process killed) is simply re-run from the start
next time. This was a deliberate choice over byte-offset resume: a
streaming CSV read is buffered ahead of whatever row is currently being
processed, so a byte offset recorded "after processing row N" almost
certainly includes bytes for rows N+1, N+2, ... already pulled into that
buffer -- resuming a Range request from that offset would silently skip
those already-buffered-but-unprocessed rows. Re-pulling a whole year
(minutes to at most a couple of hours, see CONTEXT.md's measured file
sizes) is slower but cannot silently lose rows; retrying within a year up
to MAX_RETRIES times on a transient error happens before falling back to
that full restart.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from area.match import canonical_product, find_matches, load_products

METASTORE_ITEMS_URL = "https://openpaymentsdata.cms.gov/api/1/metastore/schemas/dataset/items"
METASTORE_ITEM_URL_TEMPLATE = (
    "https://openpaymentsdata.cms.gov/api/1/metastore/schemas/dataset/items/{identifier}"
)
YEARS = [2021, 2022, 2023, 2024, 2025]
MAX_RETRIES = 5
RETRY_BACKOFF_SECONDS = [1, 2, 5, 15, 30]

HttpGet = Callable[[str], bytes]
HttpStream = Callable[[str], Any]  # returns a context-manager, readable-bytes object


def default_http_get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read()


def default_http_stream(url: str):
    return urllib.request.urlopen(url, timeout=120)


def discover_year_dataset(year: int, http_get: HttpGet = default_http_get) -> dict:
    """Finds the metastore dataset item for "<year> General Payment Data"
    and returns {identifier, title, download_url, modified}. Raises
    LookupError if no exact match is found -- this IS the spec's day-1
    gate (section 3.2: "if any product-year that should exist returns
    zero, STOP and report before loading"), generalized to the dataset
    itself being missing, which is the same failure one level up."""
    items = json.loads(http_get(METASTORE_ITEMS_URL))
    wanted_title = f"{year} General Payment Data"
    candidates = [it for it in items if it.get("title") == wanted_title]
    if not candidates:
        raise LookupError(
            f"No CMS Open Payments dataset titled {wanted_title!r} found in the metastore "
            "items list -- STOP and check before loading (spec section 3.2's day-1 gate)."
        )
    identifier = candidates[0]["identifier"]
    item = json.loads(http_get(METASTORE_ITEM_URL_TEMPLATE.format(identifier=identifier)))
    distribution = item.get("distribution") or []
    if not distribution or not distribution[0].get("downloadURL"):
        raise LookupError(f"Dataset {identifier} ({wanted_title}) has no distribution downloadURL.")
    return {
        "identifier": identifier,
        "title": wanted_title,
        "download_url": distribution[0]["downloadURL"],
        "modified": item.get("modified"),
    }


@dataclass
class PullStats:
    rows_scanned: int = 0
    rows_matched: int = 0
    matched_by_product: dict[str, int] = field(default_factory=dict)
    matched_by_manufacturer: dict[str, int] = field(default_factory=dict)


def quarter_of(date_str: str | None) -> str | None:
    """'2023-04-15' or '04/15/2023' -> '2023Q2'. Returns None if empty or
    unparseable -- the row is still matched and kept (matched rows are the
    whole point of this pull), just without a quarter until reconciled."""
    if not date_str:
        return None
    date_str = date_str.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            d = datetime.strptime(date_str, fmt)
            q = (d.month - 1) // 3 + 1
            return f"{d.year}Q{q}"
        except ValueError:
            continue
    return None


def process_row(row: dict[str, str], products: dict, dataset_identifier: str) -> dict | None:
    """The matched-row record to write to raw/<year>/matched.jsonl.gz, or
    None if this row is not a GLP-1 payment. Values are kept as the raw
    CSV strings (except quarter, which is derived) -- type conversion into
    the payments table's real column types is load_neon.py's job, so the
    raw file stays "exactly as received" per the spec's non-negotiables."""
    matches = find_matches(row, products)
    if not matches:
        return None
    product, product_generic, matched_field = canonical_product(row, matches, products)
    return {
        "record_id": row.get("Record_ID"),
        "program_year": row.get("Program_Year"),
        "payment_date": row.get("Date_of_Payment"),
        "quarter": quarter_of(row.get("Date_of_Payment")),
        "physician_id": row.get("Covered_Recipient_NPI") or None,
        "physician_specialty": row.get("Covered_Recipient_Specialty_1") or None,
        "physician_state": row.get("Recipient_State") or None,
        "manufacturer": row.get("Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name"),
        "product": product,
        "product_generic": product_generic,
        "nature_of_payment": row.get("Nature_of_Payment_or_Transfer_of_Value"),
        "amount_usd": row.get("Total_Amount_of_Payment_USDollars"),
        "matched_field": matched_field,
        "matched_slot_count": len(matches),
        "source_year_dataset": dataset_identifier,
    }


def _scan_and_filter(
    resp: Any, products: dict, dataset_identifier: str, matched_path: Path
) -> PullStats:
    stats = PullStats()
    text_stream = io.TextIOWrapper(resp, encoding="utf-8", newline="")
    reader = csv.DictReader(text_stream)
    with gzip.open(matched_path, "wt", encoding="utf-8") as out:
        for row in reader:
            stats.rows_scanned += 1
            record = process_row(row, products, dataset_identifier)
            if record is None:
                continue
            stats.rows_matched += 1
            stats.matched_by_product[record["product"]] = (
                stats.matched_by_product.get(record["product"], 0) + 1
            )
            manufacturer = record["manufacturer"] or "(unknown)"
            stats.matched_by_manufacturer[manufacturer] = (
                stats.matched_by_manufacturer.get(manufacturer, 0) + 1
            )
            out.write(json.dumps(record) + "\n")
    return stats


def pull_year(
    year: int,
    out_dir: Path,
    products: dict | None = None,
    http_get: HttpGet = default_http_get,
    http_stream: HttpStream = default_http_stream,
    force: bool = False,
) -> dict:
    """Pulls one year end to end: discover -> stream -> filter -> write.
    Returns the manifest dict (also written to raw/<year>/manifest.json).
    Skips the network entirely and returns the existing manifest if that
    year is already marked complete, unless force=True."""
    products = products or load_products()
    year_dir = out_dir / str(year)
    year_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = year_dir / "manifest.json"

    if not force and manifest_path.exists():
        existing = json.loads(manifest_path.read_text())
        if existing.get("complete"):
            return existing

    dataset = discover_year_dataset(year, http_get=http_get)
    pulled_at = datetime.now(timezone.utc).isoformat()
    matched_path = year_dir / "matched.jsonl.gz"

    attempt = 0
    while True:
        try:
            with http_stream(dataset["download_url"]) as resp:
                stats = _scan_and_filter(resp, products, dataset["identifier"], matched_path)
            break
        except (urllib.error.URLError, OSError) as exc:
            attempt += 1
            if attempt > MAX_RETRIES:
                return _write_manifest(
                    manifest_path, year, dataset, pulled_at, PullStats(), products,
                    complete=False, error=str(exc),
                )
            time.sleep(RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)])

    return _write_manifest(manifest_path, year, dataset, pulled_at, stats, products,
                            complete=True, error=None)


def _write_manifest(
    manifest_path: Path,
    year: int,
    dataset: dict,
    pulled_at: str,
    stats: PullStats,
    products: dict,
    complete: bool,
    error: str | None,
) -> dict:
    manufacturers_of_record = {m.lower() for m in products["manufacturers_of_record"]}
    other_total = sum(
        n
        for m, n in stats.matched_by_manufacturer.items()
        if m.lower() not in manufacturers_of_record
    )
    other_share_pct = (
        round(100 * other_total / stats.rows_matched, 2) if stats.rows_matched else 0.0
    )
    manifest = {
        "year": year,
        "program_year_dataset_identifier": dataset["identifier"],
        "download_url": dataset["download_url"],
        "dataset_modified": dataset["modified"],
        "pulled_at": pulled_at,
        "rows_scanned": stats.rows_scanned,
        "rows_matched": stats.rows_matched,
        "matched_by_product": stats.matched_by_product,
        "matched_by_manufacturer": stats.matched_by_manufacturer,
        "non_manufacturer_of_record_share_pct": other_share_pct,
        "complete": complete,
        "error": error,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


def pull_all_years(
    out_dir: Path,
    years: list[int] = YEARS,
    products: dict | None = None,
    http_get: HttpGet = default_http_get,
    http_stream: HttpStream = default_http_stream,
    force: bool = False,
) -> list[dict]:
    products = products or load_products()
    return [
        pull_year(year, out_dir, products, http_get=http_get, http_stream=http_stream, force=force)
        for year in years
    ]
