---
summary: 'Fertilizer purchase through FFS: offer FFS first, then Farmer ID and OTP login, urea and DAP entitlement, shops
  and a confirmed booking.'
load_when:
- any intent to buy, order or book fertilizer (urea, DAP, NPK, MOP or unspecified)
- they ask about their fertilizer entitlement or quota, or which shops have stock
- 'load it before the FFS offer or asking for state or Farmer ID: the skill has the offer wording and the supported states'
examples:
- I want to buy 2 bags of urea
- where can I book DAP?
- how much fertilizer am I allowed to buy on my Farmer ID?
not_for:
- how much fertilizer to apply
---
## FFS fertilizer availability

Ask only for missing details.

{% include "skills/ffs/booking/flow.md" %}
