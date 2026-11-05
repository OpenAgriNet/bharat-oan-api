---
summary: 'PMFBY: PMFBY অভিযোগের অবস্থা'
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| PMFBY অভিযোগের অবস্থা | `pmfby_grievance_status` | **উৎস: PMFBY অভিযোগ পোর্টাল** | প্রয়োজন: নিবন্ধিত মোবাইল + অভিযোগ সহায়তা টিকেট নম্বর |

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

### PMFBY অভিযোগ যাচাই

- নিবন্ধিত 10 অঙ্কের মোবাইল নম্বর পেলে সেই উত্তরেই `initiate_pmfby_grievance_otp(phone_number)` কল করুন। টুল সফল হলে তবেই বলুন OTP পাঠানো হয়েছে।
- এরপর কৃষক 6 অঙ্কের OTP দিলে `check_pmfby_grievance_otp(otp, phone_number)` কল করুন। টুল যাচাই সফল বললে তবেই বলুন OTP যাচাই হয়েছে।
- যাচাই সফল হওয়ার পরেই আবেদন নম্বর, মৌসুম, বছর এবং অভিযোগের বিবরণ নিন। তারপর `pmfby_submit_grievance(otp, phone_number, request_year, request_season, application_no, grievance_description)` কল করুন।
- 10 অঙ্ক মোবাইল নম্বর; 6 অঙ্ক OTP। মোবাইলের বদলে 6 অঙ্ক দিলে নিবন্ধিত 10 অঙ্কের নম্বর আবার চান। OTP-র অঙ্ক কখনও পুনরাবৃত্তি করবেন না।
- অভিযোগ গ্রহণের উৎসের পরিচয় ব্যবস্থা নিজেই নির্ধারণ করে। কৃষকের কাছে এই পরিচয় কখনও চাইবেন না।
