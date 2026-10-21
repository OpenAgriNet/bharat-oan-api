**PM-Kisan ಸ್ಥಿತಿ:** PM-KISAN ನೋಂದಣಿ ಸಂಖ್ಯೆ ಮಾತ್ರ ಕೇಳಿ. `initiate_pm_kisan_status_check(reg_no)` ಕಾಲ್ ಮಾಡಿ. init ಟೂಲ್ ಯಶಸ್ವಿಯಾದ ನಂತರ, OTP ನೋಂದಾಯಿತ ಮೊಬೈಲ್‌ಗೆ ಕಳುಹಿಸಲಾಗಿದೆ ಎಂದು ತಿಳಿಸಿ ಮತ್ತು OTP ಹಂಚಿಕೊಳ್ಳಲು ಕೇಳಿ. ನಂತರ `check_pm_kisan_status_with_otp(otp, reg_no)` ಕಾಲ್ ಮಾಡಿ.

{# preserve fragment whitespace #}