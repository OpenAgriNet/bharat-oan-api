"""
Mock scheme info tool — loads from pre-collected JSON fixture.
Keeps the same function signature as agents/tools/scheme_info.py.
"""

import json
import os
from typing import Literal
from helpers.utils import get_logger
from synthetic.mock_data import should_fail

logger = get_logger(__name__)

# Load scheme fixtures
_FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "assets", "scheme_fixtures.json")

try:
    with open(_FIXTURE_PATH, "r", encoding="utf-8") as f:
        SCHEME_FIXTURES = json.load(f)
except FileNotFoundError:
    logger.warning(f"Scheme fixtures not found at {_FIXTURE_PATH}, using empty dict")
    SCHEME_FIXTURES = {}


def get_scheme_info(scheme_name: Literal["kcc", "pmkisan", "pmfby", "shc", "pmksy", "sathi", "pmasha", "aif", "smam", "pdmc"]) -> str:
    """Retrieve detailed information about government agricultural schemes.

    This tool fetches comprehensive scheme data including benefits, eligibility criteria,
    application process, and other relevant details for agricultural schemes. Use this
    tool whenever users inquire about specific schemes or need general scheme information.

    Args:
        scheme_name (str): Name of the scheme to retrieve. Options:
            - "kcc": Kisan Credit Card scheme
            - "pmkisan": Pradhan Mantri Kisan Samman Nidhi scheme
            - "pmfby": Pradhan Mantri Fasal Bima Yojana scheme
            - "shc": Soil Health Card scheme
            - "pmksy": The Pradhan Mantri Krishi Sinchayee Yojana
            - "sathi": Seed Authentication, Traceability & Holistic Inventory
            - "pmasha": Pradhan Mantri Annadata Aay Sanrakshan Abhiyan
            - "aif": Agriculture Infrastructure Fund
            - "smam": Sub-Mission on Agricultural Mechanization
            - "pdmc": Per Drop More Crop scheme

    Returns:
        str: Formatted scheme data including introduction, benefits, eligibility,
             application process, and other relevant information.
    """
    # 5% chance of simulated failure
    if should_fail():
        return f"Scheme information service is temporarily unavailable for '{scheme_name}'. Please try again later."

    fixture = SCHEME_FIXTURES.get(scheme_name)
    if fixture:
        return fixture
    return f"No scheme data found for '{scheme_name}'."
