---
name: fertilizer
description: Government fertilizer recommendations using Soil Health Card details.
tools:
- gfr_get_crop_registries
- gfr_get_recommendations
sections:
  recommendations:
    file: recommendations/fertilizer_{language}.md
  routing_gfr_get_recommendations:
    file: routing_gfr_get_recommendations/routing_gfr_get_recommendations_{language}.md
---

# Fertilizer

Edit the localized files named above to change this workflow. Tool names refer to
the shared Python registry; listing a tool in another skill never duplicates its
schema or implementation. Loading policy is configured separately.
