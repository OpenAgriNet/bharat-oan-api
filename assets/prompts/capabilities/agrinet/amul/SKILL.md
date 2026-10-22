---
name: amul
description: Amul dairy, livestock and cooperative scheme information.
tools:
- call_amul_vistaar_network
sections:
  amul_and_scheme_disambiguation:
    file: amul_and_scheme_disambiguation/amul_and_scheme_disambiguation_{language}.md
    when_any:
    - schemes
    - amul
    - maha
  routing_call_amul_vistaar_network:
    file: routing_call_amul_vistaar_network/routing_call_amul_vistaar_network_{language}.md
---

# Amul

Edit the localized files named above to change this workflow. Tool names refer to
the shared Python registry; listing a tool in another skill never duplicates its
schema or implementation. Loading policy is configured separately.
