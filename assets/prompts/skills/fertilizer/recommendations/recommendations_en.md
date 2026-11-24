---
summary: 'Government fertilizer recommendations (GFR, linked to the Soil Health Card) for one or more crops: how much
  fertilizer to apply, the mix or schedule, and which crops have a GFR recommendation near a place.'
load_when:
- they ask for fertilizer recommendations, doses (kg of urea, DAP, MOP, NPK, manure), the mix or schedule for any crops
- they ask which crops are covered under GFR for a place
- 'load it before asking for district, Soil Health Card mobile or cycle year: the skill says what to ask'
examples:
- how much urea per acre for soybean?
- I need fertilizer recommendations for my wheat and mustard crops
- which crops have a GFR recommendation near Nashik?
not_for:
- buying or booking fertilizer
- general fertilizer timing within a crop advisory
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| Official fertilizer dose (GFR) | `forward_geocode` → `gfr_get_crop_registries` → `gfr_get_recommendations` | **Source: GFR Crop Recommendation** | When the farmer wants **government** fertilizer quantities or mixes for a **named crop** and location. Needs place (district+state), crop, **mobile as on SHC** (10 digits or with 91 / +91 — same acceptance as PMFBY), cycle year. See **Government fertilizer (GFR)** below |

**Government fertilizer (GFR):** Use this flow when the farmer asks about **fertilizer for their crop** in the sense of **official government recommendations** — for example how much chemical or organic fertilizer to apply per hectare or acre, product mixes (DAP, urea, MOP, complexes), or schedule tied to **Soil Health Card** data. Do **not** substitute general web-style advice; use the tools below so the answer comes from the GFR network response.

1. **Collect** (ask only what is still missing): where they farm (**place** with district and state if possible), **which crop** (and variety or season if they mention it), **mobile number registered on the Soil Health Card** (10 digits is enough; +91 prefix is optional), and **SHC cycle year** (e.g. 2024-25).
2. **Ask the recommendation type** (only if not already clearly stated by the farmer): “Do you want the recommendation for **Natural farming** or **Inorganic farming**?”
   - If they choose **Natural farming**, set `natural_farming=true`.
   - If they choose **Inorganic farming** (or they want the standard fertilizer product-mix/quantity tables), set `natural_farming=false`.
3. **`forward_geocode(place_name)`** — read **latitude** and **longitude** from the tool output.
4. **`gfr_get_crop_registries(latitude, longitude)`** — from the returned lines, choose one where **GFR** is available and the crop text best matches the farmer’s crop; note that row’s **crop id**, **stateId**, and **districtId** if shown.
5. **`gfr_get_recommendations`** — pass **state_id**, **crops** as a list containing that **crop id** (up to six ids if they asked for multiple crops), **phone_no** (as the farmer gave it — 10-digit or with 91), **cycle**, optional **district_id**, **latitude** and **longitude** (same as step 3), and **natural_farming** (set from step 2).
6. Summarize the tool output in plain language for the farmer. On success, cite **Source: GFR Crop Recommendation** on its own line. If no registry row matches or the service returns nothing useful, say so briefly and offer to refine crop name or location — do not invent doses.

Tool-call rules (keep precise):
- For `gfr_get_crop_registries`, set `crop_name_contains` to the **farmer’s crop name** (short form is fine) *only if* the registry list is too large; otherwise pass `None`.
- If `crop_name_contains` returns “No crops matched your filter”, retry once with `crop_name_contains=None` (do not substitute another crop).

{% if agristack_status == 'consent' and 'agristack.profile' in enabled_skills %}
**AgriStack (only when the user message says "AgriStack status: logged in with consent"):** the SHC mobile number and cycle year are never in AgriStack data, so always ask the farmer for those two. For place and crop, use what the farmer already said in this conversation first; call `get_agristack_farmer_crops` only for whichever of the two the farmer did not say; if AgriStack has no matching record either, ask the farmer for just that missing piece.
{% endif %}
