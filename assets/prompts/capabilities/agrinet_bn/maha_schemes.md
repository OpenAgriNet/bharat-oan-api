### MahaVistaar প্রকল্প — ক্রস-নেটওয়ার্ক (`call_maha_vistaar_network`)

- `"ndksp-drip-irrigation"` — Nanaji Deshmukh Krishi Sanjivani Prakalp Drip Irrigation
- `"ndksp-farm-pond-lining"` — Nanaji Deshmukh Krishi Sanjivani Prakalp Farm Pond Lining
- `"aif"` — Drip Irrigation under the Agriculture Infrastructure Fund cross-network catalog (distinct from the general Agriculture Infrastructure Fund scheme, which goes to `search_schemes` — use `call_maha_vistaar_network` only when the query is specifically about drip irrigation under AIF). Use `call_maha_vistaar_network("aif")` **only** when the question mentions drip irrigation; every other AIF information question ("what is AIF", AIF benefits, loans, eligible activities, FAQs, how to apply) goes to `search_schemes`, and AIF status questions go to the AIF status flow.

কৃষক নানাজি দেশমুখ ড্রিপ / ফার্ম পন্ড লাইনিং সম্পর্কে জিজ্ঞেস করলে `call_maha_vistaar_network` কল করুন। এই দুটির জন্য `search_schemes`/`search_documents` ব্যবহার করবেন না। **উৎস: সরকারি প্রকল্প তথ্য**।

{# preserve fragment whitespace #}