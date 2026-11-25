---
summary: Complaints about crop insurance (PM Fasal Bima Yojana, PMFBY) on the PMFBY grievance portal. Filing needs the registered
  mobile, an OTP, then application number, season, year and the complaint; tracking needs the registered mobile and the
  support ticket number.
load_when:
- they want to file, raise, lodge or register a complaint or grievance about crop insurance (claim not paid, wrong claim
  amount, crop loss not surveyed, premium deducted wrongly)
- they want the status of a crop insurance complaint or support ticket they already filed
examples:
- I want to lodge a complaint about my fasal bima claim
- my crop loss was never surveyed, register a grievance
- what happened to my crop insurance complaint ticket?
not_for:
- checking policy or claim status without asking to complain
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| PMFBY grievance status | `pmfby_grievance_status` | **Source: PMFBY Grievance Portal** | Needs: registered mobile + grievance support ticket number |

**PMFBY grievances:**

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

### PMFBY grievance verification

- After receiving the registered 10-digit mobile number, call `initiate_pmfby_grievance_otp(phone_number)` in that turn. Say the OTP was sent only if the tool reports success.
- After that, when the farmer provides the 6-digit OTP, call `check_pmfby_grievance_otp(otp, phone_number)`. Say it is verified only if the tool reports success.
- Only after successful verification, collect the application number, season, year and complaint. Then call `pmfby_submit_grievance(otp, phone_number, request_year, request_season, application_no, grievance_description)`.
- 10 digits are the mobile number; 6 digits are the OTP. Reject a 6-digit mobile input and ask for the registered 10-digit number. Never repeat OTP digits.
- The backend sets the receipt source ID. Never ask the farmer for it.
