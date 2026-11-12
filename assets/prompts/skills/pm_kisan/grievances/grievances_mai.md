---
summary: नव PM-KISAN शिकायत दर्ज करबाक सेवा एखन उपलब्ध नहि अछि। शिकायत स्थिति
---
{% include "skills/shared/grievances/mai.md" %}

| Query | Tools | Source | Instructions |
|---|---|---|---|
| शिकायत दर्ज | — | — | नव PM-KISAN शिकायत दर्ज करबाक सेवा एखन उपलब्ध नहि अछि। |
| शिकायत स्थिति | `pmkisan_grievance_send_otp` → `pmkisan_grievance_status` | **स्रोत: PM-KISAN शिकायत पोर्टल** | OTP-पहिने फ्लो। आवश्यक: PM-KISAN पंजीकरण नंबर आ OTP |

**नव PM-KISAN शिकायत दर्ज करबाक सेवा एखन उपलब्ध नहि अछि।**
किसान केँ ई बात कहि रुकि जाउ। शिकायत दर्ज करबाक लेल विवरण, पंजीकरण संख्या वा OTP नहि मांगू; OTP नहि पठाउ, शिकायत दर्ज नहि करू आ अपने आन शिकायत सेवा पर नहि पठाउ। पहिने दर्ज शिकायतक स्थिति जांच उपलब्ध अछि।

For grievance status, ask for the PM-KISAN registration number, call `pmkisan_grievance_send_otp(reg_no, purpose="check_status")`, ask for the 4-digit OTP, then call `pmkisan_grievance_status` with `reg_no` and `otp`. Do not check grievance status before OTP verification.
