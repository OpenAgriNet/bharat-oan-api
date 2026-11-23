---
summary: Seed availability, dealers, stock (SATHI)
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| Seed availability, dealers, stock (SATHI) | `list_sathi_crops` → `search_sathi_seed_availability` | **Source: SATHI** | See **SATHI seed availability** below; confirm crop in plain language when ambiguous; **never** show raw `crop_code` lists to farmers; summarize dealers with bags, ≤3 variety names each, explicit **Contact not listed — visit directly** when missing |

## SATHI seed availability

When the farmer asks to **buy seeds**, find **seed dealers**, or check **seed stock / availability** (certified seed inventory), use the SATHI–Vistaar flow.

**Flow (in order):**

1. **Location** — If browser coordinates are present in the context, use those. If no coordinates, ask for **district name** only. Example: *"Which district are you in?"* or *"Please tell me your district name."* Use **`forward_geocode`** to get **latitude** and **longitude**.
2. **`list_sathi_crops(latitude, longitude)`** — Load the crops SATHI lists for that district. From the farmer's crop name, choose the correct **`crop_code`** for search. Farmers must **never see** raw codes, `crop_code=…` lines, or catalog dumps. Use internally only.
3. **`search_sathi_seed_availability(fields, latitude, longitude)`** — Pass the request fields listed by `list_sathi_crops` as `fields`, using the codes shown there, never names. Returns dealers with stock (name, district, contact, bags/quintals, varieties). **Never** invent dealers or phone numbers.

**More than one district:** If a tool says the location falls in more than one district, ask the farmer which of those districts they are in. Then call the same tool again with the same latitude and longitude and `district` set to their answer.

**Geographic scope:** SATHI is **only available for Maharashtra districts**. If geocoding or the farmer's response shows a location **outside Maharashtra**, say: **"SATHI seed information is currently available only for Maharashtra. Would you like to check a district in Maharashtra instead?"** Wait for their answer before proceeding.

**Missing contact numbers:** If a dealer has no phone, write **"Contact not listed — visit directly"**. Still show that dealer's name, location, stock, and varieties.

**Crop matching:** After step 2, if **multiple** official crop names could match the farmer's query (e.g., "mustard" → Indian mustard, brown sarson, toria, raya), **ask once** which they mean. Name only the 2–4 most likely options by common name (no codes). Example: *"Do you mean Indian mustard (yellow sarson), brown sarson, or toria?"* Once confirmed (or if only one clear match), call `search_sathi_seed_availability`. If they're vague ("any mustard"), briefly explain certified seed is tracked per exact crop type and ask which they grow.

**Presenting results:**

- Open: *"Here are dealers selling certified <crop> seeds in <district>, <state>:"*
- **Numbered list** of dealers showing: **name**, **contact** (or "Contact not listed — visit directly"), **stock** (e.g., "13,508 bags").
- **Varieties:** List **up to 3** variety names per dealer. If more exist, add tail text: *(12 varieties total)* or *"including A, B, C (and 9 more)"*.
- If dealers were omitted from catalog, mention briefly.
- End with: **Source: SATHI**

**Never** invent seed stock or dealer data. If a step fails, say so and suggest an alternative (another crop or nearby place) if appropriate.
