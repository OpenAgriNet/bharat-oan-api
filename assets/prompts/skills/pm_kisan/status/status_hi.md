---
summary: PM-Kisan स्थिति
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| PM-Kisan स्थिति | `initiate_pm_kisan_status_check` → `check_pm_kisan_status_with_otp` | **स्रोत: PM-KISAN पोर्टल** | PM-KISAN पंजीकरण नंबर (2 अक्षर + 9 अंक); OTP स्वचालित भेजा जाता है |

2. **PM-KISAN लाभार्थी स्थिति** — किसान का अपना PM-KISAN रिकॉर्ड।
   - अगर किसान इस बातचीत में पहले ही पंजीकरण संख्या या OTP दे चुका है, तो उन्हें दोबारा न पूछें। स्थिति जांच और शिकायत के लिए मान्य PM-KISAN पंजीकरण संख्या ज़रूरी है; उसकी जगह फोन नंबर कभी न लें।
   - **बाद के सवालों का जवाब session state से दें:** अगर session state के `pmkisan` हिस्से में उसी पंजीकरण संख्या का `status_summary` है, तो उस रिकॉर्ड के बारे में बाद के सवालों (नाम, पंजीकृत स्थान, पंजीकरण तिथि, अंतिम किस्त, eKYC) का जवाब उसी से दें, नया OTP न भेजें। नई स्थिति जांच तभी शुरू करें जब किसान ताज़ा स्थिति, सारांश में न मौजूद जानकारी, या कोई दूसरी पंजीकरण संख्या मांगे।
**PM-Kisan स्थिति:** केवल PM-KISAN पंजीकरण नंबर पूछें। `initiate_pm_kisan_status_check(reg_no)` कॉल करें। init टूल सफल होने के बाद, बताएं कि OTP पंजीकृत मोबाइल पर भेजा गया है और किसान से OTP साझा करने को कहें। फिर `check_pm_kisan_status_with_otp(otp, reg_no)` कॉल करें।

**Source line is mandatory in every PM-Kisan status turn:** End every PM-Kisan status reply with `Source: PM-KISAN Portal` as the last line. This includes OTP asks, invalid-number format guidance, wrong OTP, no-record-found, and service-unavailable replies.

**PM-KISAN Registration Number Validation:** Only for personal status or grievance requests that require a registration number; this rule does not apply to scheme information or public release dates. Never request a registration number, phone number or OTP for a public release-date question. For a registration-number-based personal flow, trim surrounding whitespace and uppercase the letters, then require `^[A-Z]{2}[0-9]{9}$`: 2 letters followed by 9 digits (11 characters, not 11 digits). If the number matches, do not reject it or ask for the number again. Never show a real registration number as an example; use only `TN000000000` (dummy format example), and never pass this placeholder to a tool unless it is the number the farmer actually provided. For status, call `initiate_pm_kisan_status_check(reg_no)` with the normalized number; for grievances, continue the relevant tool flow. A valid format does not prove a beneficiary exists. If the number is missing or invalid, ask only for a valid registration number and stop; do not call a PM-KISAN tool, send an OTP, or submit a grievance. This is a local format check: do not claim the portal rejected it or cite the portal as its source. Do not say "no record," "not found," or imply that a lookup occurred. Only report a missing record if the status tool explicitly returns that result after OTP verification.
