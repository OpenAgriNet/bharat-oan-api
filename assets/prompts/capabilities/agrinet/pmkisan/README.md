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

The shared installment section is included in all language prompts and loads
with `search_schemes` under the `schemes` capability, not the personal-status
`pmkisan` capability. It distinguishes the general release date from a farmer's
payment status; responses still use the farmer's selected language.
