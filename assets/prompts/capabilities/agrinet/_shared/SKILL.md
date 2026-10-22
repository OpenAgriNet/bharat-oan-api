---
name: _shared
description: Instructions shared by several workflows; rendered once per prompt.
tools: []
sections:
  grievances:
    file: grievances/grievances_{language}.md
    when_any:
    - pmkisan
    - pmfby
  insurance_and_loan_eligibility:
    file: insurance_and_loan_eligibility/insurance_and_loan_eligibility_{language}.md
    when_any:
    - schemes
    - pmfby
    - kcc
---

#  Shared

Edit the localized files named above to change this workflow. Tool names refer to
the shared Python registry; listing a tool in another skill never duplicates its
schema or implementation. Loading policy is configured separately.
