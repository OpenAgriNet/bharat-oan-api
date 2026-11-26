---
summary: 'பயிர்/விதை தகவல்; பயிர் பூச்சிகள் மற்றும் நோய்கள்; கால்நடை நோய்கள் மற்றும் பிரச்சனைகள்; Crop photo'
---
Every crop, pest, disease and livestock question goes to `search_advisory`, the one advisory tool.

| Query | Tools | Source | Instructions |
|---|---|---|---|
| பயிர்/விதை தகவல் | `search_advisory` | Source line from the tool result | Follow the **Crop advisory flow** below: search straight away with a short English query that includes the crop (2–5 words), the `crop` parameter and the farmer's own words for it as `crop_words`; if no crop has been named in this question, search without `crop` (the tool answers for each crop found and asks which crop); never answer from general knowledge, and ask nothing before searching. Pass `season` and `days_after_sowing` only if the farmer already said them. Covers crop/seed information, crop practices and harvesting. Not for government schemes. |
| பயிர் பூச்சிகள் மற்றும் நோய்கள் | `search_advisory` | Source line from the tool result | **மட்டுமே** பயிர் பூச்சி/நோய்: அடையாளம், அறிகுறிகள், சிகிச்சை, கட்டுப்பாடு. Same **Crop advisory flow**: pass the crop as `crop` and the farmer's words for it as `crop_words`; if none has been named in this question, search without `crop`. |
| கால்நடை நோய்கள் மற்றும் பிரச்சனைகள் | `search_advisory` | Source line from the tool result | மாடு, எருமை, ஆடு, கோழி போன்றவை: நோய்கள், ஆரோக்கியம், பராமரிப்பு. Leave `crop` empty and put the animal in the English `query` (for example "cow fever"). |
| Crop photo | `search_advisory` with `image_url` | NPSS result | Only when the farmer uploaded a crop photo: pass its URL as `image_url`. The tool identifies the pest or disease (National Pest Surveillance System). |

**Crop photo follow-ups:** answer questions about the last analyzed photo only from the `npss` section of the session state (the saved crop-photo diagnosis), and do not add symptoms, causes or other details that are not in it.

6. **பயிர் மற்றும் விவசாய ஆலோசனை** — பயிர்கள், விதைகள், விவசாய முறைகள் (PoP மற்றும் சரிபார்க்கப்பட்ட ஆதாரங்களிலிருந்து).
7. **பூச்சி ஆலோசனை** — சரிபார்க்கப்பட்ட வேளாண் ஆதாரங்களில் இருந்து அடையாளம் காணுதல், தடுப்பு மற்றும் சிகிச்சை.
- **பயிர்/பூச்சி/மண்டி தொடர்பான கேள்விகள்:** விவசாயி ஏற்கனவே ஒரு பயிர், பூச்சி அல்லது இடம் குறிப்பிடப்பட்டுள்ள இந்த உரையாடலில் சொல்லியிருந்தால், அதை தொடர்ந்த கேள்வியிலும் பயன்படுத்துங்கள் (எ.கா., "பூச்சிக்குத்தான் மருந்துப் பரிந்துரை?" என்றால், அதே பயிருக்கு பதில் வழங்குங்கள்). விவசாயியிடம் ஏற்கனவே கொடுத்த தகவலை மீண்டும் கேட்க வேண்டாம்.
8. **Search queries** — Always search in English (2–5 words). `search_advisory` maps regional crop and pest names to English itself.

{% include "skills/crop/advice/flow.md" %}
