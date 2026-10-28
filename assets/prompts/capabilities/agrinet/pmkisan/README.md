# pmkisan

## Tools

- `check_pm_kisan_status_with_otp`
- `initiate_pm_kisan_status_check`
- `pmkisan_grievance_send_otp`
- `pmkisan_grievance_status`
- `pmkisan_submit_grievance`

## Prompt sections

- [instalments](instalments/)
- [status](status/)
- [validation](validation/)

## Shared sections loaded by this capability

- [grievances](../_shared/grievances/)

The section manifest preserves existing loading conditions. References to other
workflows inside an instruction do not automatically enable their tools.

The shared installment section is included unconditionally in all language
prompts. Public release-date questions use its configured announcement directly,
without loading a capability or calling search/status/OTP tools. Personal payment
status still uses the existing status flow. Responses use the selected language.
