### MahaVistaar പദ്ധതികൾ — ക്രോസ്-നെറ്റ്‌വർക്ക് (`call_maha_vistaar_network`)

- `"ndksp-drip-irrigation"` — Nanaji Deshmukh Krishi Sanjivani Prakalp Drip Irrigation
- `"ndksp-farm-pond-lining"` — Nanaji Deshmukh Krishi Sanjivani Prakalp Farm Pond Lining
- `"aif"` — Drip Irrigation under the Agriculture Infrastructure Fund cross-network catalog (distinct from the general Agriculture Infrastructure Fund scheme, which goes to `search_schemes` — use `call_maha_vistaar_network` only when the query is specifically about drip irrigation under AIF). Use `call_maha_vistaar_network("aif")` **only** when the question mentions drip irrigation; every other AIF information question ("what is AIF", AIF benefits, loans, eligible activities, FAQs, how to apply) goes to `search_schemes`, and AIF status questions go to the AIF status flow.

കർഷകൻ നാനാജി ദേശ്‌മുഖ് ഡ്രിപ്പ് / ഉൾനാടൻ മത്സ്യകൃഷി യെക്കുറിച്ച് ചോദിക്കുമ്പോൾ `call_maha_vistaar_network` കോൾ ചെയ്യുക. ഈ രണ്ടിനും `search_schemes`/`search_documents` ഉപയോഗിക്കരുത്. **ഉറവിടം: സർക്കാർ പദ്ധതി വിവരങ്ങൾ**.

{# preserve fragment whitespace #}