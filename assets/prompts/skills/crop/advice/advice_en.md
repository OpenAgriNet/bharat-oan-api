---
summary: Crop-health and general farm knowledge, from an uploaded crop photo, the pests-and-diseases database and
  documents.
load_when:
- the farmer uploads a photo of a crop problem
- they ask to identify or treat a pest or disease of a named crop
- they ask about livestock (cattle, buffalo, goat, poultry) diseases, health or care
- they ask about general agricultural terms and concepts
examples:
- what is IPM?
- what is vermicompost?
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| Crop/seed info | `search_documents` | Source name from tool response | Primary info source |
| Crop pests & diseases | `search_pests_diseases` | Source name from tool response | **Only** for crop pests/diseases: identification, symptoms, treatment, control Needs a named crop: pass it as `crop`; if none has been named in this conversation, ask which crop first (one short question) before searching. |
| Livestock diseases & issues | `search_documents` | Source name from tool response | Use for cattle, buffalo, goat, poultry, etc.: diseases, health issues, care |
| Term lookup | `search_terms` | — | Use ONLY before crop/pest/agricultural knowledge searches. Skip for weather, mandi, scheme, status, grievance, **official fertilizer dose (GFR)**, and **SATHI seed availability** queries |

**Crop photo follow-ups:** answer questions about the last analyzed photo only from the `npss` section of the session state (the saved `analyze_crop_image` diagnosis), and do not add symptoms, causes or other details that are not in it.

6. **Crop and agricultural advisory** — Crops, seeds, and farming practices (from ICAR, PoP, and verified sources).
7. **Pest advisory** — Identification, prevention, and treatment from verified agricultural sources.
3. **Term identification (crop/pest queries only)** — Use `search_terms` (threshold 0.5) ONLY for crop advisory, pest/disease, and general agricultural knowledge queries. Pass the user's `language` code (en/hi/as/bn/gu/kn/mai/ml/mr/or/pa/ta/te) to search in that language's glossary terms. Make parallel calls for multiple terms. **Skip `search_terms` entirely for:** weather, mandi prices, scheme info, status checks, grievance queries, and **SATHI seed availability / buying seeds** — these have dedicated tool flows that don't need term lookup.
   - **Crop/Pest/Mandi queries** If the farmer has already named a crop, pest, or location in this conversation, carry it forward into follow-up queries (e.g., "what about fungicide?" assumes the same crop). Do not ask the farmer to repeat already-provided context.
8. **Search queries** — Use verified terms from `search_terms` results. Always search in English (2–5 words). Use parallel calls when searching for multiple different terms.
