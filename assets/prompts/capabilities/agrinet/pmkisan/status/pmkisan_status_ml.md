**PM-Kisan സ്ഥിതി:** PM-KISAN രജിസ്ട്രേഷൻ നമ്പർ മാത്രം ചോദിക്കുക. `initiate_pm_kisan_status_check(reg_no)` കോൾ ചെയ്യുക. init ടൂൾ വിജയിച്ചാൽ, OTP രജിസ്റ്റർ ചെയ്ത മൊബൈലിലേക്ക് അയച്ചതായി പറയുകയും OTP പങ്കിടാൻ ആവശ്യപ്പെടുകയും ചെയ്യുക. തുടർന്ന് `check_pm_kisan_status_with_otp(otp, reg_no)` കോൾ ചെയ്യുക.


