---
name: agristack
description: Consent-controlled farmer profile, location and crop records.
tools:
- get_agristack_farmer_crops
- get_agristack_farmer_location
- get_agristack_farmer_profile
sections:
  rules:
    file: rules/agristack_rules_common.md
---

# Agristack

Edit the localized files named above to change this workflow. Tool names refer to
the shared Python registry; listing a tool in another skill never duplicates its
schema or implementation. Loading policy is configured separately.
