---
summary: ਨਵੀਂ PM-KISAN ਸ਼ਿਕਾਇਤ ਦਰਜ ਕਰਨ ਦੀ ਸੇਵਾ ਫਿਲਹਾਲ ਉਪਲਬਧ ਨਹੀਂ ਹੈ। ਸ਼ਿਕਾਇਤ ਸਥਿਤੀ
---
{% include "skills/shared/grievances/pa.md" %}

| Query | Tools | Source | Instructions |
|---|---|---|---|
| ਸ਼ਿਕਾਇਤ ਦਰਜ | — | — | ਨਵੀਂ PM-KISAN ਸ਼ਿਕਾਇਤ ਦਰਜ ਕਰਨ ਦੀ ਸੇਵਾ ਫਿਲਹਾਲ ਉਪਲਬਧ ਨਹੀਂ ਹੈ। |
| ਸ਼ਿਕਾਇਤ ਸਥਿਤੀ | `pmkisan_grievance_send_otp` → `pmkisan_grievance_status` | **ਸਰੋਤ: PM-KISAN ਸ਼ਿਕਾਇਤ ਪੋਰਟਲ** | OTP-ਪਹਿਲਾਂ ਪ੍ਰਵਾਹ। ਲੋੜੀਂਦਾ ਹੈ: PM-KISAN ਰਜਿਸਟ੍ਰੇਸ਼ਨ ਨੰਬਰ ਅਤੇ OTP |

**ਨਵੀਂ PM-KISAN ਸ਼ਿਕਾਇਤ ਦਰਜ ਕਰਨ ਦੀ ਸੇਵਾ ਫਿਲਹਾਲ ਉਪਲਬਧ ਨਹੀਂ ਹੈ।**
ਕਿਸਾਨ ਨੂੰ ਇਹ ਦੱਸ ਕੇ ਰੁਕੋ। ਸ਼ਿਕਾਇਤ ਦਰਜ ਕਰਨ ਲਈ ਵੇਰਵੇ, ਰਜਿਸਟ੍ਰੇਸ਼ਨ ਨੰਬਰ ਜਾਂ OTP ਨਾ ਮੰਗੋ; OTP ਨਾ ਭੇਜੋ, ਸ਼ਿਕਾਇਤ ਦਰਜ ਨਾ ਕਰੋ ਅਤੇ ਆਪਣੇ ਆਪ ਕਿਸੇ ਹੋਰ ਸ਼ਿਕਾਇਤ ਸੇਵਾ ਵੱਲ ਨਾ ਭੇਜੋ। ਪਹਿਲਾਂ ਦਰਜ ਕੀਤੀ ਸ਼ਿਕਾਇਤ ਦੀ ਸਥਿਤੀ ਜਾਂਚੀ ਜਾ ਸਕਦੀ ਹੈ।

For grievance status, ask for the PM-KISAN registration number, call `pmkisan_grievance_send_otp(reg_no, purpose="check_status")`, ask for the 4-digit OTP, then call `pmkisan_grievance_status` with `reg_no` and `otp`. Do not check grievance status before OTP verification.
