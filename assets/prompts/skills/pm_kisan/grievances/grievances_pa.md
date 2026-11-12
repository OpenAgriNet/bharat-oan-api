---
summary: 'PM-Kisan: ਸ਼ਿਕਾਇਤ ਦਰਜ; ਸ਼ਿਕਾਇਤ ਸਥਿਤੀ'
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| ਸ਼ਿਕਾਇਤ ਦਰਜ | `pmkisan_grievance_send_otp` → `pmkisan_submit_grievance` | **ਸਰੋਤ: PM-KISAN ਸ਼ਿਕਾਇਤ ਪੋਰਟਲ** | OTP-ਪਹਿਲਾਂ ਪ੍ਰਵਾਹ। ਲੋੜੀਂਦਾ ਹੈ: OTP ਅਤੇ ਸ਼ਿਕਾਇਤ ਦਰਜ ਕਰਨ ਲਈ PM-KISAN ਰਜਿਸਟ੍ਰੇਸ਼ਨ ਨੰਬਰ |
| ਸ਼ਿਕਾਇਤ ਸਥਿਤੀ | `pmkisan_grievance_send_otp` → `pmkisan_grievance_status` | **ਸਰੋਤ: PM-KISAN ਸ਼ਿਕਾਇਤ ਪੋਰਟਲ** | OTP-ਪਹਿਲਾਂ ਪ੍ਰਵਾਹ। ਲੋੜੀਂਦਾ ਹੈ: PM-KISAN ਰਜਿਸਟ੍ਰੇਸ਼ਨ ਨੰਬਰ ਅਤੇ OTP |

**PM-Kisan grievances (default unless KHC was explicitly requested):**
Temporary override: If the farmer asks to create, raise, submit or lodge a PM-KISAN grievance, reply exactly: "PM-KISAN grievance lodge service is not available right now."
Otherwise follow the normal PM-KISAN grievance workflow below.
1. Ask what the grievance is about
2. Ask for the PM-KISAN registration number for OTP verification and grievance submission.
3. Call `pmkisan_grievance_send_otp(reg_no, purpose="submit_grievance")`, tell the farmer OTP was sent to their registered mobile, and ask them to share the 4-digit OTP. Do not echo OTP digits back to the farmer.
4. After the farmer provides OTP, call `pmkisan_submit_grievance` with `reg_no`, `otp`, grievance type, and description (do not show type codes to farmers).
5. Share the Query ID for future reference and inform them the department will look into it

For grievance status, ask for the PM-KISAN registration number, call `pmkisan_grievance_send_otp(reg_no, purpose="check_status")`, ask for the 4-digit OTP, then call `pmkisan_grievance_status` with `reg_no` and `otp`. Do not check grievance status before OTP verification.
