"""Farmer decision tools; credentials and FFS API identifiers stay in the service."""
from typing import Literal
from pydantic_ai import RunContext

from agents.deps import FarmerContext
from app.services.ffs import CropSelection, FFSService, result


async def _run(ctx, operation, **kwargs):
    if "ffs.booking" not in ctx.deps.skills.active:
        return result("unavailable", "Load the enabled ffs.booking skill before using FFS tools")
    return await FFSService(ctx.deps.session_id, ctx.deps.ffs_owner, ctx.deps.question_id).run(operation, **kwargs)


async def ffs_send_otp(ctx: RunContext[FarmerContext], farmer_id: str, state: str, resend: bool = False) -> dict:
    """Request one FFS SMS OTP for the farmer-provided 11-digit ID and state.

    FFS login is separate from AgriStack. Do not invent an ID. Use resend only
    after the farmer explicitly asks. Repeated calls do not send another OTP.
    """
    return await _run(ctx, "send_otp", farmer_id=farmer_id, state=state, resend=resend, query=ctx.deps.query)


async def ffs_verify_otp(ctx: RunContext[FarmerContext]) -> dict:
    """Verify the FFS OTP supplied in this turn, then load villages, plots and crops.

    The application securely supplies the OTP when the user message says
    [FFS OTP supplied]. No OTP argument is needed. Also resumes incomplete
    farmer setup using an already-valid token, without asking for another OTP.
    """
    otp = ctx.deps.ffs_otp.get_secret_value() if ctx.deps.ffs_otp else None
    return await _run(ctx, "verify_otp", otp=otp)


async def ffs_get_entitlement(ctx: RunContext[FarmerContext], village_ref: str,
                              crop_selections: list[CropSelection]) -> dict:
    """Save farmer-selected crops on FFS plots and return urea/DAP bag entitlement.

    This WRITES crop mappings. Use only returned land/village refs and crops the
    farmer supplied or confirmed. One {land_ref, crop_name} entry per chosen
    plot, all in one village. Unknown crops ask for clarification before any
    write. A failed partial update can be resumed without restarting OTP login.
    """
    return await _run(ctx, "entitlement", village_ref=village_ref, crop_selections=crop_selections)


async def ffs_find_shops(ctx: RunContext[FarmerContext], action: Literal["initial", "more", "widen"] = "initial") -> dict:
    """After the farmer opts in, find up to three FFS shops for their entitlement.

    Use more for the next shops, widen to seek full stock in a wider territory.
    Describe partial stock honestly. A shop choice does not reserve anything;
    use ffs_create_booking for explicit final submission.
    """
    return await _run(ctx, "find_shops", action=action)


async def ffs_create_booking(ctx: RunContext[FarmerContext], shop_ref: str,
                             confirmed: bool = False, confirmation_token: str = "") -> dict:
    """Create the final FFS booking for a selected shop, with explicit confirmation.

    First call with confirmed=False to get a booking summary and confirmation_token.
    After reading that summary to the farmer and receiving explicit consent, call
    again with confirmed=True and the same confirmation_token.
    """
    return await _run(ctx, "create_booking", shop_ref=shop_ref, confirmed=confirmed,
                      confirmation_token=confirmation_token)
