"""
FarmerProfile model and random profile generator for the synthetic user agent.
"""

import random
from pydantic import BaseModel

from synthetic.mock_data import (
    LOCATIONS,
    FARMER_CROPS,
    MOOD_WEIGHTS,
    LANGUAGE_WEIGHTS,
    SCENARIOS,
    ADVERSARIAL_SCENARIO_IDS,
    random_name,
)


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
    latitude: float
    longitude: float

    # Farming
    crops: list[str]
    land_acres: float

    # Language & behavior
    language: str
    mood: str

    # Scenario
    scenario: dict

    # Extra context
    shc_cycle: str
    grievance_reg_no: str


def _render_scenario(scenario: dict, profile_data: dict) -> dict:
    """Render template variables in scenario descriptions."""
    rendered = dict(scenario)
    for key in ("description_en", "description_hi"):
        rendered[key] = rendered[key].format(**profile_data)
    return rendered


def generate_random_profile(
    language: str | None = None,
    scenario_id: str | None = None,
    mood: str | None = None,
) -> FarmerProfile:
    """Generate a random FarmerProfile for synthetic conversation generation.

    Args:
        language: Force a specific language ("en", "hi", "hinglish"). Random if None.
        scenario_id: Force a specific scenario by ID. Random if None.
        mood: Force a specific mood ("normal", "frustrated", "adversarial"). Random if None.
    """
    # Location
    loc = random.choice(LOCATIONS)

    # Crops: 1-3 random crops
    num_crops = random.randint(1, 3)
    crops = random.sample(FARMER_CROPS, num_crops)

    # Phone: 9 + 9 random digits
    phone = "9" + "".join(str(random.randint(0, 9)) for _ in range(9))

    # Aadhaar: 12 digits, not starting with 0 or 1
    first_digit = str(random.randint(2, 9))
    aadhaar = first_digit + "".join(str(random.randint(0, 9)) for _ in range(11))

    # PM-KISAN registration: 2-digit state code + 9 digits
    state_code = str(random.randint(10, 37)).zfill(2)
    pm_kisan_reg_no = state_code + "".join(str(random.randint(0, 9)) for _ in range(9))

    # Language (weighted random)
    if language is None:
        langs, weights = zip(*LANGUAGE_WEIGHTS.items())
        language = random.choices(langs, weights=weights, k=1)[0]

    # Mood (weighted random)
    if mood is None:
        moods, weights = zip(*MOOD_WEIGHTS.items())
        mood = random.choices(moods, weights=weights, k=1)[0]

    # Land acres: 0.5 to 15.0
    land_acres = round(random.uniform(0.5, 15.0), 1)

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
        latitude=loc["lat"],
        longitude=loc["lon"],
        crops=crops,
        land_acres=land_acres,
        language=language,
        mood=mood,
        scenario=scenario,
        shc_cycle=shc_cycle,
        grievance_reg_no=grievance_reg_no,
    )
