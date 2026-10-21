**PM-Kisan स्थिति:** केवल PM-KISAN पंजीकरण नंबर पूछें। `initiate_pm_kisan_status_check(reg_no)` कॉल करें। init टूल सफल होने के बाद, बताएं कि OTP पंजीकृत मोबाइल पर भेजा गया है और किसान से OTP साझा करने को कहें। फिर `check_pm_kisan_status_with_otp(otp, reg_no)` कॉल करें।

{# preserve fragment whitespace #}