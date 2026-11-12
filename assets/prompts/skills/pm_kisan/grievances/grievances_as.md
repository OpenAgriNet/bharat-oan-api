---
summary: নতুন PM-KISAN অভিযোগ দাখিল কৰাৰ সেৱা বৰ্তমান উপলব্ধ নহয়। অভিযোগৰ স্থিতি
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| অভিযোগ দাখিল | — | — | নতুন PM-KISAN অভিযোগ দাখিল কৰাৰ সেৱা বৰ্তমান উপলব্ধ নহয়। |
| অভিযোগৰ স্থিতি | `pmkisan_grievance_send_otp` → `pmkisan_grievance_status` | **উৎস: PM-KISAN অভিযোগ পৰ্টেল** | OTP-প্ৰথম প্ৰবাহ। প্ৰয়োজনীয়: PM-KISAN পঞ্জীয়ন নম্বৰ আৰু OTP |

**নতুন PM-KISAN অভিযোগ দাখিল কৰাৰ সেৱা বৰ্তমান উপলব্ধ নহয়।**
কৃষকক এই কথা জনাই ৰৈ যাওক। অভিযোগ দাখিলৰ বাবে বিৱৰণ, পঞ্জীয়ন নম্বৰ বা OTP নিবিচাৰিব; OTP নপঠিয়াব, অভিযোগ দাখিল নকৰিব আৰু নিজে আন অভিযোগ সেৱালৈ নপঠিয়াব। আগতে দাখিল কৰা অভিযোগৰ স্থিতি পৰীক্ষা কৰিব পাৰি।

For grievance status, ask for the PM-KISAN registration number, call `pmkisan_grievance_send_otp(reg_no, purpose="check_status")`, ask for the 4-digit OTP, then call `pmkisan_grievance_status` with `reg_no` and `otp`. Do not check grievance status before OTP verification.
