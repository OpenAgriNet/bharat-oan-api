from typing import Callable, Optional

from helpers.translation import BhashiniTranslator, BaseTranslator
from helpers.utils import get_logger, post_process_translation


logger = get_logger(__name__)

NPSS_SOURCE_NAME = "National Pest Surveillance System (NPSS)"
NPSS_SOURCE_OWNER = "Department of Agriculture & Farmers Welfare, Ministry of Agriculture & Farmers Welfare, Government of India"
NPSS_SOURCE_URL = "https://npss.dac.gov.in/"

LABELS = {
    "en": {"pest": "Pest", "crop": "Crop", "cause": "Cause"},
    "hi": {"pest": "कीट", "crop": "फसल", "cause": "कारण"},
    "as": {"pest": "কীট", "crop": "শস্য", "cause": "কাৰণ"},
    "bn": {"pest": "কীট", "crop": "ফসল", "cause": "কারণ"},
    "gu": {"pest": "જીવાત", "crop": "પાક", "cause": "કારણ"},
    "kn": {"pest": "ಕೀಟ", "crop": "ಬೆಳೆ", "cause": "ಕಾರಣ"},
    "ml": {"pest": "കീടം", "crop": "വിള", "cause": "കാരണം"},
    "mr": {"pest": "कीड", "crop": "पीक", "cause": "कारण"},
    "ta": {"pest": "பூச்சி", "crop": "பயிர்", "cause": "காரணம்"},
    "te": {"pest": "పురుగు", "crop": "పంట", "cause": "కారణం"},
}

SOURCE_LINES = {
    "en": f"**Source: {NPSS_SOURCE_NAME}, {NPSS_SOURCE_OWNER}, {NPSS_SOURCE_URL}**",
    "hi": f"**स्रोत: राष्ट्रीय कीट निगरानी प्रणाली (NPSS), कृषि और किसान कल्याण विभाग, भारत सरकार, {NPSS_SOURCE_URL}**",
    "as": f"**উৎস: ৰাষ্ট্ৰীয় কীট নিৰীক্ষণ ব্যৱস্থা (NPSS), কৃষি আৰু কৃষক কল্যাণ বিভাগ, ভাৰত চৰকাৰ, {NPSS_SOURCE_URL}**",
    "bn": f"**উৎস: জাতীয় কীট নজরদারি ব্যবস্থা (NPSS), কৃষি ও কৃষক কল্যাণ বিভাগ, ভারত সরকার, {NPSS_SOURCE_URL}**",
    "gu": f"**સ્રોત: રાષ્ટ્રીય જીવાત દેખરેખ પ્રણાલી (NPSS), કૃષિ અને ખેડૂત કલ્યાણ વિભાગ, ભારત સરકાર, {NPSS_SOURCE_URL}**",
    "kn": f"**ಮೂಲ: ರಾಷ್ಟ್ರೀಯ ಕೀಟ ನಿಗಾವಳಿ ವ್ಯವಸ್ಥೆ (NPSS), ಕೃಷಿ ಮತ್ತು ರೈತರ ಕಲ್ಯಾಣ ಇಲಾಖೆ, ಭಾರತ ಸರ್ಕಾರ, {NPSS_SOURCE_URL}**",
    "ml": f"**ഉറവിടം: ദേശീയ കീട നിരീക്ഷണ സംവിധാനം (NPSS), കൃഷി കർഷക ക്ഷേമ വകുപ്പ്, ഇന്ത്യ സർക്കാർ, {NPSS_SOURCE_URL}**",
    "mr": f"**स्रोत: राष्ट्रीय कीड देखरेख प्रणाली (NPSS), कृषी आणि शेतकरी कल्याण विभाग, भारत सरकार, {NPSS_SOURCE_URL}**",
    "ta": f"**மூலம்: தேசிய பூச்சி கண்காணிப்பு அமைப்பு (NPSS), வேளாண்மை மற்றும் விவசாயிகள் நலத் துறை, இந்திய அரசு, {NPSS_SOURCE_URL}**",
    "te": f"**మూలం: జాతీయ పురుగు పర్యవేక్షణ వ్యవస్థ (NPSS), వ్యవసాయ మరియు రైతు సంక్షేమ శాఖ, భారత ప్రభుత్వం, {NPSS_SOURCE_URL}**",
}

LABEL_NAMES = {"pest", "crop", "cause", "source", "source owner", "source url"}
SOURCE_PREFIXES = (
    "source:",
    "source owner:",
    "source url:",
    "स्रोत:",
    "উৎস:",
    "સ્રોત:",
    "ಮೂಲ:",
    "ഉറവിടം:",
    "மூலம்:",
    "మూలం:",
)


def post_process_npss_response(
    text: str,
    target_lang: str,
    npss_used: bool,
    translator_factory: Optional[Callable[[str, str], BaseTranslator]] = None,
) -> str:
    """Make NPSS attribution and translation deterministic after model generation."""
    if not npss_used or not text or not text.strip():
        return text

    lang = (target_lang or "en").lower()
    body = _remove_existing_source_lines(text)
    body = _remove_trailing_follow_up(body)

    if lang != "en":
        body = _translate_npss_body(body, lang, translator_factory)
        body = _localize_known_labels(body, lang)

    body = body.strip()
    source_line = SOURCE_LINES.get(lang, SOURCE_LINES["en"])
    return f"{body}\n\n{source_line}" if body else source_line


def _remove_existing_source_lines(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if _is_source_line(line):
            continue
        lines.append(line)
    return "\n".join(lines)


def _remove_trailing_follow_up(text: str) -> str:
    lines = text.rstrip().splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and lines[-1].strip().endswith(("?", "؟", "？")):
        lines.pop()
    return "\n".join(lines)


def _translate_npss_body(
    text: str,
    target_lang: str,
    translator_factory: Optional[Callable[[str, str], BaseTranslator]],
) -> str:
    translatable: list[str] = []
    line_slots: list[tuple[int, Optional[str]]] = []
    output_lines = text.splitlines()

    for idx, line in enumerate(output_lines):
        stripped = line.strip()
        if not stripped:
            continue

        parsed_label = _parse_markdown_label(stripped)
        if parsed_label:
            label, value = parsed_label
            if label in ("pest", "crop", "cause") and value:
                line_slots.append((idx, label))
                translatable.append(value)
            continue

        line_slots.append((idx, None))
        translatable.append(line)

    if not translatable:
        return text

    try:
        if translator_factory:
            translator = translator_factory("en", target_lang)
        else:
            translator = BhashiniTranslator(source_lang="en", target_lang=target_lang)
        translated = [post_process_translation(item) for item in translator.translate_texts(translatable)]
    except Exception as exc:
        logger.warning("NPSS deterministic translation failed for %s: %s", target_lang, exc)
        return text

    labels = LABELS.get(target_lang, LABELS["en"])
    for (idx, label), translated_text in zip(line_slots, translated):
        if label:
            output_lines[idx] = f"**{labels[label]}:** {translated_text}"
        else:
            output_lines[idx] = translated_text

    return "\n".join(output_lines)


def _localize_known_labels(text: str, target_lang: str) -> str:
    labels = LABELS.get(target_lang, LABELS["en"])
    output_lines = []

    for line in text.splitlines():
        parsed_label = _parse_markdown_label(line.strip())
        if not parsed_label:
            output_lines.append(line)
            continue

        label, value = parsed_label
        if label not in labels:
            output_lines.append(line)
            continue

        leading_space_count = len(line) - len(line.lstrip())
        output_lines.append(f"{line[:leading_space_count]}**{labels[label]}:** {value}")

    return "\n".join(output_lines)


def _parse_markdown_label(line: str) -> Optional[tuple[str, str]]:
    if not line.startswith("**"):
        return None

    close = line.find(":**", 2)
    if close == -1:
        return None

    label = line[2:close].strip().lower()
    if label not in LABEL_NAMES:
        return None

    return label, line[close + 3:].strip()


def _is_source_line(line: str) -> bool:
    parsed_label = _parse_markdown_label(line.strip())
    if parsed_label and parsed_label[0] in ("source", "source owner", "source url"):
        return True

    normalized = line.strip()
    if normalized.startswith("**"):
        normalized = normalized[2:]
    normalized = normalized.lower()

    return any(normalized.startswith(prefix) for prefix in SOURCE_PREFIXES)
