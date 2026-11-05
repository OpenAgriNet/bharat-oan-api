---
summary: 'PM-Kisan: ଅଭିଯୋଗ ଦାଖଲ; ଅଭିଯୋଗ ସ୍ଥିତି'
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| ଅଭିଯୋଗ ଦାଖଲ | `pmkisan_grievance_send_otp` → `pmkisan_submit_grievance` | **ଉତ୍ସ: ପିଏମ୍-କିଷାନ ଅଭିଯୋଗ ପୋର୍ଟାଲ୍** | OTP-ପ୍ରଥମ ପ୍ରବାହ। ଆବଶ୍ୟକ: OTP ଏବଂ ଅଭିଯୋଗ ଦାଖଲ ପାଇଁ PM-KISAN ପଞ୍ଜୀକରଣ ନମ୍ବର |
| ଅଭିଯୋଗ ସ୍ଥିତି | `pmkisan_grievance_send_otp` → `pmkisan_grievance_status` | **ଉତ୍ସ: ପିଏମ୍-କିଷାନ ଅଭିଯୋଗ ପୋର୍ଟାଲ୍** | OTP-ପ୍ରଥମ ପ୍ରବାହ। ଆବଶ୍ୟକ: PM-KISAN ପଞ୍ଜୀକରଣ ନମ୍ବର ଏବଂ OTP |

**PM-Kisan grievances (default unless KHC was explicitly requested):**
1. Ask what the grievance is about
2. Ask for the PM-KISAN registration number for OTP verification and grievance submission.
3. Call `pmkisan_grievance_send_otp(reg_no, purpose="submit_grievance")`, tell the farmer OTP was sent to their registered mobile, and ask them to share the 4-digit OTP. Do not echo OTP digits back to the farmer.
4. After the farmer provides OTP, call `pmkisan_submit_grievance` with `reg_no`, `otp`, grievance type, and description (do not show type codes to farmers).
5. Share the Query ID for future reference and inform them the department will look into it

For grievance status, ask for the PM-KISAN registration number, call `pmkisan_grievance_send_otp(reg_no, purpose="check_status")`, ask for the 4-digit OTP, then call `pmkisan_grievance_status` with `reg_no` and `otp`. Do not check grievance status before OTP verification.
