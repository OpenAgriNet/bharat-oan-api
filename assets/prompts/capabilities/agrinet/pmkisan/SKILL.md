---
name: pmkisan
description: PM-KISAN registration-based benefit status, OTP verification and grievances.
tools:
- check_pm_kisan_status_with_otp
- initiate_pm_kisan_status_check
- pmkisan_grievance_send_otp
- pmkisan_grievance_status
- pmkisan_submit_grievance
sections:
  status:
    file: status/pmkisan_status_{language}.md
  validation:
    file: validation/pmkisan_validation_{language}.md
  instalments:
    file: instalments/pmkisan_instalments_{language}.md
    languages:
    - en
    - hi
    - mai
    - or
    - pa
---

# Pmkisan

Edit the localized files named above to change this workflow. Tool names refer to
the shared Python registry; listing a tool in another skill never duplicates its
schema or implementation. Loading policy is configured separately.
