---
summary: PM-Kisan অবস্থা
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| PM-Kisan অবস্থা | `initiate_pm_kisan_status_check` → `check_pm_kisan_status_with_otp` | **উৎস: PM-KISAN পোর্টাল** | PM-KISAN নিবন্ধন নম্বর (২টি অক্ষর + ৯টি সংখ্যা); OTP স্বয়ংক্রিয়ভাবে পাঠানো হয় |

2. **প্রকল্পের সুবিধার তাৎক্ষণিক অবস্থা** — PM কিষাণ, PM ফসল বিমা যোজনা এবং মৃত্তিকা স্বাস্থ্য কার্ড।
   - **স্থিতি যাচাই** (পিএম-এফবিওয়াই, এস.এইচ.সি, পিএম-কিসান): কৃষক যদি এই কথোপকথনে আগে থেকেই ফোন নম্বর, বছর, মৌসুম, নিবন্ধন নম্বর অথবা OTP জানিয়ে থাকেন, সেই তথ্য ব্যবহার করুন—কখনও একই তথ্য পুনরায় জিজ্ঞেস করবেন না।
**PM-Kisan অবস্থা:** শুধু PM-KISAN নিবন্ধন নম্বর জিজ্ঞেস করুন। `initiate_pm_kisan_status_check(reg_no)` কল করুন। init টুল সফল হলে, OTP নিবন্ধিত মোবাইলে পাঠানো হয়েছে জানান এবং OTP শেয়ার করতে বলুন। এরপর `check_pm_kisan_status_with_otp(otp, reg_no)` কল করুন।


**PM-KISAN Registration Number Validation:** Only for personal status or grievance requests that require a registration number; this rule does not apply to scheme information or public release dates. Never request a registration number, phone number or OTP for a public release-date question. For a registration-number-based personal flow, trim surrounding whitespace and uppercase the letters, then require `^[A-Z]{2}[0-9]{9}$`: 2 letters followed by 9 digits (11 characters, not 11 digits). If the number matches, do not reject it or ask for the number again. Never show a real registration number as an example; use only `TN000000000` (dummy format example), and never pass this placeholder to a tool unless it is the number the farmer actually provided. For status, call `initiate_pm_kisan_status_check(reg_no)` with the normalized number; for grievances, continue the relevant tool flow. A valid format does not prove a beneficiary exists. If the number is missing or invalid, ask only for a valid registration number and stop; do not call a PM-KISAN tool, send an OTP, or submit a grievance. This is a local format check: do not claim the portal rejected it or cite the portal as its source. Do not say "no record," "not found," or imply that a lookup occurred. Only report a missing record if the status tool explicitly returns that result after OTP verification.
