---
name: weather
description: Weather forecasts and district IMD agromet advisories.
tools:
- imd_agromet_advisory
- weather_forecast
sections:
  forecast:
    file: forecast/weather_{language}.md
  agromet:
    file: agromet/agromet_{language}.md
    when_any: []
    tools:
    - imd_agromet_advisory
  routing_weather_forecast:
    file: routing_weather_forecast/routing_weather_forecast_{language}.md
  routing_imd_agromet_advisory:
    file: routing_imd_agromet_advisory/routing_imd_agromet_advisory_{language}.md
---

# Weather

Edit the localized files named above to change this workflow. Tool names refer to
the shared Python registry; listing a tool in another skill never duplicates its
schema or implementation. Loading policy is configured separately.
