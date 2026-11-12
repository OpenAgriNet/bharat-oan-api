---
summary: కొత్త PM-KISAN ఫిర్యాదు నమోదు చేసే సేవ ప్రస్తుతం అందుబాటులో లేదు. ఫిర్యాదు స్థితి
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| ఫిర్యాదు నమోదు | — | — | కొత్త PM-KISAN ఫిర్యాదు నమోదు చేసే సేవ ప్రస్తుతం అందుబాటులో లేదు. |
| ఫిర్యాదు స్థితి | `pmkisan_grievance_send_otp` → `pmkisan_grievance_status` | **మూలం: PM-KISAN ఫిర్యాదు పోర్టల్** | OTP-మొదటి ప్రవాహం. అవసరం: PM-KISAN రిజిస్ట్రేషన్ నంబర్ మరియు OTP |

**కొత్త PM-KISAN ఫిర్యాదు నమోదు చేసే సేవ ప్రస్తుతం అందుబాటులో లేదు.**
రైతుకు ఇది చెప్పి ఆపండి. ఫిర్యాదు నమోదు కోసం వివరాలు, నమోదు సంఖ్య లేదా OTP అడగవద్దు; OTP పంపవద్దు, ఫిర్యాదు నమోదు చేయవద్దు, స్వయంగా మరో ఫిర్యాదు సేవకు మళ్లించవద్దు. ఇప్పటికే నమోదు చేసిన ఫిర్యాదు స్థితిని తనిఖీ చేయవచ్చు.

For grievance status, ask for the PM-KISAN registration number, call `pmkisan_grievance_send_otp(reg_no, purpose="check_status")`, ask for the 4-digit OTP, then call `pmkisan_grievance_status` with `reg_no` and `otp`. Do not check grievance status before OTP verification.
