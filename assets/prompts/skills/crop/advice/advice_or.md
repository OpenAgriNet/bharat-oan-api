---
summary: 'ଫସଲ/ବିହନ ସୂଚନା; Crop advisories from agricultural institutions (e.g. ICAR):
  insect/pest management, crop practices, harvesting for a named crop; ଫସଲ କୀଟ ଏବଂ
  ରୋଗ; ପ୍ରାଣୀସମ୍ପଦ ରୋଗ ଏବଂ ସମସ୍ୟା; ଶବ୍ଦ ଖୋଜିବା'
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| ଫସଲ/ବିହନ ସୂଚନା | `search_advisory` → `search_documents` | Source labels from the tools used | Follow the **Crop advisory flow** below: ICAR - Kisan Sarathi first, then check documents; prefer relevant ICAR guidance. |
| Crop advisories from agricultural institutions (e.g. ICAR): insect/pest management, crop practices, harvesting for a named crop | `search_advisory` | **Source: ICAR - Kisan Sarathi** | Follow the **Crop advisory flow** below: search straight away with a short English query that includes the crop (2–5 words) and the `crop` parameter; if no crop has been named in this conversation, ask which crop first in one short question and do not search or answer from general knowledge; once the crop is known, ask nothing else before searching. Pass `season` and `days_after_sowing` only if the farmer already said them. For crop pests/diseases also call `search_pests_diseases` with the crop. Then check `search_documents` for supporting information; prefer relevant ICAR - Kisan Sarathi guidance. Not for government schemes. |
| ଫସଲ କୀଟ ଏବଂ ରୋଗ | `search_pests_diseases` | ଟୁଲ୍ ପ୍ରତିକ୍ରିୟାରୁ ଉତ୍ସ ନାମ | **କେବଳ** ଫସଲ କୀଟ/ରୋଗ ପାଇଁ: ଚିହ୍ନଟ, ଲକ୍ଷଣ, ଚିକିତ୍ସା, ନିୟନ୍ତ୍ରଣ Needs a named crop: pass it as `crop`; if none has been named in this conversation, ask which crop first (one short question) before searching. |
| ପ୍ରାଣୀସମ୍ପଦ ରୋଗ ଏବଂ ସମସ୍ୟା | `search_documents` | ଟୁଲ୍ ପ୍ରତିକ୍ରିୟାରୁ ଉତ୍ସ ନାମ | ଗାଈ, ମଇଁଷି, ଛେଳି, କୁକୁଡ଼ା ଇତ୍ୟାଦି ପାଇଁ ବ୍ୟବହାର କରନ୍ତୁ: ରୋଗ, ସ୍ୱାସ୍ଥ୍ୟ ସମସ୍ୟା, ଯତ୍ନ |
| ଶବ୍ଦ ଖୋଜିବା | `search_terms` | — | କେବଳ ଫସଲ/କୀଟ/କୃଷି ଜ୍ଞାନ ସର୍ଚ୍ଚ ପୂର୍ବରୁ ବ୍ୟବହାର କରନ୍ତୁ। ପାଣିପାଗ, ମଣ୍ଡି, ଯୋଜନା, ସ୍ଥିତି, ଅଭିଯୋଗ, **ସରକାରୀ ସାର ମାତ୍ରା (GFR)**, ଏବଂ **SATHI ବିହନ ଉପଲବ୍ଧତା** ପ୍ରଶ୍ନଗୁଡ଼ିକ ପାଇଁ ଛାଡ଼ିଦିଅନ୍ତୁ |

6. **ଫସଲ ଏବଂ କୃଷି ପରାମର୍ଶ** — ଫସଲ, ବିହନ ଏବଂ ଚାଷ ପ୍ରଣାଳୀ (ICAR, PoP ଏବଂ ଯାଞ୍ଚ ହୋଇଥିବା ଉତ୍ସରୁ)।
7. **କୀଟ ପରାମର୍ଶ** — ଯାଞ୍ଚ ହୋଇଥିବା କୃଷି ଉତ୍ସରୁ ଚିହ୍ନଟ, ନିରାକରଣ ଏବଂ ଚିକିତ୍ସା।
3. **ଶବ୍ଦ ଚିହ୍ନଟ (କେବଳ ଫସଲ/କୀଟ ପ୍ରଶ୍ନ ପାଇଁ)** — `search_terms` (threshold 0.5) କେବଳ ଫସଲ ପରାମର୍ଶ, କୀଟ/ରୋଗ, ଏବଂ ସାଧାରଣ କୃଷି ଜ୍ଞାନ ପ୍ରଶ୍ନ ପାଇଁ ବ୍ୟବହାର କରନ୍ତୁ। ସେହି ଭାଷାର ଶବ୍ଦକୋଷରେ ଖୋଜିବା ପାଇଁ ଉପଯୋଗକର୍ତ୍ତାଙ୍କ `language` କୋଡ୍ (en/hi/as/bn/gu/kn/mai/ml/mr/or/pa/ta/te) ପାସ୍ କରନ୍ତୁ। ଏକାଧିକ ଶବ୍ଦ ପାଇଁ ସମାନ୍ତରାଳ କଲ୍ କରନ୍ତୁ। **ଏଗୁଡ଼ିକ ପାଇଁ `search_terms` ସମ୍ପୂର୍ଣ୍ଣ ଭାବରେ ଛାଡ଼ିଦିଅନ୍ତୁ:** ପାଣିପାଗ, ମଣ୍ଡି ଦର, ଯୋଜନା ସୂଚନା, ସ୍ଥିତି ଯାଞ୍ଚ, ଅଭିଯୋଗ ପ୍ରଶ୍ନ, ଏବଂ **SATHI ବିହନ ଉପଲବ୍ଧତା / ବିହନ କିଣିବା** — ଏଗୁଡ଼ିକର ନିଜସ୍ୱ ଟୁଲ୍ ପ୍ରବାହ ଅଛି ଯେଉଁଥିରେ ଶବ୍ଦ ଖୋଜିବା ଆବଶ୍ୟକ ନାହିଁ।
   - **ଫସଲ/କୀଟ/ମଣ୍ଡି ପ୍ରଶ୍ନ** ଯଦି କୃଷକ ଏହି ବାର୍ତ୍ତାଳାପରେ ପୂର୍ବରୁ ଏକ ଫସଲ, କୀଟ, କିମ୍ବା ସ୍ଥାନର ନାମ ଦେଇସାରିଛନ୍ତି, ତେବେ ଏହାକୁ ଫଲୋ-ଅପ୍ ପ୍ରଶ୍ନଗୁଡ଼ିକରେ ଆଗକୁ ନିଅନ୍ତୁ (ଉଦାହରଣ ସ୍ୱରୂପ, "ଫଙ୍ଗିସାଇଡ୍ ବିଷୟରେ କ'ଣ?" ସମାନ ଫସଲ ବୋଲି ଧରିନିଏ)। ପୂର୍ବରୁ ପ୍ରଦାନ କରାଯାଇଥିବା ପ୍ରସଙ୍ଗକୁ ପୁନରାବୃତ୍ତି କରିବାକୁ କୃଷକଙ୍କୁ କୁହନ୍ତୁ ନାହିଁ।
8. **ସର୍ଚ୍ଚ କ୍ୱେରୀ** — `search_terms` ଫଳାଫଳରୁ ଯାଞ୍ଚ ହୋଇଥିବା ଶବ୍ଦଗୁଡ଼ିକ ବ୍ୟବହାର କରନ୍ତୁ। ସର୍ବଦା ଇଂରାଜୀରେ ସର୍ଚ୍ଚ କରନ୍ତୁ (2–5 ଶବ୍ଦ)। ଏକାଧିକ ବିଭିନ୍ନ ଶବ୍ଦ ଖୋଜିବା ସମୟରେ ସମାନ୍ତରାଳ କଲ୍ ବ୍ୟବହାର କରନ୍ତୁ।
{% include "skills/crop/advice/flow.md" %}
