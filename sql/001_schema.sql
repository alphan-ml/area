-- AREA v0 -- payments table (SPEC-area.md section 3.3).
--
-- Loaded by src/area/load_neon.py from the GLP-1-matched rows pulled by
-- src/area/pull_open_payments.py (see raw/<year>/manifest.json for exactly
-- which CMS Open Payments dataset/rows this table is built from). One row
-- here is one CMS Open Payments "general payment" record that matched the
-- GLP-1 product list in data/products.json.
--
-- Idempotent by design: record_id is the CMS Open Payments Record_ID (their
-- own primary key), so re-loading the same raw files with an upsert on
-- record_id changes nothing -- see tests/test_load_idempotent.py.

-- quarter is nullable, not the spec text's plain `text` (D-load-1,
-- CONTEXT.md): it is derived from payment_date via quarter_of(), which
-- returns None for a blank/unparseable date. The "full data, no sampling"
-- non-negotiable (SPEC-area.md section 1) means a row with a bad date
-- still belongs in this table -- load_neon.py must not drop it or invent
-- a quarter for it -- so the column has to accept NULL.
CREATE TABLE IF NOT EXISTS payments (
    record_id           text PRIMARY KEY,
    program_year        int NOT NULL,
    payment_date        date,
    quarter             text,                    -- e.g. '2023Q2'; NULL if payment_date was unparseable
    physician_id        text,                    -- Covered_Recipient_NPI
    physician_specialty text,                    -- Covered_Recipient_Specialty_1
    physician_state     text,                    -- Recipient_State
    manufacturer        text NOT NULL,           -- Applicable_Manufacturer_..._Making_Payment_Name
    product              text NOT NULL,           -- canonical matched product name (data/products.json match rule)
    product_generic      text,                    -- semaglutide | tirzepatide, when determinable
    nature_of_payment    text,                    -- Nature_of_Payment_or_Transfer_of_Value
    amount_usd           numeric(14, 2) NOT NULL,
    matched_field        text NOT NULL,           -- which Open Payments field matched, e.g. Name_of_Drug_..._2
    source_year_dataset   text NOT NULL            -- the CMS metastore dataset identifier this row was pulled from
);

CREATE INDEX IF NOT EXISTS payments_quarter_idx ON payments (quarter);
CREATE INDEX IF NOT EXISTS payments_specialty_idx ON payments (physician_specialty);
CREATE INDEX IF NOT EXISTS payments_state_idx ON payments (physician_state);
CREATE INDEX IF NOT EXISTS payments_product_idx ON payments (product);
CREATE INDEX IF NOT EXISTS payments_manufacturer_idx ON payments (manufacturer);

-- Read-only role for the live agent (query_tool.py, W-B2) and the web API.
-- Statement timeout keeps a bad LLM-written query from hanging a Vercel
-- function -- guardrails are enforced in query_tool.py too (W-B2), this is
-- defense in depth at the database level.
--
-- No password is set here on purpose (secrets only via env, never in a
-- committed SQL file -- BUILD INSTRUCTION rule). This CREATE ROLE is
-- LOGIN-capable but has no password until one is set out-of-band, either
-- with `ALTER ROLE area_reader PASSWORD '<real secret>'` run by hand once
-- against the real Neon database, or through Neon's own role/console API
-- (which manages the password itself, in which case this CREATE ROLE step
-- may be a no-op / superseded -- see CONTEXT.md if Neon's role model
-- differs from plain Postgres once W-B4 wires a real database).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'area_reader') THEN
        CREATE ROLE area_reader LOGIN;
    END IF;
END
$$;

ALTER ROLE area_reader SET statement_timeout = '10s';

GRANT SELECT ON payments TO area_reader;
-- q_totals / q_by_specialty / q_by_state (002_views.sql) are granted there,
-- since they must exist before GRANT can reference them.
