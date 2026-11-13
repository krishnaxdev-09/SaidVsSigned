# 07 — Rules & Public Data Engines (Roadmap P7; KFS/APR and prepayment subset in P3)

## Purpose
- **Rules engine:** check claims and session facts against **curated, cited, versioned** RBI regulatory rules. It informs, it does not give legal conclusions.
- **Public data engine:** check lender identity/registration and compare claims with the lender's **published general terms**. These are clearly separated from the customer's individually sanctioned terms.

## Prerequisites
- Docs 04–06.
- HC-6 legal/compliance review of the rule corpus before it is shown to real users.

---

## Part A — Rules engine

### Verified sources (retrieved 2026-11-13; re-verify each at HC-6)
| Rule area | Instrument | Key points used | Status |
|---|---|---|---|
| KFS | RBI/2024-25/18, *Key Facts Statement for Loans & Advances*, 15 Apr 2024 | All REs. Retail & MSME term loans. Mandatory for loans sanctioned on or after **1 Oct 2024**. KFS must include **APR** (all-inclusive annualised cost) and an amortisation schedule, in a language the borrower understands. Charges not in the KFS cannot be levied without explicit borrower consent [A: verify exact wording]. Credit cards excluded | [V] |
| Prepayment | *RBI (Pre-payment Charges on Loans) Directions, 2025*, 2 Jul 2025, applicable to loans sanctioned/renewed from **1 Jan 2026** | No prepayment charges on **floating-rate** loans to individuals for non-business purposes. MSE business-loan exemption up to a ceiling (₹7.5 cr in draft; verify final). Some lender categories exempt (e.g. NBFC-Base Layer, certain UCBs) for certain loans. No charge where prepayment is at the lender's instance | [V] (details need HC-6) |
| Digital lending | *RBI Digital Lending Directions, 2025* (RBI/2025-26/36), 8 May 2025 | KFS before execution. LSP/DLA disclosures. DLAs reported on CIMS. Cooling-off/look-up period [A] | [V] partial |
| Penal charges | *Fair Lending Practice – Penal Charges in Loan Accounts* (Aug 2023, effective 1 Jan 2024) [A] | Penal charges not to be capitalised as "penal interest" added to the rate | [A] — **not retrieved in research pass** |
| Fair Practices Code | RBI FPC for banks/NBFCs | Disclosure of terms, vernacular communication | [A] |

### Rule corpus format (`backend/data/rules/*.yaml`)
```yaml
id: RBI-PREPAY-2025-FLOATING-PERSONAL
title: No pre-payment charges on floating-rate loans to individuals (non-business)
instrument: "RBI (Pre-payment Charges on Loans) Directions, 2025"
circular_ref: "<number from PDF>"
source_url: https://www.rbi.org.in/scripts/FS_Notification.aspx?Id=12878
retrieved_on: 2026-11-13
effective_from: 2026-01-01
effective_to: null
quote: "<verbatim paragraph copied from the PDF — filled at HC-6>"
applies_when:            # all must be known & true; unknown → NOT_VERIFIED(applicability_unknown)
  borrower_type: individual
  purpose: non_business
  rate_type: floating
  sanction_or_renewal_date_gte: 2026-01-01
  lender_category_not_in: [NBFC_BL, UCB_T1, UCB_T2]   # verify list at HC-6
checks:
  - claim_types: [prepayment_charge]
    expect: { value_eq: 0 }        # spoken or document value must be 0
    on_violation: CONFLICT_REGULATORY_NOTE
  - doc_field: prepayment_charge
    expect: { value_eq: 0 }
    on_violation: CONFLICT_REGULATORY_NOTE
explain_en: "For floating-rate loans taken by individuals for personal use, RBI rules (from 1 Jan 2026) say lenders should not charge for paying early."
explain_hi: "<reviewed translation>"
reviewed_by: null          # must be set before rule is enabled in production
enabled: false
```

MVP rules [R]:
1. KFS provided (session fact: `kfs_received`)
2. APR disclosed in KFS
3. Charges not in KFS
4. Prepayment on floating personal loan
5. Penal charges not as penal interest
6. Digital-lending KFS before execution
7. "Agent says a KFS is not needed / will come later" → informational

### Session facts needed
`lender_category` (from public data engine), `borrower_type`, `purpose`, `rate_type` (fixed/floating from claim or doc), `sanction_date` (doc or "today"), `channel_digital` (bool), `kfs_received`. Missing facts produce `NOT_VERIFIED(applicability_unknown, missing=[…])` and a question to ask. The engine **never guesses applicability**.

### Output rules
- A regulatory finding is phrased as *"This may not be consistent with RBI rule X (link). Ask the lender to confirm."* It is never "illegal".
- Status: a rule violation backed by a verified spoken claim and confirmed applicability → `CONFLICT` with `RuleEvidence{rule_id, instrument, source_url, effective_from, quote}`. Otherwise UNCLEAR or NOT_VERIFIED.
- Rules with `enabled:false` or `reviewed_by:null` run in **shadow mode**: logged for evaluation, never shown to users.

### Maintenance
- Each rule carries `retrieved_on`. A quarterly checklist re-verifies sources.
- Superseded rules get `effective_to` and remain for historical sessions.

---

## Part B — Public data engine

### Checks
| Check | Source | Output |
|---|---|---|
| Lender is an RBI-regulated entity (bank / NBFC) | RBI published lists (banks; NBFC registration list from RBI/CIMS downloads) [A: exact URLs/formats to confirm] → imported weekly into `public_entities` | MATCH ("Registered NBFC, CoR no. X") / NOT_VERIFIED ("Not found — check spelling or ask for the lender's RBI registration") — **never CONFLICT on a name-match failure** |
| Digital lending app/LSP named by agent is associated with the lender | RBI DLA list on CIMS (if public) [A] | MATCH / NOT_VERIFIED |
| Agent's rate/fee vs lender's **published** range | Curated `public_snapshots` from lender product pages (manual or scraped with permission/robots.txt), each with URL + snapshot date | Informational only: "Lender's website lists processing fee up to 2.5% (as of DATE). This is a general range, not your sanctioned terms." Max status UNCLEAR |
| Lender category (for rules applicability) | Same lists | Session fact |

### Data model
- `public_entities(id, name, normalized_name, aliases[], category, registration_no, regulator, source_url, source_date)`
- `public_snapshots(id, entity_id, product_type, field, value_low, value_high, unit, conditions, source_url, captured_at, captured_by)`

Name matching: normalise case, punctuation and suffixes (Ltd, Limited, Pvt, Finance, Fincorp, NBFC). Use trigram similarity (`pg_trgm`) ≥ 0.6. Ambiguous matches → ask the user to choose.

### MVP scope [R]
- Import the RBI bank list plus the NBFC list.
- Hand-curate snapshots for **5 demo lenders** with URLs and dates.
- Scraping automation is deferred. Any scraper must respect robots.txt and terms of service.

## API
- `GET /api/v1/lenders/search?q=` → candidates
- `PUT /api/v1/sessions/{id}/facts` `{lender_entity_id?, borrower_type?, purpose?, rate_type?, sanction_date?, kfs_received?}`
- `GET /api/v1/rules` (enabled rules with sources, for transparency)

## Testing
- Each rule has ≥ 4 fixture cases: applies + violates, applies + complies, not applicable, applicability unknown.
- Public data: name-matching tests on 50 lender name variants.

## Acceptance criteria
- No rule result is shown without `source_url` and `effective_from`.
- `applicability_unknown` is returned whenever any `applies_when` fact is missing (100% in tests).
- Public data never yields `CONFLICT`.

## Human review
**HC-6:** every rule's quote, applicability, wording and translations are reviewed by someone qualified. Document reviewer and date in `reviewed_by`.

## Common failures
- Applying the 2025 prepayment rule to loans sanctioned before 1 Jan 2026.
- Treating published "starting from" rates as a promise to this customer.
- Fuzzy-matching a lender name to the wrong NBFC with a similar name.
