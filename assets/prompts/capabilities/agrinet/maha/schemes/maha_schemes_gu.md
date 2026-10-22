### MahaVistaar યોજનાઓ — ક્રોસ-નેટવર્ક (`call_maha_vistaar_network`)

- `"ndksp-drip-irrigation"` — Nanaji Deshmukh Krishi Sanjivani Prakalp Drip Irrigation
- `"ndksp-farm-pond-lining"` — Nanaji Deshmukh Krishi Sanjivani Prakalp Farm Pond Lining
- `"aif"` — Drip Irrigation under the Agriculture Infrastructure Fund cross-network catalog (distinct from the general Agriculture Infrastructure Fund scheme, which goes to `search_schemes` — use `call_maha_vistaar_network` only when the query is specifically about drip irrigation under AIF). Use `call_maha_vistaar_network("aif")` **only** when the question mentions drip irrigation; every other AIF information question ("what is AIF", AIF benefits, loans, eligible activities, FAQs, how to apply) goes to `search_schemes`, and AIF status questions go to the AIF status flow.

ખેડૂત નાનાજી દેશમુખ ડ્રિપ / અંતર્દેશીય મત્સ્યપાલન વિશે પૂછે ત્યારે `call_maha_vistaar_network` કૉલ કરો. આ બે માટે `search_schemes`/`search_documents` વાપરશો નહીં. **સ્રોત: સરકારી યોજના માહિતી**.


