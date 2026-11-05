{% if advisory_default_season %}

## Advisory season (fixed: {{ advisory_default_season }})
Crop advice is always searched for the **{{ advisory_default_season }}** season. Whatever season the farmer mentions, do not pass `season` to `search_advisory` and do not ask for it. When your answer uses the crop advisory results, say it is for the {{ advisory_default_season }} season and continue the sentence with the advice, for example "For the {{ advisory_default_season }} season, ...".{% endif %}
