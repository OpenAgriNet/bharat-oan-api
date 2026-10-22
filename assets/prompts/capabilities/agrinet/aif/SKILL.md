---
name: aif
description: Agriculture Infrastructure Fund OTP, loan and grievance status.
tools:
- check_aif_grievance_status
- check_aif_loan_status
- initiate_aif_otp
- verify_aif_otp
sections:
  status:
    file: status/aif_status_{language}.md
---

# Aif

Edit the localized files named above to change this workflow. Tool names refer to
the shared Python registry; listing a tool in another skill never duplicates its
schema or implementation. Loading policy is configured separately.
