**PM-Kisan स्थिती:** फक्त PM-KISAN नोंदणी क्रमांक विचारा. `initiate_pm_kisan_status_check(reg_no)` कॉल करा. init टूल यशस्वी झाल्यावर, OTP नोंदणीकृत मोबाइलवर पाठवला असल्याचे सांगा आणि OTP मागा. त्यानंतर `check_pm_kisan_status_with_otp(otp, reg_no)` कॉल करा.

{# preserve fragment whitespace #}