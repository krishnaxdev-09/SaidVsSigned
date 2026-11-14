"""Phase 3 / T3.5: rules, public_entities, public_snapshots (doc 11 §3 DDL, doc 07) + ≤ 5 demo lenders.

The demo lenders are the FICTIONAL lenders of the synthetic dataset (eval/datasets/agreements), marked demo_only with
a "synthetic demo data" source. No real lender's registration status is asserted.

Revision ID: 0012_rules_public_data
Revises: 0011_reports_qa
"""

from alembic import op

revision = "0012_rules_public_data"
down_revision = "0011_reports_qa"
SOURCE = "https://saidsigned.invalid/demo-data"  # synthetic demo data, not a real register
DEMO = [  # name, normalized, category, product, field, low, high, unit
    ("Aangan Home Finance Ltd. (fictional)", "aangan home finance", "HFC (demo)", "home_loan", "interest_rate", "8.50", "9.75", "%"),
    ("Udyam Capital Partners (fictional)", "udyam capital partners", "NBFC (demo)", "business_loan", "interest_rate",
     "15.00", "21.00", "%"),
    ("Navkiran Credit Ltd. (fictional)", "navkiran credit", "NBFC (demo)", "personal_loan", "processing_fee", "1.00", "3.00", "%"),
    ("Sahaj Finance Ltd. (fictional)", "sahaj finance", "NBFC (demo)", "personal_loan", "interest_rate", "11.50", "16.00", "%"),
    ("Prakash Lending Co. (fictional)", "prakash lending", "NBFC (demo)", "personal_loan", "interest_rate", "13.00", "18.00", "%"),
]


def upgrade() -> None:
    op.execute("""
CREATE TABLE rules (id text, version int, body jsonb, source_url text, effective_from date, effective_to date,
  reviewed_by text, hc6_approved_at timestamptz, enabled bool DEFAULT false, PRIMARY KEY (id, version));
CREATE TABLE public_entities (id uuid PRIMARY KEY, name text, normalized_name text, aliases text[], category text,
  registration_no text, regulator text, source_url text, source_date date, demo_only bool DEFAULT true);
CREATE INDEX ON public_entities USING gin (normalized_name gin_trgm_ops);
CREATE TABLE public_snapshots (id uuid PRIMARY KEY, entity_id uuid REFERENCES public_entities, product_type text,
  field text, value_low numeric, value_high numeric, unit text, conditions text, source_url text, captured_at date,
  captured_by text);
""")
    for i, (name, norm, cat, product, field, lo, hi, unit) in enumerate(DEMO, start=1):
        eid = f"00000000-0000-4000-8000-{i:012d}"
        op.execute(f"""
INSERT INTO public_entities (id, name, normalized_name, aliases, category, registration_no, regulator, source_url,
  source_date, demo_only) VALUES ('{eid}', '{name}', '{norm}', ARRAY['{norm.split()[0]}'], '{cat}', 'DEMO-{i:04d}',
  'demo', '{SOURCE}', '2026-11-14', true);
INSERT INTO public_snapshots (id, entity_id, product_type, field, value_low, value_high, unit, conditions, source_url,
  captured_at, captured_by) VALUES (gen_random_uuid(), '{eid}', '{product}', '{field}', {lo}, {hi}, '{unit}',
  'demo published range', '{SOURCE}', '2026-11-14', 'saidsigned-demo');
""")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS public_snapshots; DROP TABLE IF EXISTS public_entities; DROP TABLE IF EXISTS rules;")
