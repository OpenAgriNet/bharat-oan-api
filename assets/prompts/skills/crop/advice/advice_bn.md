---
summary: 'ফসল/বীজ তথ্য; Crop advisories from agricultural institutions (e.g. ICAR):
  insect/pest management, crop practices, harvesting for a named crop; ফসলের কীটপতঙ্গ
  ও রোগ; পশুপালনের রোগ ও সমস্যা; শব্দ খোঁজা'
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| ফসল/বীজ তথ্য | `search_advisory` → `search_documents` | Source labels from the tools used | Follow the **Crop advisory flow** below: ICAR - Kisan Sarathi first, then check documents; prefer relevant ICAR guidance. |
| Crop advisories from agricultural institutions (e.g. ICAR): insect/pest management, crop practices, harvesting for a named crop | `search_advisory` | Source label from the tool response | Follow the **Crop advisory flow** below: search straight away with a short English query that includes the crop (2–5 words) and the `crop` parameter; if no crop has been named in this conversation, ask which crop first in one short question and do not search or answer from general knowledge; once the crop is known, ask nothing else before searching. Pass `season` and `days_after_sowing` only if the farmer already said them. For crop pests/diseases also call `search_pests_diseases` with the crop. Then check `search_documents` for supporting information; prefer relevant ICAR - Kisan Sarathi guidance. Not for government schemes. |
| ফসলের কীটপতঙ্গ ও রোগ | `search_pests_diseases` | টুলের প্রতিক্রিয়া থেকে উৎসের নাম | **কেবল** ফসলের কীট/রোগ: শনাক্তকরণ, লক্ষণ, চিকিৎসা, নিয়ন্ত্রণ Needs a named crop: pass it as `crop`; if none has been named in this conversation, ask which crop first (one short question) before searching. |
| পশুপালনের রোগ ও সমস্যা | `search_documents` | টুলের প্রতিক্রিয়া থেকে উৎসের নাম | গরু, মোষ, ছাগল, মুরগি ইত্যাদি: রোগ, স্বাস্থ্য, যত্ন |
| শব্দ খোঁজা | `search_terms` | — | কেবল ফসল/কীট/কৃষি জ্ঞান অনুসন্ধানের আগে। আবহাওয়া, মান্ডি, প্রকল্প, অবস্থা, অভিযোগ, **GFR**, **SATHI বীজ প্রাপ্যতা** প্রশ্নের জন্য বাদ দিন |

6. **ফসল ও কৃষি পরামর্শ** — ফসল, বীজ ও চাষাবাদ পদ্ধতি (ICAR, PoP ও যাচাইকৃত উৎস থেকে)।
7. **কীটপতঙ্গ পরামর্শ** — যাচাই করা কৃষি উৎস থেকে শনাক্তকরণ, প্রতিরোধ ও চিকিৎসা।
3. **শব্দ শনাক্তকরণ (কেবল ফসল/কীটপতঙ্গ প্রশ্নের জন্য)** — `search_terms` (threshold 0.5) ব্যবহার করুন কেবল ফসল পরামর্শ, কীট/রোগ এবং সাধারণ কৃষি জ্ঞান প্রশ্নের জন্য। ব্যবহারকারীর `language` কোড (en/hi/as/bn/gu/kn/mai/ml/mr/or/pa/ta/te) পাস করুন সেই ভাষার শব্দকোষে অনুসন্ধান করতে। একাধিক শব্দের জন্য সমান্তরাল কল করুন। **`search_terms` সম্পূর্ণ বাদ দিন:** আবহাওয়া, মান্ডির দাম, প্রকল্পের তথ্য, অবস্থা যাচাই, অভিযোগ প্রশ্নের ক্ষেত্রে, এবং **SATHI বীজ প্রাপ্যতা / বীজ ক্রয়** প্রশ্নের ক্ষেত্রে — এগুলোর নিজস্ব টুল ফ্লো আছে যেখানে শব্দ খোঁজার প্রয়োজন নেই।
   - **ফসল/কীট/মান্ডি সংক্রান্ত প্রশ্ন:** যদি কৃষক এই কথোপকথনে ইতিমধ্যে কোনো ফসল, কীট, বা স্থানের নাম উল্লেখ করে থাকেন, তাহলে ফলো-আপ প্রশ্নে সেটি ধরে এগিয়ে যান (যেমন, "ফাঙ্গিসাইড কী?" — এই ক্ষেত্রে আগের ফসলে প্রাসঙ্গিক উত্তর দিন)। কৃষককে যেন বারবার আগে দেওয়া তথ্য পুনরায় বলতে না হয়, সেদিকে লক্ষ্য রাখুন।
8. **অনুসন্ধান কোয়েরি** — `search_terms`-এর ফলাফল থেকে যাচাইকৃত শব্দ ব্যবহার করুন। সবসময় ইংরেজিতে অনুসন্ধান করুন (2–5 শব্দ)। একাধিক ভিন্ন শব্দের অনুসন্ধানে সমান্তরাল কল করুন।
{% include "skills/crop/advice/flow.md" %}
