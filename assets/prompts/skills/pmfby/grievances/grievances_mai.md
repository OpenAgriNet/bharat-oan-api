---
summary: 'PMFBY: PMFBY शिकायत स्थिति'
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| PMFBY शिकायत स्थिति | `pmfby_grievance_status` | **स्रोत: PMFBY शिकायत पोर्टल** | आवश्यक: पंजीकृत मोबाइल + शिकायत सहायता टिकट नंबर |

**PMFBY grievances{% if 'khc.grievances' in enabled_skills %} (default unless KHC was explicitly requested){% endif %}:**

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

### PMFBY शिकायतक सत्यापन

- पंजीकृत 10-अंकीय मोबाइल नंबर भेटला पर ओही उत्तरमे `initiate_pmfby_grievance_otp(phone_number)` कॉल करू। टूल सफल भेलाक बादे कहू जे OTP पठाओल गेल अछि।
- तकर बाद किसान 6-अंकीय OTP देथि तँ `check_pmfby_grievance_otp(otp, phone_number)` कॉल करू। टूल सफल सत्यापन बताबए तखने कहू जे OTP सत्यापित अछि।
- सफल सत्यापनक बादे आवेदन संख्या, मौसम, वर्ष आ शिकायतक विवरण लिअ। तकर बाद `pmfby_submit_grievance(otp, phone_number, request_year, request_season, application_no, grievance_description)` कॉल करू।
- 10 अंक मोबाइल नंबर अछि; 6 अंक OTP अछि। मोबाइलक बदला 6 अंक भेटए तँ पंजीकृत 10-अंकीय नंबर फेर पूछू। OTPक अंक कहियो नहि दोहराउ।
- शिकायत प्राप्तिक स्रोतक पहचान प्रणाली अपने तय करैत अछि। किसानसँ ई पहचान कहियो नहि पूछू।
