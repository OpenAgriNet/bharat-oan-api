"""
NPSS geocode mapping module.

Maps latitude/longitude coordinates to NPSS location hierarchy IDs
(state_id, district_id, sub_district_id, village_id).

Resolution order:
1. Backend-only Photon reverse geocode
2. Exact name matching through the NPSS state, district, subdistrict, and village masters

Only verified hierarchy levels are returned. Unresolved levels are omitted so the
NPSS request never records an arbitrary location.
"""
import asyncio
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


def _pick_best_row(rows: list[dict[str, Any]], candidates: list[str], name_keys: tuple[str, ...]) -> Optional[dict[str, Any]]:
    normalized_candidates = [_normalize_text(candidate) for candidate in candidates if candidate]
    normalized_candidates = [candidate for candidate in normalized_candidates if candidate and candidate != "unknown location"]
    if not rows or not normalized_candidates:
        return None

    exact_rows: list[dict[str, Any]] = []
    for row in rows:
        row_name = _normalize_text(_extract_name(row, name_keys))
        if not row_name:
            continue
        if row_name in normalized_candidates:
            exact_rows.append(row)

    return exact_rows[0] if len(exact_rows) == 1 else None


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

    state_rows = await _fetch_master_rows("States", bearer_token=bearer_token)
    state_row = _pick_best_row(
        state_rows,
        _location_candidates(props, "state"),
        ("stateName", "state", "name"),
    )
    state_id = _extract_id(state_row or {}, ("stateId", "state_id", "id"))
    if not state_id:
        logger.warning("NPSS state could not be verified from reverse geocode properties: %s", props)
        return None

    district_rows = await _fetch_master_rows("Districts", bearer_token=bearer_token, params={"stateId": state_id})
    admin_source = props
    if not props.get("county"):
        admin_source = next(
            (item for item in props.get("_nearby", []) if item.get("county")),
            props,
        )
    sub_district_candidates = _location_candidates(admin_source, "county")

    district_candidates = _location_candidates(admin_source, "state_district")
    direct_district = _pick_best_row(
        district_rows,
        district_candidates,
        ("districtName", "district", "name"),
    )

    async def fetch_subdistricts(district_row: dict[str, Any]):
        district_id = _extract_id(district_row, ("districtId", "district_id", "id"))
        rows = await _fetch_master_rows(
            "SubDistricts",
            bearer_token=bearer_token,
            params={"stateId": state_id, "districtId": district_id},
        )
        return district_row, rows

    district_row: Optional[dict[str, Any]] = None
    sub_district_row: Optional[dict[str, Any]] = None
    direct_district_id = _extract_id(direct_district or {}, ("districtId", "district_id", "id"))
    if direct_district:
        _, direct_subdistrict_rows = await fetch_subdistricts(direct_district)
        direct_subdistrict = _pick_best_row(
            direct_subdistrict_rows,
            sub_district_candidates,
            ("subDistrictName", "subdistrictName", "sub_district_name", "name"),
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
    hierarchy_matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for possible_district, subdistrict_rows in district_subdistrict_rows:
        subdistrict = _pick_best_row(
            subdistrict_rows,
            sub_district_candidates,
            ("subDistrictName", "subdistrictName", "sub_district_name", "name"),
        )
        if subdistrict:
            hierarchy_matches.append((possible_district, subdistrict))

    if not sub_district_row and len(hierarchy_matches) == 1:
        district_row, sub_district_row = hierarchy_matches[0]
    elif not sub_district_row and hierarchy_matches:
        matched_district = _pick_best_row(
            [match[0] for match in hierarchy_matches],
            district_candidates,
            ("districtName", "district", "name"),
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

    result = {"state_id": state_id}
    if not district_id:
        logger.warning("NPSS district could not be verified; submitting state only: %s", result)
        return result
    result["district_id"] = district_id
    if not sub_district_id:
        logger.warning("NPSS subdistrict could not be verified; submitting state and district: %s", result)
        return result
    result["sub_district_id"] = sub_district_id

    village_rows = await _fetch_master_rows(
        "Vilages",
        bearer_token=bearer_token,
        params={"stateId": state_id, "districtId": district_id, "subDistrictId": sub_district_id},
    )
    village_candidates = _location_candidates(
        props,
        "village",
        "hamlet",
        "town",
        "city",
        "locality",
        "district",
    )
    if props.get("osm_key") == "place":
        village_candidates.extend(_location_candidates(props, "name"))
    village_row = _pick_best_row(
        village_rows,
        village_candidates,
        ("villageName", "village", "name"),
    )
    if not village_row and props.get("osm_key") != "place":
        nearest_place = next(
            (item for item in props.get("_nearby", []) if item.get("osm_key") == "place"),
            None,
        )
        if nearest_place:
            nearby_candidates = _location_candidates(
                nearest_place,
                "village",
                "hamlet",
                "town",
                "city",
                "locality",
                "district",
                "name",
            )
            village_row = _pick_best_row(
                village_rows,
                nearby_candidates,
                ("villageName", "village", "name"),
            )
    village_id = _extract_id(village_row or {}, ("villageId", "village_id", "id"))
    if not village_id:
        logger.warning("NPSS village could not be verified; submitting verified parent hierarchy: %s", result)
        return result

    result["village_id"] = village_id
    logger.info("Resolved NPSS IDs from background master APIs: %s", result)
    return result


async def resolve_npss_location_ids(
    latitude: Optional[float],
    longitude: Optional[float],
    *,
    bearer_token: Optional[str] = None,
) -> Optional[dict]:
    try:
        master_result = await _resolve_from_master_apis(latitude, longitude, bearer_token=bearer_token)
        if master_result:
            return master_result
    except Exception as exc:
        logger.warning("NPSS background master lookup failed: %s", exc)

    return None
