---
summary: 'PMFBY: PMFBY ਸ਼ਿਕਾਇਤ ਸਥਿਤੀ'
---
{% include "skills/shared/grievances/pa.md" %}

| Query | Tools | Source | Instructions |
|---|---|---|---|
| PMFBY ਸ਼ਿਕਾਇਤ ਸਥਿਤੀ | `pmfby_grievance_status` | **ਸਰੋਤ: PMFBY ਸ਼ਿਕਾਇਤ ਪੋਰਟਲ** | ਲੋੜੀਂਦਾ ਹੈ: ਰਜਿਸਟਰਡ ਮੋਬਾਈਲ + ਸ਼ਿਕਾਇਤ ਸਹਾਇਤਾ ਟਿਕਟ ਨੰਬਰ |

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

### PMFBY ਸ਼ਿਕਾਇਤ ਦੀ ਤਸਦੀਕ

- ਰਜਿਸਟਰ ਕੀਤਾ 10 ਅੰਕਾਂ ਦਾ ਮੋਬਾਈਲ ਨੰਬਰ ਮਿਲਣ ਉੱਤੇ ਉਸੇ ਜਵਾਬ ਵਿੱਚ `initiate_pmfby_grievance_otp(phone_number)` ਕਾਲ ਕਰੋ। ਟੂਲ ਸਫਲਤਾ ਦੱਸੇ ਤਾਂ ਹੀ ਕਹੋ ਕਿ OTP ਭੇਜਿਆ ਗਿਆ ਹੈ।
- ਇਸ ਤੋਂ ਬਾਅਦ ਕਿਸਾਨ 6 ਅੰਕਾਂ ਦਾ OTP ਦੇਵੇ ਤਾਂ `check_pmfby_grievance_otp(otp, phone_number)` ਕਾਲ ਕਰੋ। ਟੂਲ ਤਸਦੀਕ ਸਫਲ ਹੋਣ ਦੀ ਪੁਸ਼ਟੀ ਕਰੇ ਤਾਂ ਹੀ ਕਹੋ ਕਿ OTP ਦੀ ਤਸਦੀਕ ਹੋ ਗਈ ਹੈ।
- ਤਸਦੀਕ ਸਫਲ ਹੋਣ ਤੋਂ ਬਾਅਦ ਹੀ ਅਰਜ਼ੀ ਨੰਬਰ, ਮੌਸਮ, ਸਾਲ ਅਤੇ ਸ਼ਿਕਾਇਤ ਦਾ ਵੇਰਵਾ ਲਵੋ। ਫਿਰ `pmfby_submit_grievance(otp, phone_number, request_year, request_season, application_no, grievance_description)` ਕਾਲ ਕਰੋ।
- 10 ਅੰਕ ਮੋਬਾਈਲ ਨੰਬਰ ਹਨ; 6 ਅੰਕ OTP ਹਨ। ਮੋਬਾਈਲ ਦੀ ਥਾਂ 6 ਅੰਕ ਮਿਲਣ ਉੱਤੇ ਰਜਿਸਟਰ ਕੀਤਾ 10 ਅੰਕਾਂ ਦਾ ਨੰਬਰ ਮੁੜ ਪੁੱਛੋ। OTP ਦੇ ਅੰਕ ਕਦੇ ਨਾ ਦੁਹਰਾਓ।
- ਸ਼ਿਕਾਇਤ ਪ੍ਰਾਪਤੀ ਦੇ ਸਰੋਤ ਦੀ ਪਛਾਣ ਸਿਸਟਮ ਆਪ ਨਿਰਧਾਰਤ ਕਰਦਾ ਹੈ। ਕਿਸਾਨ ਤੋਂ ਇਹ ਪਛਾਣ ਕਦੇ ਨਾ ਪੁੱਛੋ।
