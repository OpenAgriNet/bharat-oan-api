import re
from typing import Any, Optional


NPSS_LOCATION_REQUIRED_MARKER = "[NPSS_LOCATION_REQUIRED]"
INTERNAL_IMAGE_URL_PATTERN = re.compile(r"\[IMAGE_URL:\s*([^\]\s]+)\s*\]")

LOCATION_FIELD_LABELS = {
    "en": {"location": "village, locality, or town"},
    "hi": {"location": "गांव, इलाका या शहर"},
    "as": {"location": "গাঁও, স্থান বা চহৰ"},
    "bn": {"location": "গ্রাম, এলাকা বা শহর"},
    "gu": {"location": "ગામ, વિસ્તાર અથવા શહેર"},
    "kn": {"location": "ಗ್ರಾಮ, ಸ್ಥಳ ಅಥವಾ ಪಟ್ಟಣ"},
    "ml": {"location": "ഗ്രാമം, പ്രദേശം അല്ലെങ്കിൽ പട്ടണം"},
    "mr": {"location": "गाव, परिसर किंवा शहर"},
    "ta": {"location": "கிராமம், பகுதி அல்லது நகரம்"},
    "te": {"location": "గ్రామం, ప్రాంతం లేదా పట్టణం"},
}

LOCATION_REQUEST_TEMPLATES = {
    "en": "To continue the crop-image analysis, please share your {fields}. The image is saved; once you provide these details, I’ll continue with the NPSS analysis.",
    "hi": "फसल की छवि का विश्लेषण जारी रखने के लिए कृपया अपना {fields} बताएं। छवि सुरक्षित है; ये विवरण मिलते ही मैं NPSS विश्लेषण जारी रखूंगा।",
    "as": "শস্যৰ ছবিখনৰ বিশ্লেষণ আগবঢ়াবলৈ অনুগ্ৰহ কৰি আপোনাৰ {fields} জনাওক। ছবিখন সংৰক্ষিত আছে; এই তথ্য পালে NPSS বিশ্লেষণ আগবঢ়াম।",
    "bn": "ফসলের ছবির বিশ্লেষণ চালিয়ে যেতে অনুগ্রহ করে আপনার {fields} জানান। ছবিটি সংরক্ষিত আছে; তথ্যগুলো পেলেই NPSS বিশ্লেষণ চালিয়ে যাব।",
    "gu": "પાકની છબીનું વિશ્લેષણ ચાલુ રાખવા કૃપા કરીને તમારું {fields} જણાવો. છબી સાચવેલી છે; વિગતો મળતાં જ NPSS વિશ્લેષણ ચાલુ રાખીશ.",
    "kn": "ಬೆಳೆ ಚಿತ್ರದ ವಿಶ್ಲೇಷಣೆಯನ್ನು ಮುಂದುವರಿಸಲು ದಯವಿಟ್ಟು ನಿಮ್ಮ {fields} ತಿಳಿಸಿ. ಚಿತ್ರವನ್ನು ಉಳಿಸಲಾಗಿದೆ; ವಿವರಗಳು ದೊರೆತ ತಕ್ಷಣ NPSS ವಿಶ್ಲೇಷಣೆಯನ್ನು ಮುಂದುವರಿಸುತ್ತೇನೆ.",
    "ml": "വിളയുടെ ചിത്ര വിശകലനം തുടരാൻ ദയവായി നിങ്ങളുടെ {fields} അറിയിക്കുക. ചിത്രം സൂക്ഷിച്ചിട്ടുണ്ട്; വിവരങ്ങൾ ലഭിച്ചാൽ NPSS വിശകലനം തുടരും.",
    "mr": "पिकाच्या छायाचित्राचे विश्लेषण पुढे सुरू ठेवण्यासाठी कृपया तुमचे {fields} सांगा. छायाचित्र सुरक्षित आहे; तपशील मिळाल्यावर NPSS विश्लेषण सुरू ठेवेन.",
    "ta": "பயிர் படத்தின் பகுப்பாய்வைத் தொடர உங்கள் {fields} தெரிவிக்கவும். படம் சேமிக்கப்பட்டுள்ளது; விவரங்கள் கிடைத்ததும் NPSS பகுப்பாய்வைத் தொடர்கிறேன்.",
    "te": "పంట చిత్ర విశ్లేషణను కొనసాగించడానికి దయచేసి మీ {fields} తెలియజేయండి. చిత్రం భద్రంగా ఉంది; వివరాలు అందిన వెంటనే NPSS విశ్లేషణను కొనసాగిస్తాను.",
}

LOCATION_CONFIRMATION_TEMPLATES = {
    "en": "I couldn’t identify that place precisely. Please share one more specific village, locality, or town, with district or state if helpful. The image is still saved.",
    "hi": "उस स्थान की सटीक पहचान नहीं हो पाई। कृपया कोई अधिक स्पष्ट गांव, इलाका या शहर बताएं—जरूरत हो तो जिले या राज्य के साथ। छवि अभी भी सुरक्षित है।",
}


def build_npss_location_request(
    target_lang: str,
    missing_fields: list[str],
    *,
    needs_confirmation: bool = False,
) -> str:
    """Build a deterministic farmer-facing prompt when NPSS needs location data."""
    lang = (target_lang or "en").lower()
    if needs_confirmation:
        return LOCATION_CONFIRMATION_TEMPLATES.get(lang, LOCATION_CONFIRMATION_TEMPLATES["en"])

    labels = LOCATION_FIELD_LABELS.get(lang, LOCATION_FIELD_LABELS["en"])
    requested = missing_fields or ["location"]
    localized_fields = ", ".join(labels.get(field, field) for field in requested)
    template = LOCATION_REQUEST_TEMPLATES.get(lang, LOCATION_REQUEST_TEMPLATES["en"])
    return template.format(fields=localized_fields)


def find_pending_npss_image_url(history: list[Any]) -> Optional[str]:
    """Find the image URL from the most recent NPSS location-request tool result."""
    for message in reversed(history or []):
        for part in reversed(getattr(message, "parts", []) or []):
            if getattr(part, "part_kind", "") != "tool-return":
                continue
            content = getattr(part, "content", "")
            if not isinstance(content, str):
                continue
            if NPSS_LOCATION_REQUIRED_MARKER in content:
                match = INTERNAL_IMAGE_URL_PATTERN.search(content)
                return match.group(1) if match else None
            if (
                "**NPSS Analysis Result**" in content
                or getattr(part, "tool_name", "") == "analyze_crop_image"
            ):
                return None
    return None
