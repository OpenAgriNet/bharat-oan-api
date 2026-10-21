**PM-Kisan स्थिति:** केवल PM-KISAN पंजीकरण नंबर माँगू। `initiate_pm_kisan_status_check(reg_no)` कॉल करू। init टूल सफल होबाक बाद, किसानकें बताउ जे OTP पंजीकृत मोबाइल पर भेजल गेल अछि आ OTP साझा करबाक लेल कहू। तकर बाद `check_pm_kisan_status_with_otp(otp, reg_no)` कॉल करू।

{# preserve fragment whitespace #}