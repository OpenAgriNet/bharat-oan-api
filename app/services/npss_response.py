from typing import Callable, Optional

from helpers.translation import BhashiniTranslator, BaseTranslator
from helpers.utils import get_logger, post_process_translation


logger = get_logger(__name__)

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
    "en": "**Source: NPSS**",
    "hi": "**स्रोत: NPSS**",
    "as": "**উৎস: NPSS**",
    "bn": "**উৎস: NPSS**",
    "gu": "**સ્રોત: NPSS**",
    "kn": "**ಮೂಲ: NPSS**",
    "ml": "**ഉറവിടം: NPSS**",
    "mr": "**स्रोत: NPSS**",
    "ta": "**மூலம்: NPSS**",
    "te": "**మూలం: NPSS**",
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
LANGUAGE_SCRIPT_RANGES = {
    "hi": ("\u0900", "\u097F"),
    "mr": ("\u0900", "\u097F"),
    "as": ("\u0980", "\u09FF"),
    "bn": ("\u0980", "\u09FF"),
    "gu": ("\u0A80", "\u0AFF"),
    "kn": ("\u0C80", "\u0CFF"),
    "ml": ("\u0D00", "\u0D7F"),
    "ta": ("\u0B80", "\u0BFF"),
    "te": ("\u0C00", "\u0C7F"),
}


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
        candidate = _translation_candidate(line, target_lang)
        if not candidate:
            continue
        label, value = candidate
        line_slots.append((idx, label))
        translatable.append(value)

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


def _translation_candidate(
    line: str,
    target_lang: str,
) -> Optional[tuple[Optional[str], str]]:
    stripped = line.strip()
    if not stripped:
        return None

    parsed_label = _parse_markdown_label(stripped)
    if parsed_label:
        label, value = parsed_label
        if label not in ("pest", "crop", "cause") or not value:
            return None
        if _is_likely_in_target_language(value, target_lang):
            return None
        return label, value

    if _is_likely_in_target_language(line, target_lang):
        return None
    return None, line


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


def _is_likely_in_target_language(text: str, target_lang: str) -> bool:
    script_range = LANGUAGE_SCRIPT_RANGES.get(target_lang)
    if not script_range:
        return False

    start, end = script_range
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return False

    target_script_count = sum(1 for char in letters if start <= char <= end)
    return target_script_count / len(letters) >= 0.35
