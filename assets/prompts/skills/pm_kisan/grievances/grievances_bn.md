---
summary: নতুন PM-KISAN অভিযোগ জমা দেওয়ার পরিষেবা আপাতত উপলব্ধ নয়। অভিযোগের অবস্থা
---
{% include "skills/shared/grievances/bn.md" %}

| Query | Tools | Source | Instructions |
|---|---|---|---|
| অভিযোগ দায়ের | — | — | নতুন PM-KISAN অভিযোগ জমা দেওয়ার পরিষেবা আপাতত উপলব্ধ নয়। |
| অভিযোগের অবস্থা | `pmkisan_grievance_send_otp` → `pmkisan_grievance_status` | **উৎস: PM-KISAN অভিযোগ পোর্টাল** | OTP-প্রথম প্রবাহ। প্রয়োজন: PM-KISAN নিবন্ধন নম্বর এবং OTP |

**নতুন PM-KISAN অভিযোগ জমা দেওয়ার পরিষেবা আপাতত উপলব্ধ নয়।**
কৃষককে এটি জানিয়ে থামুন। অভিযোগ জমা দেওয়ার জন্য বিবরণ, নিবন্ধন নম্বর বা OTP চাইবেন না; OTP পাঠাবেন না, অভিযোগ জমা দেবেন না এবং নিজে থেকে অন্য অভিযোগ পরিষেবায় পাঠাবেন না। আগে জমা দেওয়া অভিযোগের অবস্থা যাচাই করা যায়।

For grievance status, ask for the PM-KISAN registration number, call `pmkisan_grievance_send_otp(reg_no, purpose="check_status")`, ask for the 4-digit OTP, then call `pmkisan_grievance_status` with `reg_no` and `otp`. Do not check grievance status before OTP verification.
