"""Turn the place a farmer types into LGD codes (state and district), the codes advisory data uses.

The list lives in `assets/data/lgd_locations.json` (states and districts with LGD codes and aliases). Matching is
on whole words, case-insensitive, and tolerant of punctuation ("U.P." = "UP"). District LGD codes are unique across
the country; a district name that exists in several states resolves to the one in the state the farmer named, else
the first listed.
"""
import json
import re
import string
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_DATA_FILE = Path(__file__).resolve().parents[1] / "assets" / "data" / "lgd_locations.json"
_PUNCT = {ord(ch): " " for ch in string.punctuation if ch != "&"}


@dataclass(frozen=True)
class Place:
    """A resolved place. Codes are None when not recognised; names are the official spellings."""

    state_code: Optional[int] = None
    district_code: Optional[int] = None
    state_name: str = ""
    district_name: str = ""

    @property
    def known(self) -> bool:
        return self.state_code is not None or self.district_code is not None

    @property
    def label(self) -> str:
        return ", ".join(part for part in (self.district_name, self.state_name) if part)


def _normalize(text: object) -> str:
    value = str(text or "").lower().replace("&", " and ")
    return " ".join(value.translate(_PUNCT).split())


@lru_cache(maxsize=1)
def _load() -> Tuple[Dict[int, str], Dict[int, Tuple[str, int]], Dict[str, List[int]], Dict[str, List[int]]]:
    """(state names, district name+state, state alias -> codes, district alias -> codes)."""
    data = json.loads(_DATA_FILE.read_text(encoding="utf-8"))
    state_names: Dict[int, str] = {}
    district_info: Dict[int, Tuple[str, int]] = {}
    state_index: Dict[str, List[int]] = {}
    district_index: Dict[str, List[int]] = {}
    for state in data.get("states", []):
        code = int(state["code"])
        state_names[code] = state["name"]
        for alias in [state["name"], *state.get("aliases", [])]:
            state_index.setdefault(_normalize(alias), []).append(code)
    for district in data.get("districts", []):
        code = int(district["code"])
        district_info[code] = (district["name"], int(district["state"]))
        for alias in [district["name"], *district.get("aliases", [])]:
            district_index.setdefault(_normalize(alias), []).append(code)
    return state_names, district_info, state_index, district_index


def _find(padded: str, index: Dict[str, List[int]], allow_short: bool) -> List[Tuple[str, List[int]]]:
    """Index entries that appear in the text as whole words, longest first."""
    found = [
        (alias, codes)
        for alias, codes in index.items()
        if alias and (allow_short or len(alias.replace(" ", "")) > 2) and f" {alias} " in padded
    ]
    return sorted(found, key=lambda item: -len(item[0]))


def resolve_place(*texts: object, allow_short: bool = True) -> Place:
    """Resolve a state and/or district from one or more pieces of text (the farmer's words, or tool arguments).

    `allow_short=False` ignores two-letter abbreviations ("UP", "MP", "HP"...). Use it when scanning free
    prose, where "up" is an ordinary word.
    """
    state_names, district_info, state_index, district_index = _load()
    padded = " " + " ".join(_normalize(t) for t in texts if t) + " "
    if padded.strip() == "":
        return Place()

    state_hits = _find(padded, state_index, allow_short)
    state_code = state_hits[0][1][0] if state_hits else None

    district_code: Optional[int] = None
    for _, codes in _find(padded, district_index, allow_short):
        in_state = [c for c in codes if state_code is not None and district_info[c][1] == state_code]
        district_code = in_state[0] if in_state else codes[0]
        break

    if district_code is not None and state_code is None:
        state_code = district_info[district_code][1]
    return Place(
        state_code=state_code,
        district_code=district_code,
        state_name=state_names.get(state_code, "") if state_code is not None else "",
        district_name=district_info[district_code][0] if district_code is not None else "",
    )
