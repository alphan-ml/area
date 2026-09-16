"""Tests for pull_open_payments.py's streaming mode (CONTEXT.md D17):
`_S3MultipartTee`, `_scan_filter_and_load`, and `pull_year_stream`. Zero
network access -- `http_stream` is a fake `io.BytesIO` fixture CSV, same
pattern as tests/test_pull_open_payments.py, and S3 is a small in-memory
fake client (no boto3/moto dependency, no real AWS calls) that implements
just the multipart-upload calls `_S3MultipartTee` makes.

The one DB-backed test (matched rows land in Postgres during the stream,
not just in matched.jsonl.gz) needs a real scratch database via
TEST_DATABASE_URL, same as tests/test_load_idempotent.py -- it SKIPS
cleanly, with a stated reason, when that's unset.
"""

from __future__ import annotations

import gzip
import io
import json
import os

import pytest

import area.pull_open_payments as pull_open_payments_module
from area.load_neon import ensure_schema
from area.match import load_products
from area.pull_open_payments import (
    METASTORE_ITEM_URL_TEMPLATE,
    METASTORE_ITEMS_URL,
    pull_year_stream,
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

FIXTURE_ROWS = [
    {
        "Record_ID": "s1",
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
        "Record_ID": "s2",
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
        "Record_ID": "s3",
        "Program_Year": "2023",
        "Date_of_Payment": "2023-02-20",
        "Covered_Recipient_NPI": "3333333333",
        "Covered_Recipient_Specialty_1": "Rheumatology",
        "Recipient_State": "FL",
        "Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name": "Acme Distributors",
        "Nature_of_Payment_or_Transfer_of_Value": "Royalty",
        "Total_Amount_of_Payment_USDollars": "75.00",
        "Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1": "Wegovy",
    },
]


def _make_fixture_csv(rows=FIXTURE_ROWS) -> bytes:
    import csv

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=FIXTURE_COLUMNS, restval="")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")


def _items_and_item(year, identifier="stream123", download_url="https://download.cms.gov/stream.csv"):
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


class FakeS3Client:
    """In-memory stand-in for boto3's S3 client, implementing only the
    multipart-upload calls `_S3MultipartTee` makes. No network, no
    moto/boto3 dependency."""

    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}
        self._uploads: dict[str, dict] = {}
        self._next_id = 1
        self.create_calls = 0
        self.abort_calls = 0

    def create_multipart_upload(self, Bucket, Key):
        self.create_calls += 1
        upload_id = str(self._next_id)
        self._next_id += 1
        self._uploads[upload_id] = {"bucket": Bucket, "key": Key, "parts": {}}
        return {"UploadId": upload_id}

    def upload_part(self, Bucket, Key, PartNumber, UploadId, Body):
        self._uploads[UploadId]["parts"][PartNumber] = bytes(Body)
        return {"ETag": f"etag-{PartNumber}"}

    def complete_multipart_upload(self, Bucket, Key, UploadId, MultipartUpload):
        upload = self._uploads.pop(UploadId)
        parts = upload["parts"]
        data = b"".join(parts[p["PartNumber"]] for p in MultipartUpload["Parts"])
        self.objects[(Bucket, Key)] = data

    def abort_multipart_upload(self, Bucket, Key, UploadId):
        self.abort_calls += 1
        self._uploads.pop(UploadId, None)

    def put_object(self, Bucket, Key, Body):
        self.objects[(Bucket, Key)] = bytes(Body)

    def head_object(self, Bucket, Key):
        return {"ContentLength": len(self.objects[(Bucket, Key)])}


def test_pull_year_stream_mirrors_raw_csv_to_s3_exactly(tmp_path):
    items_response, item_response = _items_and_item(2023)
    http_get = _make_http_get(items_response, item_response)
    csv_bytes = _make_fixture_csv()

    def http_stream(url):
        assert url == "https://download.cms.gov/stream.csv"
        return io.BytesIO(csv_bytes)

    fake_s3 = FakeS3Client()
    manifest = pull_year_stream(
        2023,
        tmp_path,
        s3_bucket="giggit-area-raw-payments",
        products=PRODUCTS,
        http_get=http_get,
        http_stream=http_stream,
        s3_client=fake_s3,
        database_url=None,
    )

    assert manifest["complete"] is True
    assert manifest["rows_scanned"] == 3
    assert manifest["rows_matched"] == 3
    assert manifest["bytes"] == len(csv_bytes)
    assert manifest["s3_bucket"] == "giggit-area-raw-payments"
    assert manifest["s3_key"] == "raw/2023/stream.csv"

    # The S3 object is byte-for-byte the original CSV -- nothing lost or
    # reordered across the tee/multipart-part boundary.
    assert fake_s3.objects[("giggit-area-raw-payments", "raw/2023/stream.csv")] == csv_bytes

    matched_path = tmp_path / "2023" / "matched.jsonl.gz"
    with gzip.open(matched_path, "rt", encoding="utf-8") as f:
        matched = [json.loads(line) for line in f if line.strip()]
    assert {r["record_id"] for r in matched} == {"s1", "s2", "s3"}


def test_pull_year_stream_splits_into_multiple_s3_parts(tmp_path, monkeypatch):
    # Force a tiny part size so the ~500-byte fixture CSV spans several
    # multipart parts, proving the tee reassembles them correctly.
    monkeypatch.setattr(pull_open_payments_module, "S3_PART_MIN_BYTES", 50)
    items_response, item_response = _items_and_item(2023)
    http_get = _make_http_get(items_response, item_response)
    csv_bytes = _make_fixture_csv()

    def http_stream(url):
        return io.BytesIO(csv_bytes)

    fake_s3 = FakeS3Client()
    manifest = pull_year_stream(
        2023,
        tmp_path,
        s3_bucket="giggit-area-raw-payments",
        products=PRODUCTS,
        http_get=http_get,
        http_stream=http_stream,
        s3_client=fake_s3,
    )
    assert manifest["complete"] is True
    assert fake_s3.objects[("giggit-area-raw-payments", "raw/2023/stream.csv")] == csv_bytes
    # Multiple parts really were used, not one big part.
    assert manifest["bytes"] > 50


def test_pull_year_stream_skips_already_complete_year_without_touching_s3(tmp_path):
    year_dir = tmp_path / "2023"
    year_dir.mkdir()
    (year_dir / "manifest.json").write_text(json.dumps({"year": 2023, "complete": True}))

    fake_s3 = FakeS3Client()

    def http_get(url):
        raise AssertionError("should not be called for an already-complete year")

    def http_stream(url):
        raise AssertionError("should not be called for an already-complete year")

    manifest = pull_year_stream(
        2023,
        tmp_path,
        s3_bucket="giggit-area-raw-payments",
        products=PRODUCTS,
        http_get=http_get,
        http_stream=http_stream,
        s3_client=fake_s3,
    )
    assert manifest["complete"] is True
    assert fake_s3.create_calls == 0


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")
def test_pull_year_stream_upserts_matched_rows_into_postgres_during_the_pull(tmp_path):
    import psycopg

    database_url = os.environ["TEST_DATABASE_URL"]
    repo_root = __import__("pathlib").Path(__file__).resolve().parent.parent
    ensure_schema(
        database_url,
        [repo_root / "sql" / "001_schema.sql", repo_root / "sql" / "002_views.sql"],
    )
    with psycopg.connect(database_url) as conn:
        conn.execute("DELETE FROM payments WHERE record_id IN ('s1', 's2', 's3')")
        conn.commit()

    items_response, item_response = _items_and_item(2023)
    http_get = _make_http_get(items_response, item_response)
    csv_bytes = _make_fixture_csv()

    def http_stream(url):
        return io.BytesIO(csv_bytes)

    fake_s3 = FakeS3Client()
    manifest = pull_year_stream(
        2023,
        tmp_path,
        s3_bucket="giggit-area-raw-payments",
        products=PRODUCTS,
        http_get=http_get,
        http_stream=http_stream,
        s3_client=fake_s3,
        database_url=database_url,
    )
    assert manifest["complete"] is True
    assert manifest["rows_upserted"] == 3

    with psycopg.connect(database_url) as conn:
        rows = conn.execute(
            "SELECT record_id FROM payments WHERE record_id IN ('s1', 's2', 's3') "
            "ORDER BY record_id"
        ).fetchall()
    assert [r[0] for r in rows] == ["s1", "s2", "s3"]

    with psycopg.connect(database_url) as conn:
        conn.execute("DELETE FROM payments WHERE record_id IN ('s1', 's2', 's3')")
        conn.commit()
