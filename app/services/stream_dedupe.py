"""Drop text the model repeats after a tool call.

Some turns stream a sentence before a tool call and then write the
same sentence again once the tool returns. The first copy is already on the wire,
so the second copy is held back until it either diverges (flush it) or finishes
matching the earlier text (drop it).
"""


def _canon(text: str) -> str:
    return "".join(ch.lower() for ch in text if ch.isalnum())


def _after_canon_prefix(text: str, length: int) -> str:
    """Raw text left after `length` canonical characters."""
    seen = 0
    for index, ch in enumerate(text):
        if ch.isalnum():
            seen += len(ch.lower())
            if seen >= length:
                return _skip_punct(text[index + 1:])
    return ""


def _skip_punct(text: str) -> str:
    """Drop the repeated copy's trailing punctuation, e.g. the '?' in 'Which state?'."""
    end = 0
    while end < len(text) and not text[end].isalnum() and not text[end].isspace():
        end += 1
    return text[end:]


class ToolBoundaryDeduper:
    def __init__(self) -> None:
        self._segment = ""
        self._prior = ""
        self._held = ""
        self._holding = False

    def tool_boundary(self) -> None:
        if self._segment.strip():
            self._prior = _canon(self._segment)
        self._segment = ""
        self._held = ""
        self._holding = bool(self._prior)

    def feed(self, text: str) -> str:
        """Return the part of `text` that is safe to send to the client."""
        if not text:
            return ""
        if not self._holding:
            self._segment += text
            return text
        self._held += text
        held = _canon(self._held)
        if self._prior.startswith(held):
            return ""  # still looks like a repeat; wait for more text
        self._holding = False
        if held.startswith(self._prior):
            out = _after_canon_prefix(self._held, len(self._prior))
        else:
            out = self._held
        self._held = ""
        self._segment += out
        return out
