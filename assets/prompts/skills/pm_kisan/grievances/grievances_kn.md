---
summary: ಹೊಸ PM-KISAN ದೂರು ಸಲ್ಲಿಸುವ ಸೇವೆ ಸದ್ಯಕ್ಕೆ ಲಭ್ಯವಿಲ್ಲ. ದೂರು ಸ್ಥಿತಿ
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| ದೂರು ಸಲ್ಲಿಕೆ | — | — | ಹೊಸ PM-KISAN ದೂರು ಸಲ್ಲಿಸುವ ಸೇವೆ ಸದ್ಯಕ್ಕೆ ಲಭ್ಯವಿಲ್ಲ. |
| ದೂರು ಸ್ಥಿತಿ | `pmkisan_grievance_send_otp` → `pmkisan_grievance_status` | **ಮೂಲ: PM-KISAN ದೂರು ಪೋರ್ಟಲ್** | OTP-ಮೊದಲ ಹರಿವು. ಅಗತ್ಯ: PM-KISAN ನೋಂದಣಿ ಸಂಖ್ಯೆ ಮತ್ತು OTP |

**ಹೊಸ PM-KISAN ದೂರು ಸಲ್ಲಿಸುವ ಸೇವೆ ಸದ್ಯಕ್ಕೆ ಲಭ್ಯವಿಲ್ಲ.**
ರೈತರಿಗೆ ಇದನ್ನು ತಿಳಿಸಿ ನಿಲ್ಲಿಸಿ. ದೂರು ಸಲ್ಲಿಸಲು ವಿವರಗಳು, ನೋಂದಣಿ ಸಂಖ್ಯೆ ಅಥವಾ OTP ಕೇಳಬೇಡಿ; OTP ಕಳುಹಿಸಬೇಡಿ, ದೂರು ಸಲ್ಲಿಸಬೇಡಿ ಮತ್ತು ಸ್ವಯಂಚಾಲಿತವಾಗಿ ಮತ್ತೊಂದು ದೂರು ಸೇವೆಗೆ ಕಳುಹಿಸಬೇಡಿ. ಈಗಾಗಲೇ ಸಲ್ಲಿಸಿದ ದೂರಿನ ಸ್ಥಿತಿಯನ್ನು ಪರಿಶೀಲಿಸಬಹುದು.

For grievance status, ask for the PM-KISAN registration number, call `pmkisan_grievance_send_otp(reg_no, purpose="check_status")`, ask for the 4-digit OTP, then call `pmkisan_grievance_status` with `reg_no` and `otp`. Do not check grievance status before OTP verification.
