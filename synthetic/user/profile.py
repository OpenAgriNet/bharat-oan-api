"""
FarmerProfile model and random profile generator for the synthetic user agent.
"""

import random
from pydantic import BaseModel

from synthetic.mock_data import (
    get_random_location,
    FARMER_CROPS,
    MOOD_WEIGHTS,
    LANGUAGE_WEIGHTS,
    LATIN_SCRIPT_PROBABILITY,
    SCENARIOS,
    ADVERSARIAL_SCENARIO_IDS,
    fake,
    random_name,
)

# PM-KISAN uses 2-letter state codes
STATE_CODES: dict[str, str] = {
    "Andhra Pradesh": "AP",
    "Assam": "AS",
    "Bihar": "BR",
    "Chhattisgarh": "CG",
    "Gujarat": "GJ",
    "Haryana": "HR",
    "Jharkhand": "JH",
    "Karnataka": "KA",
    "Kerala": "KL",
    "Madhya Pradesh": "MP",
    "Maharashtra": "MH",
    "Odisha": "OD",
    "Punjab": "PB",
    "Rajasthan": "RJ",
    "Tamil Nadu": "TN",
    "Telangana": "TG",
    "Uttar Pradesh": "UP",
    "West Bengal": "WB",
    "Delhi": "DL",
    "Goa": "GA",
    "Himachal Pradesh": "HP",
    "Jammu and Kashmir": "JK",
    "Manipur": "MN",
    "Meghalaya": "ML",
    "Mizoram": "MZ",
    "Nagaland": "NL",
    "Sikkim": "SK",
    "Tripura": "TR",
    "Uttarakhand": "UK",
}


class FarmerProfile(BaseModel):
    """Profile of a simulated Indian farmer for synthetic conversation generation."""

    # Identity
    name: str
    phone: str
    aadhaar: str
    pm_kisan_reg_no: str

    # Location
    state: str
    district: str
    village: str

    # Farming
    crops: list[str]
    land_acres: float

    # Language & behavior
    language: str
    use_latin_script: bool = False
    mood: str
    verbosity: str  # "low", "medium", "high"

    # Scenario
    scenario: dict

    # Extra context
    shc_cycle: str
    grievance_reg_no: str


def _render_scenario(scenario: dict, profile_data: dict) -> dict:
    """Render template variables in scenario descriptions."""
    rendered = dict(scenario)
    rendered["description"] = rendered["description"].format(**profile_data)
    return rendered


def generate_random_profile(
    language: str | None = None,
    scenario_id: str | None = None,
    mood: str | None = None,
) -> FarmerProfile:
    """Generate a random FarmerProfile for synthetic conversation generation.

    Args:
        language: Force a specific language ("en", "hi", etc.). Random if None.
        scenario_id: Force a specific scenario by ID. Random if None.
        mood: Force a specific mood ("normal", "frustrated", "adversarial"). Random if None.
    """
    # Location (from Photon API, falls back to hardcoded list)
    loc = get_random_location()

    # Crops: 1-3 random crops
    num_crops = random.randint(1, 3)
    crops = random.sample(FARMER_CROPS, num_crops)

    # Phone: Indian mobile number (10 digits starting with 6-9)
    phone = str(random.choice([6, 7, 8, 9])) + "".join(str(random.randint(0, 9)) for _ in range(9))

    # Aadhaar: 12-digit Indian ID via faker
    aadhaar = fake.aadhaar_id()

    # PM-KISAN registration: 2-letter state code + 9 digits
    state_code = STATE_CODES.get(loc["state"], "XX")
    pm_kisan_reg_no = state_code + "".join(str(random.randint(0, 9)) for _ in range(9))

    # Language (weighted random)
    if language is None:
        langs, weights = zip(*LANGUAGE_WEIGHTS.items())
        language = random.choices(langs, weights=weights, k=1)[0]

    # Latin script transliteration (~5% chance, only for non-English languages)
    use_latin_script = language != "en" and random.random() < LATIN_SCRIPT_PROBABILITY

    # Mood (weighted random)
    if mood is None:
        moods, weights = zip(*MOOD_WEIGHTS.items())
        mood = random.choices(moods, weights=weights, k=1)[0]

    # Land acres: 0.5 to 15.0
    land_acres = round(random.uniform(0.5, 15.0), 1)

    # Verbosity (weighted random)
    verbosity = random.choices(
        ["low", "medium", "high"],
        weights=[0.7, 0.2, 0.1],
        k=1,
    )[0]

    # Scenario
    if scenario_id is not None:
        scenario = next(s for s in SCENARIOS if s["id"] == scenario_id)
    elif mood == "adversarial":
        adversarial_scenarios = [s for s in SCENARIOS if s["id"] in ADVERSARIAL_SCENARIO_IDS]
        scenario = random.choice(adversarial_scenarios)
    else:
        normal_scenarios = [s for s in SCENARIOS if s["id"] not in ADVERSARIAL_SCENARIO_IDS]
        scenario = random.choice(normal_scenarios)

    # Render template variables in scenario descriptions
    scenario = _render_scenario(scenario, {
        "crop": random.choice(crops),
        "district": loc["district"],
        "state": loc["state"],
        "village": loc["village"],
        "land_acres": land_acres,
    })

    # Extra context
    cycle_start = random.choice([2023, 2024, 2025])
    shc_cycle = f"{cycle_start}-{str(cycle_start + 1)[-2:]}"
    grievance_reg_no = "GRV" + "".join(str(random.randint(0, 9)) for _ in range(8))

    return FarmerProfile(
        name=random_name(),
        phone=phone,
        aadhaar=aadhaar,
        pm_kisan_reg_no=pm_kisan_reg_no,
        state=loc["state"],
        district=loc["district"],
        village=loc["village"],
        crops=crops,
        land_acres=land_acres,
        language=language,
        use_latin_script=use_latin_script,
        mood=mood,
        verbosity=verbosity,
        scenario=scenario,
        shc_cycle=shc_cycle,
        grievance_reg_no=grievance_reg_no,
    )
