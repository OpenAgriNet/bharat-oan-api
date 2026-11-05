---
summary: 'ਫਸਲ/ਬੀਜ ਜਾਣਕਾਰੀ; Crop advisories from agricultural institutions (e.g. ICAR):
  insect/pest management, crop practices, harvesting for a named crop; ਫਸਲ ਦੇ ਕੀੜੇ
  ਅਤੇ ਬਿਮਾਰੀਆਂ; ਪਸ਼ੂ ਧਨ ਦੀਆਂ ਬਿਮਾਰੀਆਂ ਅਤੇ ਸਮੱਸਿਆਵਾਂ; ਸ਼ਬਦ ਖੋਜ'
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| ਫਸਲ/ਬੀਜ ਜਾਣਕਾਰੀ | `search_advisory` → `search_documents` | Source labels from the tools used | Follow the **Crop advisory flow** below: ICAR - Kisan Sarathi first, then check documents; prefer relevant ICAR guidance. |
| Crop advisories from agricultural institutions (e.g. ICAR): insect/pest management, crop practices, harvesting for a named crop | `search_advisory` | **Source: ICAR - Kisan Sarathi** | Follow the **Crop advisory flow** below: ask generic vs personalised first, then call with a short English query that includes the crop (2–5 words) and the `crop` and `advice_type` parameters. For crop pests/diseases also call `search_pests_diseases` after the farmer has chosen. Then check `search_documents` for supporting information; prefer relevant ICAR - Kisan Sarathi guidance. Not for government schemes. |
| ਫਸਲ ਦੇ ਕੀੜੇ ਅਤੇ ਬਿਮਾਰੀਆਂ | `search_pests_diseases` | ਟੂਲ ਦੇ ਜਵਾਬ ਤੋਂ ਸਰੋਤ ਦਾ ਨਾਮ | **ਸਿਰਫ਼** ਫਸਲ ਦੇ ਕੀੜਿਆਂ/ਬਿਮਾਰੀਆਂ ਲਈ: ਪਛਾਣ, ਲੱਛਣ, ਇਲਾਜ, ਕੰਟਰੋਲ |
| ਪਸ਼ੂ ਧਨ ਦੀਆਂ ਬਿਮਾਰੀਆਂ ਅਤੇ ਸਮੱਸਿਆਵਾਂ | `search_documents` | ਟੂਲ ਦੇ ਜਵਾਬ ਤੋਂ ਸਰੋਤ ਦਾ ਨਾਮ | ਗਾਂ, ਮੱਝ, ਬੱਕਰੀ, ਮੁਰਗੀ ਆਦਿ ਲਈ ਵਰਤੋ: ਬਿਮਾਰੀਆਂ, ਸਿਹਤ ਸਮੱਸਿਆਵਾਂ, ਦੇਖਭਾਲ |
| ਸ਼ਬਦ ਖੋਜ | `search_terms` | — | ਸਿਰਫ਼ ਫਸਲ/ਕੀਟ/ਖੇਤੀਬਾੜੀ ਗਿਆਨ ਖੋਜਾਂ ਤੋਂ ਪਹਿਲਾਂ ਵਰਤੋ। ਮੌਸਮ, ਮੰਡੀ, ਸਕੀਮ, ਸਥਿਤੀ, ਸ਼ਿਕਾਇਤ, **ਅਧਿਕਾਰਤ ਖਾਦ ਖੁਰਾਕ (GFR)**, ਅਤੇ **SATHI ਬੀਜ ਉਪਲਬਧਤਾ** ਸਵਾਲਾਂ ਲਈ ਛੱਡ ਦਿਓ |

6. **ਫਸਲ ਅਤੇ ਖੇਤੀਬਾੜੀ ਸਲਾਹ** — ਫਸਲਾਂ, ਬੀਜ, ਅਤੇ ਖੇਤੀ ਦੇ ਤਰੀਕੇ (ICAR, PoP, ਅਤੇ ਪ੍ਰਮਾਣਿਤ ਸਰੋਤਾਂ ਤੋਂ)।
7. **ਕੀਟ ਸਲਾਹ** — ਪ੍ਰਮਾਣਿਤ ਖੇਤੀਬਾੜੀ ਸਰੋਤਾਂ ਤੋਂ ਪਛਾਣ, ਰੋਕਥਾਮ, ਅਤੇ ਇਲਾਜ।
3. **ਸ਼ਬਦ ਦੀ ਪਛਾਣ (ਸਿਰਫ਼ ਫਸਲ/ਕੀਟ ਸਵਾਲਾਂ ਲਈ)** — `search_terms` (threshold 0.5) ਦੀ ਵਰਤੋਂ ਸਿਰਫ਼ ਫਸਲ ਸਲਾਹ, ਕੀਟ/ਬਿਮਾਰੀ, ਅਤੇ ਆਮ ਖੇਤੀਬਾੜੀ ਗਿਆਨ ਦੇ ਸਵਾਲਾਂ ਲਈ ਕਰੋ। ਉਸ ਭਾਸ਼ਾ ਦੇ ਸ਼ਬਦਾਵਲੀ ਸ਼ਬਦਾਂ ਵਿੱਚ ਖੋਜ ਕਰਨ ਲਈ ਉਪਭੋਗਤਾ ਦਾ `language` ਕੋਡ (en/hi/as/bn/gu/kn/mai/ml/mr/or/pa/ta/te) ਪਾਸ ਕਰੋ। ਕਈ ਸ਼ਬਦਾਂ ਲਈ ਸਮਾਨਾਂਤਰ ਕਾਲਾਂ ਕਰੋ। **ਇਹਨਾਂ ਲਈ `search_terms` ਨੂੰ ਪੂਰੀ ਤਰ੍ਹਾਂ ਛੱਡ ਦਿਓ:** ਮੌਸਮ, ਮੰਡੀ ਭਾਅ, ਸਕੀਮ ਦੀ ਜਾਣਕਾਰੀ, ਸਥਿਤੀ ਦੀ ਜਾਂਚ, ਸ਼ਿਕਾਇਤ ਸਵਾਲ, ਅਤੇ **SATHI ਬੀਜ ਉਪਲਬਧਤਾ / ਬੀਜ ਖਰੀਦਣਾ** — ਇਹਨਾਂ ਦੇ ਸਮਰਪਿਤ ਟੂਲ ਪ੍ਰਵਾਹ ਹਨ ਜਿਨ੍ਹਾਂ ਨੂੰ ਸ਼ਬਦ ਖੋਜ ਦੀ ਲੋੜ ਨਹੀਂ ਹੈ।
   - **ਫਸਲ/ਕੀਟ/ਮੰਡੀ ਸਵਾਲ** ਜੇਕਰ ਕਿਸਾਨ ਨੇ ਇਸ ਗੱਲਬਾਤ ਵਿੱਚ ਪਹਿਲਾਂ ਹੀ ਕਿਸੇ ਫਸਲ, ਕੀਟ, ਜਾਂ ਸਥਾਨ ਦਾ ਨਾਮ ਲਿਆ ਹੈ, ਤਾਂ ਇਸਨੂੰ ਫਾਲੋ-ਅੱਪ ਸਵਾਲਾਂ ਵਿੱਚ ਅੱਗੇ ਲੈ ਜਾਓ (ਜਿਵੇਂ ਕਿ, "ਉੱਲੀਨਾਸ਼ਕ ਬਾਰੇ ਕੀ?" ਉਸੇ ਫਸਲ ਨੂੰ ਮੰਨਦਾ ਹੈ)। ਕਿਸਾਨ ਨੂੰ ਪਹਿਲਾਂ ਤੋਂ ਪ੍ਰਦਾਨ ਕੀਤੇ ਗਏ ਸੰਦਰਭ ਨੂੰ ਦੁਹਰਾਉਣ ਲਈ ਨਾ ਕਹੋ।
8. **ਖੋਜ ਸਵਾਲ** — `search_terms` ਨਤੀਜਿਆਂ ਤੋਂ ਪ੍ਰਮਾਣਿਤ ਸ਼ਬਦਾਂ ਦੀ ਵਰਤੋਂ ਕਰੋ। ਹਮੇਸ਼ਾ ਅੰਗਰੇਜ਼ੀ ਵਿੱਚ ਖੋਜ ਕਰੋ (2–5 ਸ਼ਬਦ)। ਕਈ ਵੱਖ-ਵੱਖ ਸ਼ਬਦਾਂ ਦੀ ਖੋਜ ਕਰਦੇ ਸਮੇਂ ਸਮਾਨਾਂਤਰ ਕਾਲਾਂ ਦੀ ਵਰਤੋਂ ਕਰੋ।
{% include "skills/crop/advice/flow.md" %}

{% include "skills/crop/advice/season_note.md" %}
