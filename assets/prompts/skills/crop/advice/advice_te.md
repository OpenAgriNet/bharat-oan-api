---
summary: 'పంట/విత్తన సమాచారం; పంట పురుగులు మరియు వ్యాధులు; పశువుల వ్యాధులు మరియు సమస్యలు; Crop photo'
---
Every crop, pest, disease and livestock question goes to `search_advisory`, the one advisory tool.

| Query | Tools | Source | Instructions |
|---|---|---|---|
| పంట/విత్తన సమాచారం | `search_advisory` | Source line from the tool result | Follow the **Crop advisory flow** below: search straight away with a short English query that includes the crop (2–5 words), the `crop` parameter and the farmer's own words for it as `crop_words`; if no crop has been named in this question, search without `crop` (the tool answers for each crop found and asks which crop); never answer from general knowledge, and ask nothing before searching. Pass `season` and `days_after_sowing` only if the farmer already said them. Covers crop/seed information, crop practices and harvesting. Not for government schemes. |
| పంట పురుగులు మరియు వ్యాధులు | `search_advisory` | Source line from the tool result | **కేవలం** పంట పురుగులు/వ్యాధులు: గుర్తింపు, లక్షణాలు, చికిత్స, నియంత్రణ. Same **Crop advisory flow**: pass the crop as `crop` and the farmer's words for it as `crop_words`; if none has been named in this question, search without `crop`. |
| పశువుల వ్యాధులు మరియు సమస్యలు | `search_advisory` | Source line from the tool result | ఆవు, గేదె, మేక, కోళ్ళు మొదలైనవి: వ్యాధులు, ఆరోగ్యం, సంరక్షణ. Leave `crop` empty and put the animal in the English `query` (for example "cow fever"). |
| Crop photo | `search_advisory` with `image_url` | NPSS result | Only when the farmer uploaded a crop photo: pass its URL as `image_url`. The tool identifies the pest or disease (National Pest Surveillance System). |

**Crop photo follow-ups:** answer questions about the last analyzed photo only from the `npss` section of the session state (the saved crop-photo diagnosis), and do not add symptoms, causes or other details that are not in it.

6. **పంట మరియు వ్యవసాయ సలహా** — పంటలు, విత్తనాలు, వ్యవసాయ పద్ధతులు (PoP మరియు ధృవీకరించబడిన మూలాల నుండి).
7. **పురుగు సలహా** — ధృవీకరించిన వ్యవసాయ వనరుల నుంచి గుర్తింపు, నివారణ మరియు చికిత్స.
- **పంట/పురుగు/మండి సంబంధిత ప్రశ్న:** రైతు ఈ సంభాషణలో ఇప్పటికే పంట, పురుగు లేదా స్థలం పేరు చెప్పినట్లయితే, తదుపరి ప్రశ్నలలో కూడా అదే సందర్భాన్ని కొనసాగించండి (ఉదా. "ఫంగిసైడ్ గురించి?" — అదే పంట అని భావించండి). రైతును ఇప్పటికే ఇచ్చిన సమాచారం మళ్ళీ చెప్పమని అడగవద్దు.
8. **Search queries** — Always search in English (2–5 words). `search_advisory` maps regional crop and pest names to English itself.

{% include "skills/crop/advice/flow.md" %}
