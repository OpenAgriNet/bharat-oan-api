"""
NPSS geocode mapping module.

Maps latitude/longitude coordinates to NPSS location hierarchy IDs
(state_id, district_id, sub_district_id, village_id).

Resolution order:
1. Backend-only Photon reverse geocode
2. Verified name matching through the NPSS state, district, subdistrict, and village masters

Exact names are preferred. Small, uniquely separated spelling differences are
accepted only within the verified parent hierarchy. Unresolved or ambiguous
levels are omitted so the NPSS request never records an arbitrary location.
"""
import asyncio
from difflib import SequenceMatcher
import json
import os
import re
import unicodedata
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from helpers.utils import get_logger

logger = get_logger(__name__)

_master_cache: dict[str, list[dict[str, Any]]] = {}

NPSS_BASE_URL = os.getenv("NPSS_BASE_URL", "https://npss.dac.gov.in/api3.0").rstrip("/")
PHOTON_URL = os.getenv("PHOTON_HOST")
_parsed_photon = urlparse(PHOTON_URL or "")
PHOTON_HOST = _parsed_photon.hostname or "10.128.188.19"
PHOTON_PORT = _parsed_photon.port or 2322
PHOTON_BASE_URL = f"http://{PHOTON_HOST}:{PHOTON_PORT}"


def _normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"\s+(subdistrict|sub district|tehsil|taluka|taluk|district)$", "", text).strip()


def _extract_id(row: dict[str, Any], candidates: tuple[str, ...]) -> str:
    lowered = {str(k).lower(): v for k, v in row.items()}
    for key in candidates:
        value = lowered.get(key.lower())
        if value not in (None, ""):
            return str(value).strip()
    for key, value in row.items():
        if str(key).lower().endswith("id") and value not in (None, ""):
            return str(value).strip()
    return ""


def _extract_name(row: dict[str, Any], candidates: tuple[str, ...]) -> str:
    lowered = {str(k).lower(): v for k, v in row.items()}
    for key in candidates:
        value = lowered.get(key.lower())
        if value not in (None, ""):
            return str(value).strip()
    for key, value in row.items():
        key_lower = str(key).lower()
        if ("name" in key_lower or key_lower in {"title", "label"}) and value not in (None, ""):
            return str(value).strip()
    return ""


def _unwrap_rows(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [row for row in body if isinstance(row, dict)]
    if not isinstance(body, dict):
        return []

    for key in ("data", "result", "results", "items", "records", "response"):
        value = body.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            nested = _unwrap_rows(value)
            if nested:
                return nested

    return [body] if any(str(k).lower().endswith("id") for k in body) else []


def _candidate_variants(candidates: list[str], parent_names: Optional[list[str]] = None) -> list[str]:
    """Normalize names and remove an explicitly supplied parent prefix/suffix."""
    variants: list[str] = []
    normalized_parents = [_normalize_text(parent) for parent in (parent_names or [])]
    normalized_parents = [parent for parent in normalized_parents if parent]
    for candidate in candidates:
        normalized = _normalize_text(candidate)
        if not normalized:
            continue
        if normalized not in variants:
            variants.append(normalized)
        for parent in normalized_parents:
            for prefix, suffix in ((f"{parent} ", ""), ("", f" {parent}")):
                if prefix and normalized.startswith(prefix):
                    stripped = normalized[len(prefix):].strip()
                elif suffix and normalized.endswith(suffix):
                    stripped = normalized[:-len(suffix)].strip()
                else:
                    continue
                if stripped and stripped not in variants:
                    variants.append(stripped)
    return variants


def _find_verified_rows(
    rows: list[dict[str, Any]],
    candidates: list[str],
    name_keys: tuple[str, ...],
    *,
    parent_names: Optional[list[str]] = None,
    similarity_threshold: float = 0.86,
    minimum_margin: float = 0.08,
) -> list[dict[str, Any]]:
    """Return exact or uniquely separated fuzzy master matches within one hierarchy level."""
    variants = _candidate_variants(candidates, parent_names)
    if not rows or not variants:
        return []

    named_rows = [
        (row, _normalize_text(_extract_name(row, name_keys)))
        for row in rows
    ]
    named_rows = [(row, name) for row, name in named_rows if name]
    exact_rows = [row for row, name in named_rows if name in variants]
    if exact_rows:
        return exact_rows

    scored = [
        (
            max(SequenceMatcher(None, name, candidate).ratio() for candidate in variants),
            row,
        )
        for row, name in named_rows
    ]
    if not scored:
        return []

    best_score = max(score for score, _ in scored)
    if best_score < similarity_threshold:
        return []
    best_rows = [row for score, row in scored if score == best_score]
    lower_scores = [score for score, _ in scored if score < best_score]
    next_score = max(lower_scores, default=0.0)
    if next_score and best_score - next_score < minimum_margin:
        return []
    return best_rows


def _pick_verified_row(
    rows: list[dict[str, Any]],
    candidates: list[str],
    name_keys: tuple[str, ...],
    **kwargs: Any,
) -> Optional[dict[str, Any]]:
    matches = _find_verified_rows(rows, candidates, name_keys, **kwargs)
    return matches[0] if len(matches) == 1 else None


def _location_candidates(props: dict[str, Any], *keys: str) -> list[str]:
    values: list[str] = []
    for key in keys:
        value = props.get(key)
        if not value:
            continue
        raw_value = str(value)
        candidates = [raw_value, *(part.strip() for part in raw_value.split(","))]
        for candidate in candidates:
            if candidate and candidate not in values:
                values.append(candidate)
    return values


def _all_reverse_geocode_properties(props: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the primary Photon result followed by valid nearby results."""
    return [props, *(item for item in props.get("_nearby", []) if isinstance(item, dict))]


def _location_candidates_from_sources(
    sources: list[dict[str, Any]],
    *keys: str,
) -> list[str]:
    candidates: list[str] = []
    for source in sources:
        for candidate in _location_candidates(source, *keys):
            if candidate not in candidates:
                candidates.append(candidate)
    return candidates


def _village_candidates(props: dict[str, Any]) -> list[str]:
    candidates = _location_candidates(
        props,
        "village",
        "hamlet",
        "town",
        "city",
        "locality",
        "district",
    )
    if props.get("osm_key") == "place":
        candidates.extend(_location_candidates(props, "name"))
    return candidates


def _pick_similar_subdistrict_scope(
    hierarchy_rows: list[tuple[dict[str, Any], dict[str, Any]]],
    candidates: list[str],
) -> Optional[tuple[dict[str, Any], dict[str, Any]]]:
    """Use similarity only to narrow the district searched for an exact village."""
    normalized_candidates = [_normalize_text(candidate) for candidate in candidates if candidate]
    scored: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    for district_row, subdistrict_row in hierarchy_rows:
        row_name = _normalize_text(
            _extract_name(
                subdistrict_row,
                ("subDistrictName", "subdistrictName", "sub_district_name", "name"),
            )
        )
        if not row_name:
            continue
        score = max(
            (SequenceMatcher(None, row_name, candidate).ratio() for candidate in normalized_candidates),
            default=0.0,
        )
        scored.append((score, district_row, subdistrict_row))

    if not scored:
        return None
    best_score = max(score for score, _, _ in scored)
    best_rows = [(district, subdistrict) for score, district, subdistrict in scored if score == best_score]
    return best_rows[0] if best_score >= 0.8 and len(best_rows) == 1 else None


async def _reverse_geocode_properties(latitude: float, longitude: float) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(base_url=PHOTON_BASE_URL, timeout=10.0) as client:
            base_params = {"lat": latitude, "lon": longitude, "lang": "en"}
            response = await client.get(
                "/reverse",
                params={
                    **base_params,
                    "limit": 20,
                    "radius": 10,
                },
            )
            if response.status_code == 400:
                response = await client.get("/reverse", params=base_params)
        response.raise_for_status()
        features = response.json().get("features", [])
        if not features:
            return {}
        properties = [feature.get("properties", {}) or {} for feature in features]
        primary = dict(properties[0])
        primary["_nearby"] = properties[1:]
        return primary
    except Exception as exc:
        logger.warning("NPSS background reverse geocode failed for %s,%s: %s", latitude, longitude, exc)
        return {}


async def _fetch_master_rows(
    endpoint: str,
    *,
    bearer_token: str,
    params: Optional[dict[str, str]] = None,
) -> list[dict[str, Any]]:
    cache_key = f"{endpoint}:{json.dumps(params or {}, sort_keys=True)}"
    if cache_key in _master_cache:
        return _master_cache[cache_key]

    url = f"{NPSS_BASE_URL}/api/Vistaar/{endpoint}"
    headers = {"accept": "*/*", "Authorization": f"Bearer {bearer_token}"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, params=params, headers=headers)
    response.raise_for_status()
    rows = _unwrap_rows(response.json())
    _master_cache[cache_key] = rows
    return rows


async def _resolve_from_master_apis(
    latitude: Optional[float],
    longitude: Optional[float],
    *,
    bearer_token: Optional[str],
) -> Optional[dict]:
    if latitude is None or longitude is None or not bearer_token:
        return None

    props = await _reverse_geocode_properties(float(latitude), float(longitude))
    if not props:
        return None

    reverse_sources = _all_reverse_geocode_properties(props)
    state_rows = await _fetch_master_rows("States", bearer_token=bearer_token)
    state_candidates = _location_candidates_from_sources(reverse_sources, "state", "city")
    state_row = _pick_verified_row(
        state_rows,
        # Some Photon results (notably Delhi localities) put the state only on a
        # nearby address feature. Including city is safe because the NPSS master
        # still has to contain one unique exact match.
        state_candidates,
        ("stateName", "state", "name"),
        similarity_threshold=0.9,
    )
    state_id = _extract_id(state_row or {}, ("stateId", "state_id", "id"))
    if not state_id:
        logger.warning("NPSS state could not be verified from reverse geocode properties: %s", props)
        return None

    district_rows = await _fetch_master_rows("Districts", bearer_token=bearer_token, params={"stateId": state_id})
    # Photon is not consistent across India: administrative levels may be
    # emitted as state_district/county or as district/name on nearby boundary
    # features. NPSS exact master matching remains the final authority.
    district_candidates = _location_candidates_from_sources(
        reverse_sources,
        "state_district",
        "district",
    )
    sub_district_candidates = _location_candidates_from_sources(
        reverse_sources,
        "county",
        "district",
    )
    sub_district_candidates.extend(
        candidate
        for source in reverse_sources
        if source.get("osm_value") == "administrative" or source.get("type") == "district"
        for candidate in _location_candidates(source, "name")
        if candidate not in sub_district_candidates
    )
    canonical_state = _extract_name(state_row or {}, ("stateName", "state", "name"))
    direct_district = _pick_verified_row(
        district_rows,
        district_candidates,
        ("districtName", "district", "name"),
        parent_names=[*state_candidates, canonical_state],
    )

    async def fetch_subdistricts(district_row: dict[str, Any]):
        district_id = _extract_id(district_row, ("districtId", "district_id", "id"))
        rows = await _fetch_master_rows(
            "SubDistricts",
            bearer_token=bearer_token,
            params={"stateId": state_id, "districtId": district_id},
        )
        return district_row, rows

    async def resolve_exact_village_in_district(
        district_row: dict[str, Any],
        subdistrict_rows: list[dict[str, Any]],
    ) -> Optional[dict]:
        district_id = _extract_id(district_row, ("districtId", "district_id", "id"))
        if not district_id or not subdistrict_rows:
            return None

        async def fetch_villages(subdistrict_row: dict[str, Any]):
            subdistrict_id = _extract_id(
                subdistrict_row,
                ("subDistrictId", "subdistrictId", "sub_district_id", "id"),
            )
            rows = await _fetch_master_rows(
                "Vilages",
                bearer_token=bearer_token,
                params={
                    "stateId": state_id,
                    "districtId": district_id,
                    "subDistrictId": subdistrict_id,
                },
            )
            return subdistrict_row, rows

        village_hierarchy = await asyncio.gather(*(fetch_villages(row) for row in subdistrict_rows))

        def find_exact(candidates: list[str]):
            matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
            for subdistrict_row, village_rows in village_hierarchy:
                village_row = _pick_verified_row(
                    village_rows,
                    candidates,
                    ("villageName", "village", "name"),
                    similarity_threshold=0.84,
                )
                if village_row:
                    matches.append((subdistrict_row, village_row))
            return matches[0] if len(matches) == 1 else None

        match = find_exact(_village_candidates(props))
        if not match and props.get("osm_key") != "place":
            nearest_place = next(
                (item for item in props.get("_nearby", []) if item.get("osm_key") == "place"),
                None,
            )
            if nearest_place:
                match = find_exact(_village_candidates(nearest_place))
        if not match:
            return None

        subdistrict_row, village_row = match
        subdistrict_id = _extract_id(
            subdistrict_row,
            ("subDistrictId", "subdistrictId", "sub_district_id", "id"),
        )
        village_id = _extract_id(village_row, ("villageId", "village_id", "id"))
        if not subdistrict_id or not village_id:
            return None
        return {
            "state_id": state_id,
            "district_id": district_id,
            "sub_district_id": subdistrict_id,
            "village_id": village_id,
        }

    district_row: Optional[dict[str, Any]] = None
    sub_district_row: Optional[dict[str, Any]] = None
    direct_subdistrict_rows: list[dict[str, Any]] = []
    direct_district_id = _extract_id(direct_district or {}, ("districtId", "district_id", "id"))
    if direct_district:
        _, direct_subdistrict_rows = await fetch_subdistricts(direct_district)
        direct_subdistrict = _pick_verified_row(
            direct_subdistrict_rows,
            sub_district_candidates,
            ("subDistrictName", "subdistrictName", "sub_district_name", "name"),
            similarity_threshold=0.84,
        )
        if direct_subdistrict:
            district_row, sub_district_row = direct_district, direct_subdistrict

    remaining_districts = [
        row
        for row in district_rows
        if _extract_id(row, ("districtId", "district_id", "id")) != direct_district_id
    ]
    district_subdistrict_rows = []
    if not sub_district_row:
        district_subdistrict_rows = await asyncio.gather(*(fetch_subdistricts(row) for row in remaining_districts))
    all_district_subdistrict_rows = list(district_subdistrict_rows)
    if direct_district and direct_subdistrict_rows:
        all_district_subdistrict_rows.append((direct_district, direct_subdistrict_rows))

    hierarchy_matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for possible_district, subdistrict_rows in all_district_subdistrict_rows:
        subdistrict = _pick_verified_row(
            subdistrict_rows,
            sub_district_candidates,
            ("subDistrictName", "subdistrictName", "sub_district_name", "name"),
            similarity_threshold=0.84,
        )
        if subdistrict:
            hierarchy_matches.append((possible_district, subdistrict))

    if not sub_district_row and len(hierarchy_matches) == 1:
        district_row, sub_district_row = hierarchy_matches[0]
    elif not sub_district_row and hierarchy_matches:
        matched_district = _pick_verified_row(
            [match[0] for match in hierarchy_matches],
            district_candidates,
            ("districtName", "district", "name"),
            parent_names=[*state_candidates, canonical_state],
        )
        if matched_district:
            district_id = _extract_id(matched_district, ("districtId", "district_id", "id"))
            for possible_district, possible_subdistrict in hierarchy_matches:
                if _extract_id(possible_district, ("districtId", "district_id", "id")) == district_id:
                    district_row, sub_district_row = possible_district, possible_subdistrict
                    break

    if not district_row:
        district_row = direct_district

    district_id = _extract_id(district_row or {}, ("districtId", "district_id", "id"))
    sub_district_id = _extract_id(
        sub_district_row or {},
        ("subDistrictId", "subdistrictId", "sub_district_id", "id"),
    )

    flattened_hierarchy = [
        (possible_district, possible_subdistrict)
        for possible_district, subdistrict_rows in all_district_subdistrict_rows
        for possible_subdistrict in subdistrict_rows
    ]

    if not district_id or not sub_district_id:
        similar_scope = _pick_similar_subdistrict_scope(flattened_hierarchy, sub_district_candidates)
        if similar_scope:
            scoped_district, _ = similar_scope
            scoped_district_id = _extract_id(scoped_district, ("districtId", "district_id", "id"))
            scoped_subdistricts = next(
                (
                    rows
                    for possible_district, rows in all_district_subdistrict_rows
                    if _extract_id(possible_district, ("districtId", "district_id", "id")) == scoped_district_id
                ),
                [],
            )
            exact_result = await resolve_exact_village_in_district(scoped_district, scoped_subdistricts)
            if exact_result:
                logger.info("Resolved NPSS hierarchy from exact master village match: %s", exact_result)
                return exact_result

    result = {"state_id": state_id}
    if not district_id:
        logger.warning("NPSS district could not be verified: %s", result)
        return result
    result["district_id"] = district_id
    if not sub_district_id:
        logger.warning("NPSS subdistrict could not be verified: %s", result)
        return result
    result["sub_district_id"] = sub_district_id

    selected_village = await resolve_exact_village_in_district(district_row, [sub_district_row])
    if selected_village:
        logger.info("Resolved NPSS IDs from background master APIs: %s", selected_village)
        return selected_village

    sibling_subdistricts = next(
        (
            rows
            for possible_district, rows in all_district_subdistrict_rows
            if _extract_id(possible_district, ("districtId", "district_id", "id")) == district_id
        ),
        [],
    )
    sibling_village = await resolve_exact_village_in_district(district_row, sibling_subdistricts)
    if sibling_village:
        logger.info("Resolved NPSS hierarchy from exact sibling village match: %s", sibling_village)
        return sibling_village

    logger.warning("NPSS village could not be verified: %s", result)
    return result


async def _resolve_from_location_names(
    *,
    state: Optional[str],
    district: Optional[str],
    sub_district: Optional[str],
    village: Optional[str],
    bearer_token: Optional[str],
) -> Optional[dict]:
    """Resolve farmer-provided location names against the NPSS master hierarchy."""
    if not bearer_token or not all((state, district, sub_district, village)):
        return None

    state_rows = await _fetch_master_rows("States", bearer_token=bearer_token)
    state_row = _pick_verified_row(
        state_rows,
        [state],
        ("stateName", "state", "name"),
        similarity_threshold=0.9,
    )
    state_id = _extract_id(state_row or {}, ("stateId", "state_id", "id"))
    if not state_id:
        logger.warning("Farmer-provided NPSS state could not be verified: %s", state)
        return None

    district_rows = await _fetch_master_rows(
        "Districts",
        bearer_token=bearer_token,
        params={"stateId": state_id},
    )
    canonical_state = _extract_name(state_row or {}, ("stateName", "state", "name"))
    district_row = _pick_verified_row(
        district_rows,
        [district],
        ("districtName", "district", "name"),
        parent_names=[state, canonical_state],
    )
    district_id = _extract_id(district_row or {}, ("districtId", "district_id", "id"))
    if not district_id:
        logger.warning(
            "Farmer-provided NPSS district could not be verified: state=%s district=%s",
            state,
            district,
        )
        return None

    subdistrict_rows = await _fetch_master_rows(
        "SubDistricts",
        bearer_token=bearer_token,
        params={"stateId": state_id, "districtId": district_id},
    )
    canonical_district = _extract_name(
        district_row or {},
        ("districtName", "district", "name"),
    )
    subdistrict_matches = _find_verified_rows(
        subdistrict_rows,
        [sub_district],
        ("subDistrictName", "subdistrictName", "sub_district_name", "name"),
        parent_names=[district, canonical_district],
        similarity_threshold=0.84,
    )
    if not subdistrict_matches:
        logger.warning(
            "Farmer-provided NPSS subdistrict could not be verified: state=%s district=%s subdistrict=%s",
            state,
            district,
            sub_district,
        )
        return None

    async def fetch_villages(subdistrict_row: dict[str, Any]):
        subdistrict_id = _extract_id(
            subdistrict_row,
            ("subDistrictId", "subdistrictId", "sub_district_id", "id"),
        )
        village_rows = await _fetch_master_rows(
            "Vilages",
            bearer_token=bearer_token,
            params={
                "stateId": state_id,
                "districtId": district_id,
                "subDistrictId": subdistrict_id,
            },
        )
        return subdistrict_row, subdistrict_id, village_rows

    village_scopes = await asyncio.gather(*(fetch_villages(row) for row in subdistrict_matches))
    hierarchy_matches: list[dict[str, str]] = []
    for subdistrict_row, subdistrict_id, village_rows in village_scopes:
        canonical_subdistrict = _extract_name(
            subdistrict_row,
            ("subDistrictName", "subdistrictName", "sub_district_name", "name"),
        )
        village_matches = _find_verified_rows(
            village_rows,
            [village],
            ("villageName", "village", "name"),
            parent_names=[sub_district, canonical_subdistrict],
            similarity_threshold=0.84,
        )
        for village_row in village_matches:
            village_id = _extract_id(village_row, ("villageId", "village_id", "id"))
            if subdistrict_id and village_id:
                hierarchy_matches.append(
                    {
                        "state_id": state_id,
                        "district_id": district_id,
                        "sub_district_id": subdistrict_id,
                        "village_id": village_id,
                    }
                )

    if len(hierarchy_matches) != 1:
        logger.warning(
            "Farmer-provided NPSS hierarchy could not be uniquely verified: "
            "state=%s district=%s subdistrict=%s village=%s matches=%s",
            state,
            district,
            sub_district,
            village,
            len(hierarchy_matches),
        )
        return None

    result = hierarchy_matches[0]
    logger.info("Resolved NPSS hierarchy from farmer-provided location names: %s", result)
    return result


async def resolve_npss_location_ids(
    latitude: Optional[float],
    longitude: Optional[float],
    *,
    bearer_token: Optional[str] = None,
    state: Optional[str] = None,
    district: Optional[str] = None,
    sub_district: Optional[str] = None,
    village: Optional[str] = None,
) -> Optional[dict]:
    coordinate_result: Optional[dict] = None
    try:
        coordinate_result = await _resolve_from_master_apis(latitude, longitude, bearer_token=bearer_token)
        if coordinate_result and all(
            coordinate_result.get(key)
            for key in ("state_id", "district_id", "sub_district_id", "village_id")
        ):
            return coordinate_result
    except Exception as exc:
        logger.warning("NPSS background master lookup failed: %s", exc)

    try:
        named_result = await _resolve_from_location_names(
            state=state,
            district=district,
            sub_district=sub_district,
            village=village,
            bearer_token=bearer_token,
        )
        if named_result:
            return named_result
    except Exception as exc:
        logger.warning("NPSS farmer-provided location lookup failed: %s", exc)

    return coordinate_result
