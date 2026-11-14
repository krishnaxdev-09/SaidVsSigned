# Claim extraction — leave-one-out on the 24 few-shots (2026-11-14)

- Model: `google.gemma-4-31b` (Bedrock Mantle), prompt `claim_extract_v2`, few-shots sha256 `474c35f4a98a`, temperature 0.
- Each example is held out of the prompt (23 others as few-shots) and extracted by the real model.
- Strict match = same claim_type, utterance_kind and vagueness, overlapping quote, equal normalised values (ranges and approximation included).
- Eligibility-equivalent match = the same, except the kind may differ between promise / assertion / conditional (all verifiable), per the user's rule of 2026-11-14.
- **Eligibility-equivalent: 37/37.**
- **Expected claims 37, extracted 37, matched 36.** Recall 0.97, precision 0.97. Small, self-authored set: this is a sanity check, not the T1.8 eval.
- Gemma is not fully deterministic at temperature 0: borderline utterance kinds (promise/assertion/conditional, all verifiable) can differ between runs.

## EN01 (en) — basic assertion; value with GST context

> S1 [spk_A]: The processing fee is 2% of the loan amount plus GST.

| | type / kind / vagueness | quote | values |
|---|---|---|---|
| expected ✅ | processing_fee / assertion / specific | The processing fee is 2% of the loan amount plus GST. | '2%'→2 |
| Gemma ✅ | processing_fee / assertion / specific | The processing fee is 2% of the loan amount plus GST. | '2%'→2 |

## EN02 (en) — one sentence, two claims (EMI and tenure)

> S1 [spk_A]: Your EMI will be 7,750 rupees for 24 months.

| | type / kind / vagueness | quote | values |
|---|---|---|---|
| expected ✅ | emi / promise / specific | Your EMI will be 7,750 rupees | '7,750 rupees'→7750 |
| expected ✅ | tenure / promise / specific | for 24 months | '24 months'→24 |
| Gemma ✅ | emi / promise / specific | Your EMI will be 7,750 rupees | '7,750 rupees'→7750 |
| Gemma ✅ | tenure / promise / specific | for 24 months. | '24 months'→24 |

## EN03 (en) — customer question vs agent answer

> S1 [spk_B]: So the processing fee is zero, right?
> S2 [spk_A]: Yes, zero processing fee for you.

| | type / kind / vagueness | quote | values |
|---|---|---|---|
| expected ✅ | processing_fee / question / specific | So the processing fee is zero, right? | 'zero'→0 |
| expected ✅ | processing_fee / assertion / specific | Yes, zero processing fee for you. | 'zero processing fee'→0 |
| Gemma ✅ | processing_fee / question / specific | the processing fee is zero, right? | 'zero'→0 |
| Gemma ✅ | processing_fee / assertion / specific | Yes, zero processing fee for you. | 'zero'→0 |

## EN04 (en) — hypothetical (not a promise) vs the actual offer; reducing basis

> S1 [spk_A]: If you had a salary account with us, the rate would have been 11%.
> S2 [spk_A]: For you it is 13.5% reducing.

| | type / kind / vagueness | quote | values |
|---|---|---|---|
| expected ✅ | interest_rate / hypothetical / specific | If you had a salary account with us, the rate would have been 11%. | '11%'→11 |
| expected ✅ | interest_rate / assertion / specific | For you it is 13.5% reducing. | '13.5% reducing'→13.5 |
| Gemma ✅ | interest_rate / hypothetical / specific | If you had a salary account with us, the rate would have been 11%. | '11%'→11 |
| Gemma ✅ | interest_rate / assertion / specific | For you it is 13.5% reducing. | '13.5%'→13.5 |

## EN05 (en) — conditional promise with a zero charge

> S1 [spk_A]: If you pay all EMIs on time, there is no penal charge.

| | type / kind / vagueness | quote | values |
|---|---|---|---|
| expected ✅ | penal_charge / conditional / specific | If you pay all EMIs on time, there is no penal charge. | 'no penal charge'→0 |
| Gemma ✅ | penal_charge / conditional / specific | there is no penal charge. | 'no penal charge'→0 |

## EN06 (en) — vague claims keep no values and are marked vague

> S1 [spk_A]: Our rates are the lowest in the market and there are no hidden charges.

| | type / kind / vagueness | quote | values |
|---|---|---|---|
| expected ✅ | interest_rate / assertion / vague | Our rates are the lowest in the market | — |
| expected ✅ | general_assurance / assertion / vague | there are no hidden charges. | — |
| Gemma ✅ | interest_rate / assertion / vague | Our rates are the lowest in the market | — |
| Gemma ✅ | general_assurance / assertion / vague | there are no hidden charges. | — |

## EN07 (en) — correction: both the first value and the restatement are claims

> S1 [spk_A]: Processing fee is 1%.
> S2 [spk_A]: Sorry, I mean 1.5% for this scheme.

| | type / kind / vagueness | quote | values |
|---|---|---|---|
| expected ✅ | processing_fee / assertion / specific | Processing fee is 1%. | '1%'→1 |
| expected ✅ | processing_fee / assertion / specific | Sorry, I mean 1.5% for this scheme. | '1.5%'→1.5 |
| Gemma ✅ | processing_fee / assertion / specific | Processing fee is 1%. | '1%'→1 |
| Gemma ✅ | processing_fee / assertion / specific | Sorry, I mean 1.5% for this scheme. | '1.5%'→1.5 |

## EN08 (en) — bundled insurance deducted from the loan

> S1 [spk_A]: Insurance is compulsory, the premium of 8,400 will be deducted from the loan amount.

| | type / kind / vagueness | quote | values |
|---|---|---|---|
| expected ✅ | insurance_bundled / assertion / specific | Insurance is compulsory, the premium of 8,400 will be deducted from the loan amount. | '8,400'→8400 |
| Gemma ✅ | insurance_bundled / assertion / specific | Insurance is compulsory, the premium of 8,400 will be deducted from the loan amount. | '8,400'→8400 |

## EN09 (en) — zero prepayment charge; floating rate stated without a number gives no rate claim

> S1 [spk_A]: Since it is a floating rate loan, there is no prepayment charge at all.

| | type / kind / vagueness | quote | values |
|---|---|---|---|
| expected ❌ missed | prepayment_charge / assertion / specific | there is no prepayment charge at all. | 'no prepayment charge'→0 |
| Gemma ⚠️ extra/different | prepayment_charge / conditional / specific | there is no prepayment charge at all. | 'no prepayment charge'→0 |

## EN10 (en) — approximate range stays a range, marked approximate

> S1 [spk_A]: The interest will be around 12 to 13 percent.

| | type / kind / vagueness | quote | values |
|---|---|---|---|
| expected ✅ | interest_rate / promise / approximate | The interest will be around 12 to 13 percent. | '12 to 13 percent'→12–13 |
| Gemma ✅ | interest_rate / promise / approximate | The interest will be around 12 to 13 percent. | '12 to 13 percent'→12–13 |

## EN11 (en) — opinion vs document assurance

> S1 [spk_A]: Honestly, I think this is the best loan for you.
> S2 [spk_A]: Everything I said is written in the agreement.

| | type / kind / vagueness | quote | values |
|---|---|---|---|
| expected ✅ | general_assurance / opinion / vague | I think this is the best loan for you. | — |
| expected ✅ | document_assurance / assertion / specific | Everything I said is written in the agreement. | — |
| Gemma ✅ | general_assurance / opinion / vague | Honestly, I think this is the best loan for you. | — |
| Gemma ✅ | document_assurance / assertion / specific | Everything I said is written in the agreement. | — |

## EN12 (en) — lender identity and net disbursement

> S1 [spk_A]: I'm calling from Navkiran Credit, an RBI-registered NBFC.
> S2 [spk_A]: You will receive 4,95,001 in your account.

| | type / kind / vagueness | quote | values |
|---|---|---|---|
| expected ✅ | lender_identity / assertion / specific | I'm calling from Navkiran Credit, an RBI-registered NBFC. | — |
| expected ✅ | net_disbursement / promise / specific | You will receive 4,95,001 in your account. | '4,95,001'→495001 |
| Gemma ✅ | lender_identity / assertion / specific | I'm calling from Navkiran Credit, an RBI-registered NBFC. | — |
| Gemma ✅ | net_disbursement / promise / specific | You will receive 4,95,001 in your account. | '4,95,001'→495001 |

## HI01 (hinglish) — Hindi number word with GST note

> S1 [spk_A]: Processing fee sirf ek percent hai, GST alag se.

| | type / kind / vagueness | quote | values |
|---|---|---|---|
| expected ✅ | processing_fee / assertion / specific | Processing fee sirf ek percent hai, GST alag se. | 'ek percent'→1 |
| Gemma ✅ | processing_fee / assertion / specific | Processing fee sirf ek percent hai, GST alag se. | 'ek percent'→1 |

## HI02 (hinglish) — EMI in hazaar, tenure in saal

> S1 [spk_A]: Aapka EMI 36 hazaar hoga, teen saal ke liye.

| | type / kind / vagueness | quote | values |
|---|---|---|---|
| expected ✅ | emi / promise / specific | Aapka EMI 36 hazaar hoga | '36 hazaar'→36000 |
| expected ✅ | tenure / promise / specific | teen saal ke liye. | 'teen saal'→36 |
| Gemma ✅ | emi / promise / specific | Aapka EMI 36 hazaar hoga | '36 hazaar'→36000 |
| Gemma ✅ | tenure / promise / specific | teen saal ke liye. | 'teen saal'→36 |

## HI03 (hinglish) — customer echo 'zero fee na?' is a question, not a promise

> S1 [spk_B]: Zero fee na?
> S2 [spk_A]: Haan ji, bilkul zero.

| | type / kind / vagueness | quote | values |
|---|---|---|---|
| expected ✅ | processing_fee / question / specific | Zero fee na? | 'Zero'→0 |
| expected ✅ | processing_fee / assertion / specific | Haan ji, bilkul zero. | 'zero'→0 |
| Gemma ✅ | processing_fee / question / specific | Zero fee na? | 'Zero'→0 |
| Gemma ✅ | processing_fee / assertion / specific | Haan ji, bilkul zero. | 'zero'→0 |

## HI04 (hinglish) — hypothetical with 'agar ... hota toh'; credit score is not a loan term

> S1 [spk_A]: Agar aapka CIBIL 800 hota toh rate 10 percent milta.

| | type / kind / vagueness | quote | values |
|---|---|---|---|
| expected ✅ | interest_rate / hypothetical / specific | Agar aapka CIBIL 800 hota toh rate 10 percent milta. | '10 percent'→10 |
| Gemma ✅ | interest_rate / hypothetical / specific | Agar aapka CIBIL 800 hota toh rate 10 percent milta. | '10 percent'→10 |

## HI05 (hinglish) — conditional with 'toh'; zero via 'koi nahi'

> S1 [spk_A]: Time pe EMI bharoge toh penal charge koi nahi.

| | type / kind / vagueness | quote | values |
|---|---|---|---|
| expected ✅ | penal_charge / conditional / specific | Time pe EMI bharoge toh penal charge koi nahi. | 'koi nahi'→0 |
| Gemma ✅ | penal_charge / conditional / specific | Time pe EMI bharoge toh penal charge koi nahi. | 'koi nahi'→0 |

## HI06 (hinglish) — vague Hinglish claims keep no values

> S1 [spk_A]: Hamare rates market mein sabse kam hain, koi hidden charge nahi.

| | type / kind / vagueness | quote | values |
|---|---|---|---|
| expected ✅ | interest_rate / assertion / vague | Hamare rates market mein sabse kam hain | — |
| expected ✅ | general_assurance / assertion / vague | koi hidden charge nahi. | — |
| Gemma ✅ | interest_rate / assertion / vague | Hamare rates market mein sabse kam hain | — |
| Gemma ✅ | general_assurance / assertion / vague | koi hidden charge nahi. | — |

## HI07 (hinglish) — self-correction 'nahi nahi': both values are claims

> S1 [spk_A]: Fee dedh percent hai, nahi nahi, do percent.

| | type / kind / vagueness | quote | values |
|---|---|---|---|
| expected ✅ | processing_fee / assertion / specific | Fee dedh percent hai | 'dedh percent'→1.5 |
| expected ✅ | processing_fee / assertion / specific | nahi nahi, do percent. | 'do percent'→2 |
| Gemma ✅ | processing_fee / assertion / specific | Fee dedh percent hai | 'dedh percent'→1.5 |
| Gemma ✅ | processing_fee / assertion / specific | nahi nahi, do percent. | 'do percent'→2 |

## HI08 (hinglish) — insurance deducted from the loan ('kat jayega')

> S1 [spk_A]: Insurance lena padega, 18,500 loan se hi kat jayega.

| | type / kind / vagueness | quote | values |
|---|---|---|---|
| expected ✅ | insurance_bundled / assertion / specific | Insurance lena padega, 18,500 loan se hi kat jayega. | '18,500'→18500 |
| Gemma ✅ | insurance_bundled / assertion / specific | Insurance lena padega, 18,500 loan se hi kat jayega. | '18,500'→18500 |

## HI09 (hinglish) — zero prepayment charge

> S1 [spk_A]: Prepayment charge koi nahi hai, kabhi bhi band kar sakte ho.

| | type / kind / vagueness | quote | values |
|---|---|---|---|
| expected ✅ | prepayment_charge / assertion / specific | Prepayment charge koi nahi hai, kabhi bhi band kar sakte ho. | 'koi nahi'→0 |
| Gemma ✅ | prepayment_charge / assertion / specific | Prepayment charge koi nahi hai | 'koi nahi'→0 |

## HI10 (hinglish) — approximate range with 'lagbhag ... se ... ke beech'

> S1 [spk_A]: Interest lagbhag 12 se 13 percent ke beech rahega.

| | type / kind / vagueness | quote | values |
|---|---|---|---|
| expected ✅ | interest_rate / promise / approximate | Interest lagbhag 12 se 13 percent ke beech rahega. | '12 se 13 percent'→12–13 |
| Gemma ✅ | interest_rate / promise / approximate | Interest lagbhag 12 se 13 percent ke beech rahega. | '12 se 13 percent'→12–13 |

## HI11 (hinglish) — opinion vs document assurance in Hinglish

> S1 [spk_A]: Mere hisaab se ye best loan hai.
> S2 [spk_A]: Jo bhi bola hai sab agreement mein likha hai.

| | type / kind / vagueness | quote | values |
|---|---|---|---|
| expected ✅ | general_assurance / opinion / vague | Mere hisaab se ye best loan hai. | — |
| expected ✅ | document_assurance / assertion / specific | Jo bhi bola hai sab agreement mein likha hai. | — |
| Gemma ✅ | general_assurance / opinion / vague | Mere hisaab se ye best loan hai. | — |
| Gemma ✅ | document_assurance / assertion / specific | Jo bhi bola hai sab agreement mein likha hai. | — |

## HI12 (hinglish) — Devanagari mixed with Latin; loan amount and tenure

> S1 [spk_A]: आपका loan दस लाख का approve हो गया है, tenure 5 saal.

| | type / kind / vagueness | quote | values |
|---|---|---|---|
| expected ✅ | loan_amount / assertion / specific | आपका loan दस लाख का approve हो गया है | 'दस लाख'→1000000 |
| expected ✅ | tenure / assertion / specific | tenure 5 saal. | '5 saal'→60 |
| Gemma ✅ | loan_amount / assertion / specific | आपका loan दस लाख का approve हो गया है | 'दस लाख'→1000000 |
| Gemma ✅ | tenure / assertion / specific | tenure 5 saal. | '5 saal'→60 |
