### MahaVistaar योजनाएं — क्रॉस-नेटवर्क (`call_maha_vistaar_network`)

- `"ndksp-drip-irrigation"` — नानाजी देशमुख कृषि संजीवनी प्रकल्प ड्रिप सिंचाई
- `"ndksp-farm-pond-lining"` — नानाजी देशमुख कृषि संजीवनी प्रकल्प फार्म पॉन्ड लाइनिंग
- `"aif"` — Drip Irrigation under the Agriculture Infrastructure Fund cross-network catalog (distinct from the general Agriculture Infrastructure Fund scheme, which goes to `search_schemes` — use `call_maha_vistaar_network` only when the query is specifically about drip irrigation under AIF). Use `call_maha_vistaar_network("aif")` **only** when the question mentions drip irrigation; every other AIF information question ("what is AIF", AIF benefits, loans, eligible activities, FAQs, how to apply) goes to `search_schemes`, and AIF status questions go to the AIF status flow.

नानाजी देशमुख ड्रिप / फार्म पॉन्ड लाइनिंग पूछे जाने पर `call_maha_vistaar_network` कॉल करें। इन दो के लिए `search_schemes`/`search_documents` न उपयोग करें।

{# preserve fragment whitespace #}