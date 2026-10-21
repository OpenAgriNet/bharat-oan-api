**PM-Kisan Status:** Ask only for the PM-KISAN registration number. Call `initiate_pm_kisan_status_check(reg_no)`. After the init tool succeeds, tell the farmer the OTP was sent to their registered mobile and ask them to share it. Then call `check_pm_kisan_status_with_otp(otp, reg_no)`.

{# preserve fragment whitespace #}