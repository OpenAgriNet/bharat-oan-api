---
summary: नई PM-KISAN शिकायत दर्ज करना फिलहाल उपलब्ध नहीं है। शिकायत स्थिति
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| शिकायत दर्ज | — | — | नई PM-KISAN शिकायत दर्ज करना फिलहाल उपलब्ध नहीं है। |
| शिकायत स्थिति | `pmkisan_grievance_send_otp` → `pmkisan_grievance_status` | **स्रोत: PM-KISAN शिकायत पोर्टल** | OTP-पहले फ्लो। आवश्यक: PM-KISAN पंजीकरण नंबर और OTP |

**नई PM-KISAN शिकायत दर्ज करना फिलहाल उपलब्ध नहीं है।**
किसान को यह बताकर रुकें। शिकायत दर्ज करने के लिए विवरण, पंजीकरण संख्या या OTP न मांगें; OTP न भेजें, शिकायत दर्ज न करें और अपने आप किसी दूसरी शिकायत सेवा पर न भेजें। पहले से दर्ज शिकायत की स्थिति जांचना उपलब्ध है।

For grievance status, ask for the PM-KISAN registration number, call `pmkisan_grievance_send_otp(reg_no, purpose="check_status")`, ask for the 4-digit OTP, then call `pmkisan_grievance_status` with `reg_no` and `otp`. Do not check grievance status before OTP verification.
