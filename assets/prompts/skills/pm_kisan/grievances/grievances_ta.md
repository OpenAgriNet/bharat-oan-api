---
summary: புதிய PM-KISAN புகார் பதிவு செய்யும் சேவை தற்போது கிடைக்கவில்லை. புகார் நிலை
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| புகார் பதிவு | — | — | புதிய PM-KISAN புகார் பதிவு செய்யும் சேவை தற்போது கிடைக்கவில்லை. |
| புகார் நிலை | `pmkisan_grievance_send_otp` → `pmkisan_grievance_status` | **ஆதாரம்: PM-KISAN புகார் போர்ட்டல்** | OTP-முதல் ஓட்டம். தேவை: PM-KISAN பதிவு எண் மற்றும் OTP |

**புதிய PM-KISAN புகார் பதிவு செய்யும் சேவை தற்போது கிடைக்கவில்லை.**
விவசாயியிடம் இதைத் தெரிவித்து நிறுத்துங்கள். புகார் பதிவு செய்ய விவரங்கள், பதிவு எண் அல்லது OTP கேட்காதீர்கள்; OTP அனுப்பாதீர்கள், புகார் பதிவு செய்யாதீர்கள், தானாக வேறு புகார் சேவைக்கு மாற்றாதீர்கள். ஏற்கனவே பதிவு செய்த புகாரின் நிலையைச் சரிபார்க்கலாம்.

For grievance status, ask for the PM-KISAN registration number, call `pmkisan_grievance_send_otp(reg_no, purpose="check_status")`, ask for the 4-digit OTP, then call `pmkisan_grievance_status` with `reg_no` and `otp`. Do not check grievance status before OTP verification.
