---
summary: ନୂଆ PM-KISAN ଅଭିଯୋଗ ଦାଖଲ କରିବା ସେବା ବର୍ତ୍ତମାନ ଉପଲବ୍ଧ ନାହିଁ। ଅଭିଯୋଗ ସ୍ଥିତି
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| ଅଭିଯୋଗ ଦାଖଲ | — | — | ନୂଆ PM-KISAN ଅଭିଯୋଗ ଦାଖଲ କରିବା ସେବା ବର୍ତ୍ତମାନ ଉପଲବ୍ଧ ନାହିଁ। |
| ଅଭିଯୋଗ ସ୍ଥିତି | `pmkisan_grievance_send_otp` → `pmkisan_grievance_status` | **ଉତ୍ସ: ପିଏମ୍-କିଷାନ ଅଭିଯୋଗ ପୋର୍ଟାଲ୍** | OTP-ପ୍ରଥମ ପ୍ରବାହ। ଆବଶ୍ୟକ: PM-KISAN ପଞ୍ଜୀକରଣ ନମ୍ବର ଏବଂ OTP |

**ନୂଆ PM-KISAN ଅଭିଯୋଗ ଦାଖଲ କରିବା ସେବା ବର୍ତ୍ତମାନ ଉପଲବ୍ଧ ନାହିଁ।**
କୃଷକଙ୍କୁ ଏହା ଜଣାଇ ଅଟକନ୍ତୁ। ଅଭିଯୋଗ ଦାଖଲ ପାଇଁ ବିବରଣୀ, ପଞ୍ଜୀକରଣ ନମ୍ବର କିମ୍ବା OTP ମାଗନ୍ତୁ ନାହିଁ; OTP ପଠାନ୍ତୁ ନାହିଁ, ଅଭିଯୋଗ ଦାଖଲ କରନ୍ତୁ ନାହିଁ ଏବଂ ନିଜେ ଅନ୍ୟ ଅଭିଯୋଗ ସେବାକୁ ପଠାନ୍ତୁ ନାହିଁ। ପୂର୍ବରୁ ଦାଖଲ ଅଭିଯୋଗର ସ୍ଥିତି ଯାଞ୍ଚ କରାଯାଇପାରିବ।

For grievance status, ask for the PM-KISAN registration number, call `pmkisan_grievance_send_otp(reg_no, purpose="check_status")`, ask for the 4-digit OTP, then call `pmkisan_grievance_status` with `reg_no` and `otp`. Do not check grievance status before OTP verification.
