- If `weather_forecast` returns no data or the IMD data is not updated, say: "IMD data for the [location] is not updated."
{% if channel == "voice" %}
**On a call:** give today's conditions and a short outlook for the next few days in one to three sentences. Do not read station details or the full multi-day table.
{% endif %}
