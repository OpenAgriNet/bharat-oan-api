---
name: soil_health
description: Soil Health Card status and report lookup.
tools:
- check_shc_status
sections:
  status:
    file: status/soil_health_status_{language}.md
  report:
    file: report/soil_health_report_{language}.md
  routing_check_shc_status:
    file: routing_check_shc_status/routing_check_shc_status_{language}.md
---

# Soil Health

Edit the localized files named above to change this workflow. Tool names refer to
the shared Python registry; listing a tool in another skill never duplicates its
schema or implementation. Loading policy is configured separately.
