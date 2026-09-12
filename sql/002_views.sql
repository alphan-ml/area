-- AREA v0 -- quarterly aggregate views (SPEC-area.md section 3.3).
-- Read by the forecast tool (build_series.py, W-B2) and the query tool's
-- allow-listed schema (W-B2); never written to.

CREATE OR REPLACE VIEW q_totals AS
SELECT
    quarter,
    product,
    manufacturer,
    SUM(amount_usd) AS total_usd,
    COUNT(*)        AS payment_count
FROM payments
GROUP BY quarter, product, manufacturer;

CREATE OR REPLACE VIEW q_by_specialty AS
SELECT
    quarter,
    physician_specialty,
    SUM(amount_usd) AS total_usd,
    COUNT(*)        AS payment_count
FROM payments
GROUP BY quarter, physician_specialty;

CREATE OR REPLACE VIEW q_by_state AS
SELECT
    quarter,
    physician_state,
    SUM(amount_usd) AS total_usd,
    COUNT(*)        AS payment_count
FROM payments
GROUP BY quarter, physician_state;

GRANT SELECT ON q_totals, q_by_specialty, q_by_state TO area_reader;
