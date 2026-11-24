---
summary: 'ICAR - Kisan Sarathi crop advisories by crop, season, sowing stage and district: the preferred source for
  crop questions (search_advisory before search_documents, after the term lookup of the crop advice steps).'
load_when:
- any question about growing a named crop - sowing time and method, seed rate, varieties to grow, spacing, irrigation,
  when to apply fertilizer or manure, weed control, intercropping, pest and disease management, harvesting and post-harvest
examples:
- how should I irrigate my paddy?
- what intercrops go with cauliflower?
- aphids in wheat sown 40 days ago
not_for:
- the official government fertilizer dose table
- where to buy seed
- the IMD agromet advisory
---
ICAR - Kisan Sarathi advisories come first for crop questions: these rows replace the **Crop/seed info** row of the crop advice table.

| Query | Tools | Source | Instructions |
|---|---|---|---|
| Crop/seed info | `search_advisory` → `search_documents` | Source labels from the tools used | Follow the **Crop advisory flow** below: ICAR - Kisan Sarathi first, then check documents; prefer relevant ICAR guidance. |
| Crop advisories from agricultural institutions (e.g. ICAR): insect/pest management, crop practices, harvesting for a named crop | `search_advisory` | **Source: ICAR - Kisan Sarathi** | Follow the **Crop advisory flow** below: after the `search_terms` term lookup, search straight away with a short English query that includes the crop (2–5 words) and the `crop` parameter; if no crop has been named in this conversation, ask which crop first in one short question and do not search or answer from general knowledge; once the crop is known, ask nothing else before searching. Pass `season` and `days_after_sowing` only if the farmer already said them. For crop pests/diseases also call `search_pests_diseases` with the crop. Then check `search_documents` for supporting information; prefer relevant ICAR - Kisan Sarathi guidance. Not for government schemes. |

{% include "skills/crop/advice/flow.md" %}
