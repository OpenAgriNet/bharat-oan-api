"""
Mock PM-KISAN status tools — fake OTP flow + random beneficiary status.
Keeps the same function signatures as agents/tools/pmkisan_scheme_status.py.
"""

import random
from datetime import datetime, timedelta
from typing import List
from pydantic_ai import ModelRetry
from pydantic_ai.tools import RunContext
from synthetic.deps import FarmerContext
from synthetic.mock_data import (
    BANK_NAMES,
    INSTALLMENT_AMOUNTS,
    PAYMENT_MODES,
    should_fail,
)


def _random_masked_aadhaar() -> str:
    return "XXXXXXXX" + str(random.randint(1000, 9999))


def _random_masked_account() -> str:
    return "XXXXXXXX" + str(random.randint(1000, 9999))


def initiate_pm_kisan_status_check(ctx: RunContext[FarmerContext], reg_no: str) -> str:
    """Initiate PM Kisan status check by sending OTP to farmer's mobile.

    Use this tool to initiate the process to check the status of a farmer's scheme benefit by sending an OTP to their registered mobile number.

    Args:
        reg_no (str): PM Kisan registration number, usually a 11 digit string such (2 digit state code + 9 digit unique number)

    Returns:
        str: Response from the scheme status check service
    """
    reg_no = str(reg_no).strip()
    if not reg_no or len(reg_no) < 5:
        raise ModelRetry("Invalid registration number. Please provide a valid PM-KISAN registration number (11 digits).")

    return (
        "OTP has been sent to the registered mobile number. "
        "Please provide the 4-digit OTP received via SMS to check your PM-KISAN status."
    )


def check_pm_kisan_status_with_otp(ctx: RunContext[FarmerContext], otp: str, reg_no: str) -> str:
    """Check PM Kisan status using OTP after initiating the OTP check.

    Use this tool to check the status of a farmer's PM Kisan benefit using the OTP received via SMS after calling initiate_pm_kisan_status_check.

    Args:
        otp (str): A 4 digit OTP received via SMS
        reg_no (str): PM Kisan registration number, usually a 11 digit string such (2 digit state code + 9 digit unique number)

    Returns:
        str: Detailed scheme status information including beneficiary details, payment status, and any issues or next steps
    """
    otp_clean = str(otp).strip()
    if not otp_clean.isdigit() or len(otp_clean) != 4:
        raise ModelRetry("Invalid OTP format. Please provide a 4-digit OTP received via SMS.")

    # 5% chance of simulated failure
    if should_fail():
        return "PM-KISAN status service is temporarily unavailable. Please try again later."

    # Use farmer profile from context, fall back to random
    farmer_name = ctx.deps.farmer_name or "Ramesh Kumar"

    # Random installment status
    total_installments = random.randint(8, 18)
    last_installment = random.randint(1, total_installments)
    last_payment_date = (ctx.deps.today_date - timedelta(days=random.randint(30, 180))).strftime("%d/%m/%Y")

    bank = random.choice(BANK_NAMES)
    payment_mode = random.choice(PAYMENT_MODES)
    status = random.choice(["Active", "Active", "Active", "Payment Pending", "Aadhaar Verification Required"])

    lines: List[str] = []
    # Mask phone/aadhaar from profile or generate random masks
    if ctx.deps.farmer_phone:
        masked_phone = f"XXXXXX{ctx.deps.farmer_phone[-4:]}"
    else:
        masked_phone = f"XXXXXX{random.randint(1000, 9999)}"

    if ctx.deps.farmer_aadhaar:
        masked_aadhaar = f"XXXXXXXX{ctx.deps.farmer_aadhaar[-4:]}"
    else:
        masked_aadhaar = _random_masked_aadhaar()

    lines.append(f"Customer: {farmer_name}")
    lines.append(f"Phone: {masked_phone}")
    lines.append(f"Status: **{status}**")
    lines.append(f"Registration Number: {reg_no}")
    lines.append(f"Aadhaar: {masked_aadhaar}")
    lines.append(f"")
    lines.append(f"**Payment Details:**")
    lines.append(f"Total Installments Received: {last_installment}")
    lines.append(f"Amount per Installment: Rs. {INSTALLMENT_AMOUNTS[0]}")
    lines.append(f"Last Payment Date: {last_payment_date}")
    lines.append(f"Bank: {bank}")
    lines.append(f"Account: {_random_masked_account()}")
    lines.append(f"Payment Mode: {payment_mode}")

    if status == "Payment Pending":
        lines.append(f"")
        lines.append(f"**Note:** Your next installment is under processing. Please wait for 2-3 weeks.")
    elif status == "Aadhaar Verification Required":
        lines.append(f"")
        lines.append(f"**Note:** Please complete Aadhaar-based eKYC at your nearest CSC center to receive pending installments.")

    return "\n".join(lines)
