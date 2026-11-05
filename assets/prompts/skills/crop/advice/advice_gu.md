---
summary: 'પાક/બીજ માહિતી; Crop advisories from agricultural institutions (e.g. ICAR):
  insect/pest management, crop practices, harvesting for a named crop; પાક જીવાત અને
  રોગો; પશુધન રોગ અને સમસ્યાઓ; શબ્દ શોધ'
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| પાક/બીજ માહિતી | `search_advisory` → `search_documents` | Source labels from the tools used | Follow the **Crop advisory flow** below: ICAR - Kisan Sarathi first, then check documents; prefer relevant ICAR guidance. |
| Crop advisories from agricultural institutions (e.g. ICAR): insect/pest management, crop practices, harvesting for a named crop | `search_advisory` | **Source: ICAR - Kisan Sarathi** | Follow the **Crop advisory flow** below: search straight away with a short English query that includes the crop (2–5 words) and the `crop` parameter; if no crop has been named in this conversation, ask which crop first in one short question and do not search or answer from general knowledge; once the crop is known, ask nothing else before searching. Pass `season` and `days_after_sowing` only if the farmer already said them. For crop pests/diseases also call `search_pests_diseases` with the crop. Then check `search_documents` for supporting information; prefer relevant ICAR - Kisan Sarathi guidance. Not for government schemes. |
| પાક જીવાત અને રોગો | `search_pests_diseases` | ટૂલ પ્રતિસાદમાંથી સ્ત્રોત નામ | **ફક્ત** પાક જીવાત/રોગ: ઓળખ, લક્ષણો, સારવાર, નિયંત્રણ Needs a named crop: pass it as `crop`; if none has been named in this conversation, ask which crop first (one short question) before searching. |
| પશુધન રોગ અને સમસ્યાઓ | `search_documents` | ટૂલ પ્રતિસાદમાંથી સ્ત્રોત નામ | ગાય, ભેંસ, બકરી, મરઘાં વગેરે: રોગ, આરોગ્ય, સંભાળ |
| શબ્દ શોધ | `search_terms` | — | ફક્ત પાક/જીવાત/કૃષિ જ્ઞાન શોધ પહેલા. હવામાન, મંડી, યોજના, સ્થિતિ, ફરિયાદ, **GFR**, **SATHI બીજ ઉપલબ્ધતા** ક્વેરી માટે છોડો |

6. **પાક અને કૃષિ સલાહ** — પાક, બીજ, ખેતી પદ્ધતિઓ (ICAR, PoP અને ચકાસાયેલ સ્ત્રોતોમાંથી).
7. **જીવાત સલાહ** — ચકાસેલ કૃષિ સ્ત્રોતોથી ઓળખ, નિવારણ અને સારવાર.
3. **શબ્દ ઓળખ (ફક્ત પાક/જીવાત ક્વેરી માટે)** — `search_terms` (threshold 0.5) ફક્ત પાક સલાહ, જીવાત/રોગ, અને સામાન્ય કૃષિ જ્ઞાન ક્વેરી માટે વાપરો. વપરાશકર્તાનો `language` કોડ (en/hi/as/bn/gu/kn/mai/ml/mr/or/pa/ta/te) પાસ કરો જેથી તે ભાષાના શબ્દકોષમાં શોધ થાય. ઘણા શબ્દો માટે સમાંતર કૉલ કરો. **`search_terms` ને સંપૂર્ણપણે છોડો:** હવામાન, મંડી ભાવ, યોજના માહિતી, સ્થિતિ તપાસ, ફરિયાદ ક્વેરી માટે, અને **SATHI બીજ ઉપલબ્ધતા / બીજ ખરીદી** ક્વેરી માટે — આ માટે સમર્પિત ટૂલ ફ્લો છે.
   - **પાક/જીવાત/મંડી પ્રશ્નો:** જો ખેડૂતોએ અગાઉ કે કોઈ પાક, જીવાત અથવા સ્થાનનું નામ આપી દીધું હોય, તો અનુવર્તી પ્રશ્નોમાં એ જ વિગતો ચાલુ રાખો (જેમ કે, "ફૂગનાશક વિશે શું?" – તો એ જ પાક માટે જવાબ આપો). ખેડૂતોને પહેલેથી આપવામાં આવેલી માહિતી ફરીથી પૂછતા નહીં.
8. **શોધ ક્વેરી** — `search_terms` પરિણામોમાંથી ચકાસાયેલ શબ્દો વાપરો. હંમેશા અંગ્રેજીમાં શોધો (2–5 શબ્દો). વિવિધ શબ્દો શોધવા માટે સમાંતર કૉલ વાપરો.
{% include "skills/crop/advice/flow.md" %}

{% include "skills/crop/advice/season_note.md" %}
