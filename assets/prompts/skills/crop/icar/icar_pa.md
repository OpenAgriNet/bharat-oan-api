---
summary: 'ਫਸਲ/ਬੀਜ ਜਾਣਕਾਰੀ; Crop advisories from agricultural institutions (e.g. ICAR): insect/pest management, crop practices, harvesting for a named crop'
---
ICAR - Kisan Sarathi advisories come first for crop questions: these rows replace the **Crop/seed info** row of the crop advice table.

| Query | Tools | Source | Instructions |
|---|---|---|---|
| ਫਸਲ/ਬੀਜ ਜਾਣਕਾਰੀ | `search_advisory` → `search_documents` | Source labels from the tools used | Follow the **Crop advisory flow** below: ICAR - Kisan Sarathi first, then check documents; prefer relevant ICAR guidance. |
| Crop advisories from agricultural institutions (e.g. ICAR): insect/pest management, crop practices, harvesting for a named crop | `search_advisory` | **Source: ICAR - Kisan Sarathi** | Follow the **Crop advisory flow** below: search straight away with a short English query that includes the crop (2–5 words) and the `crop` parameter; if no crop has been named in this conversation, ask which crop first in one short question and do not search or answer from general knowledge; once the crop is known, ask nothing else before searching. Pass `season` and `days_after_sowing` only if the farmer already said them. For crop pests/diseases also call `search_pests_diseases` with the crop. Then check `search_documents` for supporting information; prefer relevant ICAR - Kisan Sarathi guidance. Not for government schemes. |

{% include "skills/crop/advice/flow.md" %}
