"""Farmer decision tools; credentials and FFS API identifiers stay in the service."""
from typing import Literal
from pydantic_ai import RunContext

from agents.deps import FarmerContext
from app.services.ffs import BookingQuantity, CropSelection, FFSService, result
from helpers.ffs_tracing import observation, result_summary


async def _run(ctx, operation, **kwargs):
    name = "ffs_get_entitlement" if operation == "entitlement" else "ffs_" + operation
    service = FFSService(ctx.deps.session_id, ctx.deps.ffs_owner, ctx.deps.question_id)
    metadata = {"session_id": ctx.deps.session_id, "question_id": ctx.deps.question_id,
                "channel": "chat", "operation": operation}
    with observation("tool:" + name, as_type="tool", metadata=metadata) as diagnostic:
        try:
            if "ffs.booking" not in ctx.deps.skills.active:
                answer = result("unavailable", "Load the enabled ffs.booking skill before using FFS tools")
                diagnostic["reason"] = "skill_not_loaded"
            else:
                answer = await service.run(operation, **kwargs)
            diagnostic.update(result_summary(answer))
            return answer
        finally:
            diagnostic.update(service.diagnostics)
            diagnostic["stage_after"] = service.state.get("stage", "not_started")


async def ffs_send_otp(ctx: RunContext[FarmerContext], farmer_id: str, state: str, resend: bool = False) -> dict:
    """After FFS opt-in, request one SMS OTP using the 11-digit Farmer ID and state.

    For any fertilizer purchase intent, first ask whether they want to buy through
    FFS and wait for agreement. Call this identifier Farmer ID, never FFS ID.
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

    Use more for the next shops, widen for a wider territory. Keep the returned
    ranking and show each shop's urea/DAP availability and stock counts; shops
    need not cover the full entitlement. A shop choice does not reserve anything;
    use ffs_create_booking for explicit final submission.
    Use backend_exchanges and the restored last requests/results to recover from
    business responses by changing action or clarifying the farmer's choice.
    """
    return await _run(ctx, "find_shops", action=action)


async def ffs_create_booking(ctx: RunContext[FarmerContext], shop_ref: str,
                             confirmed: bool = False,
                             quantities: list[BookingQuantity] | None = None) -> dict:
    """Book the farmer's selected fertilizer at the chosen shop after one confirmation.

    Ask once whether to book the selected products and whole-bag quantities at
    this shop. When the farmer agrees (including "book it from this shop"), pass
    confirmed=True and quantities directly. No local token, preview prerequisite,
    expiry or extra turn is required. Do not ask for the same agreement again.
    If agreement is still missing, confirmed=False returns an optional summary.
    Quantities may be omitted to reuse the saved selection for the same shop.
    Never invent quantities or default to full entitlement.

    Read the entire backend_exchanges response to determine what succeeded or
    what must change. FFS decides current stock and quantity eligibility. Recover
    from explicit business rejections using backend data and the farmer's choices;
    a replacement quantity/shop supplied by the farmer is an instruction to book
    that correction, not a reason for another confirmation question. Do not
    silently change quantity or repeat an identical rejection. Stop on system
    failure or an uncertain write outcome; verify uncertainty before resubmitting.
    Do not append an offer for another product to the confirmation question.
    """
    return await _run(ctx, "create_booking", shop_ref=shop_ref, confirmed=confirmed,
                      quantities=quantities)
