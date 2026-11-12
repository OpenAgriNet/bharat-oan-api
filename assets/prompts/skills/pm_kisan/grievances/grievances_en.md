---
summary: New PM-KISAN grievance submission is temporarily unavailable. Grievance status
---
{% include "skills/shared/grievances/en.md" %}

| Query | Tools | Source | Instructions |
|---|---|---|---|
| Grievance submit | — | — | New PM-KISAN grievance submission is temporarily unavailable. |
| Grievance status | `pmkisan_grievance_send_otp` → `pmkisan_grievance_status` | **Source: PM-KISAN Grievance Portal** | OTP-first flow. Needs: PM-KISAN registration number and OTP |

**New PM-KISAN grievance submission is temporarily unavailable.**
Tell the farmer this in their selected language and stop. Do not collect complaint details, a registration number or an OTP for submission; do not send a submission OTP, submit a complaint or redirect to another grievance service automatically. Existing grievance-status checks remain available.

For grievance status, ask for the PM-KISAN registration number, call `pmkisan_grievance_send_otp(reg_no, purpose="check_status")`, ask for the 4-digit OTP, then call `pmkisan_grievance_status` with `reg_no` and `otp`. Do not check grievance status before OTP verification.
