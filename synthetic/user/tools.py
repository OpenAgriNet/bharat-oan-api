"""
Tools available to the synthetic farmer user agent.
"""

import random
from typing import Literal

from pydantic import BaseModel
from pydantic_ai import RunContext

from synthetic.user.profile import FarmerProfile


class EndConversation(BaseModel):
    """Returned by the user agent when the conversation is complete."""
    message: str


async def check_otp(ctx: RunContext[FarmerProfile], scheme: Literal['pmkisan', 'pmfby']) -> str:
    """Simulate receiving an OTP on the farmer's phone. Generates a fresh OTP each time."""
    length = 4 if scheme == 'pmkisan' else 6
    otp = "".join(str(random.randint(0, 9)) for _ in range(length))
    return f"OTP received on your phone: {otp}"
