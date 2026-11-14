# HC-6 rule review package (2026-11-14)

> For a qualified legal/compliance reviewer. **No rule is enabled** (`enabled: false`, `reviewed_by: null`); the rules run in shadow only and nothing reaches users. HC-6 approves each rule individually (doc 07 §2) in Phase 4. Quotes are verbatim fragments retrieved from the source URL on the date shown; the reviewer confirms the full paragraphs, applicability lists, exemptions and the en/hi wording. Fixtures and agreements are synthetic.

Decision needed per rule: **approve as written / approve with changes / keep in shadow**. Approval records `reviewed_by` + `hc6_approved_at`; `rules.user_visible` also needs the startup check updated.

## RBI-KFS-2024-APR-DISCLOSED (v1)

**The KFS discloses the Annual Percentage Rate (APR)**

- Instrument: RBI/2024-25/18 DOR.STR.REC.13/13.03.00/2024-25 — Key Facts Statement (KFS) for Loans & Advances (15 Apr 2024)
- Source: https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=12663&Mode=0 (retrieved 2026-11-14)
- Effective from: 2024-10-01
- Applicability: Retail and MSME term loans sanctioned on or after 1 Oct 2024.
- Engine applies when: `{'loan_kind': 'retail_or_msme_term', 'sanction_date_gte': '2024-10-01'}`; checks: `[{'target': 'doc_text', 'expect': {'contains_any': ['annual percentage rate', 'apr']}, 'on_violation': 'CONFLICT'}]`

Verbatim fragments:

> ¶6: “The KFS shall also include a computation sheet of annual percentage rate (APR)”
> ¶11: “all new retail and MSME term loans sanctioned on or after October 1, 2024, including fresh loans to existing customers”

- Draft (en): Your documents don't seem to show the Annual Percentage Rate (APR). This may not be consistent with RBI's KFS circular (RBI/2024-25/18). Ask the lender to confirm the APR in writing.
- Draft (hi): आपके दस्तावेज़ों में सालाना प्रतिशत दर (APR) नहीं दिखती। यह RBI के KFS सर्कुलर (RBI/2024-25/18) के अनुरूप न हो सकता है। लेंडर से APR लिखित में पूछें।
- Shadow on 4 fixtures (positive = violates): precision 1.00, recall 1.00 (tp 1, fp 0, fn 0)

## RBI-KFS-2024-CHARGES-IN-KFS (v1)

**Charges not mentioned in the KFS are not levied**

- Instrument: RBI/2024-25/18 DOR.STR.REC.13/13.03.00/2024-25 — Key Facts Statement (KFS) for Loans & Advances (15 Apr 2024)
- Source: https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=12663&Mode=0 (retrieved 2026-11-14)
- Effective from: 2024-10-01
- Applicability: Retail and MSME term loans sanctioned on or after 1 Oct 2024. Reviewer to confirm how 'explicit consent' interacts with charges stated elsewhere in the agreement.
- Engine applies when: `{'loan_kind': 'retail_or_msme_term', 'sanction_date_gte': '2024-10-01'}`; checks: `[{'target': 'doc_term', 'fields': ['other_charge', 'insurance_bundled', 'processing_fee'], 'expect': {'in_kfs_table': True}, 'on_violation': 'CONFLICT'}]`

Verbatim fragments:

> ¶8: “Any fees, charges, etc. which are not mentioned in the KFS, cannot be charged by the REs to the borrower at any stage”
> ¶8: “without explicit consent of the borrower.”

- Draft (en): A charge appears in your documents but not in the Key Fact Statement. This may not be consistent with RBI's KFS circular (RBI/2024-25/18). Ask the lender to confirm whether it applies.
- Draft (hi): एक चार्ज आपके दस्तावेज़ों में है, लेकिन KFS में नहीं। यह RBI के KFS सर्कुलर (RBI/2024-25/18) के अनुरूप न हो सकता है। लेंडर से पूछें कि यह लागू है या नहीं।
- Shadow on 5 fixtures (positive = violates): precision 1.00, recall 1.00 (tp 1, fp 0, fn 0)

## RBI-KFS-2024-PROVIDED (v1)

**A Key Fact Statement is provided for retail and MSME term loans**

- Instrument: RBI/2024-25/18 DOR.STR.REC.13/13.03.00/2024-25 — Key Facts Statement (KFS) for Loans & Advances (15 Apr 2024)
- Source: https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=12663&Mode=0 (retrieved 2026-11-14)
- Effective from: 2024-10-01
- Applicability: All REs in the circular's scope; retail and MSME term loans sanctioned on or after 1 Oct 2024.
- Engine applies when: `{'loan_kind': 'retail_or_msme_term', 'sanction_date_gte': '2024-10-01'}`; checks: `[{'target': 'session', 'expect': {'document_kind_present': 'kfs'}, 'on_violation': 'CONFLICT'}]`

Verbatim fragments:

> ¶2: “shall be applicable in cases of all retail and MSME term loan products”
> ¶4: “REs shall provide a KFS to all prospective borrowers to help them take an informed view before executing the loan contract”
> ¶11: “all new retail and MSME term loans sanctioned on or after October 1, 2024, including fresh loans to existing customers”

- Draft (en): Your documents don't seem to include a Key Fact Statement. RBI's KFS circular (RBI/2024-25/18) asks lenders to give one before you sign. Ask the lender for the KFS.
- Draft (hi): आपके दस्तावेज़ों में की फ़ैक्ट स्टेटमेंट (KFS) नहीं दिखती। RBI के KFS सर्कुलर (RBI/2024-25/18) के अनुसार साइन करने से पहले KFS दी जानी चाहिए। लेंडर से KFS माँगें।
- Shadow on 5 fixtures (positive = violates): precision 1.00, recall 1.00 (tp 1, fp 0, fn 0)

## RBI-PENAL-2023-NOT-INTEREST (v1)

**Penal charges are not levied as penal interest added to the rate**

- Instrument: RBI/2023-24/53 DoR.MCS.REC.28/01.01.001/2023-24 — Fair Lending Practice - Penal Charges in Loan Accounts (18 Aug 2023)
- Source: https://rbi.org.in/Scripts/NotificationUser.aspx?Id=12527&Mode=0 (retrieved 2026-11-14)
- Effective from: 2024-01-01
- Applicability: Commercial banks, co-operative banks, NBFCs and AIFIs (scope per the circular); from 1 Jan 2024 (an RBI notice later extended the timeline for existing loans — reviewer to confirm the dates).
- Engine applies when: `{'sanction_date_gte': '2024-01-01'}`; checks: `[{'target': 'doc_text', 'expect': {'not_contains_any': ['penal interest']}, 'on_violation': 'CONFLICT'}]`

Verbatim fragments:

> ¶3(i): “Penalty, if charged, for non-compliance of material terms and conditions of loan contract by the borrower shall be treated”
> ¶3(i): “shall not be levied in the form of ‘penal interest’ that is added to the rate of interest”
> ¶3(viii): “These instructions shall come into effect from January 1, 2024.”

- Draft (en): Your documents mention 'penal interest'. This may not be consistent with RBI's circular on penal charges (RBI/2023-24/53), which asks for penal charges instead of added interest. Ask the lender to confirm how late payments are charged.
- Draft (hi): आपके दस्तावेज़ों में 'पेनल इंटरेस्ट' लिखा है। यह RBI के पेनल चार्ज सर्कुलर (RBI/2023-24/53) के अनुरूप न हो सकता है। लेंडर से पूछें कि देर से भुगतान पर क्या चार्ज लगेगा।
- Shadow on 4 fixtures (positive = violates): precision 1.00, recall 1.00 (tp 1, fp 0, fn 0)

## RBI-PREPAY-2025-FLOATING-PERSONAL (v1)

**No pre-payment charges on floating-rate loans to individuals (non-business)**

- Instrument: RBI/2025-26/64 — Reserve Bank of India (Pre-payment Charges on Loans) Directions, 2025 (2 Jul 2025)
- Source: https://www.rbi.org.in/scripts/FS_Notification.aspx?Id=12878 (retrieved 2026-11-14)
- Effective from: 2026-01-01
- Applicability: Floating-rate loans to individuals for non-business purposes, sanctioned or renewed on or after 1 Jan 2026; commercial banks (excl. payments banks), co-operative banks, NBFCs and All India Financial Institutions (para 4). Reviewer to confirm the full para 4 list and any exemptions.
- Engine applies when: `{'borrower_type': 'individual', 'purpose': 'non_business', 'rate_type': 'floating', 'sanction_date_gte': '2026-01-01'}`; checks: `[{'target': 'doc_term', 'fields': ['prepayment_charge'], 'expect': {'value_eq': 0}, 'on_violation': 'CONFLICT'}, {'target': 'claim', 'claim_types': ['prepayment_charge'], 'expect': {'value_eq': 0}, 'on_violation': 'CONFLICT'}]`

Verbatim fragments:

> ¶4: “These Directions shall apply to all commercial banks (excluding payments banks), co-operative banks, NBFCs”
> ¶5: “An RE shall adhere to the following Directions regarding levy of pre-payment charges on all floating rate loans”
> ¶5(i): “For all loans granted for purposes other than business to individuals, with or without co-obligant(s),”
> ¶5(i): “an RE shall not levy pre-payment charges;”
> ¶3(ii): “These Directions shall be applicable to all loans and advances sanctioned or renewed on or after January 1, 2026.”

- Draft (en): Your loan seems to be a floating-rate loan for personal use, but a prepayment charge is mentioned. This may not be consistent with RBI's Pre-payment Charges Directions, 2025. Ask the lender to confirm.
- Draft (hi): आपका लोन फ़्लोटिंग रेट वाला निजी लोन लगता है, फिर भी प्रीपेमेंट चार्ज बताया गया है। यह RBI के प्री-पेमेंट चार्ज निर्देश, 2025 के अनुरूप न हो सकता है। लेंडर से पुष्टि करें।
- Shadow on 7 fixtures (positive = violates): precision 1.00, recall 1.00 (tp 2, fp 0, fn 0)

## Would-be outcomes on the synthetic agreements (shadow)

Assumed facts (not from users): individual borrower, sanctioned 2026-02-01, non-business except AG07 (business loan); rate type as printed.

| Agreement | RBI-KFS-2024-APR-DISCLOSED | RBI-KFS-2024-CHARGES-IN-KFS | RBI-KFS-2024-PROVIDED | RBI-PENAL-2023-NOT-INTEREST | RBI-PREPAY-2025-FLOATING-PERSONAL |
|---|---|---|---|---|---|
| AG01 | violates | complies | complies | complies | not_applicable |
| AG02 | violates | violates | complies | complies | not_applicable |
| AG03 | violates | complies | complies | complies | not_applicable |
| AG04 | violates | complies | complies | complies | not_applicable |
| AG05 | violates | complies | complies | complies | violates |
| AG06 | violates | complies | complies | violates | not_applicable |
| AG07 | violates | complies | complies | violates | not_applicable |
| AG08 | violates | violates | complies | complies | not_applicable |
| AG09 | violates | complies | complies | violates | violates |
| AG10 | violates | complies | complies | complies | not_applicable |

## Known limitations

- Fixture precision/recall measure the engine's logic on hand-written cases, not real documents.
- `RBI-KFS-2024-APR-DISCLOSED` uses a text search for 'annual percentage rate'/'APR'; KFS layouts vary.
- `RBI-PENAL-2023-NOT-INTEREST`: RBI later extended the timeline for existing loans; dates to confirm.
- Facts such as borrower type and sanction date come only from the user (`PUT /sessions/{id}/facts`); missing facts give `applicability_unknown`, never a guess.
