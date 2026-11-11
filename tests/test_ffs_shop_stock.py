"""Discovery counts relevant shops, pages before widening, and never changes orders."""
import asyncio
from copy import deepcopy
import json

import httpx

from app.services import ffs
from agents.tools.ffs import ffs_get_entitlement, ffs_find_shops, ffs_create_booking
from tests.test_ffs import setup, context, login, selection


def order(urea=1, dap=0):
    return [ffs.OrderItem(product=name, product_id=pid, bags=bags)
            for name, pid, bags in [("urea", 1089, urea), ("DAP", 1005, dap)] if bags]


def retailer(rid, urea=0, dap=0):
    return {"retailerId": rid, "retailerName": f"Shop {rid}", "availableProducts": [
        {"productGroupId": 11, "products": [{"productId": 1089, "noBagsAvailable": urea}]},
        {"productGroupId": 12, "products": [{"productId": 1005, "noBagsAvailable": dap}]}]}


def install_pages(setup, monkeypatch, pages):
    original = setup.backend.respond

    async def respond(request):
        if not request.url.path.endswith("getRetailersByProductAvailabilityV4"):
            return await original(request)
        body = json.loads(request.content)
        setup.backend.calls.append((request.url.path, body))
        territory = pages.get(body["territoryLevel"], [[]])
        assert body["pageSize"] == 3
        assert 1 <= body["pageNo"] <= len(territory)
        return httpx.Response(200, json={"code": 200, "status": "success",
            "data": deepcopy(territory[body["pageNo"] - 1]), "totalPages": len(territory)})

    class Client(setup.client_class):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(respond), **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", Client)


async def prepare():
    village, crops = selection(await login())
    await ffs_get_entitlement(context("entitlement"), village, crops)


def searches(setup):
    return [body for path, body in setup.backend.calls if path.endswith("getRetailersByProductAvailabilityV4")]


def test_discovery_counts_matching_shops_then_ranks_and_stops(setup, monkeypatch):
    install_pages(setup, monkeypatch, {"V": [
        [retailer(1, dap=100), retailer(2, urea=1), retailer(3)],
        [retailer(4, urea=1, dap=2), retailer(5, urea=100)],
        [retailer(6, urea=1000, dap=1000)],
    ]})

    async def run():
        await prepare()
        answer = await ffs_find_shops(context("search"), order=order())
        assert [s["name"] for s in answer["data"]["shops"]] == ["Shop 4", "Shop 5", "Shop 2"]
        assert [(b["territoryLevel"], b["pageNo"]) for b in searches(setup)] == [("V", 1), ("V", 2)]
        assert answer["data"]["has_more"]
        assert all("stock" not in s and "noBagsAvailable" not in s for s in answer["data"]["shops"])
        assert "backend_exchanges" not in json.dumps(answer)
        assert {p["productId"]: p["noBagsRequested"] for p in searches(setup)[0]["selectedProducts"]} == {1089: 1, 1005: 1}
        state = await ffs.get_session("session-1")
        assert [(p["product_id"], p["bags"]) for p in state["order"]] == [(1089, 1)]
        booked = await ffs_create_booking(context("book"), answer["data"]["shops"][0]["shop_ref"], True)
        assert booked["status"] == "ok"
        lines = setup.backend.booking_payloads[0]["bookingRequest"][0]["productLines"]
        assert [(p["productId"], p["noBagsRequested"]) for p in lines] == [(1089, 1)]
    asyncio.run(run())


def test_pages_continue_past_four_before_widening_and_more_resumes(setup, monkeypatch):
    install_pages(setup, monkeypatch, {"V": [
        [retailer(1, urea=1)], [retailer(1, urea=1)], [retailer(2, dap=50)],
        [retailer(3)], [retailer(4, urea=2)], [retailer(5, urea=3)],
        [retailer(6, urea=4)],
    ], "T": [[retailer(1, urea=1), retailer(7, urea=5), retailer(8, urea=6)]],
        "D": [[retailer(9, urea=500)]]})

    async def run():
        await prepare()
        first = await ffs_find_shops(context("search"), order=order())
        assert len(first["data"]["shops"]) == 3
        assert [(b["territoryLevel"], b["pageNo"]) for b in searches(setup)] == [("V", n) for n in range(1, 7)]
        assert await ffs_find_shops(context("search")) == first
        more = await ffs_find_shops(context("more"), "more")
        assert [s["name"] for s in more["data"]["shops"]] == ["Shop 6", "Shop 8", "Shop 7"]
        assert [(b["territoryLevel"], b["pageNo"]) for b in searches(setup)][-2:] == [("V", 7), ("T", 1)]
        assert not ({s["shop_ref"] for s in first["data"]["shops"]} & {s["shop_ref"] for s in more["data"]["shops"]})
    asyncio.run(run())


def test_fewer_than_three_expands_through_state_and_keeps_partial_stock(setup, monkeypatch):
    install_pages(setup, monkeypatch, {"V": [[retailer(1, dap=99)]],
        "T": [[retailer(2, urea=1)]], "D": [[retailer(3)]], "S": [[retailer(4, urea=2)]]})

    async def run():
        await prepare()
        answer = await ffs_find_shops(context("search"), order=order(urea=3))
        shops = answer["data"]["shops"]
        assert [s["name"] for s in shops] == ["Shop 2", "Shop 4"]
        assert [s["territory"] for s in shops] == ["T", "S"]
        assert all(s["supply"] == "partial" for s in shops)
        assert all(p["noBagsRequested"] == 1 for b in searches(setup) for p in b["selectedProducts"])
        assert (await ffs.get_session("session-1"))["order"][0]["bags"] == 3
        assert not answer["data"]["has_more"]
        assert [b["territoryLevel"] for b in searches(setup)] == ["V", "T", "D", "S"]
        assert (await ffs_find_shops(context("more"), "more"))["status"] == "no_more_shops"
    asyncio.run(run())


def test_multiple_products_one_shop_order_is_unchanged(setup, monkeypatch):
    install_pages(setup, monkeypatch, {"V": [[retailer(1, 2, 1), retailer(2, 50, 0), retailer(3, 0, 50)]]})

    async def run():
        await prepare()
        answer = await ffs_find_shops(context("search"), order=order(urea=2, dap=1))
        assert [s["name"] for s in answer["data"]["shops"]] == ["Shop 1", "Shop 2", "Shop 3"]
        assert [s["supply"] for s in answer["data"]["shops"]] == ["full", "partial", "partial"]
        booked = await ffs_create_booking(context("book"), answer["data"]["shops"][0]["shop_ref"], True)
        assert booked["status"] == "ok"
        requests = setup.backend.booking_payloads[0]["bookingRequest"]
        assert len(requests) == 1
        assert {p["productId"]: p["noBagsRequested"] for p in requests[0]["productLines"]} == {1089: 2, 1005: 1}
    asyncio.run(run())


def test_empty_unknown_and_unrelated_shops_do_not_fill_batch(setup, monkeypatch):
    unknown = retailer(2)
    del unknown["availableProducts"][0]["products"][0]["noBagsAvailable"]
    install_pages(setup, monkeypatch, {"V": [[retailer(1), unknown, retailer(3, dap=500)]]})

    async def run():
        await prepare()
        answer = await ffs_find_shops(context("search"), order=order())
        assert answer["status"] == "no_more_shops"
        assert answer["data"]["shops"] == []
        assert not answer["data"]["has_more"]
        assert [b["territoryLevel"] for b in searches(setup)] == ["V", "T", "D", "S"]
    asyncio.run(run())


def test_no_order_or_over_allowance_does_not_start_discovery(setup):
    async def run():
        await prepare()
        assert (await ffs_find_shops(context("no-order")))["status"] == "needs_order"
        assert (await ffs_find_shops(context("too-many"), order=order(urea=7)))["status"] == "needs_order"
        assert searches(setup) == []
        assert not (await ffs.get_session("session-1")).get("order")
    asyncio.run(run())


def test_offered_bags_does_not_depend_on_gfr_weights(setup):
    for row in setup.backend.gfr[0]["recommendations"]:
        row.pop("kgPerBag", None)
        row["noOfBagsRequired"] = 999

    async def run():
        await prepare()
        answer = await ffs_find_shops(context("search"), order=order(urea=3))
        assert answer["status"] == "ok"
        state = await ffs.get_session("session-1")
        assert state["order"][0]["bags"] == 3
        assert state["order"][0]["kg_per_bag"] == 45
        rejected = await ffs_find_shops(context("too-many"), order=order(urea=7))
        assert rejected["data"]["allowed_bags"] == 6
    asyncio.run(run())
