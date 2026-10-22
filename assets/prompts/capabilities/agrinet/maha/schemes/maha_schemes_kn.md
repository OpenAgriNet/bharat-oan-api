### MahaVistaar ಯೋಜನೆಗಳು — ಕ್ರಾಸ್-ನೆಟ್‌ವರ್ಕ್ (`call_maha_vistaar_network`)

- `"ndksp-drip-irrigation"` — Nanaji Deshmukh Krishi Sanjivani Prakalp Drip Irrigation
- `"ndksp-farm-pond-lining"` — Nanaji Deshmukh Krishi Sanjivani Prakalp Farm Pond Lining
- `"aif"` — Drip Irrigation under the Agriculture Infrastructure Fund cross-network catalog (distinct from the general Agriculture Infrastructure Fund scheme, which goes to `search_schemes` — use `call_maha_vistaar_network` only when the query is specifically about drip irrigation under AIF). Use `call_maha_vistaar_network("aif")` **only** when the question mentions drip irrigation; every other AIF information question ("what is AIF", AIF benefits, loans, eligible activities, FAQs, how to apply) goes to `search_schemes`, and AIF status questions go to the AIF status flow.

ರೈತರು ನಾನಾಜಿ ದೇಶಮುಖ ಡ್ರಿಪ್ / ಒಳನಾಡು ಮೀನುಗಾರಿಕೆ ಬಗ್ಗೆ ಕೇಳಿದಾಗ `call_maha_vistaar_network` ಕರೆ ಮಾಡಿ. ಈ ಎರಡಕ್ಕೂ `search_schemes`/`search_documents` ಬಳಸಬೇಡಿ. **ಮೂಲ: ಸರ್ಕಾರಿ ಯೋಜನೆ ಮಾಹಿತಿ**.


