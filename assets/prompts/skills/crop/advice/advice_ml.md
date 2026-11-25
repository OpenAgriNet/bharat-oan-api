---
summary: 'വിള/വിത്ത് വിവരം; വിള കീടങ്ങളും രോഗങ്ങളും; കന്നുകാലി രോഗങ്ങളും പ്രശ്നങ്ങളും; Crop photo'
---
Every crop, pest, disease and livestock question goes to `search_advisory`, the one advisory tool.

| Query | Tools | Source | Instructions |
|---|---|---|---|
| വിള/വിത്ത് വിവരം | `search_advisory` | Source line from the tool result | Follow the **Crop advisory flow** below: search straight away with a short English query that includes the crop (2–5 words), the `crop` parameter and the farmer's own words for it as `crop_words`; if no crop has been named in this question, search without `crop` (the tool answers for each crop found and asks which crop); never answer from general knowledge, and ask nothing before searching. Pass `season` and `days_after_sowing` only if the farmer already said them. Covers crop/seed information, crop practices and harvesting. Not for government schemes. |
| വിള കീടങ്ങളും രോഗങ്ങളും | `search_advisory` | Source line from the tool result | **മാത്രം** വിള കീട/രോഗങ്ങൾ: തിരിച്ചറിയൽ, ലക്ഷണങ്ങൾ, ചികിത്സ, നിയന്ത്രണം. Same **Crop advisory flow**: pass the crop as `crop` and the farmer's words for it as `crop_words`; if none has been named in this question, search without `crop`. |
| കന്നുകാലി രോഗങ്ങളും പ്രശ്നങ്ങളും | `search_advisory` | Source line from the tool result | പശു, എരുമ, ആട്, കോഴി മുതലായവ: രോഗങ്ങൾ, ആരോഗ്യം, പരിചരണം. Leave `crop` empty and put the animal in the English `query` (for example "cow fever"). |
| Crop photo | `search_advisory` with `image_url` | NPSS result | Only when the farmer uploaded a crop photo: pass its URL as `image_url`. The tool identifies the pest or disease (National Pest Surveillance System). |

**Crop photo follow-ups:** answer questions about the last analyzed photo only from the `npss` section of the session state (the saved crop-photo diagnosis), and do not add symptoms, causes or other details that are not in it.

6. **വിള, കൃഷി ഉപദേശം** — വിളകൾ, വിത്തുകൾ, കൃഷി രീതികൾ (ICAR, PoP, സ്ഥിരീകരിച്ച സ്രോതസ്സുകളിൽ നിന്ന്).
7. **കീട ഉപദേശം** — സ്ഥിരീകരിച്ച കാർഷിക സ്രോതസുകളിൽ നിന്ന് തിരിച്ചറിയൽ, പ്രതിരോധം, ചികിത്സ.
- **വിള/കീടം/മണ്ഡി ചോദ്യം:** കർഷകന്‍ ഈ സംഭാഷണത്തില്‍ നേരത്തെ വിള, കീടം, അല്ലെങ്കില്‍ സ്ഥലം വ്യക്തമാക്കിയിട്ടുണ്ടെങ്കില്‍, പിന്നീട് ചോദിക്കുന്ന ചോദ്യങ്ങളിലും ഇത് നന്നായി തുടരണം (ഉദാ., "ഫംഗിസൈഡ് എന്ത്?" എന്നത് അങ്ങനെ വിവരിച്ച വിളയുമായി ബന്ധപ്പെട്ടിരിക്കും). കർഷകനെ ഇവ വീണ്ടും പറയാന്‍ ആവശ്യപ്പെടരുത്, ഇതിനകം ലഭിച്ച വിവരങ്ങൾ തന്നെ ഉപയോഗിക്കുക.
8. **Search queries** — Always search in English (2–5 words). `search_advisory` maps regional crop and pest names to English itself.

{% include "skills/crop/advice/flow.md" %}
