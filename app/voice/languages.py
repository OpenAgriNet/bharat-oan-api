"""Language registry for voice calls.

Internal codes are the same as the rest of bharat-oan-api (Odia is "or"); Sarvam's
X-Language header and our response use its ISO-style codes (Odia is "od").
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LanguageSpec:
    code: str
    name: str
    sarvam_code: str
    recording_disclaimer: str
    closing_message: str
    hold_message: str
    delay_message: str


LANGUAGES: dict[str, LanguageSpec] = {
    "en": LanguageSpec(
        "en", "English", "en-IN",
        "This call is being recorded for training and quality improvement. Your information will remain secure.",
        "Thank you for calling Bharati from the Central Agriculture Ministry. Wishing you a very happy Dipawali—have a wonderful day!",
        "Information pertaining to your question is being fetched. Please wait.",
        "We are sorry for the wait. Please stay with us.",
    ),
    "hi": LanguageSpec(
        "hi", "Hindi", "hi-IN",
        "यह कॉल प्रशिक्षण और गुणवत्ता सुधार हेतु रिकॉर्ड की जा रही है। आपकी जानकारी सुरक्षित रहेगी।",
        "केंद्रीय कृषि मंत्रालय की भारत विस्तार सेवा में कॉल करने के लिए धन्यवाद। दीपावली की मंगल कामनाओं के साथ, आपका दिन शुभ रहे|",
        "आपके प्रश्न से संबंधित जानकारी एकत्रित की जा रही है। कृपया प्रतीक्षा करें।",
        "प्रतीक्षा के लिए खेद है। कृपया हमारे साथ बने रहें।",
    ),
    "or": LanguageSpec(
        "or", "Odia", "od-IN",
        "ଏହି କଲ୍ ପ୍ରଶିକ୍ଷଣ ଏବଂ ଗୁଣବତ୍ତା ଉନ୍ନତି ପାଇଁ ରେକର୍ଡ କରାଯାଉଛି। ଆପଣଙ୍କ ସୂଚନା ସୁରକ୍ଷିତ ରହିବ।",
        "କେନ୍ଦ୍ର କୃଷି ମନ୍ତ୍ରଣାଳୟରୁ ଭାରତୀଙ୍କୁ କଲ୍ କରିଥିବାରୁ ଧନ୍ୟବାଦ। ଆପଣଙ୍କୁ ଦୀପାବଳିର ଅନେକ ଶୁଭେଚ୍ଛା—ଆପଣଙ୍କ ଦିନଟି ବହୁତ ଭଲରେ କଟୁ!",
        "ଆପଣଙ୍କ ପ୍ରଶ୍ନ ସମ୍ବନ୍ଧୀୟ ସୂଚନା ସଂଗ୍ରହ କରାଯାଉଛି। ଦୟାକରି ଅପେକ୍ଷା କରନ୍ତୁ।",
        "ଅପେକ୍ଷା ପାଇଁ ଦୁଃଖିତ। ଦୟାକରି ଆମ ସହିତ ରୁହନ୍ତୁ।",
    ),
    "pa": LanguageSpec(
        "pa", "Punjabi", "pa-IN",
        "ਇਹ ਕਾਲ ਸਿਖਲਾਈ ਅਤੇ ਗੁਣਵੱਤਾ ਵਿੱਚ ਸੁਧਾਰ ਲਈ ਰਿਕਾਰਡ ਕੀਤੀ ਜਾ ਰਹੀ ਹੈ। ਤੁਹਾਡੀ ਜਾਣਕਾਰੀ ਸੁਰੱਖਿਅਤ ਰਹੇਗੀ।",
        "ਕੇਂਦਰੀ ਖੇਤੀਬਾੜੀ ਮੰਤਰਾਲੇ ਤੋਂ ਭਾਰਤੀ ਵੱਲੋਂ ਕਾਲ ਕਰਨ ਲਈ ਧੰਨਵਾਦ। ਤੁਹਾਨੂੰ ਦੀਵਾਲੀ ਦੀਆਂ ਬਹੁਤ-ਬਹੁਤ ਮੁਬਾਰਕਾਂ—ਤੁਹਾਡਾ ਦਿਨ ਬਹੁਤ ਵਧੀਆ ਰਹੇ!",
        "ਤੁਹਾਡੇ ਸਵਾਲ ਨਾਲ ਸਬੰਧਤ ਜਾਣਕਾਰੀ ਇਕੱਠੀ ਕੀਤੀ ਜਾ ਰਹੀ ਹੈ। ਕਿਰਪਾ ਕਰਕੇ ਉਡੀਕ ਕਰੋ।",
        "ਉਡੀਕ ਲਈ ਮੁਆਫ਼ੀ ਚਾਹੁੰਦੇ ਹਾਂ। ਕਿਰਪਾ ਕਰਕੇ ਸਾਡੇ ਨਾਲ ਬਣੇ ਰਹੋ।",
    ),
    "ta": LanguageSpec(
        "ta", "Tamil", "ta-IN",
        "இந்த அழைப்பு பயிற்சி மற்றும் தர மேம்பாட்டிற்காக பதிவு செய்யப்படுகிறது. உங்கள் தகவல்கள் பாதுகாக்கப்படும்.",
        "மத்திய வேளாண் அமைச்சகத்திலிருந்து பாரதி பேசுகிறேன், அழைத்தமைக்கு நன்றி. உங்களுக்கு இனிய தீபாவளி நல்வாழ்த்துகள்—இன்றைய நாள் சிறப்பாக அமையட்டும்!",
        "உங்கள் கேள்வி தொடர்பான தகவல்கள் சேகரிக்கப்படுகின்றன. தயவுசெய்து காத்திருங்கள்.",
        "காத்திருப்புக்கு வருந்துகிறோம். தயவுசெய்து எங்களுடன் இணைந்திருங்கள்.",
    ),
    "te": LanguageSpec(
        "te", "Telugu", "te-IN",
        "ఈ కాల్ శిక్షణ మరియు నాణ్యత మెరుగుదల కోసం రికార్డ్ చేయబడుతోంది. మీ వ్యక్తిగత సమాచారం సురక్షితంగా ఉంటుంది.",
        "కేంద్ర వ్యవసాయ మంత్రిత్వ శాఖ నుండి భారతి మాట్లాడుతున్నాను, కాల్ చేసినందుకు ధన్యవాదాలు. మీకు దీపావళి శుభాకాంక్షలు—ఈ రోజు మీకు అద్భుతంగా సాగాలని కోరుకుంటున్నాను!",
        "మీ ప్రశ్నకు సంబంధించిన సమాచారం సేకరిస్తున్నాము. దయచేసి వేచి ఉండండి.",
        "వేచి ఉంచినందుకు క్షమించండి. దయచేసి మాతో ఉండండి.",
    ),
    "kn": LanguageSpec(
        "kn", "Kannada", "kn-IN",
        "ಈ ಕರೆಯನ್ನು ತರಬೇತಿ ಮತ್ತು ಗುಣಮಟ್ಟ ಸುಧಾರಣೆಗಾಗಿ ರೆಕಾರ್ಡ್ ಮಾಡಲಾಗುತ್ತಿದೆ. ನಿಮ್ಮ ಮಾಹಿತಿ ಸುರಕ್ಷಿತವಾಗಿರುತ್ತದೆ.",
        "ಕೇಂದ್ರ ಕೃಷಿ ಸಚಿವಾಲಯದಿಂದ ಭಾರತಿ ಕರೆ ಮಾಡಿದ್ದಾರೆ. ನಿಮಗೆ ದೀಪಾವಳಿಯ ಶುಭಾಶಯಗಳು—ನಿಮ್ಮ ದಿನವು ಅದ್ಭುತವಾಗಿರಲಿ!",
        "ನಿಮ್ಮ ಪ್ರಶ್ನೆಗೆ ಸಂಬಂಧಿಸಿದ ಮಾಹಿತಿಯನ್ನು ಸಂಗ್ರಹಿಸಲಾಗುತ್ತಿದೆ. ದಯವಿಟ್ಟು ನಿರೀಕ್ಷಿಸಿ.",
        "ಕಾಯಿಸಿದ್ದಕ್ಕೆ ಕ್ಷಮಿಸಿ. ದಯವಿಟ್ಟು ನಮ್ಮೊಂದಿಗೆ ಇರಿ.",
    ),
    "ml": LanguageSpec(
        "ml", "Malayalam", "ml-IN",
        "ഈ കോൾ പരിശീലനത്തിനും ഗുണനിലവാര മെച്ചപ്പെടുത്തലിനുമായി റെക്കോർഡ് ചെയ്യുന്നു. നിങ്ങളുടെ വ്യക്തിഗത വിവരങ്ങൾ സുരക്ഷിതമായിരിക്കും.",
        "കേന്ദ്ര കൃഷി മന്ത്രാലയത്തിൽ നിന്ന് ഭാരതി വിളിച്ചതിന് നന്ദി. നിങ്ങൾക്ക് ദീപാവലി ആശംസകൾ—ഇന്നത്തെ ദിവസം മനോഹരമാകട്ടെ!",
        "നിങ്ങളുടെ ചോദ്യവുമായി ബന്ധപ്പെട്ട വിവരങ്ങൾ ശേഖരിക്കുന്നു. ദയവായി കാത്തിരിക്കൂ.",
        "കാത്തിരിപ്പിന് ക്ഷമിക്കണം. ദയവായി ഞങ്ങളോടൊപ്പം തുടരൂ.",
    ),
    "gu": LanguageSpec(
        "gu", "Gujarati", "gu-IN",
        "આ કૉલ પ્રશિક્ષણ અને ગુણવત્તા સુધારણા માટે રેકૉર્ડ કરવામાં આવી રહ્યો છે. તમારી માહિતી સુરક્ષિત રહેશે.",
        "કેન્દ્રીય કૃષિ મંત્રાલયમાંથી ભારતીનો સંપર્ક કરવા બદલ આભાર. તમને દિવાળીની ખૂબ ખૂબ શુભેચ્છાઓ—તમારો દિવસ ખૂબ જ સરસ રહે!",
        "તમારા પ્રશ્ન સંબંધિત માહિતી એકત્રિત કરવામાં આવી રહી છે. કૃપા કરીને રાહ જુઓ.",
        "રાહ જોવડાવવા બદલ દિલગીર છીએ. કૃપા કરીને અમારી સાથે જોડાયેલા રહો.",
    ),
    "mr": LanguageSpec(
        "mr", "Marathi", "mr-IN",
        "हा कॉल प्रशिक्षण आणि गुणवत्ता सुधारणेसाठी रेकॉर्ड केला जात आहे. तुमची माहिती सुरक्षित राहील.",
        "केंद्रीय कृषी मंत्रालयाकडून भारतीशी संपर्क साधल्याबद्दल धन्यवाद. तुम्हाला दिवाळीच्या खूप खूप शुभेच्छा—तुमचा दिवस आनंददायी असो!",
        "तुमच्या प्रश्नाशी संबंधित माहिती गोळा केली जात आहे. कृपया प्रतीक्षा करा.",
        "प्रतीक्षेबद्दल क्षमस्व. कृपया आमच्यासोबत रहा.",
    ),
    "bn": LanguageSpec(
        "bn", "Bengali", "bn-IN",
        "এই কলটি প্রশিক্ষণ এবং গুণমান উন্নয়নের জন্য রেকর্ড করা হচ্ছে। আপনার তথ্য সুরক্ষিত থাকবে।",
        "কেন্দ্রীয় কৃষি মন্ত্রকের ভারতী-র পক্ষ থেকে ফোন করার জন্য আপনাকে ধন্যবাদ। আপনাকে দীপাবলির অনেক অনেক শুভেচ্ছা—আপনার দিনটি দারুণ কাটুক!",
        "আপনার প্রশ্ন সম্পর্কিত তথ্য সংগ্রহ করা হচ্ছে। অনুগ্রহ করে অপেক্ষা করুন।",
        "অপেক্ষার জন্য দুঃখিত। অনুগ্রহ করে আমাদের সঙ্গে থাকুন।",
    ),
}

SUPPORTED_LANGUAGES = {code: spec.name for code, spec in LANGUAGES.items()}
SUPPORTED_LANGUAGE_CODES = frozenset(LANGUAGES)
DEFAULT_LANGUAGE = "hi"
LANGUAGE_ALIASES = {"od": "or"}
ISO_TO_INTERNAL_LANGUAGE = {
    "en": "en",
    "hi": "hi",
    "od": "or",
    "pa": "pa",
    "ta": "ta",
    "te": "te",
    "kn": "kn",
    "ml": "ml",
    "gu": "gu",
    "mr": "mr",
    "bn": "bn",
}
INTERNAL_TO_ISO_LANGUAGE = {
    internal: iso for iso, internal in ISO_TO_INTERNAL_LANGUAGE.items()
}
SUPPORTED_ISO_LANGUAGE_CODES = frozenset(ISO_TO_INTERNAL_LANGUAGE)


def normalize_language(lang: str | None, *, fallback: str = DEFAULT_LANGUAGE) -> str:
    if not lang:
        return fallback
    raw = lang.strip().lower()
    code = LANGUAGE_ALIASES.get(raw, raw)
    return code if code in LANGUAGES else fallback


def parse_iso_language_header(lang: str | None) -> str:
    """Validate a Sarvam X-Language value and return its internal code."""
    raw = (lang or "").strip().lower()
    try:
        return ISO_TO_INTERNAL_LANGUAGE[raw]
    except KeyError as exc:
        supported = ", ".join(sorted(SUPPORTED_ISO_LANGUAGE_CODES))
        raise ValueError(
            f"X-Language must be one of these supported codes: {supported}"
        ) from exc


def iso_language_code(lang: str | None) -> str:
    """Return the public Sarvam language code for an internal language code."""
    internal = normalize_language(lang, fallback="")
    try:
        return INTERNAL_TO_ISO_LANGUAGE[internal]
    except KeyError as exc:
        raise ValueError(f"Unsupported internal language code: {lang!r}") from exc


def is_supported(lang: str | None) -> bool:
    return bool(lang) and normalize_language(lang, fallback="") in LANGUAGES


def get_language(lang: str | None) -> LanguageSpec:
    return LANGUAGES[normalize_language(lang)]


def sarvam_language_code(lang: str | None) -> str:
    return get_language(lang).sarvam_code
