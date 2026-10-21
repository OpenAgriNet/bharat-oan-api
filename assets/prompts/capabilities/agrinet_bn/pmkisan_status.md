**PM-Kisan অবস্থা:** শুধু PM-KISAN নিবন্ধন নম্বর জিজ্ঞেস করুন। `initiate_pm_kisan_status_check(reg_no)` কল করুন। init টুল সফল হলে, OTP নিবন্ধিত মোবাইলে পাঠানো হয়েছে জানান এবং OTP শেয়ার করতে বলুন। এরপর `check_pm_kisan_status_with_otp(otp, reg_no)` কল করুন।

{# preserve fragment whitespace #}