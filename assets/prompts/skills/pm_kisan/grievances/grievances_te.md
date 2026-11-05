---
summary: 'PM-Kisan: ఫిర్యాదు నమోదు; ఫిర్యాదు స్థితి'
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| ఫిర్యాదు నమోదు | `pmkisan_grievance_send_otp` → `pmkisan_submit_grievance` | **మూలం: PM-KISAN ఫిర్యాదు పోర్టల్** | OTP-మొదటి ప్రవాహం. OTP మరియు ఫిర్యాదు కోసం PM-KISAN రిజిస్ట్రేషన్ నంబర్ అవసరం |
| ఫిర్యాదు స్థితి | `pmkisan_grievance_send_otp` → `pmkisan_grievance_status` | **మూలం: PM-KISAN ఫిర్యాదు పోర్టల్** | OTP-మొదటి ప్రవాహం. అవసరం: PM-KISAN రిజిస్ట్రేషన్ నంబర్ మరియు OTP |

**PM-Kisan grievances (default unless KHC was explicitly requested):**
1. Ask what the grievance is about
2. Ask for the PM-KISAN registration number for OTP verification and grievance submission.
3. Call `pmkisan_grievance_send_otp(reg_no, purpose="submit_grievance")`, tell the farmer OTP was sent to their registered mobile, and ask them to share the 4-digit OTP. Do not echo OTP digits back to the farmer.
4. After the farmer provides OTP, call `pmkisan_submit_grievance` with `reg_no`, `otp`, grievance type, and description (do not show type codes to farmers).
5. Share the Query ID for future reference and inform them the department will look into it

For grievance status, ask for the PM-KISAN registration number, call `pmkisan_grievance_send_otp(reg_no, purpose="check_status")`, ask for the 4-digit OTP, then call `pmkisan_grievance_status` with `reg_no` and `otp`. Do not check grievance status before OTP verification.
