"""
NPSS geocode mapping module.

Maps latitude/longitude coordinates to NPSS location hierarchy IDs
(state_id, district_id, sub_district_id, village_id).

Resolution order:
1. Local JSON mapping from NPSS_GEOCODE_MAP_PATH / assets/data/npss_geocode_map.json
2. Backend-only Photon reverse geocode + NPSS master APIs
3. Optional legacy default fallback, gated by NPSS_ALLOW_DEFAULT_LOCATION
"""
import json
import os
import re
import unicodedata
from typing import Any, Dict, Optional
from pathlib import Path
from urllib.parse import urlparse

import httpx

from helpers.utils import get_logger

logger = get_logger(__name__)

# Legacy fallback location IDs. Disabled by default because sending these values
# records every NPSS request against the same place and corrupts reporting.
DEFAULT_LOCATION = {
    "state_id": "1",
    "district_id": "1",
    "sub_district_id": "1",
    "village_id": "1",
}
ALLOW_DEFAULT_LOCATION = os.getenv("NPSS_ALLOW_DEFAULT_LOCATION", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

_npss_geocode_map: Dict[str, dict] = {}
_master_cache: dict[str, list[dict[str, Any]]] = {}

NPSS_BASE_URL = os.getenv("NPSS_BASE_URL", "https://npss.dac.gov.in/api3.0").rstrip("/")
PHOTON_URL = os.getenv("PHOTON_HOST")
_parsed_photon = urlparse(PHOTON_URL or "")
PHOTON_HOST = _parsed_photon.hostname or "10.128.188.19"
PHOTON_PORT = _parsed_photon.port or 2322
PHOTON_BASE_URL = f"http://{PHOTON_HOST}:{PHOTON_PORT}"
INDIA_BBOX = "68.0,6.0,98.0,36.0"


def _load_geocode_map() -> Dict[str, dict]:
    """Load the NPSS geocode mapping from JSON file."""
    global _npss_geocode_map

    if _npss_geocode_map:
        return _npss_geocode_map

    # Determine path: prefer env override, then default assets location
    env_path = os.getenv("NPSS_GEOCODE_MAP_PATH")
    if env_path:
        map_path = Path(env_path)
    else:
        base_dir = Path(__file__).resolve().parent.parent.parent
        map_path = base_dir / "assets" / "data" / "npss_geocode_map.json"

    if not map_path.is_file():
        logger.warning(f"NPSS geocode map file not found at {map_path}. Using empty map.")
        _npss_geocode_map = {}
        return _npss_geocode_map

    try:
        with open(map_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Filter out metadata keys
        _npss_geocode_map = {
            k: v for k, v in data.items()
            if not k.startswith("_") and k != "format"
        }
        logger.info(f"Loaded {_npss_geocode_map.__len__()} NPSS geocode entries from {map_path}")
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"Failed to load NPSS geocode map: {e}")
        _npss_geocode_map = {}

    return _npss_geocode_map


def _legacy_default_location() -> dict:
    if ALLOW_DEFAULT_LOCATION:
        logger.warning(
            "Using legacy NPSS default location IDs. Set NPSS_ALLOW_DEFAULT_LOCATION=false "
            "and configure NPSS_GEOCODE_MAP_PATH to avoid inaccurate location reporting."
        )
        return DEFAULT_LOCATION.copy()
    return {}


def _normalize_location_ids(result: dict) -> dict:
    return {
        "state_id": str(result.get("state_id", "")).strip(),
        "district_id": str(result.get("district_id", "")).strip(),
        "sub_district_id": str(result.get("sub_district_id", "")).strip(),
        "village_id": str(result.get("village_id", "")).strip(),
    }


def get_npss_location_ids(latitude: Optional[float], longitude: Optional[float]) -> Optional[dict]:
    """
    Look up NPSS location IDs for given coordinates.

    Args:
        latitude: Latitude coordinate
        longitude: Longitude coordinate

    Returns:
        Dict with state_id, district_id, sub_district_id, village_id when a
        mapping exists. Returns None if no mapping exists, unless the legacy
        NPSS_ALLOW_DEFAULT_LOCATION escape hatch is enabled.
    """
    if latitude is None or longitude is None:
        logger.warning("No coordinates provided for NPSS geocode lookup.")
        return _legacy_default_location() or None

    # Round to 1 decimal place for bucketed lookup
    key = f"{round(latitude, 1)},{round(longitude, 1)}"
    mapping = _load_geocode_map()

    if key in mapping:
        result = _normalize_location_ids(mapping[key])
        if all(result.values()):
            logger.info(f"NPSS geocode lookup hit for {key}: {result}")
            return result
        logger.warning(f"NPSS geocode lookup for {key} is incomplete: {result}")

    logger.warning(f"NPSS geocode lookup miss for {key}.")
    return _legacy_default_location() or None


def _normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


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
        return rows[0] if len(rows) == 1 else None

    best_row = None
    best_score = 0
    for row in rows:
        row_name = _normalize_text(_extract_name(row, name_keys))
        if not row_name:
            continue
        score = 0
        for candidate in normalized_candidates:
            if row_name == candidate:
                score = max(score, 100)
            elif row_name in candidate or candidate in row_name:
                score = max(score, 80)
            elif set(row_name.split()) & set(candidate.split()):
                score = max(score, 20)
        if score > best_score:
            best_row = row
            best_score = score

    return best_row if best_score >= 20 else None


def _pick_deterministic_child_row(rows: list[dict[str, Any]], id_keys: tuple[str, ...]) -> Optional[dict[str, Any]]:
    if not rows:
        return None
    return sorted(rows, key=lambda row: _extract_id(row, id_keys) or json.dumps(row, sort_keys=True))[0]


def _location_candidates(props: dict[str, Any], *keys: str) -> list[str]:
    values: list[str] = []
    for key in keys:
        value = props.get(key)
        if value and str(value) not in values:
            values.append(str(value))
    return values


async def _reverse_geocode_properties(latitude: float, longitude: float) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(base_url=PHOTON_BASE_URL, timeout=10.0) as client:
            response = await client.get(
                "/reverse",
                params={
                    "lat": latitude,
                    "lon": longitude,
                    "lang": "en",
                    "bbox": INDIA_BBOX,
                },
            )
        response.raise_for_status()
        features = response.json().get("features", [])
        if not features:
            return {}
        return features[0].get("properties", {}) or {}
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
    if not state_row:
        state_row = _pick_deterministic_child_row(state_rows, ("stateId", "state_id", "id"))
        if state_row:
            logger.warning("NPSS state exact match failed; using deterministic master state: %s", state_row)
    state_id = _extract_id(state_row or {}, ("stateId", "state_id", "id"))
    if not state_id:
        return None

    district_rows = await _fetch_master_rows("Districts", bearer_token=bearer_token, params={"stateId": state_id})
    district_row = _pick_best_row(
        district_rows,
        _location_candidates(props, "district", "county", "city"),
        ("districtName", "district", "name"),
    )
    if not district_row:
        district_row = _pick_deterministic_child_row(district_rows, ("districtId", "district_id", "id"))
        if district_row:
            logger.warning(
                "NPSS district exact match failed; using deterministic child within state %s: %s",
                state_id,
                district_row,
            )
    district_id = _extract_id(district_row or {}, ("districtId", "district_id", "id"))
    if not district_id:
        return None

    sub_district_rows = await _fetch_master_rows(
        "SubDistricts",
        bearer_token=bearer_token,
        params={"stateId": state_id, "districtId": district_id},
    )
    sub_district_row = _pick_best_row(
        sub_district_rows,
        _location_candidates(props, "city", "county", "name"),
        ("subDistrictName", "subdistrictName", "sub_district_name", "name"),
    )
    if not sub_district_row:
        sub_district_row = _pick_deterministic_child_row(
            sub_district_rows,
            ("subDistrictId", "subdistrictId", "sub_district_id", "id"),
        )
        if sub_district_row:
            logger.warning(
                "NPSS subdistrict exact match failed; using deterministic child within district %s: %s",
                district_id,
                sub_district_row,
            )
    sub_district_id = _extract_id(sub_district_row or {}, ("subDistrictId", "subdistrictId", "sub_district_id", "id"))
    if not sub_district_id:
        return None

    village_rows = await _fetch_master_rows(
        "Vilages",
        bearer_token=bearer_token,
        params={"stateId": state_id, "districtId": district_id, "subDistrictId": sub_district_id},
    )
    village_row = _pick_best_row(
        village_rows,
        _location_candidates(props, "name", "city"),
        ("villageName", "village", "name"),
    )
    if not village_row:
        village_row = _pick_deterministic_child_row(village_rows, ("villageId", "village_id", "id"))
        if village_row:
            logger.warning(
                "NPSS village exact match failed; using deterministic child within subdistrict %s: %s",
                sub_district_id,
                village_row,
            )
    village_id = _extract_id(village_row or {}, ("villageId", "village_id", "id"))
    if not village_id:
        return None

    result = {
        "state_id": state_id,
        "district_id": district_id,
        "sub_district_id": sub_district_id,
        "village_id": village_id,
    }
    logger.info("Resolved NPSS IDs from background master APIs: %s", result)
    return result


async def resolve_npss_location_ids(
    latitude: Optional[float],
    longitude: Optional[float],
    *,
    bearer_token: Optional[str] = None,
) -> Optional[dict]:
    local_result = get_npss_location_ids(latitude, longitude)
    if local_result:
        return local_result

    try:
        master_result = await _resolve_from_master_apis(latitude, longitude, bearer_token=bearer_token)
        if master_result:
            return master_result
    except Exception as exc:
        logger.warning("NPSS background master lookup failed: %s", exc)

    return _legacy_default_location() or None
