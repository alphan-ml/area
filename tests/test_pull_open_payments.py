"""Fixture-based tests for area/pull_open_payments.py -- zero network
access. Every http_get/http_stream here is a fake built from small,
hand-written fixtures that mimic the REAL CMS Open Payments API shape
(metastore items/item JSON, and a 91-column-shaped CSV reduced to just
the columns match.py/process_row actually read). The real shapes were
confirmed live against openpaymentsdata.cms.gov / download.cms.gov
before this module was written -- see CONTEXT.md's decision log.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import urllib.error

import pytest

import area.pull_open_payments as pull_open_payments_module
from area.match import load_products
from area.pull_open_payments import (
    MAX_RETRIES,
    METASTORE_ITEM_URL_TEMPLATE,
    METASTORE_ITEMS_URL,
    discover_year_dataset,
    process_row,
    pull_all_years,
    pull_year,
    quarter_of,
)

PRODUCTS = load_products()

FIXTURE_COLUMNS = (
    [
        "Record_ID",
        "Program_Year",
        "Date_of_Payment",
        "Covered_Recipient_NPI",
        "Covered_Recipient_Specialty_1",
        "Recipient_State",
        "Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name",
        "Nature_of_Payment_or_Transfer_of_Value",
        "Total_Amount_of_Payment_USDollars",
    ]
    + [f"Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_{n}" for n in range(1, 6)]
    + [f"Product_Category_or_Therapeutic_Area_{n}" for n in range(1, 6)]
)

# Row 1 & 2 match a manufacturer-of-record product. Row 3 matches too but
# is paid by a non-manufacturer-of-record entity (exercises the
# non_manufacturer_of_record_share_pct sanity-check math). Row 4 is a
# real, unrelated drug and must be dropped.
FIXTURE_ROWS = [
    {
        "Record_ID": "1",
        "Program_Year": "2023",
        "Date_of_Payment": "2023-04-15",
        "Covered_Recipient_NPI": "1111111111",
        "Covered_Recipient_Specialty_1": "Endocrinology",
        "Recipient_State": "CA",
        "Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name": "Novo Nordisk",
        "Nature_of_Payment_or_Transfer_of_Value": "Consulting Fee",
        "Total_Amount_of_Payment_USDollars": "100.50",
        "Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1": "Ozempic",
    },
    {
        "Record_ID": "2",
        "Program_Year": "2023",
        "Date_of_Payment": "2023-07-01",
        "Covered_Recipient_NPI": "2222222222",
        "Covered_Recipient_Specialty_1": "Internal Medicine",
        "Recipient_State": "NY",
        "Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name": "Eli Lilly",
        "Nature_of_Payment_or_Transfer_of_Value": "Food and Beverage",
        "Total_Amount_of_Payment_USDollars": "200.00",
        "Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1": "Mounjaro",
    },
    {
        "Record_ID": "3",
        "Program_Year": "2023",
        "Date_of_Payment": "2023-01-10",
        "Covered_Recipient_NPI": "3333333333",
        "Covered_Recipient_Specialty_1": "Family Medicine",
        "Recipient_State": "TX",
        "Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name": "Acme Distributors",
        "Nature_of_Payment_or_Transfer_of_Value": "Travel",
        "Total_Amount_of_Payment_USDollars": "50.00",
        "Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1": "Wegovy",
    },
    {
        "Record_ID": "4",
        "Program_Year": "2023",
        "Date_of_Payment": "2023-02-20",
        "Covered_Recipient_NPI": "4444444444",
        "Covered_Recipient_Specialty_1": "Rheumatology",
        "Recipient_State": "FL",
        "Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name": "AbbVie",
        "Nature_of_Payment_or_Transfer_of_Value": "Royalty",
        "Total_Amount_of_Payment_USDollars": "75.00",
        "Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1": "Humira",
    },
]


def _make_fixture_csv(rows=FIXTURE_ROWS) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=FIXTURE_COLUMNS, restval="")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")


def _items_and_item(year, identifier="abc123", download_url="https://download.cms.gov/foo.csv"):
    items_response = [{"identifier": identifier, "title": f"{year} General Payment Data"}]
    item_response = {
        identifier: {"distribution": [{"downloadURL": download_url}], "modified": "2026-01-01"}
    }
    return items_response, item_response


def _make_http_get(items_response, item_response_by_id):
    def http_get(url):
        if url == METASTORE_ITEMS_URL:
            return json.dumps(items_response).encode("utf-8")
        for identifier, item in item_response_by_id.items():
            if url == METASTORE_ITEM_URL_TEMPLATE.format(identifier=identifier):
                return json.dumps(item).encode("utf-8")
        raise AssertionError(f"unexpected URL requested: {url}")

    return http_get


def _full_row(**overrides):
    """A minimal but complete Open Payments-shaped row: all the columns
    process_row/find_matches read, with sensible non-empty defaults so a
    single override (usually a drug name) is enough to make a case."""
    row = {
        "Record_ID": "42",
        "Program_Year": "2023",
        "Date_of_Payment": "2023-04-15",
        "Covered_Recipient_NPI": "1234567890",
        "Covered_Recipient_Specialty_1": "Endocrinology",
        "Recipient_State": "CA",
        "Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name": "Novo Nordisk",
        "Nature_of_Payment_or_Transfer_of_Value": "Consulting Fee",
        "Total_Amount_of_Payment_USDollars": "123.45",
    }
    for n in range(1, 6):
        row[f"Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_{n}"] = ""
        row[f"Product_Category_or_Therapeutic_Area_{n}"] = ""
    row.update(overrides)
    return row


# --- discover_year_dataset -------------------------------------------------


def test_discover_year_dataset_returns_identifier_and_download_url():
    items_response, item_response = _items_and_item(2023)
    result = discover_year_dataset(2023, http_get=_make_http_get(items_response, item_response))
    assert result == {
        "identifier": "abc123",
        "title": "2023 General Payment Data",
        "download_url": "https://download.cms.gov/foo.csv",
        "modified": "2026-01-01",
    }


def test_discover_year_dataset_raises_lookuperror_when_title_missing():
    # The day-1 gate: a year whose dataset title isn't in the metastore
    # items list must STOP loudly, not silently pull nothing.
    items_response = [{"identifier": "x", "title": "2023 Research Payment Data"}]
    with pytest.raises(LookupError):
        discover_year_dataset(2023, http_get=_make_http_get(items_response, {}))


def test_discover_year_dataset_raises_lookuperror_when_no_distribution():
    items_response = [{"identifier": "abc123", "title": "2023 General Payment Data"}]
    item_response = {"abc123": {"distribution": [], "modified": None}}
    with pytest.raises(LookupError):
        discover_year_dataset(2023, http_get=_make_http_get(items_response, item_response))


def test_discover_year_dataset_raises_lookuperror_when_downloadurl_blank():
    items_response = [{"identifier": "abc123", "title": "2023 General Payment Data"}]
    item_response = {"abc123": {"distribution": [{"downloadURL": ""}], "modified": None}}
    with pytest.raises(LookupError):
        discover_year_dataset(2023, http_get=_make_http_get(items_response, item_response))


# --- quarter_of --------------------------------------------------------


def test_quarter_of_iso_date():
    assert quarter_of("2023-04-15") == "2023Q2"


def test_quarter_of_us_slash_date():
    assert quarter_of("04/15/2023") == "2023Q2"


def test_quarter_of_quarter_boundaries():
    assert quarter_of("2023-01-01") == "2023Q1"
    assert quarter_of("2023-03-31") == "2023Q1"
    assert quarter_of("2023-12-31") == "2023Q4"


def test_quarter_of_none_and_empty_return_none():
    assert quarter_of(None) is None
    assert quarter_of("") is None


def test_quarter_of_unparseable_returns_none():
    assert quarter_of("not-a-date") is None


# --- process_row -------------------------------------------------------


def test_process_row_returns_none_for_non_glp1_row():
    row = _full_row(Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1="Humira")
    assert process_row(row, PRODUCTS, "dataset-abc") is None


def test_process_row_builds_expected_record_for_matching_row():
    row = _full_row(Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1="Ozempic")
    record = process_row(row, PRODUCTS, "dataset-abc")
    assert record == {
        "record_id": "42",
        "program_year": "2023",
        "payment_date": "2023-04-15",
        "quarter": "2023Q2",
        "physician_id": "1234567890",
        "physician_specialty": "Endocrinology",
        "physician_state": "CA",
        "manufacturer": "Novo Nordisk",
        "product": "Ozempic",
        "product_generic": "semaglutide",
        "nature_of_payment": "Consulting Fee",
        "amount_usd": "123.45",
        "matched_field": "Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1",
        "matched_slot_count": 1,
        "source_year_dataset": "dataset-abc",
    }


# --- pull_year: end-to-end scan/filter/manifest -------------------------


def test_pull_year_end_to_end_writes_matched_rows_and_manifest(tmp_path):
    items_response, item_response = _items_and_item(2023)
    http_get = _make_http_get(items_response, item_response)
    csv_bytes = _make_fixture_csv()

    def http_stream(url):
        assert url == "https://download.cms.gov/foo.csv"
        return io.BytesIO(csv_bytes)

    manifest = pull_year(
        2023, tmp_path, products=PRODUCTS, http_get=http_get, http_stream=http_stream
    )

    assert manifest["complete"] is True
    assert manifest["error"] is None
    assert manifest["rows_scanned"] == 4
    assert manifest["rows_matched"] == 3
    assert manifest["matched_by_product"] == {"Ozempic": 1, "Mounjaro": 1, "Wegovy": 1}
    assert manifest["matched_by_manufacturer"] == {
        "Novo Nordisk": 1,
        "Eli Lilly": 1,
        "Acme Distributors": 1,
    }
    # 1 of 3 matched rows (Acme Distributors) is not a manufacturer of record.
    assert manifest["non_manufacturer_of_record_share_pct"] == pytest.approx(33.33, abs=0.01)

    manifest_path = tmp_path / "2023" / "manifest.json"
    assert json.loads(manifest_path.read_text()) == manifest

    matched_path = tmp_path / "2023" / "matched.jsonl.gz"
    with gzip.open(matched_path, "rt", encoding="utf-8") as f:
        records = [json.loads(line) for line in f]
    assert [r["record_id"] for r in records] == ["1", "2", "3"]
    assert [r["product"] for r in records] == ["Ozempic", "Mounjaro", "Wegovy"]


def test_pull_year_skips_network_when_already_complete(tmp_path):
    year_dir = tmp_path / "2023"
    year_dir.mkdir()
    existing_manifest = {"year": 2023, "complete": True, "rows_matched": 99}
    (year_dir / "manifest.json").write_text(json.dumps(existing_manifest))

    def poison_http_get(url):
        raise AssertionError("http_get should not be called when the year is already complete")

    def poison_http_stream(url):
        raise AssertionError("http_stream should not be called when the year is already complete")

    result = pull_year(
        2023, tmp_path, products=PRODUCTS, http_get=poison_http_get, http_stream=poison_http_stream
    )
    assert result == existing_manifest


def test_pull_year_force_true_repulls_even_when_marked_complete(tmp_path):
    year_dir = tmp_path / "2023"
    year_dir.mkdir()
    stale_manifest = {"year": 2023, "complete": True, "rows_matched": 99, "rows_scanned": 999}
    (year_dir / "manifest.json").write_text(json.dumps(stale_manifest))

    items_response, item_response = _items_and_item(2023)
    http_get = _make_http_get(items_response, item_response)
    csv_bytes = _make_fixture_csv()

    def http_stream(url):
        return io.BytesIO(csv_bytes)

    manifest = pull_year(
        2023, tmp_path, products=PRODUCTS, http_get=http_get, http_stream=http_stream, force=True
    )
    assert manifest["complete"] is True
    assert manifest["rows_scanned"] == 4
    assert manifest["rows_matched"] == 3  # fresh data, not the stale 99


def test_pull_year_retries_then_gives_up_and_records_error(tmp_path, monkeypatch):
    monkeypatch.setattr(pull_open_payments_module.time, "sleep", lambda seconds: None)

    items_response, item_response = _items_and_item(2023)
    http_get = _make_http_get(items_response, item_response)

    attempts = []

    def failing_http_stream(url):
        attempts.append(url)
        raise urllib.error.URLError("connection reset")

    manifest = pull_year(
        2023, tmp_path, products=PRODUCTS, http_get=http_get, http_stream=failing_http_stream
    )

    assert manifest["complete"] is False
    assert "connection reset" in manifest["error"]
    assert manifest["rows_scanned"] == 0
    assert manifest["rows_matched"] == 0
    assert len(attempts) == MAX_RETRIES + 1  # 1 initial try + MAX_RETRIES retries


# --- pull_all_years ------------------------------------------------------


def test_pull_all_years_pulls_each_year_and_returns_manifests(tmp_path):
    years = [2023, 2024]
    identifiers = {2023: "id-2023", 2024: "id-2024"}
    items_response = [
        {"identifier": identifiers[y], "title": f"{y} General Payment Data"} for y in years
    ]
    item_response = {
        identifiers[y]: {
            "distribution": [{"downloadURL": f"https://download.cms.gov/{y}.csv"}],
            "modified": "2026-01-01",
        }
        for y in years
    }
    http_get = _make_http_get(items_response, item_response)
    csv_bytes = _make_fixture_csv()
    requested_urls = []

    def http_stream(url):
        requested_urls.append(url)
        return io.BytesIO(csv_bytes)

    manifests = pull_all_years(
        tmp_path, years=years, products=PRODUCTS, http_get=http_get, http_stream=http_stream
    )

    assert [m["year"] for m in manifests] == years
    assert all(m["complete"] for m in manifests)
    assert requested_urls == [f"https://download.cms.gov/{y}.csv" for y in years]
    assert (tmp_path / "2023" / "manifest.json").exists()
    assert (tmp_path / "2024" / "manifest.json").exists()
