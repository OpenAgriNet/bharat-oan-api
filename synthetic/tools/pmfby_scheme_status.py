"""
Mock PMFBY status tools — fake OTP flow + random policy/claim status.
Keeps the same function signatures as agents/tools/pmfby_scheme_status.py.
"""

import random
from datetime import datetime, timedelta
from typing import Literal, List
from pydantic_ai import ModelRetry
from pydantic_ai.tools import RunContext
from synthetic.deps import FarmerContext
from synthetic.mock_data import (
    PMFBY_CROPS,
    SEASONS,
    CLAIM_STATUSES,
    POLICY_STATUSES,
    should_fail,
)


def initiate_pmfby_status_check(ctx: RunContext[FarmerContext], phone_number: str) -> str:
    """Initiate PMFBY status check by sending OTP to farmer's mobile.

    Use this tool to initiate the process. Sends an OTP to the farmer's registered mobile.
    After OTP is received, use check_pmfby_status_with_otp with the OTP and inquiry details.

    Args:
        phone_number (str): Phone number registered with PMFBY (required)

    Returns:
        str: Response from the scheme init service (OTP sent confirmation)
    """
    phone = str(phone_number).strip()
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) < 10:
        raise ModelRetry("Invalid phone number. Please provide a valid 10-digit Indian mobile number.")

    return (
        "OTP has been sent to your registered mobile. "
        "Please enter the 6-digit OTP you received to see your policy/claim status."
    )


def check_pmfby_status_with_otp(
    ctx: RunContext[FarmerContext],
    otp: str,
    phone_number: str,
    inquiry_type: Literal["policy_status", "claim_status"],
    year: str,
    season: Literal["Kharif", "Rabi", "Summer"],
) -> str:
    """Check PMFBY status using OTP after initiating the OTP check.

    Use this tool to check policy or claim status using the OTP received via SMS
    after calling initiate_pmfby_status_check.

    Args:
        otp (str): 6-digit OTP received via SMS (used as order_id for API)
        phone_number (str): Phone number registered with PMFBY (required)
        inquiry_type (str): Type of inquiry - 'policy_status' or 'claim_status' (required)
        year (str): Year for which status is requested (required)
        season (str): Season - Kharif, Rabi, or Summer (required)

    Returns:
        str: Detailed scheme status information
    """
    otp_str = str(otp).strip() if otp else ""
    digits = "".join(c for c in otp_str if c.isdigit())
    if len(digits) != 6:
        raise ModelRetry(
            "PMFBY OTP must be exactly 6 digits. Please ask the farmer for the 6-digit OTP they received on their mobile."
        )

    # 5% chance of simulated failure
    if should_fail():
        return "PMFBY status service is temporarily unavailable. Please try again later."

    # Use farmer profile from context where available
    if ctx.deps.farmer_crops:
        crop = random.choice(ctx.deps.farmer_crops)
    else:
        crop = random.choice(PMFBY_CROPS)
    area = round(ctx.deps.farmer_land_acres, 2) if ctx.deps.farmer_land_acres else round(random.uniform(0.5, 10.0), 2)
    sum_insured = random.randint(30000, 200000)
    premium = round(sum_insured * random.uniform(0.015, 0.05))

    lines: List[str] = []

    if inquiry_type == "policy_status":
        policy_status = random.choice(POLICY_STATUSES)
        policy_no = f"PMFBY-{year}-{random.randint(100000, 999999)}"

        lines.append(f"**PMFBY Policy Status - {season} {year}**")
        lines.append(f"")
        lines.append(f"Policy Number: {policy_no}")
        lines.append(f"Status: **{policy_status}**")
        lines.append(f"Crop: {crop}")
        lines.append(f"Season: {season} {year}")
        lines.append(f"Area Insured: {area} hectares")
        lines.append(f"Sum Insured: Rs. {sum_insured:,}")
        lines.append(f"Premium Paid: Rs. {premium:,}")

        if policy_status == "Active":
            expiry = (ctx.deps.today_date + timedelta(days=random.randint(30, 180))).strftime("%d/%m/%Y")
            lines.append(f"Valid Until: {expiry}")
        elif policy_status == "Under Review":
            lines.append(f"")
            lines.append(f"**Note:** Your policy application is being reviewed. Please check back in 7-10 days.")

    else:  # claim_status
        claim_status = random.choice(CLAIM_STATUSES)
        claim_no = f"CLM-{year}-{random.randint(100000, 999999)}"

        lines.append(f"**PMFBY Claim Status - {season} {year}**")
        lines.append(f"")
        lines.append(f"Claim Number: {claim_no}")
        lines.append(f"Status: **{claim_status}**")
        lines.append(f"Crop: {crop}")
        lines.append(f"Season: {season} {year}")
        lines.append(f"Sum Insured: Rs. {sum_insured:,}")

        if claim_status == "Settled":
            settlement = random.randint(int(sum_insured * 0.3), sum_insured)
            settlement_date = (ctx.deps.today_date - timedelta(days=random.randint(10, 90))).strftime("%d/%m/%Y")
            lines.append(f"Settlement Amount: Rs. {settlement:,}")
            lines.append(f"Settlement Date: {settlement_date}")
        elif claim_status == "Approved":
            settlement = random.randint(int(sum_insured * 0.3), sum_insured)
            lines.append(f"Approved Amount: Rs. {settlement:,}")
            lines.append(f"**Note:** Payment will be credited to your bank account within 15 working days.")
        elif claim_status == "Under Assessment":
            lines.append(f"**Note:** Crop loss assessment is in progress. An officer will visit your field soon.")
        elif claim_status == "Rejected":
            lines.append(f"**Note:** Claim rejected due to non-notified area/crop. Please contact your insurance company.")

    return "\n".join(lines)
