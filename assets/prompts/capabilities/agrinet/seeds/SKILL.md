---
name: seeds
description: SATHI crop groups, crops and seed availability.
tools:
- get_sathi_crop_groups
- list_sathi_crops_in_group
- search_sathi_seed_availability
sections:
  availability:
    file: availability/seed_availability_{language}.md
---

# Seeds

Edit the localized files named above to change this workflow. Tool names refer to
the shared Python registry; listing a tool in another skill never duplicates its
schema or implementation. Loading policy is configured separately.
