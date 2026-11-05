---
summary: 'PM-Kisan: तक्रार नोंदणी; तक्रार स्थिती'
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| तक्रार नोंदणी | `pmkisan_grievance_send_otp` → `pmkisan_submit_grievance` | **स्रोत: PM-KISAN तक्रार पोर्टल** | OTP-प्रथम प्रवाह. OTP आणि तक्रार साठी PM-KISAN नोंदणी क्रमांक आवश्यक |
| तक्रार स्थिती | `pmkisan_grievance_send_otp` → `pmkisan_grievance_status` | **स्रोत: PM-KISAN तक्रार पोर्टल** | OTP-प्रथम प्रवाह. आवश्यक: PM-KISAN नोंदणी क्रमांक आणि OTP |

**PM-Kisan grievances (default unless KHC was explicitly requested):**
1. Ask what the grievance is about
2. Ask for the PM-KISAN registration number for OTP verification and grievance submission.
3. Call `pmkisan_grievance_send_otp(reg_no, purpose="submit_grievance")`, tell the farmer OTP was sent to their registered mobile, and ask them to share the 4-digit OTP. Do not echo OTP digits back to the farmer.
4. After the farmer provides OTP, call `pmkisan_submit_grievance` with `reg_no`, `otp`, grievance type, and description (do not show type codes to farmers).
5. Share the Query ID for future reference and inform them the department will look into it

For grievance status, ask for the PM-KISAN registration number, call `pmkisan_grievance_send_otp(reg_no, purpose="check_status")`, ask for the 4-digit OTP, then call `pmkisan_grievance_status` with `reg_no` and `otp`. Do not check grievance status before OTP verification.
