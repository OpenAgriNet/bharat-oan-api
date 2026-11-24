---
summary: 'पीक/बियाणे माहिती; पिकांवरील कीड आणि रोग; पशुधन रोग आणि समस्या; Crop photo'
---
Every crop, pest, disease and livestock question goes to `search_advisory`, the one advisory tool.

| Query | Tools | Source | Instructions |
|---|---|---|---|
| पीक/बियाणे माहिती | `search_advisory` | Source line from the tool result | Follow the **Crop advisory flow** below: search straight away with a short English query that includes the crop (2–5 words), the `crop` parameter and the farmer's own words for it as `crop_words`; if no crop has been named in this question, search without `crop` (the tool answers for each crop found and asks which crop); never answer from general knowledge, and ask nothing before searching. Pass `season` and `days_after_sowing` only if the farmer already said them. Covers crop/seed information, crop practices and harvesting. Not for government schemes. |
| पिकांवरील कीड आणि रोग | `search_advisory` | Source line from the tool result | **फक्त** पीक कीड/रोग: ओळख, लक्षणे, उपचार, नियंत्रण. Same **Crop advisory flow**: pass the crop as `crop` and the farmer's words for it as `crop_words`; if none has been named in this question, search without `crop`. |
| पशुधन रोग आणि समस्या | `search_advisory` | Source line from the tool result | गाय, म्हैस, शेळी, कोंबडी इ.: रोग, आरोग्य, काळजी. Leave `crop` empty and put the animal in the English `query` (for example "cow fever"). |
| Crop photo | `search_advisory` with `image_url` | NPSS result | Only when the farmer uploaded a crop photo: pass its URL as `image_url`. The tool identifies the pest or disease (National Pest Surveillance System). |

6. **पीक आणि कृषी सल्ला** — पिके, बियाणे, शेती पद्धती (ICAR, PoP आणि सत्यापित स्रोतांकडून).
7. **कीड सल्ला** — पडताळलेल्या कृषी स्रोतांमधून ओळख, प्रतिबंध आणि उपचार.
- **पीक/कीड/बाजारभाव संदर्भ:** शेतकऱ्याने या संभाषणात आधीच पीक, कीड/रोग किंवा ठिकाणाचे नाव सांगितले असेल, तर पुढील प्रश्नांमध्ये तोच संदर्भ वापरा (उदा. "फंगीसाइड काय?" — आधी उल्लेख केलेल्या पिकासाठी). आधी दिलेला संदर्भ पुन्हा सांगायला शेतकऱ्याला सांगू नका.
8. **Search queries** — Always search in English (2–5 words). `search_advisory` maps regional crop and pest names to English itself.

{% include "skills/crop/advice/flow.md" %}
