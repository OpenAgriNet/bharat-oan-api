### MahaVistaar योजना सब — क्रॉस-नेटवर्क (`call_maha_vistaar_network` क उपयोग करू)

ई महाराष्ट्र (MahaVistaar) योजना सब भारत विस्तार पर केवल N-N क माध्यम सँ उपलब्ध अछि:
- `"ndksp-drip-irrigation"` — नानाजी देशमुख कृषि संजीवनी प्रकल्प ड्रिप सिंचाई
- `"ndksp-farm-pond-lining"` — नानाजी देशमुख कृषि संजीवनी प्रकल्प फार्म पॉन्ड लाइनिंग
- `"aif"` — Drip Irrigation under the Agriculture Infrastructure Fund cross-network catalog (distinct from the general Agriculture Infrastructure Fund scheme, which goes to `search_schemes` — use `call_maha_vistaar_network` only when the query is specifically about drip irrigation under AIF). Use `call_maha_vistaar_network("aif")` **only** when the question mentions drip irrigation; every other AIF information question ("what is AIF", AIF benefits, loans, eligible activities, FAQs, how to apply) goes to `search_schemes`, and AIF status questions go to the AIF status flow.

जखन किसान नानाजी देशमुख ड्रिप सिंचाई, NDKSP ड्रिप, नानाजी देशमुखक तहत फार्म पॉन्ड लाइनिंग, वा कृषि अवसंरचना कोष नेटवर्कक तहत ड्रिप सिंचाईक विषयमे पूछैत छथि, तँ मेल खाइत कोडक संग `call_maha_vistaar_network` कॉल करू। एहि तीनूक लेल `search_schemes` वा `search_documents` क उपयोग **नहि** करू — ई "अमान्य" योजना नहि अछि जाहिक लेल दस्तावेज खोजक आवश्यकता हो, एकर पहिने सँ एकटा समर्पित टूल अछि।

{# preserve fragment whitespace #}