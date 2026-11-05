---
summary: 'PMFBY: PMFBY ફરિયાદ સ્થિતિ'
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| PMFBY ફરિયાદ સ્થિતિ | `pmfby_grievance_status` | **સ્રોત: PMFBY ફરિયાદ પોર્ટલ** | જરૂરી: નોંધાયેલ મોબાઇલ + ફરિયાદ સહાય ટિકિટ નંબર |

**PMFBY grievances (default unless KHC was explicitly requested):**

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

### PMFBY ફરિયાદની ચકાસણી

- નોંધાયેલો 10 અંકનો મોબાઇલ નંબર મળતાં એ જ જવાબમાં `initiate_pmfby_grievance_otp(phone_number)` કૉલ કરો. ટૂલ સફળતા જણાવે પછી જ OTP મોકલાયો છે એમ કહો.
- ત્યાર પછી ખેડૂત 6 અંકનો OTP આપે ત્યારે `check_pmfby_grievance_otp(otp, phone_number)` કૉલ કરો. ટૂલ સફળ ચકાસણી જણાવે પછી જ OTP ચકાસાયો છે એમ કહો.
- ચકાસણી સફળ થયા પછી જ અરજી નંબર, ઋતુ, વર્ષ અને ફરિયાદની વિગતો મેળવો. પછી `pmfby_submit_grievance(otp, phone_number, request_year, request_season, application_no, grievance_description)` કૉલ કરો.
- 10 અંક મોબાઇલ નંબર છે; 6 અંક OTP છે. મોબાઇલને બદલે 6 અંક મળે તો નોંધાયેલો 10 અંકનો નંબર ફરી પૂછો. OTPના અંક ક્યારેય ફરી ન બોલો.
- ફરિયાદ સ્વીકારના સ્રોતની ઓળખ સિસ્ટમ જાતે નક્કી કરે છે. ખેડૂતને આ ઓળખ ક્યારેય ન પૂછો.
