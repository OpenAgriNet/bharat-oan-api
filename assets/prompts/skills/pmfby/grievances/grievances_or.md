---
summary: 'PMFBY: PMFBY ଅଭିଯୋଗ ସ୍ଥିତି'
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| PMFBY ଅଭିଯୋଗ ସ୍ଥିତି | `pmfby_grievance_status` | **ଉତ୍ସ: ପିଏମ୍ଏଫ୍ବିୱାଇ (PMFBY) ଅଭିଯୋଗ ପୋର୍ଟାଲ୍** | ଆବଶ୍ୟକ: ପଞ୍ଜୀକୃତ ମୋବାଇଲ୍ + ଅଭିଯୋଗ ସହାୟତା ଟିକେଟ୍ ନମ୍ବର |

**PMFBY grievances{% if 'khc.grievances' in enabled_skills %} (default unless KHC was explicitly requested){% endif %}:**

*File a new grievance:*
1. Ask registered mobile number → `initiate_pmfby_grievance_otp(phone_number)`
2. Ask for 6-digit OTP (never echo digits) → `check_pmfby_grievance_otp(otp, phone_number)`
3. Collect: PMFBY application number, **which season and year** (request season + request year), and **what is the complaint** (grievance description)
4. Submit → `pmfby_submit_grievance(otp, phone_number, request_year, request_season, application_no, grievance_description)`

*Track an existing PMFBY grievance:*
1. Ask for **both** registered mobile number and grievance support ticket number (either order is fine).
2. **Do not call** `pmfby_grievance_status` until you have **both** values.
3. **Classify each reply:** exactly **10 digits** → mobile (`phone_number`); **longer numeric string** (e.g. 12–15 digits) → ticket (`grievance_support_ticket_no`). If the farmer sends only the ticket, acknowledge it and ask **only** for the missing mobile — **never** pass the ticket as `phone_number`.
4. When both are known → `pmfby_grievance_status(phone_number, grievance_support_ticket_no)`.

### PMFBY ଅଭିଯୋଗର ଯାଞ୍ଚ

- ପଞ୍ଜୀକୃତ 10 ଅଙ୍କର ମୋବାଇଲ୍ ନମ୍ବର ମିଳିଲେ ସେହି ଉତ୍ତରରେ `initiate_pmfby_grievance_otp(phone_number)` କଲ୍ କରନ୍ତୁ। ଟୁଲ୍ ସଫଳତା ଜଣାଇବା ପରେ ହିଁ OTP ପଠାଯାଇଛି ବୋଲି କୁହନ୍ତୁ।
- ଏହାପରେ ଚାଷୀ 6 ଅଙ୍କର OTP ଦେଲେ `check_pmfby_grievance_otp(otp, phone_number)` କଲ୍ କରନ୍ତୁ। ଟୁଲ୍ ଯାଞ୍ଚ ସଫଳ ହୋଇଛି ବୋଲି ଜଣାଇଲେ ହିଁ OTP ଯାଞ୍ଚ ହୋଇଛି ବୋଲି କୁହନ୍ତୁ।
- ଯାଞ୍ଚ ସଫଳ ହେବା ପରେ ହିଁ ଆବେଦନ ନମ୍ବର, ଋତୁ, ବର୍ଷ ଏବଂ ଅଭିଯୋଗର ବିବରଣୀ ନିଅନ୍ତୁ। ତାପରେ `pmfby_submit_grievance(otp, phone_number, request_year, request_season, application_no, grievance_description)` କଲ୍ କରନ୍ତୁ।
- 10 ଅଙ୍କ ମୋବାଇଲ୍ ନମ୍ବର; 6 ଅଙ୍କ OTP। ମୋବାଇଲ୍ ବଦଳରେ 6 ଅଙ୍କ ମିଳିଲେ ପଞ୍ଜୀକୃତ 10 ଅଙ୍କର ନମ୍ବର ପୁଣି ପଚାରନ୍ତୁ। OTPର ଅଙ୍କ କେବେ ଦୋହରାନ୍ତୁ ନାହିଁ।
- ଅଭିଯୋଗ ଗ୍ରହଣର ଉତ୍ସ ପରିଚୟ ପ୍ରଣାଳୀ ନିଜେ ନିର୍ଦ୍ଧାରଣ କରେ। ଚାଷୀଙ୍କୁ ଏହି ପରିଚୟ କେବେ ପଚାରନ୍ତୁ ନାହିଁ।
