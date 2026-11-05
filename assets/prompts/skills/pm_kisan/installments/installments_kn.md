---
summary: PM-KISAN public installment release date; no personal status check or OTP.
---
**PM-KISAN 24th installment release date:** For a general release-date question such as "When will the next PM-KISAN installment be released?", answer directly from the configured announcement below. Do not call search_schemes, load_skill or any status/OTP tool for this question. No registration number or OTP is needed; do not ask for a phone number either. This public announcement is not a tool workflow and is an exception to the general scheme-search and skill-loading rules. A prior status-check offer or pending request for identifiers does not change the intent of the farmer's latest release-date question. Use past tense for this released installment. Use the matching English or Hindi answer exactly; translate it into the selected language otherwise. Preserve the date and tense; do not invent a disbursement location.

{% include "skills/pm_kisan/installments/release.md" %}

Cite **Source: Government Scheme Information**. For this release-date answer, do not add a status-check offer or a follow-up question. The announcement does not establish that the farmer has received payment.

**Personal PM-KISAN installment status:** For "PM Kisan installment status", "has my installment been credited?", or questions about the farmer's own payment or amount, follow the **PM-Kisan Status** flow (`initiate_pm_kisan_status_check` → `check_pm_kisan_status_with_otp`). Ask only for a missing registration number first, then verify the OTP. Answer only from the status tool output; the general release announcement does not establish that the farmer has received payment.
