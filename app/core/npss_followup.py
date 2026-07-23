import re
from typing import Any, Optional


NPSS_LOCATION_REQUIRED_MARKER = "[NPSS_LOCATION_REQUIRED]"
INTERNAL_IMAGE_URL_PATTERN = re.compile(r"\[IMAGE_URL:\s*([^\]\s]+)\s*\]")

LOCATION_FIELD_LABELS = {
    "en": {"state": "state", "district": "district", "sub-district/tehsil": "sub-district/tehsil", "village": "village"},
    "hi": {"state": "राज्य", "district": "जिला", "sub-district/tehsil": "उप-जिला/तहसील", "village": "गांव"},
    "as": {"state": "ৰাজ্য", "district": "জিলা", "sub-district/tehsil": "উপ-জিলা/তহচিল", "village": "গাঁও"},
    "bn": {"state": "রাজ্য", "district": "জেলা", "sub-district/tehsil": "উপজেলা/তহসিল", "village": "গ্রাম"},
    "gu": {"state": "રાજ્ય", "district": "જિલ્લો", "sub-district/tehsil": "પેટા-જિલ્લો/તહેસીલ", "village": "ગામ"},
    "kn": {"state": "ರಾಜ್ಯ", "district": "ಜಿಲ್ಲೆ", "sub-district/tehsil": "ಉಪಜಿಲ್ಲೆ/ತಾಲೂಕು", "village": "ಗ್ರಾಮ"},
    "ml": {"state": "സംസ്ഥാനം", "district": "ജില്ല", "sub-district/tehsil": "ഉപജില്ല/താലൂക്ക്", "village": "ഗ്രാമം"},
    "mr": {"state": "राज्य", "district": "जिल्हा", "sub-district/tehsil": "उपजिल्हा/तहसील", "village": "गाव"},
    "ta": {"state": "மாநிலம்", "district": "மாவட்டம்", "sub-district/tehsil": "துணை மாவட்டம்/தாலுகா", "village": "கிராமம்"},
    "te": {"state": "రాష్ట్రం", "district": "జిల్లా", "sub-district/tehsil": "ఉప జిల్లా/తహసీల్", "village": "గ్రామం"},
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
    "en": "Please confirm the official spelling of your state, district, sub-district/tehsil, and village. The image is saved, and I’ll continue the NPSS analysis once those details are confirmed.",
    "hi": "कृपया अपने राज्य, जिला, उप-जिला/तहसील और गांव के आधिकारिक नाम व वर्तनी की पुष्टि करें। छवि सुरक्षित है; पुष्टि मिलते ही मैं NPSS विश्लेषण जारी रखूंगा।",
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
    requested = missing_fields or ["state", "district", "sub-district/tehsil", "village"]
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
            if "**NPSS Analysis Result**" in content:
                return None
            if NPSS_LOCATION_REQUIRED_MARKER not in content:
                continue
            match = INTERNAL_IMAGE_URL_PATTERN.search(content)
            return match.group(1) if match else None
    return None
