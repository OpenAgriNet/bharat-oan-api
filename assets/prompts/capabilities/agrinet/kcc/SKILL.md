---
name: kcc
description: Kisan Credit Card OTP and application selection/status.
tools:
- check_kcc_application_status
- initiate_kcc_otp
- select_kcc_application
sections:
  status:
    file: status/kcc_status_{language}.md
---

# Kcc

Edit the localized files named above to change this workflow. Tool names refer to
the shared Python registry; listing a tool in another skill never duplicates its
schema or implementation. Loading policy is configured separately.
