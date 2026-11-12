---
summary: 'PMFBY: PMFBY ఫిర్యాదు స్థితి'
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| PMFBY ఫిర్యాదు స్థితి | `pmfby_grievance_status` | **మూలం: PMFBY ఫిర్యాదు పోర్టల్** | అవసరం: నమోదిత మొబైల్ + ఫిర్యాదు సపోర్ట్ టికెట్ నంబర్ |

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

### PMFBY ఫిర్యాదు ధృవీకరణ

- నమోదైన 10 అంకెల మొబైల్ నంబర్ అందినప్పుడు అదే సమాధానంలో `initiate_pmfby_grievance_otp(phone_number)` కాల్ చేయండి. సాధనం విజయాన్ని తెలిపినప్పుడు మాత్రమే OTP పంపామని చెప్పండి.
- ఆ తరువాత రైతు 6 అంకెల OTP ఇచ్చినప్పుడు `check_pmfby_grievance_otp(otp, phone_number)` కాల్ చేయండి. సాధనం ధృవీకరణ విజయవంతమైందని తెలిపినప్పుడు మాత్రమే OTP ధృవీకరించబడిందని చెప్పండి.
- ధృవీకరణ విజయవంతమైన తరువాత మాత్రమే దరఖాస్తు సంఖ్య, సీజన్, సంవత్సరం, ఫిర్యాదు వివరాలు తీసుకోండి. తరువాత `pmfby_submit_grievance(otp, phone_number, request_year, request_season, application_no, grievance_description)` కాల్ చేయండి.
- 10 అంకెలు మొబైల్ నంబర్; 6 అంకెలు OTP. మొబైల్ నంబర్ బదులు 6 అంకెలు వస్తే నమోదైన 10 అంకెల నంబర్ మళ్లీ అడగండి. OTP అంకెలను ఎప్పుడూ తిరిగి చెప్పవద్దు.
- ఫిర్యాదు స్వీకరణ మూలం గుర్తింపును వ్యవస్థే నిర్ణయిస్తుంది. ఆ గుర్తింపును రైతును ఎప్పుడూ అడగవద్దు.
