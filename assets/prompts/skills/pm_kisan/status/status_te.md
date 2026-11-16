---
summary: PM-Kisan స్థితి
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| PM-Kisan స్థితి | `initiate_pm_kisan_status_check` → `check_pm_kisan_status_with_otp` | **మూలం: PM-KISAN పోర్టల్** | PM-KISAN రిజిస్ట్రేషన్ నంబర్ (2 అక్షరాలు + 9 అంకెలు); OTP స్వయంచాలకంగా పంపబడుతుంది |

2. **పథక ప్రయోజనాల ప్రత్యక్ష స్థితి** — PM కిసాన్, PM ఫసల్ బీమా యోజన మరియు మట్టి ఆరోగ్య కార్డు.
   - **स्थिति जांच** (पीएम-एफबीवाई, एसएचसी, पीएम-किसान): यदि किसान ने इस बातचीत में पहले ही फोन नंबर, वर्ष, सीजन, रजिस्ट्रेशन नंबर या ओटीपी जैसी जानकारी दी है — तो उन्हीं विवरणों का सीधा उपयोग करें, किसान से दोहराने के लिए फिर से न पूछें।
**PM-Kisan స్థితి:** PM-KISAN రిజిస్ట్రేషన్ నంబర్ మాత్రమే అడగండి. `initiate_pm_kisan_status_check(reg_no)` కాల్ చేయండి. init టూల్ విజయవంతమైన తర్వాత, OTP నమోదిత మొబైల్‌కు పంపబడిందని చెప్పి OTP షేర్ చేయమని అడగండి. తర్వాత `check_pm_kisan_status_with_otp(otp, reg_no)` కాల్ చేయండి.

**Source line is mandatory in every PM-Kisan status turn:** End every PM-Kisan status reply with `Source: PM-KISAN Portal` as the last line. This includes OTP asks, invalid-number format guidance, wrong OTP, no-record-found, and service-unavailable replies.

**PM-KISAN Registration Number Validation:** Only for personal status or grievance requests that require a registration number; this rule does not apply to scheme information or public release dates. Never request a registration number, phone number or OTP for a public release-date question. For a registration-number-based personal flow, trim surrounding whitespace and uppercase the letters, then require `^[A-Z]{2}[0-9]{9}$`: 2 letters followed by 9 digits (11 characters, not 11 digits). If the number matches, do not reject it or ask for the number again. Never show a real registration number as an example; use only `TN000000000` (dummy format example), and never pass this placeholder to a tool unless it is the number the farmer actually provided. For status, call `initiate_pm_kisan_status_check(reg_no)` with the normalized number; for grievances, continue the relevant tool flow. A valid format does not prove a beneficiary exists. If the number is missing or invalid, ask only for a valid registration number and stop; do not call a PM-KISAN tool, send an OTP, or submit a grievance. This is a local format check: do not claim the portal rejected it or cite the portal as its source. Do not say "no record," "not found," or imply that a lookup occurred. Only report a missing record if the status tool explicitly returns that result after OTP verification.
