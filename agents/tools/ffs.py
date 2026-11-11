"""Farmer decision tools; credentials and FFS API identifiers stay in the service."""
from typing import Literal
from pydantic_ai import RunContext

from agents.deps import FarmerContext
from app.services.ffs import OrderItem, CropSelection, FFSService, STATE_CODES, result
from helpers.ffs_tracing import observation, result_summary


async def _run(ctx, operation, **kwargs):
    name = "ffs_get_entitlement" if operation == "entitlement" else "ffs_" + operation
    service = FFSService(ctx.deps.session_id, ctx.deps.question_id)
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


async def ffs_send_otp(ctx: RunContext[FarmerContext], farmer_id: str, state_lgd_code: str, resend: bool = False) -> dict:
    """After FFS opt-in, request one SMS OTP using the 11-digit Farmer ID and state LGD code.

    For any fertilizer purchase intent, first ask whether they want to buy through
    FFS and wait for agreement. Explain that this integration offers urea/DAP and
    requires a Farmer ID. Ask for missing login details one question at a time.
    Call this identifier Farmer ID, never FFS ID.
    Interpret state names, local languages, clear spelling/ASR variants or places
    using the state/LGD list below; pass the code as a string, never a state name.
    If the intended state or Farmer ID is unclear, ask the farmer; do not guess.
    The list is the provider's geographic master, not proof of farmer eligibility.
    FFS login is separate from AgriStack. Do not invent an ID. Use resend only
    after the farmer requests another code, including paraphrases in any language;
    a negation such as "don't resend" is not a resend request. After login expiry
    or an FFS OTP rejection, a new send call works without resend=True. Otherwise,
    use resend=True for a requested replacement of an already-sent code. Never
    send another code automatically on rejection; follow the farmer's request.
    """
    return await _run(ctx, "send_otp", farmer_id=farmer_id, state_lgd_code=state_lgd_code, resend=resend)


# One provider snapshot supplies both model guidance and code membership validation.
ffs_send_otp.__doc__ += "\nState LGD codes:\n" + "\n".join(
    f"{name}: {code}" for code, name in STATE_CODES.items()
)


async def ffs_verify_otp(ctx: RunContext[FarmerContext], otp: str | None = None) -> dict:
    """Verify the FFS OTP supplied in this turn, then load villages, plots and crops.

    Read the OTP from the farmer's current message and pass six digits as a string,
    preserving leading zeros. Interpret clear spoken numbers, e.g. "double zero
    one two three four" as "001234". Ask for clarification if incomplete or ambiguous;
    never invent digits or try several guesses. FFS validates whether it is correct.
    Omit otp only to resume incomplete setup with an already-valid login; do not
    ask for another OTP for setup_incomplete. Do not echo the OTP in your reply.
    """
    return await _run(ctx, "verify_otp", otp=otp)


async def ffs_get_entitlement(ctx: RunContext[FarmerContext], village_ref: str,
                              crop_selections: list[CropSelection] | None = None,
                              crop_name: str | None = None, crop_code: str | None = None,
                              selected_plots_only: bool = False) -> dict:
    """Save farmer-selected crops on FFS plots and return urea/DAP bag entitlement.

    This booking flow supports one village at a time; tell the farmer this.
    If they ask to add another village, ask which village to use before calling.
    An explicit switch replaces the previous selection; it never combines them.
    Do not split a multi-village request into separate calls or add their allowances.
    Report the returned village scope and its allowance only.

    Once village and planned crops are known, call automatically; no permission
    question is needed. This WRITES crop mappings. For one crop, pass village_ref,
    crop_name and optional crop_code: Python expands all plots in that village.
    Do not enumerate or ask the farmer to confirm every plot. For multiple crops,
    supply crop_selections with one mapping per plot. Only when the farmer explicitly
    asks for fewer plots, set selected_plots_only=true and supply those mappings.
    Prefer either village-wide crop fields or per-plot mappings. FFS decides eligibility;
    do not invent exclusions from land-status labels. If FFS rejects a plot,
    explain the affected plot/reason and ask the farmer what to do. Change to a
    subset only on their instruction; do not silently omit or retry a rejected plot.

    For wheat, maize or paddy (including local-language names), pass crop_code from
    common_crops, either at village level or inside each per-plot mapping. This
    bypasses the specialist. For other crops omit the code and preserve the farmer's
    wording/descriptors. Ambiguity returns candidates before any write. Partial
    updates can resume without new OTP login. Eligibility is not an order: explain
    returned allowances, collect requested products/bags and confirm before shops.
    """
    return await _run(ctx, "entitlement", village_ref=village_ref, crop_selections=crop_selections,
                      crop_name=crop_name, crop_code=crop_code, selected_plots_only=selected_plots_only)


async def ffs_find_shops(ctx: RunContext[FarmerContext], action: Literal["initial", "more", "widen"] = "initial",
                         order: list[OrderItem] | None = None, shop_ref: str | None = None) -> dict:
    """Store the farmer's order and find up to three shops for that order.

    Eligibility is per product group; an order is specific products and bags.
    Collect products/counts ONLY from the farmer's words, reusing earlier requests.
    Never copy allowance into an order or invent quantities from a shop-choice yes.
    Confirm their product/count choice before searching unless already confirmed.
    Pass order on initial search or an explicit correction; more/widen reuse it.
    order is a JSON ARRAY, for example:
    [{"product": "urea", "product_id": 1089, "bags": 1}].
    Each item uses product, not name; do not wrap the array in a products object.
    Wait for this tool's result before calling ffs_create_booking. Never call both
    tools in one response: booking must consume the successfully updated order.
    On correction pass the chosen shop_ref if available. Stable shop_ref identifies
    the retailer; numbers 1–3 only index the displayed list. Keep that shop and
    book an agreed correction directly. FFS checks stock when booking; cached
    shop stock must not block the correction. Search alternatives if requested.
    Never silently switch to the first retailer in a new list.

    Common product IDs (all states): generic urea = Neem Coated Urea, 1089, 45 kg;
    generic DAP = DAP, 1005, 50 kg. Pass these IDs directly with product and bags.
    Named variants (sulphur-coated urea, zinc DAP, MAP, TSP) are different products:
    preserve the farmer's words and omit product_id to invoke product resolution.
    For ambiguity use a returned candidate ID; never invent an ID or substitute.
    Product lookup still supplies actual plant IDs, weights and local availability.
    Discovery probes one-bag availability for urea, DAP and ordered products;
    these search probes are not order quantities and never change the order.
    Bags across products in one group must fit its offeredBags allowance. If over,
    explain the returned limit and ask for a lower choice; never cap it yourself.

    Keep the returned ranking within village, tehsil, district, then state:
    both urea/DAP shops first, then single-product shops, then stock volumes
    internally. Discovery pages until three distinct shops carry an ordered
    product, or every territory is exhausted. Lower-stock shops still count;
    unrelated, zero-stock or unknown-stock rows do not fill the three choices.
    More continues from the saved cursor, finishing a territory before widening.
    Never reveal stock counts. Present only numbered returned shops,
    their level, ordered-product availability and full/partial/unknown supply.
    More gets the next options; widen moves further. A shop choice reserves nothing.
    After a clear booking rejection, use the farmer's corrected order here, then
    book directly when their instruction supplies agreement; no repeated approval.
    """
    return await _run(ctx, "find_shops", action=action, order=order, shop_ref=shop_ref)


async def ffs_create_booking(ctx: RunContext[FarmerContext], shop_ref: str,
                             confirmed: bool = False) -> dict:
    """Submit the one stored order at the chosen shop after one confirmation.

    There is no quantity argument: shop search and booking read the same order.
    If the farmer changes products or bags, update that order with ffs_find_shops.
    Wait for that tool's successful result before invoking this tool. Never send
    an order update and a booking together in the same model response.
    Ask once whether to book the order at this shop. An explicit instruction such
    as 'book it from the first shop' already supplies agreement: confirmed=True.
    No local confirmation token, preview prerequisite, expiry or extra turn.
    confirmed=False can return a summary if agreement is missing.
    Do not append another-product offers to the confirmation question.
    Follow the compact provider outcome. Never silently change quantities, repeat
    an identical rejection or reveal shop stock volumes. Clear rejections allow
    a farmer-selected correction. FFS enforces existing and parallel bookings;
    every confirmed attempt reaches FFS, including a retry after an unknown outcome.
    Do not automatically offer another booking after success. If the farmer asks
    for another product/order, submit their actual choice and explain FFS's fresh
    response. Never describe an earlier receipt as a new booking. A timeout does
    not prove failure; a retry uses the same approved order, without another
    confirmation round. Do not keep retrying an identical provider rejection.
    """
    return await _run(ctx, "create_booking", shop_ref=shop_ref, confirmed=confirmed)
