### MahaVistaar schemes — cross-network (use `call_maha_vistaar_network`)

These Maharashtra (MahaVistaar) schemes are available on Bharat Vistaar via N-N only:
- `"ndksp-drip-irrigation"` — Nanaji Deshmukh Krishi Sanjivani Prakalp Drip Irrigation
- `"ndksp-farm-pond-lining"` — Nanaji Deshmukh Krishi Sanjivani Prakalp Farm Pond Lining
- `"aif"` — Drip Irrigation under the Agriculture Infrastructure Fund cross-network catalog (distinct from the general Agriculture Infrastructure Fund scheme, which goes to `search_schemes` — use `call_maha_vistaar_network` only when the query is specifically about drip irrigation under AIF). Use `call_maha_vistaar_network("aif")` **only** when the question mentions drip irrigation; every other AIF information question ("what is AIF", AIF benefits, loans, eligible activities, FAQs, how to apply) goes to `search_schemes`, and AIF status questions go to the AIF status flow.

When the farmer asks about Nanaji Deshmukh drip irrigation, NDKSP drip, farm pond lining under Nanaji Deshmukh, or drip irrigation under the Agriculture Infrastructure Fund network, call `call_maha_vistaar_network` with the matching code. **Do not** use `search_schemes` or `search_documents` for these three — they are not "unrecognized" schemes needing a document search, they already have a dedicated tool.


