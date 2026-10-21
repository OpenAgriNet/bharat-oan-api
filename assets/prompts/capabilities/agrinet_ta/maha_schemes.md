### MahaVistaar திட்டங்கள் — குறுக்கு-பிணையம் (`call_maha_vistaar_network`)

- `"ndksp-drip-irrigation"` — Nanaji Deshmukh Krishi Sanjivani Prakalp Drip Irrigation
- `"ndksp-farm-pond-lining"` — Nanaji Deshmukh Krishi Sanjivani Prakalp Farm Pond Lining
- `"aif"` — Drip Irrigation under the Agriculture Infrastructure Fund cross-network catalog (distinct from the general Agriculture Infrastructure Fund scheme, which goes to `search_schemes` — use `call_maha_vistaar_network` only when the query is specifically about drip irrigation under AIF). Use `call_maha_vistaar_network("aif")` **only** when the question mentions drip irrigation; every other AIF information question ("what is AIF", AIF benefits, loans, eligible activities, FAQs, how to apply) goes to `search_schemes`, and AIF status questions go to the AIF status flow.

விவசாயி நானாஜி தேஷ்முக் டிரிப் / உள்நாட்டு மீன்வளம் பற்றி கேட்டால் `call_maha_vistaar_network` ஐ அழைக்கவும். இந்த இரண்டிற்கும் `search_schemes`/`search_documents` பயன்படுத்த வேண்டாம். **ஆதாரம்: அரசு திட்ட தகவல்**.

{# preserve fragment whitespace #}