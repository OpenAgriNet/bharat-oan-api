"""
Mock Soil Health Card status tool — generates realistic SHC reports.
Matches the format of real Soil Health Card reports from the Government of India.
Includes report link, farmer info, soil parameters with ratings, and
per-crop fertilizer recommendations (Combo-1 / Combo-2).
"""

import random
import string
from datetime import timedelta
from pydantic_ai.tools import RunContext
from synthetic.deps import FarmerContext
from synthetic.mock_data import (
    LOCATIONS,
    SHC_SOIL_TYPES,
    SHC_CROP_FERTILIZERS,
    SHC_ORGANIC_REC,
    random_name,
    should_fail,
)


# ─── Nutrient rating functions ───────────────────────────────────────────────

def _ph_rating(v: float) -> str:
    if v < 5.5:
        return "Acidic"
    if v < 6.5:
        return "Slightly Acidic"
    if v < 7.5:
        return "Neutral"
    if v <= 8.5:
        return "Alkaline"
    return "Highly Alkaline"


def _ec_rating(v: float) -> str:
    if v < 1.0:
        return "Normal"
    if v < 3.0:
        return "Saline"
    return "Highly Saline"


def _oc_rating(v: float) -> str:
    if v < 0.50:
        return "Low"
    if v <= 0.75:
        return "Medium"
    return "High"


def _npk_rating(v: float, low: float, high: float) -> str:
    if v < low:
        return "Low"
    if v <= high:
        return "Medium"
    return "High"


def _micro_rating(v: float, threshold: float) -> str:
    return "Deficient" if v < threshold else "Sufficient"


def _format_combo(combo: dict) -> str:
    """Format fertilizer combination: 'DAP - 17 Kg Per Acre, Urea - 45 Kg Per Acre'."""
    parts = []
    for fert, base_amount in combo.items():
        amount = max(0, base_amount + random.randint(-2, 2))
        parts.append(f"{fert} - {amount} Kg Per Acre")
    return ", ".join(parts)


# ─── Main tool function ─────────────────────────────────────────────────────

async def check_shc_status(
    ctx: RunContext[FarmerContext],
    phone_number: str,
    cycle: str,
) -> str:
    """Check soil health card status.

    Use this tool to check soil health card status for a farmer.

    Args:
        phone_number (str): Phone number registered with the scheme
        cycle (str): Cycle year for which status is requested (e.g., "2023-24", "2024-25"). You must ask the user for the cycle year if not provided. Do not mention the format specification to the user - ask naturally for the cycle year.

    Returns:
        str: Detailed soil health card information with report link
    """
    if should_fail():
        return "Soil health card service is temporarily unavailable. Please try again later."

    # ~15% chance no SHC found for this phone/cycle
    if random.random() < 0.15:
        last4 = phone_number[-4:] if len(phone_number) >= 4 else phone_number
        return f"No Soil Health Card found for phone number ending ****{last4} for cycle {cycle}. Please verify your phone number and cycle year."

    today = ctx.deps.today_date

    # ── Generate identifiers ──────────────────────────────────────────────
    file_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    report_url = f"https://chat-vistaar.da.gov.in/api/file/{file_id}"

    year_part = today.year
    test_id = (
        f"SHC{year_part}-{random.randint(100, 999)}-{random.randint(100000, 999999)}"
        f"-{''.join(random.choices(string.ascii_letters, k=3))}"
        f"-{''.join(random.choices(string.ascii_letters + string.digits, k=2))}"
    )

    testing_date = (today - timedelta(days=random.randint(30, 365))).strftime("%B %d, %Y")

    # ── Farmer and plot info ──────────────────────────────────────────────
    name = random_name()
    loc = random.choice(LOCATIONS)
    last4 = phone_number[-4:] if len(phone_number) >= 4 else phone_number
    masked_phone = f"+91******{last4}"

    area = round(random.uniform(0.2, 5.0), 3)
    soil_type = random.choice(SHC_SOIL_TYPES)

    # ── Soil parameters ───────────────────────────────────────────────────
    ph = round(max(4.5, min(9.5, random.gauss(7.0, 0.8))), 2)
    ec = round(random.uniform(0.01, 0.8), 2)
    if random.random() < 0.10:
        ec = round(random.uniform(0.8, 2.0), 2)
    oc = round(random.uniform(0.20, 1.00), 2)

    # Macronutrients (N can be NOT AVAILABLE ~12%)
    n_value = round(random.uniform(140, 520), 2) if random.random() > 0.12 else None
    p_value = round(random.uniform(5, 45), 2)
    k_value = round(random.uniform(80, 350), 2)
    s_value = round(random.uniform(5, 40), 2)

    # Micronutrients (B can be NOT AVAILABLE ~10%)
    zn_value = round(random.uniform(0.3, 5.0), 2)
    b_value = round(random.uniform(0.1, 3.0), 2) if random.random() > 0.10 else None
    fe_value = round(random.uniform(2.0, 25.0), 2)
    mn_value = round(random.uniform(1.0, 15.0), 2)
    cu_value = round(random.uniform(0.1, 5.0), 2)

    # ── Build output ──────────────────────────────────────────────────────
    lines = []

    lines.append(f"Report: {report_url}")
    lines.append("")

    lines.append("**Soil Health Card Report**")
    lines.append(f"Test ID: {test_id}")
    lines.append(f"Testing Date: {testing_date}")
    lines.append("Validity: 3 years or 3 crop seasons")
    lines.append(f"Center: STL {loc['district']}")
    lines.append("")

    lines.append("**Card Issued To**")
    lines.append(f"Name: {name}")
    lines.append(f"Phone: {masked_phone}")
    lines.append(f"Address: {loc['village']}, {loc['district']}, {loc['state']}")
    lines.append("")

    lines.append("**Plot Details**")
    lines.append(f"Area: {area} Hectare")
    lines.append(f"Soil Type: {soil_type}")
    lines.append("")

    lines.append("**Soil Sample Details**")
    lines.append(f"PH (pH): {ph} (Range: 5.5 - 8.5) — {_ph_rating(ph)}")
    lines.append(f"EC (dS/m): {ec} (Range: < 1) — {_ec_rating(ec)}")
    lines.append(f"Organic Carbon (OC): {oc} w% (Range: 0.50 - 0.75) — {_oc_rating(oc)}")

    if n_value is not None:
        lines.append(f"Available Nitrogen (N): {n_value} kg/ha (Range: 280 - 560) — {_npk_rating(n_value, 280, 560)}")
    else:
        lines.append("Available Nitrogen (N): NOT AVAILABLE")

    lines.append(f"Available Phosphorus (P): {p_value} kg/ha (Range: 10 - 25) — {_npk_rating(p_value, 10, 25)}")
    lines.append(f"Available Potassium (K): {k_value} kg/ha (Range: 120 - 280) — {_npk_rating(k_value, 120, 280)}")
    lines.append(f"Available Sulphur (S): {s_value} ppm (Range: > 10) — {_micro_rating(s_value, 10)}")
    lines.append(f"Available Zinc (Zn): {zn_value} ppm (Range: > 0.6) — {_micro_rating(zn_value, 0.6)}")

    if b_value is not None:
        lines.append(f"Available Boron (B): {b_value} ppm (Range: > 0.5) — {_micro_rating(b_value, 0.5)}")
    else:
        lines.append("Available Boron (B): NOT AVAILABLE")

    lines.append(f"Available Iron (Fe): {fe_value} ppm (Range: > 4.5) — {_micro_rating(fe_value, 4.5)}")
    lines.append(f"Available Manganese (Mn): {mn_value} ppm (Range: > 2) — {_micro_rating(mn_value, 2.0)}")
    lines.append(f"Available Copper (Cu): {cu_value} ppm (Range: > 0.2) — {_micro_rating(cu_value, 0.2)}")
    lines.append("")

    # ── Fertilizer recommendations ────────────────────────────────────────
    lines.append("**Fertilizer Recommendations**")
    lines.append("Note: Choose either the Organic Fertilizer Recommendation or one of the Fertilizer Combinations (1 or 2). Do not apply them together.")
    lines.append("")

    num_crops = random.randint(4, 7)
    selected_crops = random.sample(SHC_CROP_FERTILIZERS, min(num_crops, len(SHC_CROP_FERTILIZERS)))

    for crop_data in selected_crops:
        lines.append(f"Crop: {crop_data['crop']}")
        lines.append(f"Combo-1: {_format_combo(crop_data['combo1'])}")
        lines.append(f"Combo-2: {_format_combo(crop_data['combo2'])}")
        lines.append("---")

    lines.append("")
    lines.append("**Exclusive Organic Fertilizer Recommendations (same for all crops)**")
    lines.append(SHC_ORGANIC_REC)

    return "\n".join(lines)
