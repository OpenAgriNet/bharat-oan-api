---
name: khc
description: Kisan Helpline Center grievance registration/status, location/category lookup and OTP verification.
tools:
- check_khc_grievance_status
- find_khc_location
- get_khc_grievance_options
- initiate_khc_otp
- lookup_khc_reference
- register_khc_grievance
- verify_khc_otp
sections:
  rules:
    file: rules/khc_rules_common.md
---

# Khc

Edit the localized files named above to change this workflow. Tool names refer to
the shared Python registry; listing a tool in another skill never duplicates its
schema or implementation. Loading policy is configured separately.
