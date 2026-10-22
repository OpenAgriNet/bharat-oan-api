"""Script allowlists for vLLM chat; ASCII remains allowed, including English.

Ranges retained from OD-2764_v1. Common/Inherited Unicode properties preserve
shared punctuation, combining marks and emoji without enumerating other scripts.
Positive classes avoid the historical xgrammar non-ASCII denylist bug (#848).
"""
from pydantic_ai.models.openai import OpenAIChatModelSettings

_SCRIPT_EXCLUSIVE_RANGES = {
    "bn": "\u0980-\u0983\u0985-\u098c\u098f-\u0990\u0993-\u09a8\u09aa-\u09b0\u09b2\u09b6-\u09b9\u09bc-\u09c4\u09c7-\u09c8\u09cb-\u09ce\u09d7\u09dc-\u09dd\u09df-\u09e3\u09e6-\u09fe",  # Bengali (Assamese shares this block)
    "gu": "\u0a81-\u0a83\u0a85-\u0a8d\u0a8f-\u0a91\u0a93-\u0aa8\u0aaa-\u0ab0\u0ab2-\u0ab3\u0ab5-\u0ab9\u0abc-\u0ac5\u0ac7-\u0ac9\u0acb-\u0acd\u0ad0\u0ae0-\u0ae3\u0ae6-\u0af1\u0af9-\u0aff",  # Gujarati
    "kn": "\u0c80-\u0c8c\u0c8e-\u0c90\u0c92-\u0ca8\u0caa-\u0cb3\u0cb5-\u0cb9\u0cbc-\u0cc4\u0cc6-\u0cc8\u0cca-\u0ccd\u0cd5-\u0cd6\u0cdc-\u0cde\u0ce0-\u0ce3\u0ce6-\u0cef\u0cf1-\u0cf3",  # Kannada
    "ta": "\u0b82-\u0b83\u0b85-\u0b8a\u0b8e-\u0b90\u0b92-\u0b95\u0b99-\u0b9a\u0b9c\u0b9e-\u0b9f\u0ba3-\u0ba4\u0ba8-\u0baa\u0bae-\u0bb9\u0bbe-\u0bc2\u0bc6-\u0bc8\u0bca-\u0bcd\u0bd0\u0bd7\u0be6-\u0bfa",  # Tamil
    "te": "\u0c00-\u0c0c\u0c0e-\u0c10\u0c12-\u0c28\u0c2a-\u0c39\u0c3c-\u0c44\u0c46-\u0c48\u0c4a-\u0c4d\u0c55-\u0c56\u0c58-\u0c5a\u0c5c-\u0c5d\u0c60-\u0c63\u0c66-\u0c6f\u0c77-\u0c7f",  # Telugu
    "ml": "\u0d00-\u0d0c\u0d0e-\u0d10\u0d12-\u0d44\u0d46-\u0d48\u0d4a-\u0d4f\u0d54-\u0d63\u0d66-\u0d7f",  # Malayalam
}
_ASCII_RANGE = "\t\n\r \u0020-\u007e"  # printable ASCII + whitespace; raw control bytes (NUL etc.) break xgrammar's parser
_COMMON_INHERITED_RANGE = (  # 181 ranges, computed from the full Unicode codepoint space (excludes Cc control chars)
    "\u0020-\u0040\u005b-\u0060\u007b-\u007e\u00a0-\u00a9\u00ab-\u00b9\u00bb-\u00bf\u00d7\u00f7\u02b9-\u02df\u02e5-\u02e9\u02ec-\u036f\u0374\u037e\u0385\u0387\u0485-\u0486\u0605\u060c\u061b\u061f\u0640\u064b-\u0655\u0670\u06dd\u08e2\u0951-\u0954\u0964-\u0965\u0e3f\u0fd5-\u0fd8\u10fb\u16eb-\u16ed\u1735-\u1736\u1802-\u1803\u1805\u1ab0-\u1add\u1ae0-\u1aeb\u1cd0-\u1cfa\u1dc0-\u1dff\u2000-\u2064\u2066-\u2070\u2074-\u207e\u2080-\u208e\u20a0-\u20c1\u20d0-\u20f0\u2100-\u2125\u2127-\u2129\u212c-\u2131\u2133-\u214d\u214f-\u215f\u2189-\u218b\u2190-\u2429\u2440-\u244a\u2460-\u27ff\u2900-\u2b73\u2b76-\u2bff\u2e00-\u2e5d\u2ff0-\u3004\u3006\u3008-\u3020\u302a-\u302d\u3030-\u3037\u303c-\u303f\u3099-\u309c\u30a0\u30fb-\u30fc\u3190-\u319f\u31c0-\u31e5\u31ef\u3220-\u325f\u327f-\u32cf\u32ff\u3358-\u33ff\u4dc0-\u4dff\ua700-\ua721\ua788-\ua78a\ua830-\ua839\ua92e\ua9cf\uab5b\uab6a-\uab6b\ufd3e-\ufd3f\ufe00-\ufe19\ufe20-\ufe2d\ufe30-\ufe52\ufe54-\ufe66\ufe68-\ufe6b\ufeff\uff01-\uff20\uff3b-\uff40\uff5b-\uff65\uff70\uff9e-\uff9f\uffe0-\uffe6\uffe8-\uffee\ufff9-\ufffd\U00010100-\U00010102\U00010107-\U00010133\U00010137-\U0001013f\U00010190-\U0001019c\U000101d0-\U000101fd\U000102e0-\U000102fb\U0001133b\U0001bca0-\U0001bca3\U0001cc00-\U0001ccfc\U0001cd00-\U0001ceb3\U0001ceba-\U0001ced0\U0001cee0-\U0001cef0\U0001cf00-\U0001cf2d\U0001cf30-\U0001cf46\U0001cf50-\U0001cfc3\U0001d000-\U0001d0f5\U0001d100-\U0001d126\U0001d129-\U0001d1ea\U0001d2c0-\U0001d2d3\U0001d2e0-\U0001d2f3\U0001d300-\U0001d356\U0001d360-\U0001d378\U0001d400-\U0001d454\U0001d456-\U0001d49c\U0001d49e-\U0001d49f\U0001d4a2\U0001d4a5-\U0001d4a6\U0001d4a9-\U0001d4ac\U0001d4ae-\U0001d4b9\U0001d4bb\U0001d4bd-\U0001d4c3\U0001d4c5-\U0001d505\U0001d507-\U0001d50a\U0001d50d-\U0001d514\U0001d516-\U0001d51c\U0001d51e-\U0001d539\U0001d53b-\U0001d53e\U0001d540-\U0001d544\U0001d546\U0001d54a-\U0001d550\U0001d552-\U0001d6a5\U0001d6a8-\U0001d7cb\U0001d7ce-\U0001d7ff\U0001ec71-\U0001ecb4\U0001ed01-\U0001ed3d\U0001f000-\U0001f02b\U0001f030-\U0001f093\U0001f0a0-\U0001f0ae\U0001f0b1-\U0001f0bf\U0001f0c1-\U0001f0cf\U0001f0d1-\U0001f0f5\U0001f100-\U0001f1ad\U0001f1e6-\U0001f1ff\U0001f201-\U0001f202\U0001f210-\U0001f23b\U0001f240-\U0001f248\U0001f250-\U0001f251\U0001f260-\U0001f265\U0001f300-\U0001f6d8\U0001f6dc-\U0001f6ec\U0001f6f0-\U0001f6fc\U0001f700-\U0001f7d9\U0001f7e0-\U0001f7eb\U0001f7f0\U0001f800-\U0001f80b\U0001f810-\U0001f847\U0001f850-\U0001f859\U0001f860-\U0001f887\U0001f890-\U0001f8ad\U0001f8b0-\U0001f8bb\U0001f8c0-\U0001f8c1\U0001f8d0-\U0001f8d8\U0001f900-\U0001fa57\U0001fa60-\U0001fa6d\U0001fa70-\U0001fa7c\U0001fa80-\U0001fa8a\U0001fa8e-\U0001fac6\U0001fac8\U0001facd-\U0001fadc\U0001fadf-\U0001faea\U0001faef-\U0001faf8\U0001fb00-\U0001fb92\U0001fb94-\U0001fbfa\U000e0001\U000e0020-\U000e007f\U000e0100-\U000e01ef"
)


def _escape_for_char_class(chars: str) -> str:
    """Escape metacharacters and control bytes before handing a character set
    to xgrammar: a raw '[' mid-class is misparsed as a nested bracket, and raw
    control bytes (tab/newline/CR) break its EBNF lexer — both need to arrive
    as regex escape sequences, not literal bytes."""
    chars = chars.replace("\\", "\\\\")
    chars = chars.replace("]", "\\]").replace("[", "\\[").replace("^", "\\^")
    return chars.replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")


# Precomputed once at import time, not per-request

_GUIDED_DECODING_PATTERNS = {
    lang: f"^[{_escape_for_char_class(exclusive + _ASCII_RANGE + _COMMON_INHERITED_RANGE)}]*$"
    for lang, exclusive in _SCRIPT_EXCLUSIVE_RANGES.items()
}
_GUIDED_DECODING_PATTERNS["as"] = _GUIDED_DECODING_PATTERNS["bn"]  # Assamese shares Bengali's block


def guided_decoding_settings(lang_code: str | None, model_kind: str) -> OpenAIChatModelSettings | None:
    """Apply only to the selected vLLM attempt, including after routing/fallback."""
    pattern = _GUIDED_DECODING_PATTERNS.get((lang_code or "").lower())
    if model_kind != "vllm" or pattern is None:
        return None
    return OpenAIChatModelSettings(extra_body={"structured_outputs": {"regex": pattern}})
