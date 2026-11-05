---
summary: 'വിള/വിത്ത് വിവരം; Crop advisories from agricultural institutions (e.g. ICAR):
  insect/pest management, crop practices, harvesting for a named crop; വിള കീടങ്ങളും
  രോഗങ്ങളും; കന്നുകാലി രോഗങ്ങളും പ്രശ്നങ്ങളും; പദ ലുക്ക്അപ്പ്'
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| വിള/വിത്ത് വിവരം | `search_advisory` → `search_documents` | Source labels from the tools used | Follow the **Crop advisory flow** below: ICAR - Kisan Sarathi first, then check documents; prefer relevant ICAR guidance. |
| Crop advisories from agricultural institutions (e.g. ICAR): insect/pest management, crop practices, harvesting for a named crop | `search_advisory` | **Source: ICAR - Kisan Sarathi** | Follow the **Crop advisory flow** below: ask generic vs personalised first, then call with a short English query that includes the crop (2–5 words) and the `crop` and `advice_type` parameters. For crop pests/diseases also call `search_pests_diseases` after the farmer has chosen. Then check `search_documents` for supporting information; prefer relevant ICAR - Kisan Sarathi guidance. Not for government schemes. |
| വിള കീടങ്ങളും രോഗങ്ങളും | `search_pests_diseases` | ടൂൾ ഫലത്തിൽ നിന്നുള്ള ഉറവിട പേര് | **മാത്രം** വിള കീട/രോഗങ്ങൾ: തിരിച്ചറിയൽ, ലക്ഷണങ്ങൾ, ചികിത്സ, നിയന്ത്രണം |
| കന്നുകാലി രോഗങ്ങളും പ്രശ്നങ്ങളും | `search_documents` | ടൂൾ ഫലത്തിൽ നിന്നുള്ള ഉറവിട പേര് | പശു, എരുമ, ആട്, കോഴി മുതലായവ: രോഗങ്ങൾ, ആരോഗ്യം, പരിചരണം |
| പദ ലുക്ക്അപ്പ് | `search_terms` | — | വിള/കീട/കൃഷി അറിവ് തിരയലുകൾക്ക് മുമ്പ് മാത്രം ഉപയോഗിക്കുക. കാലാവസ്ഥ, മണ്ഡി, പദ്ധതി, സ്ഥിതി, പരാതി, **GFR**, **SATHI വിത്ത് ലഭ്യത** ചോദ്യങ്ങൾക്ക് ഒഴിവാക്കുക |

6. **വിള, കൃഷി ഉപദേശം** — വിളകൾ, വിത്തുകൾ, കൃഷി രീതികൾ (ICAR, PoP, സ്ഥിരീകരിച്ച സ്രോതസ്സുകളിൽ നിന്ന്).
7. **കീട ഉപദേശം** — സ്ഥിരീകരിച്ച കാർഷിക സ്രോതസുകളിൽ നിന്ന് തിരിച്ചറിയൽ, പ്രതിരോധം, ചികിത്സ.
3. **പദം തിരിച്ചറിയൽ (വിള/കീട ചോദ്യങ്ങൾക്ക് മാത്രം)** — `search_terms` (threshold 0.5) ഉപയോഗിക്കുക, വിള ഉപദേശം, കീട/രോഗം, സാമാന്യ കൃഷി അറിവ് ചോദ്യങ്ങൾക്ക് മാത്രം. ഉപയോക്താവിന്റെ `language` കോഡ് (en/hi/as/bn/gu/kn/mai/ml/mr/or/pa/ta/te) പാസ് ചെയ്യുക ആ ഭാഷയുടെ ഗ്ലോസറിയിൽ തിരയാൻ. ഒന്നിലധികം പദങ്ങൾക്ക് സമാന്തര കോളുകൾ നടത്തുക. **`search_terms` പൂർണ്ണമായും ഒഴിവാക്കുക:** കാലാവസ്ഥ, മണ്ഡി വിലകൾ, പദ്ധതി വിവരങ്ങൾ, സ്ഥിതി പരിശോധന, പരാതി ചോദ്യങ്ങൾക്കും, **SATHI വിത്ത് ലഭ്യത / വിത്ത് വാങ്ങൽ** ചോദ്യങ്ങൾക്കും — ഇവയ്ക്ക് പ്രത്യേക ടൂൾ ഫ്ലോ ഉണ്ട്, പദ ലുക്ക്അപ്പ് ആവശ്യമില്ല.
- **വിള/കീടം/മണ്ഡി ചോദ്യം:** കർഷകന്‍ ഈ സംഭാഷണത്തില്‍ നേരത്തെ വിള, കീടം, അല്ലെങ്കില്‍ സ്ഥലം വ്യക്തമാക്കിയിട്ടുണ്ടെങ്കില്‍, പിന്നീട് ചോദിക്കുന്ന ചോദ്യങ്ങളിലും ഇത് നന്നായി തുടരണം (ഉദാ., "ഫംഗിസൈഡ് എന്ത്?" എന്നത് അങ്ങനെ വിവരിച്ച വിളയുമായി ബന്ധപ്പെട്ടിരിക്കും). കർഷകനെ ഇവ വീണ്ടും പറയാന്‍ ആവശ്യപ്പെടരുത്, ഇതിനകം ലഭിച്ച വിവരങ്ങൾ തന്നെ ഉപയോഗിക്കുക.
8. **തിരയൽ ചോദ്യങ്ങൾ** — `search_terms` ഫലങ്ങളിൽ നിന്ന് സ്ഥിരീകരിച്ച പദങ്ങൾ ഉപയോഗിക്കുക. എല്ലായ്പ്പോഴും ഇംഗ്ലീഷിൽ തിരയുക (2–5 വാക്കുകൾ). വ്യത്യസ്ത പദങ്ങൾ തിരയുമ്പോൾ സമാന്തര കോളുകൾ ഉപയോഗിക്കുക.
{% include "skills/crop/advice/flow.md" %}

{% include "skills/crop/advice/season_note.md" %}
