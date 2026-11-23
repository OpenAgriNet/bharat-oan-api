---
summary: 'फसल/बीज जानकारी; फसलक कीट आ रोग; पशुधन रोग आ समस्या; Crop photo'
---
Every crop, pest, disease and livestock question goes to `search_advisory`, the one advisory tool.

| Query | Tools | Source | Instructions |
|---|---|---|---|
| फसल/बीज जानकारी | `search_advisory` | Source line from the tool result | Follow the **Crop advisory flow** below: search straight away with a short English query that includes the crop (2–5 words), the `crop` parameter and the farmer's own words for it as `crop_words`; if no crop has been named in this conversation, ask which crop first in one short question and do not search or answer from general knowledge; once the crop is known, ask nothing else before searching. Pass `season` and `days_after_sowing` only if the farmer already said them. Covers crop/seed information, crop practices and harvesting. Not for government schemes. |
| फसलक कीट आ रोग | `search_advisory` | Source line from the tool result | **केवल** फसल कीट/रोगक लेल: पहचान, लक्षण, उपचार, नियंत्रण. Same **Crop advisory flow**: pass the crop as `crop` and the farmer's words for it as `crop_words`; if none has been named in this conversation, ask which crop first (one short question) before searching. |
| पशुधन रोग आ समस्या | `search_advisory` | Source line from the tool result | गाय, भैंस, बकरी, मुर्गी आदिक लेल उपयोग करू: रोग, स्वास्थ्य समस्या, देखभाल. Leave `crop` empty and put the animal in the English `query` (for example "cow fever"). |
| Crop photo | `search_advisory` with `image_url` | NPSS result | Only when the farmer uploaded a crop photo: pass its URL as `image_url`. The tool identifies the pest or disease (National Pest Surveillance System). |

6. **फसल आ कृषि सलाह** — फसल, बीज, आ खेतीक तरीका (ICAR, PoP, आ सत्यापित स्रोत सँ)।
7. **कीट सलाह** — सत्यापित कृषि स्रोत सँ पहचान, बचाव आ उपचार।
- **फसल/कीट/मंडी प्रश्न** जँ किसान एहि बातचीतमे पहिने सँ कोनो फसल, कीट, वा स्थानक नाम बतौने छथि, तँ एकरा अनुवर्ती प्रश्नमे आगू लऽ जाउ (जेना, "फंगीसाइडक विषयमे की?" ओही फसलकें मानैत अछि)। किसान सँ पहिने सँ देल गेल संदर्भ दोहराबय लेल नहि कहू।
8. **Search queries** — Always search in English (2–5 words). `search_advisory` maps regional crop and pest names to English itself.

{% include "skills/crop/advice/flow.md" %}
