"""
Mock PM-KISAN grievance tools — fake submission + random status.
Removes AES-GCM encryption complexity of agents/tools/grievance.py.
"""

import json
import random
import string
from datetime import datetime, timedelta
from typing import List, Literal
from pydantic_ai import ModelRetry
from pydantic_ai.tools import RunContext
from helpers.utils import get_logger
from synthetic.deps import FarmerContext
from synthetic.mock_data import GRIEVANCE_STATUSES, OFFICER_REPLIES, should_fail

logger = get_logger(__name__)

# Load grievance types for validation
_GRIEVANCE_JSON_PATH = "assets/grievance_types.json"

GRIEVANCE_MAPPING = json.load(open(_GRIEVANCE_JSON_PATH, "r", encoding="utf-8"))
GRIEVANCE_TYPES = Literal[tuple(GRIEVANCE_MAPPING.keys())]  # type: ignore

def submit_pmkisan_grievance(identity_no: str, grievance_description: str, grievance_type: GRIEVANCE_TYPES) -> str:
    """
    Create and submit a grievance to the PM-KISAN portal.

    Args:
        identity_no: PM-KISAN Registration Number (11-character alphanumeric string) or 12-digit Aadhaar number registered with PM-KISAN.
        grievance_description: Description of the grievance (plain text).
        grievance_type: type of grievance

    Returns:
        A user-friendly message summarizing submission outcome.
    """
    # Validate grievance type
    if not grievance_type or grievance_type not in GRIEVANCE_MAPPING:
        choices = '", "'.join(GRIEVANCE_TYPES)
        raise ModelRetry(f'Invalid grievance type: "{grievance_type}". Please select from: "{choices}".')

    if not grievance_description or len(grievance_description.strip()) < 10:
        raise ModelRetry("Please provide a brief grievance description.")

    identity_no = str(identity_no).strip()
    if not identity_no or len(identity_no) < 5:
        raise ModelRetry("Invalid identity number. Please provide a valid PM-KISAN registration number or Aadhaar number.")

    # 5% chance of simulated failure
    if should_fail():
        return "Grievance submission service is temporarily unavailable. Please try again later."

    # Generate random grievance ID
    grievance_id = "GRV" + "".join(random.choices(string.digits, k=8))

    return (
        f"Grievance submitted successfully.\n"
        f"Grievance ID: {grievance_id}\n"
        f"Type: {grievance_type}\n"
        f"Status: Registered\n"
        f"Please save your Grievance ID for future reference. "
        f"You can check the status using your registration number."
    )


def check_pmkisan_grievance_status(ctx: RunContext[FarmerContext], identity_no: str) -> str:
    """
    Check grievance status by PM-KISAN Registration Number or Aadhaar number (registered).

    Args:
        identity_no: PM-KISAN Registration Number (11-character alphanumeric string) or 12-digit Aadhaar registered with PM-KISAN.

    Returns:
        The grievance status string (registration number, dates, officer reply, etc.), or an explanatory message if not found yet.
    """
    identity_no = str(identity_no).strip()
    if not identity_no or len(identity_no) < 5:
        raise ModelRetry("Invalid identity number. Please provide a valid PM-KISAN registration number or Aadhaar number.")

    # Random chance of no grievance found
    if random.random() < 0.15:
        return "No grievances found for this registration number."

    status = random.choice(GRIEVANCE_STATUSES)
    grievance_date = (ctx.deps.today_date - timedelta(days=random.randint(5, 90))).strftime("%d/%m/%Y")
    grievance_id = "GRV" + "".join(random.choices(string.digits, k=8))

    descriptions = [
        "Installment not received for the last 2 cycles",
        "Bank account number is incorrect in the system",
        "eKYC verification failed despite correct Aadhaar",
        "Name mismatch between Aadhaar and PM-KISAN records",
        "Payment showing as failed but amount not reversed",
    ]

    lines: List[str] = []
    lines.append(f"Registration Number: {identity_no}")
    lines.append(f"Grievance Details:")
    lines.append(f"  Grievance ID: {grievance_id}")
    lines.append(f"  Date: {grievance_date}")
    lines.append(f"  Description: {random.choice(descriptions)}")
    lines.append(f"  Status: **{status}**")
    lines.append(f"Officer Response:")

    if status == "Resolved":
        reply_date = (ctx.deps.today_date - timedelta(days=random.randint(1, 30))).strftime("%d/%m/%Y")
        lines.append(f"  Reply: {random.choice(OFFICER_REPLIES)}")
        lines.append(f"  Reply Date: {reply_date}")
    elif status == "Under Review":
        lines.append(f"  Reply: Your complaint is being reviewed by the concerned department.")
    else:
        lines.append(f"  Reply: Not yet responded")

    return "\n".join(lines)
