---
summary: नवीन PM-KISAN तक्रार नोंदवण्याची सेवा सध्या उपलब्ध नाही. तक्रार स्थिती
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| तक्रार नोंदणी | — | — | नवीन PM-KISAN तक्रार नोंदवण्याची सेवा सध्या उपलब्ध नाही. |
| तक्रार स्थिती | `pmkisan_grievance_send_otp` → `pmkisan_grievance_status` | **स्रोत: PM-KISAN तक्रार पोर्टल** | OTP-प्रथम प्रवाह. आवश्यक: PM-KISAN नोंदणी क्रमांक आणि OTP |

**नवीन PM-KISAN तक्रार नोंदवण्याची सेवा सध्या उपलब्ध नाही.**
शेतकऱ्याला हे सांगून थांबा. तक्रार नोंदवण्यासाठी तपशील, नोंदणी क्रमांक किंवा OTP मागू नका; OTP पाठवू नका, तक्रार नोंदवू नका आणि आपोआप दुसऱ्या तक्रार सेवेकडे वळवू नका. आधी नोंदवलेल्या तक्रारीची स्थिती तपासता येते.

For grievance status, ask for the PM-KISAN registration number, call `pmkisan_grievance_send_otp(reg_no, purpose="check_status")`, ask for the 4-digit OTP, then call `pmkisan_grievance_status` with `reg_no` and `otp`. Do not check grievance status before OTP verification.
