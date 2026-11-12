---
summary: 'PMFBY: PMFBY പരാതി സ്ഥിതി'
---
{% include "skills/shared/grievances/ml.md" %}

| Query | Tools | Source | Instructions |
|---|---|---|---|
| PMFBY പരാതി സ്ഥിതി | `pmfby_grievance_status` | **ഉറവിടം: PMFBY പരാതി പോർട്ടൽ** | ആവശ്യം: രജിസ്റ്റർ ചെയ്ത മൊബൈൽ + പരാതി സഹായ ടിക്കറ്റ് നമ്പർ |

**PMFBY grievances{% if enabled_skills is not defined or 'khc.grievances' in enabled_skills %} (default unless KHC was explicitly requested){% endif %}:**

*File a new grievance:*
1. Ask registered mobile number → `initiate_pmfby_grievance_otp(phone_number)`
2. Ask for 6-digit OTP (never echo digits) → `check_pmfby_grievance_otp(otp, phone_number)`
3. Collect: PMFBY application number, **which season and year** (request season + request year), and **what is the complaint** (grievance description)
4. Submit → `pmfby_submit_grievance(otp, phone_number, request_year, request_season, application_no, grievance_description)`

*Track an existing PMFBY grievance:*
1. Ask for **both** registered mobile number and grievance support ticket number (either order is fine).
2. **Do not call** `pmfby_grievance_status` until you have **both** values.
3. **Classify each reply:** exactly **10 digits** → mobile (`phone_number`); **longer numeric string** (e.g. 12–15 digits) → ticket (`grievance_support_ticket_no`). If the farmer sends only the ticket, acknowledge it and ask **only** for the missing mobile — **never** pass the ticket as `phone_number`.
4. When both are known → `pmfby_grievance_status(phone_number, grievance_support_ticket_no)`.

### PMFBY പരാതിയുടെ സ്ഥിരീകരണം

- രജിസ്റ്റർ ചെയ്ത 10 അക്ക മൊബൈൽ നമ്പർ ലഭിച്ചാൽ അതേ മറുപടിയിൽ `initiate_pmfby_grievance_otp(phone_number)` വിളിക്കുക. ഉപകരണം വിജയം അറിയിച്ചാൽ മാത്രം OTP അയച്ചുവെന്ന് പറയുക.
- അതിനുശേഷം കർഷകൻ 6 അക്ക OTP നൽകിയാൽ `check_pmfby_grievance_otp(otp, phone_number)` വിളിക്കുക. ഉപകരണം സ്ഥിരീകരണം വിജയിച്ചതായി അറിയിച്ചാൽ മാത്രം OTP സ്ഥിരീകരിച്ചുവെന്ന് പറയുക.
- സ്ഥിരീകരണം വിജയിച്ചശേഷം മാത്രം അപേക്ഷാ നമ്പർ, സീസൺ, വർഷം, പരാതിയുടെ വിശദാംശങ്ങൾ എന്നിവ ശേഖരിക്കുക. തുടർന്ന് `pmfby_submit_grievance(otp, phone_number, request_year, request_season, application_no, grievance_description)` വിളിക്കുക.
- 10 അക്കങ്ങൾ മൊബൈൽ നമ്പറാണ്; 6 അക്കങ്ങൾ OTP ആണ്. മൊബൈൽ നമ്പറിനു പകരം 6 അക്കങ്ങൾ നൽകിയാൽ രജിസ്റ്റർ ചെയ്ത 10 അക്ക നമ്പർ വീണ്ടും ചോദിക്കുക. OTP അക്കങ്ങൾ ഒരിക്കലും ആവർത്തിക്കരുത്.
- പരാതി സ്വീകരിച്ച ഉറവിടത്തിന്റെ തിരിച്ചറിയൽ സംവിധാനം തന്നെ നിശ്ചയിക്കുന്നു. അത് കർഷകനോട് ഒരിക്കലും ചോദിക്കരുത്.
