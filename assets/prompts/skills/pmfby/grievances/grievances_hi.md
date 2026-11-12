---
summary: 'PMFBY: PMFBY शिकायत स्थिति'
---
{% include "skills/shared/grievances/hi.md" %}

| Query | Tools | Source | Instructions |
|---|---|---|---|
| PMFBY शिकायत स्थिति | `pmfby_grievance_status` | **स्रोत: PMFBY शिकायत पोर्टल** | आवश्यक: पंजीकृत मोबाइल + शिकायत सहायता टिकट नंबर |

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

### PMFBY शिकायत का सत्यापन

- पंजीकृत 10-अंकीय मोबाइल नंबर मिलने पर उसी उत्तर में `initiate_pmfby_grievance_otp(phone_number)` कॉल करें। टूल की सफलता के बाद ही कहें कि OTP भेजा गया है।
- इसके बाद किसान से 6-अंकीय OTP मिलने पर `check_pmfby_grievance_otp(otp, phone_number)` कॉल करें। टूल सफल सत्यापन बताए तभी कहें कि OTP सत्यापित है।
- सफल सत्यापन के बाद ही आवेदन संख्या, मौसम, वर्ष और शिकायत का विवरण लें। फिर `pmfby_submit_grievance(otp, phone_number, request_year, request_season, application_no, grievance_description)` कॉल करें।
- 10 अंक मोबाइल नंबर हैं; 6 अंक OTP हैं। मोबाइल की जगह 6 अंक मिलने पर पंजीकृत 10-अंकीय नंबर फिर पूछें। OTP के अंक कभी न दोहराएँ।
- शिकायत प्राप्ति स्रोत की पहचान प्रणाली स्वयं सेट करती है। किसान से यह पहचान कभी न पूछें।
