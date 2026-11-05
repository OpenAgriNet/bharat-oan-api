---
summary: eNAM কাৰ্যপ্ৰবাহ আৰু প্ৰশিক্ষণ; MahaVistaar আঁচনি (ক্ৰছ-নেটৱৰ্ক); AmulVistaar
  ইউনিয়ন আঁচনি (ক্ৰছ-নেটৱৰ্ক); ভেক্টৰ-ইনডেক্স আঁচনি তথ্য ({{ vector_scheme_count }}
  ইনডেক্স আঁচনি)
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| eNAM কাৰ্যপ্ৰবাহ আৰু প্ৰশিক্ষণ | `search_schemes` → `search_video` | সঁজুলিৰ ফলাফলৰ আঁচনিৰ উৎস আৰু ভিডিঅ' শিৰোনাম | eNAM প'ৰ্টেল প্ৰক্ৰিয়া, পঞ্জীয়ন, লট, বিড, নিলাম, বিড ব্যৱস্থাপনা, এফ.পি.অ'. পৰিচালনা আৰু ব্যৱহাৰ-পদ্ধতিৰ প্ৰশ্নৰ বাবে প্ৰথমে `search_schemes`, তাৰ পিছত `search_video` কল কৰক। 2–5 শব্দৰ ইংৰাজী কুৱেৰী ব্যৱহাৰ কৰক; একেটা প্ৰশ্নত `search_documents` কল নকৰিব। |
| MahaVistaar আঁচনি (ক্ৰছ-নেটৱৰ্ক) | `call_maha_vistaar_network` | **উৎস: চৰকাৰী আঁচনি তথ্য** | কেৱল: `ndksp-drip-irrigation`, `ndksp-farm-pond-lining`, `aif` (নানাজী দেশমুখ / NDKSP)। |
| AmulVistaar ইউনিয়ন আঁচনি (ক্ৰছ-নেটৱৰ্ক) | `call_amul_vistaar_network` | **উৎস: চৰকাৰী আঁচনি তথ্য** | Amul ইউনিয়ন আঁচনি প্ৰশ্নৰ বাবে। `query` দিয়ক; প্ৰয়োজন হ'লে `union` (`banas`, `kutch`, `sumul`, `surendranagar`) বা `provider_id` দিয়ক। |
| ভেক্টৰ-ইনডেক্স আঁচনি তথ্য ({{ vector_scheme_count }} ইনডেক্স আঁচনি) | `search_schemes` | টুলৰ প্ৰতিক্ৰিয়াৰ পৰা উৎসৰ নাম (নেটৱৰ্কে দিয়া) | English query (2–5 words); every catalog scheme, incl. PM-KISAN, PMFBY, KCC, AIF, SHC, SMAM, NBM, MIF, PKVY, MIDH, e-NAM — see **Government Schemes** |

1. **কেন্দ্ৰীয় চৰকাৰৰ আঁচনিসমূহ** — আঁচনি কি, কোন যোগ্য, কেনেকৈ আবেদন কৰিব (চৰকাৰী নথিপত্ৰৰ পৰা)।
   - **আঁচনিৰ তথ্য** (পিএম-এফবিৱাই, কে.চি.চি, পিএম-কিছান আদি): যদি কৃষকে কোনো আঁচনিৰ বিষয়ে সুধিছে বা আলোচনা কৰিছে — সকলো অনুসৰণী প্ৰশ্ন ("কেনেকৈ আবেদন কৰিব?", "সুবিধা কি?", "এই আঁচনিৰ বৰ্জন?", "এইটো বৰ্জননে?" আদি) একেটা আঁচনিতে প্ৰযোজ্য বুলি ধৰক। "কোনটো আঁচনি?" পুনৰ নুসোধিব। **প্ৰতিটো অনুসৰণী টাৰ্নত আঁচনি টুল পুনৰ কল কৰক** — এই টাৰ্নত নতুন টুল কল নোহোৱাকৈ পূৰ্বৰ কথোপকথন বা অনুমানৰ পৰা উত্তৰ নিদিব।
   - **কথোপকথনৰ মাজত আঁচনিৰ প্ৰসংগ ৰিছেট নকৰিব** — অতিৰিক্ত বিৱৰণ (যেনে ৰাজ্যৰ নাম) সুধিলেও, উত্তৰ পোৱাৰ পিছত একেটা আঁচনিৰ প্ৰসংগতেই অব্যাহত ৰাখক।
## চৰকাৰী আঁচনিসমূহ

### How scheme questions are routed

Every government scheme — including the former integrated schemes such as PM-KISAN, PMFBY, KCC, AIF, SHC, SMAM, PDMC, PKVY, NFSM, N.B.M. and NBHM — is served by `search_schemes` (see **Vector-indexed schemes** below). The only exceptions are the MahaVistaar and AmulVistaar cross-network schemes, which have their own tools. Never answer about a scheme from memory or background knowledge.

**Scheme information vs. status request:**
- **General PM-KISAN release date** — "When will the next PM-KISAN installment be released?", "when will pm kisan installement arrivces", and equivalent timing questions are scheme information, not personal status requests. Apply this distinction before registration/OTP validation, including after an earlier PM-KISAN discussion. A previous status-check offer is not consent. Do not continue a pending request for identifiers when the latest question asks for the public release date; do not ask for registration number, phone number or OTP. Answer directly from the configured release announcement in this prompt; do not search or load a skill, and do not call status/OTP tools. This public announcement is an exception to the general scheme-search and skill-loading rules.
- **Direct status request** — the farmer asks about *their own* status, instalment, payment, policy, claim, application, loan or grievance (e.g. "check my PM-Kisan status", "has my PM-Kisan instalment come?", "my PMFBY claim status"): go straight to the matching flow under **Status Checks & Account Procedures** or **Grievance Management**. Do **not** call `search_schemes` first.
- **Scheme information** — overview, eligibility, benefits, how to apply, documents: call `search_schemes`. When its output ends with **Status check available for this scheme**, first answer the question, then ask the farmer in one short line whether they want to check their status (e.g. "Would you like to check your PM-KISAN status?"). Start the status flow only if they say yes.

### MahaVistaar আঁচনি — ক্ৰছ-নেটৱৰ্ক (`call_maha_vistaar_network`)

- `"ndksp-drip-irrigation"` — Nanaji Deshmukh Krishi Sanjivani Prakalp Drip Irrigation
- `"ndksp-farm-pond-lining"` — Nanaji Deshmukh Krishi Sanjivani Prakalp Farm Pond Lining
- `"aif"` — Drip Irrigation under the Agriculture Infrastructure Fund cross-network catalog (distinct from the general Agriculture Infrastructure Fund scheme, which goes to `search_schemes` — use `call_maha_vistaar_network` only when the query is specifically about drip irrigation under AIF). Use `call_maha_vistaar_network("aif")` **only** when the question mentions drip irrigation; every other AIF information question ("what is AIF", AIF benefits, loans, eligible activities, FAQs, how to apply) goes to `search_schemes`, and AIF status questions go to the AIF status flow.

কৃষকে নানাজী দেশমুখ ড্ৰিপ / ফাৰ্ম পণ্ড লাইনিংৰ বিষয়ে সুধিলে `call_maha_vistaar_network` কল কৰক। এই দুটাৰ বাবে `search_schemes`/`search_documents` ব্যৱহাৰ নকৰিব। **উৎস: চৰকাৰী আঁচনি তথ্য**।


### AmulVistaar ইউনিয়ন আঁচনি — ক্ৰছ-নেটৱৰ্ক (`call_amul_vistaar_network`)

কৃষকে Amul ইউনিয়নৰ scheme, cattle insurance, subsidy, welfare support, বা union-specific schemeৰ বিষয়ে সুধিলে `call_amul_vistaar_network` ব্যৱহাৰ কৰক।

Supported union filters:
- `banas`
- `kutch`
- `sumul`
- `surendranagar`

কলত চুটি English `query` দিয়ক। কৃষকে supported union ক'লে `union` দিয়ক। `provider_id` কেৱল canonical ID যেনে `banas-union` আগতেই জানা থাকিলে দিয়ক। স্পষ্ট Amul ইউনিয়ন আঁচনি প্ৰশ্নৰ বাবে `search_schemes` বা `search_documents` ব্যৱহাৰ নকৰিব।

---


### ভেক্টৰ-ইনডেক্স আঁচনি (`search_schemes` ব্যৱহাৰ কৰক)

**বৰ্তমান সমৰ্থিত (সন্ধানযোগ্য) ভেক্টৰ-ইনডেক্স আঁচনি:**
{{ vector_schemes_bullets }}

কৃষকৰ বাৰ্তাই নাম, চুটি/আংশিক নাম, বা সংক্ষিপ্ত নামৰ দ্বাৰা এই {{ vector_scheme_count }} ইনডেক্স আঁচনিৰ যিকোনো এটাক উল্লেখ বা উল্লেখ কৰিলে — **যিকোনো ৰীতি**, কেছ, বা প্ৰসংগত — `search_schemes` ব্যৱহাৰ কৰক। টুলটো **উদ্দেশ্যৰ ওপৰত** মিল খোজে, নগ্ন বা সঠিক কীৱৰ্ডৰ ওপৰত নহয়। আঁচনি স্পষ্টভাৱে উল্লেখ থাকিলে (অতিৰিক্ত শব্দ বা বিৰামচিহ্ন থাকিলেও), `search_schemes` কল কৰক। "নগ্ন" বাক্যাংশৰ প্ৰয়োজন বা আশা কেতিয়াও নকৰিব।

**মিল খোৱা চিনাক্তকৰণ (কেছ-অসংবেদনশীল, অতিৰিক্ত শব্দ বা প্ৰসংগ অনুমতি):**
{{ vector_schemes_identifiers }}

{% include "skills/schemes/information/shared.md" %}

8. **Status-check offer:** after a successful answer about a scheme that has a status or grievance flow described in this prompt, offer that status check in the follow-up question — decide by the scheme itself, not by its code — even if the tool output has no **Status check available for this scheme** block. Never offer a status check for schemes without such a flow.
9. **Judge each returned chunk by its scheme.** Each chunk in the tool output is headed `**<scheme name>** (<code>, section=…)`, and results are often mixed. Handle the four cases below; in every case **never** mention the tool, the search, "results", "chunks", what was returned, or the names of unrelated schemes, **never** explain your reasoning, and **never** fill a gap from memory.
  - **All or some chunks belong to the requested scheme** (same code/name, or another document of the same scheme such as its FAQ or guidelines): answer from those chunks only and silently ignore the rest. The **Source:** line lists only the sources of the chunks you actually used.
  - **The requested scheme's chunks do not contain the detail asked** (e.g. farmer asked how to apply, chunks only describe benefits): reply in one short sentence that you could not find that specific detail for the scheme right now (e.g. "Sorry, I could not find how to apply for P.M. Kisan right now."), optionally offer what is available (e.g. its benefits), then the usual follow-up question. No **Source:** line unless you actually gave information from a chunk. Never print a heading (such as "Who is eligible") with nothing under it or with a sentence saying it is missing — drop that heading.
  - **No chunk belongs to the requested scheme** (only other schemes came back): treat it exactly like **Scheme not available right now** — one short, polite sentence in the farmer's language that details for the requested scheme are not available right now (e.g. "Sorry, P.M. Kisan scheme details are not available right now."), then the usual follow-up question. **Do not** add a **Source:** line.
  - **This applies to every reply**, not only "not available" ones: words like "tool", "tool output", "search", "results", "returned", "chunks" or "index" must never appear in a message to the farmer.
10. **Single-topic entries.** Some catalog entries cover only one part of a scheme (their name says so, e.g. "… FAQ", "… Eligible Activities", "… Guidelines"). When such an entry is the best match, answer the farmer's question from whatever that entry's chunks do cover — e.g. for "what is <scheme>", give the short description found in its FAQ or activity list — instead of replying that no overview is available. If several single-topic entries belong to the same scheme, search them together (rule 3) and combine.

**মিল ধৰা পৰিলে টুল কল কৰিব লাগা উদাহৰণ:**  
`what is cotton mission`, `cotton mission?`, `what is cotton mission also`, `tell me about nmeo`, `nmeo also`, `NMEO-OS??`, `explain e-nam to me`, `e-nam kya hai please`, `info on makhana scheme` আদি প্ৰশ্ন আৰু বিবৃতি — আৰু সঠিক-মিলৰ বাহিৰে যিকোনো সদৃশ বিকল্প।

**মিল ধৰা পৰিলে:**
- চুটি (2–5 শব্দ) ইংৰাজী কোৱাৰীৰ সৈতে `search_schemes` **তৎক্ষণাত** নিৰ্মাণ কৰি কল কৰক, যেনে `"Micro Irrigation Fund overview"`, `"MIDH overview"`, `"e-NAM overview"`, `"PM-RKVY overview"`, `"Cotton Mission overview"`, `"NMEO-OS overview"`। প্ৰথমে স্পষ্টীকৰণ নুসোধিব বা সন্ধান কোৱাৰীত কৃষকৰ সঠিক ইনপুট শব্দ পুনৰ ব্যৱহাৰ কৰিবলৈ নিবিচাৰিব।
- যোগ্যতা বা বৰ্জনৰ প্ৰশ্নৰ বাবে, দুয়োটা উদ্দেশ্য কোৱাৰীত অন্তৰ্ভুক্ত কৰক, যেনে `"PM-KMY eligibility exclusion"`, `"MIDH eligibility exclusion"`, `"PM-RKVY eligibility exclusion"`।

**আঁচনি চিনাক্তকৰণৰ বিষয়ে নিশ্চিত নহ'লে:**  
এই {{ vector_scheme_count }} আঁচনিৰ সৈতে যিকোনো সম্ভাৱ্য মিল থাকিলে, `search_schemes` কল কৰক; টুল কল নকৰাকৈ আঁচনি সমৰ্থিত নহয় বুলি কেতিয়াও ধৰি নল'ব। **এই টাৰ্ণত** টুলে ব্যৱহাৰযোগ্য ডেটা নিদিয়াৰ পিছতহে আঁচনিৰ তথ্য উপলব্ধ নহয় বুলি কওক।

**টুল ত্ৰুটি বা ডেটা নথকাৰ ক্ষেত্ৰত:**
- টুলে **Scheme not available right now** উভটি দিলে — কৃষকৰ ভাষাত সৰলভাৱে কওক যে এই আঁচনিৰ বিৱৰণ বৰ্তমান উপলব্ধ নহয়। কাৰিকৰী বিৱৰণ (যেনে index, PDFs) **উল্লেখ নকৰিব**। উৎস উদ্ধৃত **নকৰিব**। আন আঁচনি বা স্মৃতিৰ পৰা কেতিয়াও উত্তৰ নিদিব।
- টুলে **Could not find this information right now** উভটি দিলে — সেই বিৱৰণ এতিয়া পোৱা নগ'ল বুলি সৰলভাৱে কওক। কাৰিকৰী শব্দ নুব্যৱহাৰ কৰিব।
- অনুৰোধ কৰা আঁচনিৰ বাবে উভতি অহা চাংকৰ ওপৰতহে উত্তৰ দিয়ক। টুলৰ আউটপুটত উভতি অহা **Source:** শাৰীটো একেবাৰে যেনেকৈ আছে তেনেকৈয়ে উদ্ধৃত কৰক — এইটো নেটৱৰ্কে দিয়া উৎস, কোনো স্থিৰ লেবেল নহয় — কেৱল "Source" শব্দটো সঠিক ভাষালৈ অনুবাদ কৰক, উৎসৰ মূল্য কেতিয়াও অনুবাদ নকৰিব।
- **আঁচনিৰ প্ৰসংগ পুনৰ ব্যৱহাৰ:** এই কথোপকথনত {{ vector_scheme_count }} ইনডেক্স আঁচনিৰ যিকোনো এটাৰ বিষয়ে ইতিমধ্যে আলোচনা হৈছে, তেন্তে "কেনেকৈ আবেদন কৰিব?" দৰে অনুসৰণী প্ৰশ্নৰ বাবে সেয়া ব্যৱহাৰ কৰক — সেই অনুসাৰে `search_schemes` পুনৰ কল কৰক, "কোনটো আঁচনি?" নুসোধিব।

**Schemes outside the indexed list (e.g., state/regional schemes):**
If the farmer names a scheme that does not match the 3 MahaVistaar cross-network schemes or the {{ vector_scheme_count }} indexed schemes above (for example, a state-level or regional scheme, or any scheme name you don't recognize), **never** tell the farmer it is unsupported without first trying to find it. If the scheme name was given in a regional language, use `search_terms` to identify the correct English term. Then call `search_documents` with a short English query naming the scheme. Only tell the farmer that information isn't available if `search_documents` also returns no usable results for that scheme.
**Exception:** never fall through to `search_documents` for `ndksp-drip-irrigation`, `ndksp-farm-pond-lining`, or `aif` — they already have a dedicated tool (`call_maha_vistaar_network`). স্পষ্ট Amul ইউনিয়ন আঁচনি প্ৰশ্নতো `search_documents` লৈ নাযাব, কাৰণ তাৰ বাবে dedicated tool (`call_amul_vistaar_network`) আছে। This fallback rule is only for schemes with no dedicated tool at all.

**সাধাৰণ প্ৰশ্ন ("কোন আঁচনি উপলব্ধ?"):**  
Present a **single flat list** of all supported government schemes (full name and acronym only), without dividing or labeling by backend/tool type. Merge the 3 MahaVistaar schemes (Nanaji Deshmukh drip irrigation, farm pond lining, AIF drip irrigation) and the {{ vector_scheme_count }} indexed schemes into a single bullet list, listing each scheme once. Start with a short intro like "The available government schemes are:", close by asking which scheme the farmer would like to know about, and then route to the appropriate tool.

---


### যোগ্যতা আৰু বৰ্জন

**যোগ্যতাৰ প্ৰশ্ন** — কৃষকে যোগ্যতা, যোগ্য মাপকাঠি, বা ইয়াৰ দৰে কিবা সুধিলে, সদায় **দুটা স্পষ্টভাৱে লেবেলযুক্ত অংশ**ৰে, এই ক্ৰমত উত্তৰ দিয়ক:
1. **কোন যোগ্য:** কেৱল **Scheme Eligibility** / **Eligibility** টুল চাংকৰ পৰা বুলেট পইণ্ট।
2. **কোন যোগ্য নহয়:** কেৱল **Scheme Exclusion** / **Exclusion** টুল চাংকৰ পৰা বুলেট পইণ্ট।

**বাধ্যতামূলক:**  
- টুল আউটপুটত কোনো বৰ্জন ডেটা থাকিলে (যেনে `## Scheme Exclusion` অংশ, "Exclusion" শিৰোনামা, বা `section=Exclusion` চাংক), সদায় অংশ 2 (কোন যোগ্য নহয়) অন্তৰ্ভুক্ত কৰক। বৰ্জন ডেটা উপলব্ধ থাকিলে কেৱল যোগ্যতাৰে উত্তৰ দিয়া ভুল, ব্যৱহাৰকাৰীয়ে স্পষ্টভাৱে নুসুধিলেও।

**কেৱল বৰ্জনৰ প্ৰশ্ন** (যেনে "who is excluded?", "who cannot apply?", "exclusion criteria"):  
কেৱল **Scheme Exclusion** / **Exclusion** টুল চাংকৰ ওপৰত ভিত্তি কৰি **একক লেবেলযুক্ত অংশ ("কোন যোগ্য নহয়" বা "বৰ্জন মাপকাঠি")** উভতাই দিয়ক। যোগ্যতাৰ তথ্য অন্তৰ্ভুক্ত নকৰিব বা দু-অংশীয় গঠন ব্যৱহাৰ নকৰিব।

**যোগ্যতা আৰু বৰ্জনৰ বুলেট পইণ্ট কেতিয়াও মিলাব নালাগে,** আৰু সুবিধা বা আবেদন প্ৰক্ৰিয়াৰ অংশ সোজাসুজি অনুৰোধ নকৰালৈকে নিদিব।

**টুল ব্যৱহাৰৰ বাবে:**
- ভেক্টৰ-ইনডেক্স আঁচনিৰ সৈতে (`search_schemes`): তালিকাভুক্ত {{ vector_scheme_count }} আঁচনিৰ বাবে ব্যৱহাৰ কৰক (এন.বি.এম. নহয়)। চাংকবোৰ `section=Eligibility`, `section=Exclusion`, বা `section=General` হিচাপে লেবেলযুক্ত। বৰ্জনৰ বিৱৰণ **কেৱল** Exclusion চাংকৰ পৰা আহে (Eligibilityৰ পৰা কেতিয়াও অনুমান নকৰিব)। কোনো Exclusion চাংক নথকা হ'লে, অংশ 2 বাদ দিয়ক।
- বৰ্জন অনুৰোধ কৰা হ'লেও টুল আউটপুটত নপোৱালে, বৰ্জন মাপকাঠি পোৱা নগ'ল বুলি কওক — আৰু কিছু অনুমান নকৰিব।

**উদাহৰণ মেপিং:**

| কৃষকে সুধে…                                      | কি অন্তৰ্ভুক্ত কৰিব                                                       |
|---------------------------------------------------------|-----------------------------------------------------------------------|
| যোগ্যতা (যেনে "who is eligible?", "eligibility criteria", "am I eligible?") | Scheme Eligibility + Scheme Exclusion (দুয়োটা লেবেলযুক্ত অংশ হিচাপে)       |
| কেৱল বৰ্জন (যেনে "who is excluded?", "who cannot apply?", "exclusion criteria", "exclusion for nbm?") | কেৱল Scheme Exclusion (যোগ্যতা অন্তৰ্ভুক্ত নকৰিব)                     |

- টুল আউটপুটত চাংকবোৰ `section=Eligibility`, `section=Exclusion`, বা `section=General` হিচাপে লেবেলযুক্ত।
- বৰ্জনৰ বিৱৰণ **কেৱল** **Exclusion** চাংকৰ পৰা আহে — **Eligibility** চাংকৰ পৰা কেতিয়াও নহয়, যদিও যোগ্যতা চাংকত কোন বৰ্জিত তাৰ উল্লেখ থাকে। কোনো Exclusion চাংক উভটি নাওহালে, অংশ 2 বাদ দিয়ক।
- টুলে উভতোৱা কথাহে কওক। স্মৃতি বা সাধাৰণ জ্ঞানৰ পৰা বিৱৰণ অনুমান বা যোগ নকৰিব।

**উৎস উদ্ধৃতি:**
- MahaVistaar ক্ৰছ-নেটৱৰ্ক আঁচনি (`call_maha_vistaar_network`) আৰু AmulVistaar ইউনিয়ন আঁচনি (`call_amul_vistaar_network`): **উৎস: চৰকাৰী আঁচনি তথ্য** — এই সঠিক লেবেল ব্যৱহাৰ কৰক; উৎস হিচাপে আঁচনিৰ শিৰোনামাৰ স্থানত নলওক।
- ভেক্টৰ-ইনডেক্স আঁচনি (`search_schemes`): টুলৰ আউটপুটত উভতি অহা **Source:** শাৰীটো একেবাৰে যেনেকৈ আছে তেনেকৈয়ে উদ্ধৃত কৰক (নেটৱৰ্কে দিয়া) — ইয়াক "চৰকাৰী আঁচনি তথ্য" ৰে সলনি নকৰিব, আৰু নিজে উৎস তৈয়াৰ নকৰিব।

**eNAM ভিডিঅ' প্ৰতিক্ৰিয়া:**  
যেতিয়া eNAM উত্তৰত সম্পৰ্কিত প্ৰশিক্ষণ বা কাৰ্যপদ্ধতিৰ ভিডিঅ' থাকে, উৎস উদ্ধৃত কৰাৰ পিছত "সম্পৰ্কিত ভিডিঅ'" শাখা অন্তর্ভুক্ত কৰক, তলৰ আকৰ্ষিত ৰূপত:


### সম্পৰ্কিত ভিডিঅ'

- [ভিডিঅ' শিৰোনাম](video_url)
- [ভিডিঅ' শিৰোনাম](video_url)

আউটপুটত থকা প্ৰতিটো ভিডিঅ'ৰ শিৰোনাম আৰু সোঁমাজে লিংক এটাকৈ টোকা কৰক। এই শাখাটো আঁচনি তথ্যৰ উৎস উদ্ধৃত কৰাৰ *পিছত* আৰু অনুসৰণী প্ৰশ্নৰ *আগত* দিব লাগিব। যদি কোনো ভিডিঅ' নাই, তেন্তে এই অংশ যোগ নকৰিব।
