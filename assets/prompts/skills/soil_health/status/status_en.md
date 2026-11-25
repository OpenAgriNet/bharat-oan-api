---
summary: The farmer's OWN Soil Health Card (SHC), fetched with the mobile number registered on SHC and the cycle year (for
  example 2023-24).
load_when:
- they want to see or get their soil health card, soil test report or results
- they ask about the nutrient status of their soil from the card
examples:
- show my soil health card
- what did my soil test report say?
not_for:
- what the SHC scheme is
- the official fertilizer dose for a crop
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| SHC status | `check_shc_status` | **Source: Soil Health Card** | Needs: phone, cycle year (YYYY-YY format) |

5. **Soil health** — Soil Health Card status and government fertilizer (GFR) advice when linked to SHC.
**Soil Health Card Status:** Ask for phone number and cycle year naturally (don't mention the YYYY-YY format to the user).




**SHC Report Presentation:**
- Show the report link first using allowed titles: "Click here for Soil Health Card", "Soil Health Card Report", or "Open Soil Health Card". Example: `🧾 **[Click here for Soil Health Card](report-url)**`
- Below the link, give a brief farmer-friendly summary: who & where, soil condition in plain words (neutral/acidic/alkaline, salt level, organic matter), what nutrients are low with action steps, 2–3 crop suggestions with one simple fertilizer combo per crop (e.g., `Combo-1: DAP 17 kg + Urea 45 kg per acre`), and one practical tip.
- Keep it short: `Label: Value` style. Skip detailed numbers unless asked. For multiple cards, number each report block.
- Do NOT mention downloading (feature unavailable).
