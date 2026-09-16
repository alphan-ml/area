'''Tests for area.causal.diff_in_diff -- raw pulled data (same fixture
format as test_build_series.py) -> a 2x2 diff-in-diff estimate. Offline,
zero network, zero secrets.'''

from __future__ import annotations

import gzip
import json

from area.causal.diff_in_diff import build_group_series, estimate_diff_in_diff, run


def _row(record_id, quarter, manufacturer, amount, program_year=2023):
    return {
        'record_id': record_id,
        'program_year': str(program_year),
        'payment_date': '2023-02-01',
        'quarter': quarter,
        'physician_id': '123',
        'physician_specialty': 'Endocrinology',
        'physician_state': 'CA',
        'manufacturer': manufacturer,
        'product': 'Ozempic',
        'product_generic': 'semaglutide',
        'nature_of_payment': 'Food and Beverage',
        'amount_usd': str(amount),
        'matched_field': 'Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_1',
        'source_year_dataset': 'test-dataset',
    }


def _write_year(raw_dir, year, rows, complete=True):
    year_dir = raw_dir / str(year)
    year_dir.mkdir(parents=True, exist_ok=True)
    with gzip.open(year_dir / 'matched.jsonl.gz', 'wt', encoding='utf-8') as f:
        for row in rows:
            f.write(json.dumps(row) + chr(10))
    manifest = {'complete': complete, 'rows_matched': len(rows)}
    (year_dir / 'manifest.json').write_text(json.dumps(manifest))


def test_build_group_series_splits_by_manufacturer():
    raw_dir_rows = [
        _row('r1', '2023Q1', 'Novo Nordisk', '100.00'),
        _row('r2', '2023Q1', 'Generic Co', '10.00'),
        _row('r3', '2023Q2', 'Eli Lilly', '200.00'),
    ]
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        raw_dir = Path(td) / 'raw'
        _write_year(raw_dir, 2023, raw_dir_rows)
        treated, control = build_group_series(raw_dir)
    assert treated == {'2023Q1': 100.0, '2023Q2': 200.0}
    assert control == {'2023Q1': 10.0}


def test_estimate_diff_in_diff_computes_real_arithmetic():
    treated = {'2023Q1': 100.0, '2023Q2': 200.0}
    control = {'2023Q1': 50.0, '2023Q2': 60.0}
    result = estimate_diff_in_diff(treated, control, '2023Q2')
    assert result['ok'] is True
    assert result['treated_mean_before_usd'] == 100.0
    assert result['treated_mean_after_usd'] == 200.0
    assert result['control_mean_before_usd'] == 50.0
    assert result['control_mean_after_usd'] == 60.0
    assert result['treated_delta_usd'] == 100.0
    assert result['control_delta_usd'] == 10.0
    assert result['diff_in_diff_usd'] == 90.0


def test_estimate_diff_in_diff_reports_insufficient_data_honestly():
    treated = {'2023Q1': 100.0}
    control = {'2023Q1': 50.0}
    # event quarter after every real quarter -- no 'after' period exists
    result = estimate_diff_in_diff(treated, control, '2024Q1')
    assert result['ok'] is False
    assert 'insufficient data' in result['reason']
    assert 'diff_in_diff_usd' not in result


def test_run_writes_real_output_and_picks_median_event_quarter(tmp_path):
    raw_dir = tmp_path / 'raw'
    rows = [
        _row('r1', '2023Q1', 'Novo Nordisk', '100.00'),
        _row('r2', '2023Q1', 'Generic Co', '10.00'),
        _row('r3', '2023Q2', 'Novo Nordisk', '150.00'),
        _row('r4', '2023Q2', 'Generic Co', '12.00'),
        _row('r5', '2023Q3', 'Novo Nordisk', '300.00'),
        _row('r6', '2023Q3', 'Generic Co', '20.00'),
    ]
    _write_year(raw_dir, 2023, rows)
    out_path = tmp_path / 'causal' / 'latest.json'

    result = run(raw_dir, out_path)

    assert out_path.exists()
    written = json.loads(out_path.read_text())
    assert written['ok'] is True
    assert result == written
    assert result['event_quarter'] == '2023Q2'  # median of [Q1, Q2, Q3]
    assert result['treated_manufacturers'] == ['Novo Nordisk', 'Eli Lilly']


def test_run_reports_no_data_when_raw_dir_has_no_complete_year(tmp_path):
    raw_dir = tmp_path / 'raw'
    raw_dir.mkdir()
    out_path = tmp_path / 'causal' / 'latest.json'
    result = run(raw_dir, out_path)
    assert result['ok'] is False
    assert 'no real matched rows' in result['reason']
    assert out_path.exists()


def test_run_respects_custom_treated_manufacturers_and_event_quarter(tmp_path):
    raw_dir = tmp_path / 'raw'
    rows = [
        _row('r1', '2023Q1', 'Acme Pharma', '100.00'),
        _row('r2', '2023Q2', 'Acme Pharma', '300.00'),
        _row('r3', '2023Q1', 'Other Co', '10.00'),
        _row('r4', '2023Q2', 'Other Co', '11.00'),
    ]
    _write_year(raw_dir, 2023, rows)
    out_path = tmp_path / 'causal' / 'latest.json'

    result = run(
        raw_dir, out_path,
        treated_manufacturers=('Acme Pharma',),
        event_quarter='2023Q2',
    )
    assert result['ok'] is True
    assert result['treated_manufacturers'] == ['Acme Pharma']
    assert result['event_quarter'] == '2023Q2'
    assert result['diff_in_diff_usd'] == (300.0 - 100.0) - (11.0 - 10.0)
