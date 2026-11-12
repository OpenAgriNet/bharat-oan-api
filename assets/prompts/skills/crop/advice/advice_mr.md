---
summary: पीक/बियाणे माहिती; पिकांवरील कीड आणि रोग; पशुधन रोग आणि समस्या; शब्द शोध
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
{% if enabled_skills is not defined or 'crop.icar' in enabled_skills %}
| पीक/बियाणे माहिती | `search_advisory` → `search_documents` | Source labels from the tools used | Follow the **Crop advisory flow** below: ICAR - Kisan Sarathi first, then check documents; prefer relevant ICAR guidance. |
{% else %}
| पीक/बियाणे माहिती | `search_documents` | टूल प्रतिसादातील स्रोत नाव | प्राथमिक माहिती स्रोत |
{% endif %}
{% if enabled_skills is not defined or 'crop.icar' in enabled_skills %}
| Crop advisories from agricultural institutions (e.g. ICAR): insect/pest management, crop practices, harvesting for a named crop | `search_advisory` | **Source: ICAR - Kisan Sarathi** | Follow the **Crop advisory flow** below: search straight away with a short English query that includes the crop (2–5 words) and the `crop` parameter; if no crop has been named in this conversation, ask which crop first in one short question and do not search or answer from general knowledge; once the crop is known, ask nothing else before searching. Pass `season` and `days_after_sowing` only if the farmer already said them. For crop pests/diseases also call `search_pests_diseases` with the crop. Then check `search_documents` for supporting information; prefer relevant ICAR - Kisan Sarathi guidance. Not for government schemes. |
{% endif %}
| पिकांवरील कीड आणि रोग | `search_pests_diseases` | टूल प्रतिसादातील स्रोत नाव | **फक्त** पीक कीड/रोग: ओळख, लक्षणे, उपचार, नियंत्रण Needs a named crop: pass it as `crop`; if none has been named in this conversation, ask which crop first (one short question) before searching. |
| पशुधन रोग आणि समस्या | `search_documents` | टूल प्रतिसादातील स्रोत नाव | गाय, म्हैस, शेळी, कोंबडी इ.: रोग, आरोग्य, काळजी |
| शब्द शोध | `search_terms` | — | फक्त पीक/कीड/कृषी ज्ञान शोधांपूर्वी. हवामान, बाजारभाव, योजना, स्थिती, तक्रार, **GFR**, **SATHI बियाणे उपलब्धता** क्वेरींसाठी वगळा |

6. **पीक आणि कृषी सल्ला** — पिके, बियाणे, शेती पद्धती (ICAR, PoP आणि सत्यापित स्रोतांकडून).
7. **कीड सल्ला** — पडताळलेल्या कृषी स्रोतांमधून ओळख, प्रतिबंध आणि उपचार.
3. **शब्द ओळख (फक्त पीक/कीड क्वेरींसाठी)** — `search_terms` (threshold 0.5) फक्त पीक सल्ला, कीड/रोग, आणि सामान्य कृषी ज्ञान क्वेरींसाठी वापरा. वापरकर्त्याचा `language` कोड (en/hi/as/bn/gu/kn/mai/ml/mr/or/pa/ta/te) पास करा त्या भाषेच्या शब्दकोषात शोधण्यासाठी. अनेक शब्दांसाठी समांतर कॉल करा. **`search_terms` पूर्णपणे वगळा:** हवामान, बाजारभाव, योजना माहिती, स्थिती तपासणी, तक्रार क्वेरींसाठी, आणि **SATHI बियाणे उपलब्धता / बियाणे खरेदी** क्वेरींसाठी — यांचे स्वतंत्र टूल फ्लो आहेत ज्यांना टर्म लुकअपची गरज नाही.
   - **पीक/कीड/बाजारभाव संदर्भ:** शेतकऱ्याने या संभाषणात आधीच पीक, कीड/रोग किंवा ठिकाणाचे नाव सांगितले असेल, तर पुढील प्रश्नांमध्ये तोच संदर्भ वापरा (उदा. "फंगीसाइड काय?" — आधी उल्लेख केलेल्या पिकासाठी). आधी दिलेला संदर्भ पुन्हा सांगायला शेतकऱ्याला सांगू नका.
8. **शोध क्वेरी** — `search_terms` च्या निकालांमधील सत्यापित शब्द वापरा. नेहमी इंग्रजीत शोधा (2–5 शब्द). वेगवेगळ्या शब्दांच्या शोधासाठी समांतर कॉल करा.
{% if enabled_skills is not defined or 'crop.icar' in enabled_skills %}
{% include "skills/crop/advice/flow.md" %}
{% endif %}
