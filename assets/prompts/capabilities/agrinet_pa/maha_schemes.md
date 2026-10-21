### MahaVistaar ਸਕੀਮਾਂ — ਕ੍ਰਾਸ-ਨੈੱਟਵਰਕ (`call_maha_vistaar_network` ਦੀ ਵਰਤੋਂ ਕਰੋ)

ਇਹ ਮਹਾਰਾਸ਼ਟਰ (MahaVistaar) ਸਕੀਮਾਂ ਭਾਰਤ ਵਿਸਤਾਰ 'ਤੇ ਸਿਰਫ਼ N-N ਰਾਹੀਂ ਉਪਲਬਧ ਹਨ:
- `"ndksp-drip-irrigation"` — ਨਾਨਾਜੀ ਦੇਸ਼ਮੁਖ ਕ੍ਰਿਸ਼ੀ ਸੰਜੀਵਨੀ ਪ੍ਰਕਲਪ ਡ੍ਰਿੱਪ ਸਿੰਚਾਈ
- `"ndksp-farm-pond-lining"` — ਨਾਨਾਜੀ ਦੇਸ਼ਮੁਖ ਕ੍ਰਿਸ਼ੀ ਸੰਜੀਵਨੀ ਪ੍ਰਕਲਪ ਫਾਰਮ ਪੌਂਡ ਲਾਈਨਿੰਗ
- `"aif"` — Drip Irrigation under the Agriculture Infrastructure Fund cross-network catalog (distinct from the general Agriculture Infrastructure Fund scheme, which goes to `search_schemes` — use `call_maha_vistaar_network` only when the query is specifically about drip irrigation under AIF). Use `call_maha_vistaar_network("aif")` **only** when the question mentions drip irrigation; every other AIF information question ("what is AIF", AIF benefits, loans, eligible activities, FAQs, how to apply) goes to `search_schemes`, and AIF status questions go to the AIF status flow.

ਜਦੋਂ ਕਿਸਾਨ ਨਾਨਾਜੀ ਦੇਸ਼ਮੁਖ ਡ੍ਰਿੱਪ ਸਿੰਚਾਈ, NDKSP ਡ੍ਰਿੱਪ, ਨਾਨਾਜੀ ਦੇਸ਼ਮੁਖ ਅਧੀਨ ਫਾਰਮ ਪੌਂਡ ਲਾਈਨਿੰਗ, ਜਾਂ ਖੇਤੀਬਾੜੀ ਬੁਨਿਆਦੀ ਢਾਂਚਾ ਫੰਡ ਨੈੱਟਵਰਕ ਅਧੀਨ ਡ੍ਰਿੱਪ ਸਿੰਚਾਈ ਬਾਰੇ ਪੁੱਛਦਾ ਹੈ, ਤਾਂ ਮੇਲ ਖਾਂਦੇ ਕੋਡ ਨਾਲ `call_maha_vistaar_network` ਨੂੰ ਕਾਲ ਕਰੋ। ਇਹਨਾਂ ਤਿੰਨਾਂ ਲਈ `search_schemes` ਜਾਂ `search_documents` ਦੀ ਵਰਤੋਂ **ਨਾ** ਕਰੋ — ਇਹ "ਅਣਪਛਾਤੀਆਂ" ਸਕੀਮਾਂ ਨਹੀਂ ਹਨ ਜਿਨ੍ਹਾਂ ਨੂੰ ਦਸਤਾਵੇਜ਼ ਖੋਜ ਦੀ ਲੋੜ ਹੈ, ਇਹਨਾਂ ਕੋਲ ਪਹਿਲਾਂ ਹੀ ਇੱਕ ਸਮਰਪਿਤ ਟੂਲ ਹੈ।

{# preserve fragment whitespace #}