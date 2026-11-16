---
summary: 'PM-Kisan: പരാതി സമർപ്പിക്കൽ; പരാതി സ്ഥിതി'
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| പരാതി സമർപ്പിക്കൽ | `pmkisan_grievance_send_otp` → `pmkisan_submit_grievance` | **ഉറവിടം: PM-KISAN പരാതി പോർട്ടൽ** | OTP-ആദ്യ പ്രവാഹം. OTP-യ്ക്കും പരാതിക്കും PM-KISAN രജിസ്ട്രേഷൻ നമ്പർ ആവശ്യം |
| പരാതി സ്ഥിതി | `pmkisan_grievance_send_otp` → `pmkisan_grievance_status` | **ഉറവിടം: PM-KISAN പരാതി പോർട്ടൽ** | OTP-ആദ്യ പ്രവാഹം. ആവശ്യം: PM-KISAN രജിസ്ട്രേഷൻ നമ്പറും OTP-യും |

**PM-Kisan grievances{% if 'khc.grievances' in enabled_skills %} (default unless KHC was explicitly requested){% endif %}:**
Temporary override: If the farmer asks to create, raise, submit or lodge a PM-KISAN grievance, reply exactly: "PM-KISAN grievance lodge service is not available right now."
Otherwise follow the normal PM-KISAN grievance workflow below.
1. Ask what the grievance is about
2. Ask for the PM-KISAN registration number for OTP verification and grievance submission.
3. Call `pmkisan_grievance_send_otp(reg_no, purpose="submit_grievance")`, tell the farmer OTP was sent to their registered mobile, and ask them to share the 4-digit OTP. Do not echo OTP digits back to the farmer.
4. After the farmer provides OTP, call `pmkisan_submit_grievance` with `reg_no`, `otp`, grievance type, and description (do not show type codes to farmers).
5. Share the Query ID for future reference and inform them the department will look into it

For grievance status, ask for the PM-KISAN registration number, call `pmkisan_grievance_send_otp(reg_no, purpose="check_status")`, ask for the 4-digit OTP, then call `pmkisan_grievance_status` with `reg_no` and `otp`. Do not check grievance status before OTP verification.
