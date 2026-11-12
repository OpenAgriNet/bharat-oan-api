---
summary: 'PMFBY: PMFBY तक्रार स्थिती'
---
{% include "skills/shared/grievances/mr.md" %}

| Query | Tools | Source | Instructions |
|---|---|---|---|
| PMFBY तक्रार स्थिती | `pmfby_grievance_status` | **स्रोत: PMFBY तक्रार पोर्टल** | आवश्यक: नोंदणीकृत मोबाइल + तक्रार सहाय्य टिकिट क्रमांक |

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

### PMFBY तक्रारीची पडताळणी

- नोंदणीकृत 10 अंकी मोबाइल क्रमांक मिळाल्यावर त्याच उत्तरात `initiate_pmfby_grievance_otp(phone_number)` कॉल करा. साधनाने यश सांगितल्यावरच OTP पाठवला आहे असे सांगा.
- त्यानंतर शेतकऱ्याने 6 अंकी OTP दिल्यावर `check_pmfby_grievance_otp(otp, phone_number)` कॉल करा. साधनाने पडताळणी यशस्वी झाल्याचे सांगितल्यावरच OTP पडताळला आहे असे सांगा.
- पडताळणी यशस्वी झाल्यावरच अर्ज क्रमांक, हंगाम, वर्ष आणि तक्रारीचा तपशील घ्या. नंतर `pmfby_submit_grievance(otp, phone_number, request_year, request_season, application_no, grievance_description)` कॉल करा.
- 10 अंक म्हणजे मोबाइल क्रमांक; 6 अंक म्हणजे OTP. मोबाइलऐवजी 6 अंक मिळाल्यास नोंदणीकृत 10 अंकी क्रमांक पुन्हा विचारा. OTPचे अंक कधीही पुन्हा सांगू नका.
- तक्रार प्राप्तीच्या स्रोताची ओळख प्रणाली स्वतः ठरवते. शेतकऱ्याला ती ओळख कधीही विचारू नका.
