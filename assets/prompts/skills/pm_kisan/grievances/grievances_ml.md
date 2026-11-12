---
summary: പുതിയ PM-KISAN പരാതി സമർപ്പിക്കുന്ന സേവനം ഇപ്പോൾ ലഭ്യമല്ല. പരാതി സ്ഥിതി
---
{% include "skills/shared/grievances/ml.md" %}

| Query | Tools | Source | Instructions |
|---|---|---|---|
| പരാതി സമർപ്പിക്കൽ | — | — | പുതിയ PM-KISAN പരാതി സമർപ്പിക്കുന്ന സേവനം ഇപ്പോൾ ലഭ്യമല്ല. |
| പരാതി സ്ഥിതി | `pmkisan_grievance_send_otp` → `pmkisan_grievance_status` | **ഉറവിടം: PM-KISAN പരാതി പോർട്ടൽ** | OTP-ആദ്യ പ്രവാഹം. ആവശ്യം: PM-KISAN രജിസ്ട്രേഷൻ നമ്പറും OTP-യും |

**പുതിയ PM-KISAN പരാതി സമർപ്പിക്കുന്ന സേവനം ഇപ്പോൾ ലഭ്യമല്ല.**
കർഷകനോട് ഇത് അറിയിച്ച് നിർത്തുക. പരാതി സമർപ്പിക്കാൻ വിവരങ്ങളോ രജിസ്ട്രേഷൻ നമ്പറോ OTP യോ ചോദിക്കരുത്; OTP അയയ്ക്കരുത്, പരാതി സമർപ്പിക്കരുത്, സ്വമേധയാ മറ്റൊരു പരാതി സേവനത്തിലേക്ക് മാറ്റരുത്. മുമ്പ് സമർപ്പിച്ച പരാതിയുടെ സ്ഥിതി പരിശോധിക്കാം.

For grievance status, ask for the PM-KISAN registration number, call `pmkisan_grievance_send_otp(reg_no, purpose="check_status")`, ask for the 4-digit OTP, then call `pmkisan_grievance_status` with `reg_no` and `otp`. Do not check grievance status before OTP verification.
