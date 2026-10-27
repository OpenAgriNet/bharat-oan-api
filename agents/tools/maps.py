"""
Maps tool for geocoding using Photon.
"""
import os
import httpx
from dotenv import load_dotenv
from typing import Optional, Any, Tuple
from urllib.parse import urlparse
from pydantic import BaseModel, field_validator
from langfuse import observe
from helpers.utils import get_logger

logger = get_logger(__name__)

load_dotenv()

# Photon configuration
photon_url = os.getenv("PHOTON_HOST")
parsed = urlparse(photon_url)
PHOTON_HOST = parsed.hostname or "10.128.188.19"
PHOTON_PORT = parsed.port or 2322
PHOTON_BASE_URL = f"http://{PHOTON_HOST}:{PHOTON_PORT}"

# Shared async client for connection reuse
_http_client = httpx.AsyncClient(base_url=PHOTON_BASE_URL, timeout=10.0)

logger.info(f"Using Photon geocoder at {PHOTON_HOST}:{PHOTON_PORT}")

# India bounding box [min_lon, min_lat, max_lon, max_lat]
INDIA_BBOX = [68.0, 6.0, 98.0, 36.0]


class Location(BaseModel):
    """Location model for the maps tool."""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    place_name: Optional[str] = None

    @field_validator('latitude', 'longitude')
    @classmethod
    def round_coordinates(cls, v):
        if v is not None:
            return round(float(v), 3)
        return v

    def model_post_init(self, __context__: Any) -> None:
        """Called after the model is initialized."""
        super().model_post_init(__context__)

    def _location_string(self):
        if self.latitude and self.longitude:
            return f"{self.place_name} (Latitude: {self.latitude}, Longitude: {self.longitude})"
        else:
            return "Location not available"

    def __str__(self):
        return f"{self.place_name} ({self.latitude}, {self.longitude})"


def _in_india(coords: list) -> bool:
    return (len(coords) >= 2 and INDIA_BBOX[1] <= coords[1] <= INDIA_BBOX[3]
            and INDIA_BBOX[0] <= coords[0] <= INDIA_BBOX[2])


def _feature_to_location(feature: dict, fallback_name: str = None) -> Location:
    """Convert a Photon GeoJSON feature to a Location object."""
    props = feature.get("properties", {})
    coords = feature.get("geometry", {}).get("coordinates", [])

    # Photon returns [lon, lat]
    longitude = coords[0] if len(coords) > 0 else None
    latitude = coords[1] if len(coords) > 1 else None

    # Build display name: Name, City/County, State, Country
    name_parts = []
    if props.get("name"):
        name_parts.append(props["name"])
    city = props.get("city")
    if city and city != props.get("name"):
        name_parts.append(city)
    elif props.get("county") and props.get("county") != props.get("name"):
        name_parts.append(props["county"])
    if props.get("state"):
        name_parts.append(props["state"])
    if props.get("country"):
        name_parts.append(props["country"])

    display_name = ", ".join(name_parts) if name_parts else (fallback_name or "Unknown Location")

    return Location(place_name=display_name, latitude=latitude, longitude=longitude)



def _area_name(feature: dict) -> Optional[str]:
    """'Srinagar, Jammu and Kashmir, India' for a point: its town or district, never the nearest shop or street."""
    props = feature.get("properties", {})
    parts = [props.get("city") or props.get("county"), props.get("state"), props.get("country")]
    return ", ".join(part for part in parts if part) or None


async def locate(place_name: str) -> Optional[Tuple[Location, Optional[str]]]:
    """Best Photon match for a place name (preferring India) and its place type, e.g. "city" or "state".

    None when nothing is found; httpx errors are left to the caller.
    """
    response = await _http_client.get("/api", params={
        "q": place_name,
        "limit": 10,
        "lang": "en",
        "bbox": f"{INDIA_BBOX[0]},{INDIA_BBOX[1]},{INDIA_BBOX[2]},{INDIA_BBOX[3]}",
    })
    response.raise_for_status()
    features = response.json().get("features", [])
    if not features:
        return None
    in_india = (feature for feature in features if _in_india(feature.get("geometry", {}).get("coordinates", [])))
    feature = next(in_india, features[0])
    return _feature_to_location(feature, place_name), feature.get("properties", {}).get("type")


@observe(name="tool:forward_geocode", as_type="tool")
async def forward_geocode(place_name: str) -> str:
    """Forward Geocoding to get latitude and longitude from a place name in India.

    Args:
        place_name (str): The place name to geocode, in English. For best results, include additional context like district or state (e.g. "Pune, Maharashtra" or "Latur, Maharashtra").

    Returns:
        str: The location details or an error message if not found in India.
    """
    try:
        found = await locate(place_name)
        if found:
            location, _ = found
            return location._location_string()
        else:
            logger.info(f"No results found for place: {place_name}")
            return f"No location found for '{place_name}'. Please check the spelling or try a different location name."

    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        if status == 400:
            logger.warning(f"Photon bad request for '{place_name}': {e.response.text}")
            return f"Invalid geocoding request for '{place_name}'. Please check the place name and try again."
        else:
            logger.error(f"Photon server error ({status}) for '{place_name}': {e.response.text}")
            return f"Unable to find location for '{place_name}'. Geocoding service returned an error. Please try again later."
    except httpx.TimeoutException:
        logger.error(f"Photon timeout for '{place_name}'")
        return f"Unable to find location for '{place_name}'. The geocoding service timed out. Please try again later."
    except httpx.ConnectError:
        logger.error(f"Photon connection error for '{place_name}'")
        return f"Unable to find location for '{place_name}'. Could not connect to the geocoding service."
    except Exception as e:
        logger.error(f"Unexpected error during forward geocoding for '{place_name}': {e}")
        return f"Unable to find location for '{place_name}'. Please try again later."



@observe(name="tool:reverse_geocode", as_type="tool")
async def reverse_geocode(latitude: float, longitude: float) -> Optional[Location]:
    """Reverse Geocoding to get place name from latitude and longitude.

    Args:
        latitude (float): The latitude of the location.
        longitude (float): The longitude of the location.

    Returns:
        Location: The location of the place.
    """
    try:
        response = await _http_client.get("/reverse", params={
            "lat": latitude,
            "lon": longitude,
            "lang": "en",
        })
        response.raise_for_status()
        features = response.json().get("features", [])

        if features:
            return Location(place_name=_area_name(features[0]) or "Unknown Location",
                            latitude=latitude, longitude=longitude)
        else:
            logger.info(f"No results found for coordinates: ({latitude}, {longitude})")
            return Location(latitude=latitude, longitude=longitude, place_name="Unknown Location")

    except httpx.HTTPStatusError as e:
        logger.error(f"Photon HTTP error ({e.response.status_code}) for ({latitude}, {longitude}): {e.response.text}")
        return Location(latitude=latitude, longitude=longitude, place_name="Unknown Location")
    except httpx.TimeoutException:
        logger.error(f"Photon timeout for ({latitude}, {longitude})")
        return Location(latitude=latitude, longitude=longitude, place_name="Unknown Location")
    except httpx.ConnectError:
        logger.error(f"Photon connection error for ({latitude}, {longitude})")
        return Location(latitude=latitude, longitude=longitude, place_name="Unknown Location")
    except Exception as e:
        logger.error(f"Unexpected error during reverse geocoding for ({latitude}, {longitude}): {e}")
        return Location(latitude=latitude, longitude=longitude, place_name="Unknown Location")
