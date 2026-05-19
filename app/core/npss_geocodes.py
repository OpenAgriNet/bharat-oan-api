"""
NPSS geocode mapping module.

Maps latitude/longitude coordinates to NPSS location hierarchy IDs
(state_id, district_id, sub_district_id, village_id).

The mapping data is loaded from assets/data/npss_geocode_map.json.
Coordinates are rounded to 1 decimal place for lookup.

To populate the full mapping:
1. Export the complete geocode dataset from your source
2. Update assets/data/npss_geocode_map.json with all entries
3. Restart the application to reload
"""
import json
import os
from typing import Dict, Optional
from pathlib import Path
from helpers.utils import get_logger

logger = get_logger(__name__)

# Default fallback location IDs when no mapping is found
DEFAULT_LOCATION = {
    "state_id": "1",
    "district_id": "1",
    "sub_district_id": "1",
    "village_id": "1",
}

_npss_geocode_map: Dict[str, dict] = {}


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

    if not map_path.exists():
        logger.warning(f"NPSS geocode map not found at {map_path}. Using empty map.")
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


def get_npss_location_ids(latitude: Optional[float], longitude: Optional[float]) -> dict:
    """
    Look up NPSS location IDs for given coordinates.

    Args:
        latitude: Latitude coordinate
        longitude: Longitude coordinate

    Returns:
        Dict with state_id, district_id, sub_district_id, village_id.
        Falls back to DEFAULT_LOCATION if no mapping exists.
    """
    if latitude is None or longitude is None:
        logger.warning("No coordinates provided for NPSS geocode lookup. Using default location.")
        return DEFAULT_LOCATION.copy()

    # Round to 1 decimal place for bucketed lookup
    key = f"{round(latitude, 1)},{round(longitude, 1)}"
    mapping = _load_geocode_map()

    if key in mapping:
        result = mapping[key]
        logger.info(f"NPSS geocode lookup hit for {key}: {result}")
        return {
            "state_id": str(result.get("state_id", DEFAULT_LOCATION["state_id"])),
            "district_id": str(result.get("district_id", DEFAULT_LOCATION["district_id"])),
            "sub_district_id": str(result.get("sub_district_id", DEFAULT_LOCATION["sub_district_id"])),
            "village_id": str(result.get("village_id", DEFAULT_LOCATION["village_id"])),
        }

    logger.warning(f"NPSS geocode lookup miss for {key}. Using default location.")
    return DEFAULT_LOCATION.copy()
