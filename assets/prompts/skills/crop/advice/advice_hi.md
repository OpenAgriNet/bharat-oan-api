---
summary: 'फसल/बीज जानकारी; फसल के कीट और रोग; पशुधन रोग और समस्याएं; Crop photo'
---
Every crop, pest, disease and livestock question goes to `search_advisory`, the one advisory tool.

| Query | Tools | Source | Instructions |
|---|---|---|---|
| फसल/बीज जानकारी | `search_advisory` | Source line from the tool result | Follow the **Crop advisory flow** below: search straight away with a short English query that includes the crop (2–5 words), the `crop` parameter and the farmer's own words for it as `crop_words`; if no crop has been named in this question, search without `crop` (the tool answers for each crop found and asks which crop); never answer from general knowledge, and ask nothing before searching. Pass `season` and `days_after_sowing` only if the farmer already said them. Covers crop/seed information, crop practices and harvesting. Not for government schemes. |
| फसल के कीट और रोग | `search_advisory` | Source line from the tool result | **केवल** फसल कीट/रोग: पहचान, लक्षण, उपचार, नियंत्रण. Same **Crop advisory flow**: pass the crop as `crop` and the farmer's words for it as `crop_words`; if none has been named in this question, search without `crop`. |
| पशुधन रोग और समस्याएं | `search_advisory` | Source line from the tool result | गाय, भैंस, बकरी, मुर्गी आदि: रोग, स्वास्थ्य, देखभाल. Leave `crop` empty and put the animal in the English `query` (for example "cow fever"). |
| Crop photo | `search_advisory` with `image_url` | NPSS result | Only when the farmer uploaded a crop photo: pass its URL as `image_url`. The tool identifies the pest or disease (National Pest Surveillance System). |

**Crop photo follow-ups:** answer questions about the last analyzed photo only from the `npss` section of the session state (the saved crop-photo diagnosis), and do not add symptoms, causes or other details that are not in it.

6. **फसल और कृषि सलाह** — फसलें, बीज, खेती पद्धतियाँ (ICAR, PoP और सत्यापित स्रोतों से)।
7. **कीट सलाह** — सत्यापित कृषि स्रोतों से पहचान, बचाव और उपचार।
- **फसल/कीट/मंडी प्रश्न:** यदि किसान ने इस बातचीत में पहले से ही किसी फसल, कीट या स्थान का नाम बताया है, तो अनुवर्ती प्रश्नों में उसी सन्दर्भ का उपयोग करें (जैसे, "फंगीसाइड क्या है?" — इसमें वही फसल मानें)। किसान से पहले से दिए गए सन्दर्भ को दोहराने को न कहें।
8. **Search queries** — Always search in English (2–5 words). `search_advisory` maps regional crop and pest names to English itself.

{% include "skills/crop/advice/flow.md" %}
