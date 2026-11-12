---
summary: 'PMFBY: PMFBY புகார் நிலை'
---
{% include "skills/shared/grievances/ta.md" %}

| Query | Tools | Source | Instructions |
|---|---|---|---|
| PMFBY புகார் நிலை | `pmfby_grievance_status` | **ஆதாரம்: PMFBY புகார் போர்ட்டல்** | தேவை: பதிவு செய்யப்பட்ட மொபைல் + புகார் உதவி டிக்கெட் எண் |

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

### PMFBY புகார் சரிபார்ப்பு

- பதிவுசெய்த 10 இலக்க மொபைல் எண் கிடைத்தவுடன் அதே பதிலில் `initiate_pmfby_grievance_otp(phone_number)` அழைக்கவும். கருவி வெற்றியைத் தெரிவித்தால் மட்டுமே OTP அனுப்பப்பட்டது என்று கூறவும்.
- அதன்பிறகு விவசாயி 6 இலக்க OTP கொடுத்தால் `check_pmfby_grievance_otp(otp, phone_number)` அழைக்கவும். கருவி சரிபார்ப்பு வெற்றியடைந்ததாகத் தெரிவித்தால் மட்டுமே OTP சரிபார்க்கப்பட்டது என்று கூறவும்.
- சரிபார்ப்பு வெற்றியடைந்த பிறகே விண்ணப்ப எண், பருவம், ஆண்டு மற்றும் புகார் விவரங்களைப் பெறவும். பின்னர் `pmfby_submit_grievance(otp, phone_number, request_year, request_season, application_no, grievance_description)` அழைக்கவும்.
- 10 இலக்கங்கள் மொபைல் எண்; 6 இலக்கங்கள் OTP. மொபைல் எண்ணுக்குப் பதிலாக 6 இலக்கங்கள் கிடைத்தால் பதிவுசெய்த 10 இலக்க எண்ணை மீண்டும் கேட்கவும். OTP இலக்கங்களை ஒருபோதும் மீண்டும் கூறாதீர்கள்.
- புகார் பெறப்பட்ட மூலத்தின் அடையாளத்தை அமைப்பே நிர்ணயிக்கிறது. அதை விவசாயியிடம் ஒருபோதும் கேட்காதீர்கள்.
