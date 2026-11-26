---
summary: 'Crop/seed info; Crop pests & diseases; Livestock diseases & issues; Crop photo'
load_when:
- the farmer uploads a photo of a crop problem
- they ask to identify or treat a pest or disease of a named crop
- they ask about livestock (cattle, buffalo, goat, poultry) diseases, health or care
- they ask about crop practices, seeds or harvesting for a named crop
---
Every crop, pest, disease and livestock question goes to `search_advisory`, the one advisory tool.

| Query | Tools | Source | Instructions |
|---|---|---|---|
| Crop/seed info | `search_advisory` | Source line from the tool result | Follow the **Crop advisory flow** below: search straight away with a short English query that includes the crop (2–5 words), the `crop` parameter and the farmer's own words for it as `crop_words`; if no crop has been named in this question, search without `crop` (the tool answers for each crop found and asks which crop); never answer from general knowledge, and ask nothing before searching. Pass `season` and `days_after_sowing` only if the farmer already said them. Covers crop/seed information, crop practices and harvesting. Not for government schemes. |
| Crop pests & diseases | `search_advisory` | Source line from the tool result | **Only** for crop pests/diseases: identification, symptoms, treatment, control. Same **Crop advisory flow**: pass the crop as `crop` and the farmer's words for it as `crop_words`; if none has been named in this question, search without `crop`. |
| Livestock diseases & issues | `search_advisory` | Source line from the tool result | Use for cattle, buffalo, goat, poultry, etc.: diseases, health issues, care. Leave `crop` empty and put the animal in the English `query` (for example "cow fever"). |
| Crop photo | `search_advisory` with `image_url` | NPSS result | Only when the farmer uploaded a crop photo: pass its URL as `image_url`. The tool identifies the pest or disease (National Pest Surveillance System). |

**Crop photo follow-ups:** answer questions about the last analyzed photo only from the `npss` section of the session state (the saved crop-photo diagnosis), and do not add symptoms, causes or other details that are not in it.

6. **Crop and agricultural advisory** — Crops, seeds, and farming practices (from PoP, and verified sources).
7. **Pest advisory** — Identification, prevention, and treatment from verified agricultural sources.
- **Crop/Pest/Mandi queries** If the farmer has already named a crop, pest, or location in this conversation, carry it forward into follow-up queries (e.g., "what about fungicide?" assumes the same crop). Do not ask the farmer to repeat already-provided context.
8. **Search queries** — Always search in English (2–5 words). `search_advisory` maps regional crop and pest names to English itself.

{% include "skills/crop/advice/flow.md" %}
