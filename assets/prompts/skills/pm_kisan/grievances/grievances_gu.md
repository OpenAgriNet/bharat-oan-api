---
summary: 'PM-Kisan: ફરિયાદ નોંધણી; ફરિયાદ સ્થિતિ'
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| ફરિયાદ નોંધણી | `pmkisan_grievance_send_otp` → `pmkisan_submit_grievance` | **સ્રોત: PM-KISAN ફરિયાદ પોર્ટલ** | OTP-પ્રથમ પ્રવાહ. OTP અને ફરિયાદ માટે PM-KISAN નોંધણી નંબર જરૂરી |
| ફરિયાદ સ્થિતિ | `pmkisan_grievance_send_otp` → `pmkisan_grievance_status` | **સ્રોત: PM-KISAN ફરિયાદ પોર્ટલ** | OTP-પ્રથમ પ્રવાહ. જરૂરી: PM-KISAN નોંધણી નંબર અને OTP |

**PM-Kisan grievances{% if 'khc.grievances' in enabled_skills %} (default unless KHC was explicitly requested){% endif %}:**
Temporary override: If the farmer asks to create, raise, submit or lodge a PM-KISAN grievance, reply exactly: "PM-KISAN grievance lodge service is not available right now."
Otherwise follow the normal PM-KISAN grievance workflow below.
1. Ask what the grievance is about
2. Ask for the PM-KISAN registration number for OTP verification and grievance submission.
3. Call `pmkisan_grievance_send_otp(reg_no, purpose="submit_grievance")`, tell the farmer OTP was sent to their registered mobile, and ask them to share the 4-digit OTP. Do not echo OTP digits back to the farmer.
4. After the farmer provides OTP, call `pmkisan_submit_grievance` with `reg_no`, `otp`, grievance type, and description (do not show type codes to farmers).
5. Share the Query ID for future reference and inform them the department will look into it

For grievance status, ask for the PM-KISAN registration number, call `pmkisan_grievance_send_otp(reg_no, purpose="check_status")`, ask for the 4-digit OTP, then call `pmkisan_grievance_status` with `reg_no` and `otp`. Do not check grievance status before OTP verification.
