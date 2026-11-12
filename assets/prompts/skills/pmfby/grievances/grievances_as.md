---
summary: 'PMFBY: PMFBY অভিযোগৰ স্থিতি'
---
{% include "skills/shared/grievances/as.md" %}

| Query | Tools | Source | Instructions |
|---|---|---|---|
| PMFBY অভিযোগৰ স্থিতি | `pmfby_grievance_status` | **উৎস: PMFBY অভিযোগ পৰ্টেল** | প্ৰয়োজন: পঞ্জীয়ন মোবাইল + অভিযোগ সহায়তা টিকেট নম্বৰ |

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

### PMFBY অভিযোগৰ যাচাই

- পঞ্জীয়ন কৰা 10 অংকৰ মোবাইল নম্বৰ পালে সেই উত্তৰতে `initiate_pmfby_grievance_otp(phone_number)` কল কৰক। টুলে সফলতা দেখুৱালেহে OTP পঠোৱা হৈছে বুলি কওক।
- তাৰ পিছত কৃষকে 6 অংকৰ OTP দিলে `check_pmfby_grievance_otp(otp, phone_number)` কল কৰক। টুলে যাচাই সফল বুলি জনালেহে OTP যাচাই হৈছে বুলি কওক।
- যাচাই সফল হোৱাৰ পিছতহে আবেদন নম্বৰ, ঋতু, বছৰ আৰু অভিযোগৰ বিৱৰণ লওক। তাৰ পিছত `pmfby_submit_grievance(otp, phone_number, request_year, request_season, application_no, grievance_description)` কল কৰক।
- 10 অংক মোবাইল নম্বৰ; 6 অংক OTP। মোবাইলৰ সলনি 6 অংক দিলে পঞ্জীয়ন কৰা 10 অংকৰ নম্বৰ আকৌ সোধক। OTPৰ অংক কেতিয়াও পুনৰ নকব।
- অভিযোগ গ্ৰহণৰ উৎসৰ পৰিচয় ব্যৱস্থাই নিজে নিৰ্ধাৰণ কৰে। কৃষকক এই পৰিচয় কেতিয়াও নুসুধিব।
