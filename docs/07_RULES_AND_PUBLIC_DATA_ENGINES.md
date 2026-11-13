# 07 — Rules & Public Data Engines

> **Version 0.2.1 — 2026-11-13 (upload-first MVP revision; HC-6 scope clarified in 0.2.1).** Original: `_archive/v0.1-2026-11-13/07_…md`.
> **Scope:**
> - [MVP-limited] a selected RBI rule subset in **shadow mode only** (never user-visible); curated data for ≤ 5 demo lenders (results capped at UNCLEAR).
> - [Deferred] full regulatory rules engine (Later-L6), automated lender-site scraping and RBI list automation (Later-L5).
>
> Status mapping per doc 08 §3.2 (`CONFLICT_REGULATORY_NOTE` removed).

## 1. Purpose
- **Rules:** run cited, versioned RBI rules over claims and document terms. In the MVP this exists to **build and evaluate** the engine, not to inform users.
- **Public data:** demonstrate lender identity checks and published-range context, clearly labelled as general information.

- **Depends on:** docs 04, 05, 06, 08, 11.

## 2. Prerequisites
- **HC-6** (legal/compliance review) governs the approval of every curated rule and every user-facing regulatory finding. Every rule stays in shadow mode until HC-6 approves it individually (`reviewed_by` + `hc6_approved_at` recorded; `rules.user_visible` flag in doc 06 §4a). The review package is produced in Phase 3 (task T3.7). The HC-6 approval checkpoint occurs in Phase 4, after the package is ready. It does not block the MVP release: without it, all rules ship in shadow.

## 3. Rules engine

### 3.1 Sources (retrieved 2026-11-13; quotes and applicability to be verified at HC-6)
| Area | Instrument | Status |
|---|---|---|
| KFS / APR disclosure | RBI/2024-25/18 (15 Apr 2024). All REs, retail and MSME term loans, mandatory for sanctions from 1 Oct 2024 | [V] |
| Prepayment charges | RBI (Pre-payment Charges on Loans) Directions, 2025 (2 Jul 2025; loans sanctioned/renewed from 1 Jan 2026). Floating-rate loans to individuals for non-business use carry no charge; exemptions and ceilings to verify | [V] partial |
| Digital lending | RBI Digital Lending Directions 2025 (RBI/2025-26/36, 8 May 2025) | [V] partial |
| Penal charges | Aug 2023 circular | **[A] not retrieved** |

### 3.2 Rule format (`backend/data/rules/*.yaml`)
```yaml
id: RBI-PREPAY-2025-FLOATING-PERSONAL
version: 1
title: No pre-payment charges on floating-rate loans to individuals (non-business)
instrument: "RBI (Pre-payment Charges on Loans) Directions, 2025"
source_url: https://www.rbi.org.in/scripts/FS_Notification.aspx?Id=12878
retrieved_on: 2026-11-13
effective_from: 2026-01-01
quote: null                 # verbatim paragraph — to be filled and checked at HC-6
applies_when: { borrower_type: individual, purpose: non_business, rate_type: floating,
                sanction_or_renewal_date_gte: 2026-01-01, lender_category_not_in: [ ...verify at HC-6 ] }
checks:
  - target: claim            # or doc_term
    claim_types: [prepayment_charge]
    expect: { value_eq: 0 }
    on_violation: CONFLICT   # finding_kind: regulatory (doc 08 §3.2)
explain_en: null             # reviewed text, HC-6
explain_hi: null
reviewed_by: null
enabled: false
```

### 3.3 Behaviour
- The engine always runs. When `enabled:false`, `reviewed_by:null` or there is no HC-6 approval, it returns `NOT_VERIFIED(rule_not_enabled)` to the orchestrator and stores the would-be verdict in `shadow_verdicts`. These are **excluded from merge, UI, events and reports** (doc 08 §7).
- **Applicability:** any missing fact → `NOT_VERIFIED(applicability_unknown, missing=[…])` (shadow in the MVP). The engine never guesses.
- **Phrasing when enabled later:** "This may not be consistent with {instrument} ({link}). Ask the lender to confirm." It never says "illegal".
- **MVP rule subset (shadow):** KFS provided; APR disclosed; charges not in the KFS; prepayment on floating personal loans; penal charges not as penal interest.

## 4. Public data engine [MVP-limited: demo only]
| Check | Source (MVP) | Max status |
|---|---|---|
| Lender identity / RBI registration | Hand-curated `public_entities` rows for ≤ 5 demo lenders, each with `source_url` + `source_date` | MATCH ("listed as RBI-registered NBFC per {source}, {date}") or `NOT_VERIFIED(entity_not_found)` |
| Published fee/rate ranges | Hand-curated `public_snapshots` with URL and capture date | `UNCLEAR(public_data_only)`. Never CONFLICT |

Rules:
- The UI shows "Demo data — general published information, not your sanctioned terms".
- Name matching uses `pg_trgm` ≥ 0.6. Ambiguous matches → the user picks the lender.
- **Deferred → Later-L5:** RBI list import automation and lender-site scraping (respecting robots.txt and terms).

## 5. API
- `GET /api/v1/lenders/search?q=` (demo data)
- `PUT /api/v1/sessions/{id}/facts` (stored; used by shadow rules)
- `GET /api/v1/rules` returns **nothing user-facing in the MVP** (admin-only listing of the shadow corpus)

## 6. Testing
- Each rule: ≥ 4 fixtures (violates, complies, not applicable, unknown).
- Shadow verdicts never reach `verification_results`, events or reports (test).
- Public data never yields CONFLICT (property test).

## 7. Acceptance criteria (Phase 3/4)
- 0 user-visible rule outputs in the MVP.
- Shadow precision/recall on fixtures is reported for HC-6 planning.

## 8. Human review
- **HC-6:** each rule's verbatim quote, applicability list, wording, translations and reviewer. Status: not started.

## 9. Common failures
- Applying the 2025 prepayment rule to pre-2026 sanctions.
- Presenting published "starting from" rates as this customer's terms.
