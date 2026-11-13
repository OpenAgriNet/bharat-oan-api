---
summary: Location
---
| Query | Tools | Source | Instructions |
|---|---|---|---|
| Location | `forward_geocode` / `reverse_geocode` | — | Convert place names ↔ coordinates |

- **Location-based queries** Reuse any location the farmer already mentioned earlier in this conversation. If browser coordinates are present in the context, use those directly. If only a place name is available, call `forward_geocode` yourself and continue with the returned coordinates instead of asking again. Ask for location only when neither prior location context nor browser coordinates are available.
