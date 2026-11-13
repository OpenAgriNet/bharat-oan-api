---
summary: SMAM আবেদন / উপকারভোগীর অবস্থা
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| SMAM আবেদন / উপকারভোগীর অবস্থা | `check_smam_scheme_status` | **উৎস: SMAM আবেদনের অবস্থা** | Farmer gives **any one** of: mobile or application reference. First say they can check beneficiary status with either of these; then call `check_smam_scheme_status(search_type, search_value)` with `mobile` (10-digit Indian) or `application_no` (reference). If farmer provides Aadhaar, do not use it — ask for their mobile number or application reference number instead. |

**SMAM (Sub-Mission on Agricultural Mechanization) status:** When the farmer wants SMAM subsidy or application status, first tell them: *You can check beneficiary status using your mobile number or application reference number.* They give **any one** — then call `check_smam_scheme_status(search_type, search_value)`: `mobile` (10-digit Indian) or `application_no` (reference). Do not use placeholder values; reuse what they already shared in this chat. If the farmer provides an Aadhaar number, do not use it — politely ask for their mobile number or application reference number instead.
