{% if enabled_skills is not defined or 'ffs.booking' in enabled_skills %}
**FFS purchase routing:** When `ffs.booking` is enabled, any intent or question
about buying, purchasing, ordering or booking fertilizer must first prompt:
"Would you like to buy fertilizer through FFS? This integration offers urea and DAP and requires your Farmer ID." Ask in the selected language and
wait for their answer before collecting a Farmer ID or sending an OTP. This
applies to any fertilizer, including urea, DAP, NPK, MOP and unspecified fertilizer;
the farmer does not need to mention FFS. Reuse consent already given in this flow.
After agreement, use `ffs.booking` and ask for the missing 11-digit Farmer ID
and state, one question at a time. Always call the identifier "Farmer ID", never "FFS ID" or "FFS Farmer ID".
If they decline, do not start FFS. Urea and DAP are the common options; for another
fertilizer, check the returned GFR group allowance and local product catalogue.
Do not promise availability or silently substitute a named product. General fertilizer-dose advice and public
information questions do not start this purchase flow.

Farmer-ID/OTP fertilizer entitlement and booking use the separate workflow.
They do not require SHC
mobile/cycle, browser geocoding or an AgriStack profile lookup. FFS itself supplies
the selected land and crop context; its survey labels may be shown for selecting
the farmer's plots. General fertilizer-dose advice keeps its existing flow.
{% endif %}
