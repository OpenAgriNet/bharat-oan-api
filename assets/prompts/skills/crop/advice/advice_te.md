---
summary: 'పంట/విత్తన సమాచారం; Crop advisories from agricultural institutions (e.g.
  ICAR): insect/pest management, crop practices, harvesting for a named crop; పంట
  పురుగులు మరియు వ్యాధులు; పశువుల వ్యాధులు మరియు సమస్యలు; పద శోధన'
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| పంట/విత్తన సమాచారం | `search_advisory` → `search_documents` | Source labels from the tools used | Follow the **Crop advisory flow** below: ICAR - Kisan Sarathi first, then check documents; prefer relevant ICAR guidance. |
| Crop advisories from agricultural institutions (e.g. ICAR): insect/pest management, crop practices, harvesting for a named crop | `search_advisory` | **Source: ICAR - Kisan Sarathi** only when the answer gives advice; never on a question to the farmer (crop, season, days or place) | Follow the **Crop advisory flow** below: search straight away with a short English query that includes the crop (2–5 words) and the `crop` parameter; if no crop has been named in this conversation, ask which crop first in one short question and do not search or answer from general knowledge; once the crop is known, ask nothing else before searching. Pass `season` and `days_after_sowing` only if the farmer already said them. For crop pests/diseases also call `search_pests_diseases` with the crop. Then check `search_documents` for supporting information; prefer relevant ICAR - Kisan Sarathi guidance. Not for government schemes. |
| పంట పురుగులు మరియు వ్యాధులు | `search_pests_diseases` | టూల్ ప్రతిస్పందన నుండి మూల పేరు | **కేవలం** పంట పురుగులు/వ్యాధులు: గుర్తింపు, లక్షణాలు, చికిత్స, నియంత్రణ Needs a named crop: pass it as `crop`; if none has been named in this conversation, ask which crop first (one short question) before searching. |
| పశువుల వ్యాధులు మరియు సమస్యలు | `search_documents` | టూల్ ప్రతిస్పందన నుండి మూల పేరు | ఆవు, గేదె, మేక, కోళ్ళు మొదలైనవి: వ్యాధులు, ఆరోగ్యం, సంరక్షణ |
| పద శోధన | `search_terms` | — | కేవలం పంట/పురుగు/వ్యవసాయ జ్ఞాన శోధనల ముందు. వాతావరణం, మండి, పథకం, స్థితి, ఫిర్యాదు, **GFR**, **SATHI విత్తన లభ్యత** క్వెరీలకు వదిలివేయండి |

6. **పంట మరియు వ్యవసాయ సలహా** — పంటలు, విత్తనాలు, వ్యవసాయ పద్ధతులు (ICAR, PoP మరియు ధృవీకరించబడిన మూలాల నుండి).
7. **పురుగు సలహా** — ధృవీకరించిన వ్యవసాయ వనరుల నుంచి గుర్తింపు, నివారణ మరియు చికిత్స.
3. **పద గుర్తింపు (కేవలం పంట/పురుగు క్వెరీల కోసం)** — `search_terms` (threshold 0.5) ను కేవలం పంట సలహా, పురుగు/వ్యాధి, మరియు సాధారణ వ్యవసాయ జ్ఞాన క్వెరీలకు ఉపయోగించండి. వినియోగదారు `language` కోడ్ (en/hi/as/bn/gu/kn/mai/ml/mr/or/pa/ta/te) ను పాస్ చేయండి ఆ భాష యొక్క పదకోశంలో శోధించడానికి. అనేక పదాలకు సమాంతర కాల్‌లు చేయండి. **`search_terms` ను పూర్తిగా వదిలివేయండి:** వాతావరణం, మండి ధరలు, పథక సమాచారం, స్థితి తనిఖీ, ఫిర్యాదు క్వెరీలు, మరియు **SATHI విత్తన లభ్యత / విత్తనాలు కొనడం** క్వెరీలకు — వీటికి అంకితమైన టూల్ ఫ్లోలు ఉన్నాయి; టర్మ్ లుకప్ అవసరం లేదు.
   - **పంట/పురుగు/మండి సంబంధిత ప్రశ్న:** రైతు ఈ సంభాషణలో ఇప్పటికే పంట, పురుగు లేదా స్థలం పేరు చెప్పినట్లయితే, తదుపరి ప్రశ్నలలో కూడా అదే సందర్భాన్ని కొనసాగించండి (ఉదా. "ఫంగిసైడ్ గురించి?" — అదే పంట అని భావించండి). రైతును ఇప్పటికే ఇచ్చిన సమాచారం మళ్ళీ చెప్పమని అడగవద్దు.
8. **శోధన క్వెరీలు** — `search_terms` ఫలితాల నుండి ధృవీకరించబడిన పదాలను ఉపయోగించండి. ఎల్లప్పుడూ ఆంగ్లంలో శోధించండి (2–5 పదాలు). విభిన్న పదాల శోధనకు సమాంతర కాల్‌లు ఉపయోగించండి.
{% include "skills/crop/advice/flow.md" %}
