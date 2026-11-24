"""KHC tools save what later turns need (IDs, workflow step, grievance number) in the "khc" section."""
import asyncio
import json
from types import SimpleNamespace

from agents.deps import FarmerContext
from agents.tools import khc
from tests.test_khc_tool import envelope, registration

REAL_CLIENT = khc.httpx.AsyncClient

STATE = {"id": 9, "name": "Uttar Pradesh"}
SCHEME = {"id": 38, "name": "AIF", "division": 31}


def ctx():
    return SimpleNamespace(deps=FarmerContext(session_id="s", query="q"))


def saved(c):
    return c.deps.session_state.get("khc", {})


def fake_http(monkeypatch, response, calls):
    """Like tests.test_khc_tool.fake_http, but safe to call more than once in a test."""
    monkeypatch.setenv("BAP_ENDPOINT", "https://bap.test")
    monkeypatch.setattr(khc.httpx, "AsyncClient", lambda **kwargs: REAL_CLIENT(
        transport=khc.httpx.MockTransport(lambda request: calls.append(request) or response), **kwargs,
    ))


def respond(monkeypatch, code, fields, message="ok"):
    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope("init", code, fields, message)), [])


def tree(categories):
    return {"state": json.dumps(STATE), "scheme": json.dumps(SCHEME), "categories": json.dumps(categories)}


def test_registration_otp_steps_and_farmer_type(monkeypatch):
    c = ctx()
    respond(monkeypatch, "otp_sent", {})
    asyncio.run(khc.initiate_khc_otp(c, "9876543210"))
    assert saved(c) == {"mobile": "9876543210", "otp_step": "sent"}

    respond(monkeypatch, "invalid_otp", {}, "Wrong OTP")
    asyncio.run(khc.verify_khc_otp(c, "9876543210", "111111"))
    assert saved(c) == {"mobile": "9876543210", "otp_step": "sent"}

    respond(monkeypatch, "otp_verified", {"isRegistered": "false"})
    asyncio.run(khc.verify_khc_otp(c, "9876543210", "123456"))
    assert saved(c) == {"mobile": "9876543210", "otp_step": "verified", "farmer_type": "new"}
    assert "123456" not in json.dumps(c.deps.session_state)  # The OTP is used once and never saved.

    # A new OTP to another mobile replaces the mobile and resets the step.
    respond(monkeypatch, "otp_sent", {})
    asyncio.run(khc.initiate_khc_otp(c, "9123456780"))
    assert saved(c) == {"mobile": "9123456780", "otp_step": "sent"}


def test_failed_send_saves_nothing(monkeypatch):
    c = ctx()
    respond(monkeypatch, "invalid_mobile", {}, "Invalid mobile")
    asyncio.run(khc.initiate_khc_otp(c, "9876543210"))
    assert saved(c) == {}


def test_one_village_saves_state_and_village_ids(monkeypatch):
    c = ctx()
    village = {"level": "village", "id": 5, "name": "Abupur", "state": STATE,
               "district": {"id": 221, "name": "Meerut"}, "subDistrict": {"id": 1067, "name": "Meerut Block"}}
    respond(monkeypatch, "khc_location", {"matches": json.dumps([village]), "total": "1"})
    asyncio.run(khc.find_khc_location(c, "Abupur", "UP", "Meerut"))
    assert saved(c) == {"state": "Uttar Pradesh", "state_id": 9, "village": "Abupur", "village_id": 5}


def test_several_villages_save_nothing(monkeypatch):
    c = ctx()
    rows = [{"level": "village", "id": i, "name": "Rampur", "state": STATE,
             "district": {"id": 1, "name": "D"}, "subDistrict": {"id": 10 + i, "name": f"S{i}"}} for i in (1, 2)]
    respond(monkeypatch, "khc_location", {"matches": json.dumps(rows), "total": "2"})
    asyncio.run(khc.find_khc_location(c, "Rampur", "UP", "D"))
    assert saved(c) == {}


def test_tree_with_one_option_saves_all_scheme_and_problem_ids(monkeypatch):
    c = ctx()
    respond(monkeypatch, "khc_grievance_tree", tree([
        {"id": 39, "name": "Loan", "subCategories": [{"id": 1725, "name": "Bank refused loan"}]},
    ]))
    asyncio.run(khc.get_khc_grievance_options(c, "UP", "AIF", "bank refused loan"))
    assert saved(c) == {
        "state": "Uttar Pradesh", "state_id": 9, "scheme": "AIF", "scheme_id": 38, "division_id": 31,
        "problem_category": "Loan", "problem_category_id": 39,
        "problem_subcategory": "Bank refused loan", "problem_subcategory_id": 1725,
    }


def test_tree_with_several_options_keeps_short_catalogue_and_no_guess(monkeypatch):
    c = ctx()
    respond(monkeypatch, "khc_grievance_tree", tree([
        {"id": 39, "name": "Loan", "subCategories": [{"id": 1725, "name": "Refused"}, {"id": 1726, "name": "Delay"}]},
    ]))
    asyncio.run(khc.get_khc_grievance_options(c, "UP", "AIF", "loan problem"))
    assert "problem_subcategory_id" not in saved(c) and "problem_category_id" not in saved(c)
    assert saved(c)["problem_options"] == [
        {"category": "Loan", "category_id": 39, "subcategories": {"Refused": 1725, "Delay": 1726}},
    ]


def test_tree_with_long_catalogue_saves_no_options(monkeypatch):
    c = ctx()
    subs = [{"id": 1000 + i, "name": f"Sub {i}"} for i in range(khc._MAX_REMEMBERED_OPTIONS + 1)]
    respond(monkeypatch, "khc_grievance_tree", tree([{"id": 39, "name": "Loan", "subCategories": subs}]))
    asyncio.run(khc.get_khc_grievance_options(c, "UP", "AIF", "loan problem"))
    assert saved(c)["scheme_id"] == 38 and "problem_options" not in saved(c)


def test_tree_without_concern_or_with_ambiguous_scheme(monkeypatch):
    c = ctx()
    respond(monkeypatch, "khc_grievance_tree", {
        "state": json.dumps(STATE), "needs": json.dumps("scheme"), "match": json.dumps("several"),
        "schemes": json.dumps([SCHEME, {"id": 40, "name": "AIF 2", "division": 32}]),
    })
    asyncio.run(khc.get_khc_grievance_options(c, "UP", "a"))
    assert saved(c) == {"state": "Uttar Pradesh", "state_id": 9}

    respond(monkeypatch, "khc_grievance_tree", tree([]))
    asyncio.run(khc.get_khc_grievance_options(c, "UP", "AIF"))
    assert saved(c) == {"state": "Uttar Pradesh", "state_id": 9, "scheme": "AIF", "scheme_id": 38, "division_id": 31}


def test_malformed_catalogue_saves_nothing(monkeypatch):
    c = ctx()
    respond(monkeypatch, "khc_grievance_tree", tree([]))
    asyncio.run(khc.get_khc_grievance_options(c, "UP", "AIF", "loan refused"))
    assert saved(c) == {}


def test_changing_state_drops_old_village_scheme_and_problem(monkeypatch):
    c = ctx()
    c.deps.session_state["khc"] = {"state_id": 10, "state": "Bihar", "village_id": 7, "scheme_id": 38,
                                   "division_id": 31, "problem_subcategory_id": 1725, "mobile": "9876543210"}
    respond(monkeypatch, "khc_grievance_tree", tree([]))
    asyncio.run(khc.get_khc_grievance_options(c, "UP", "AIF"))
    assert saved(c) == {"state": "Uttar Pradesh", "state_id": 9, "scheme": "AIF", "scheme_id": 38,
                        "division_id": 31, "mobile": "9876543210"}


def test_reference_lookup_saves_only_a_single_row(monkeypatch):
    c = ctx()
    respond(monkeypatch, "khc_reference", {
        "options": json.dumps([{"problemsubcategory": 1725, "subcategoryname": "Bank refused loan"},
                               {"problemsubcategory": 1726, "subcategoryname": "Delay"}]),
        "total": "2", "page": "1",
    })
    asyncio.run(khc.lookup_khc_reference(c, "sub-categories", 39))
    assert saved(c) == {}

    respond(monkeypatch, "khc_reference", {
        "options": json.dumps([{"villageid": "219159", "villagename": "Test Village"}]), "total": "1", "page": "1",
    })
    asyncio.run(khc.lookup_khc_reference(c, "villages", 1067, name="test village"))
    assert saved(c) == {"village": "Test Village", "village_id": 219159}

    respond(monkeypatch, "khc_reference", {
        "options": json.dumps([{"scheme": 38, "schemename": "AIF", "division": 31}]), "total": "1", "page": "1",
    })
    asyncio.run(khc.lookup_khc_reference(c, "schemes", 9, name="AIF"))
    assert saved(c)["scheme_id"] == 38 and saved(c)["division_id"] == 31


def test_registration_saves_grievance_number_and_clears_in_progress_ids(monkeypatch):
    c = ctx()
    c.deps.session_state["khc"] = {
        "mobile": "9876543210", "otp_step": "verified", "farmer_type": "existing",
        "state": "Bihar", "state_id": 10, "village": "V", "village_id": 219159, "scheme": "AIF", "scheme_id": 38,
        "division_id": 31, "problem_category": "Loan", "problem_category_id": 39,
        "problem_subcategory": "Refused", "problem_subcategory_id": 1725,
    }
    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope("init")), [])
    asyncio.run(khc.register_khc_grievance(c, **registration()))
    assert saved(c) == {"mobile": "9876543210", "otp_step": "verified", "farmer_type": "existing",
                        "grievance_number": "BR/240926/2"}


def test_unconfirmed_registration_keeps_ids(monkeypatch):
    c = ctx()
    before = {"otp_step": "verified", "state_id": 10, "village_id": 219159}
    c.deps.session_state["khc"] = dict(before)
    fake_http(monkeypatch, khc.httpx.Response(500), [])
    asyncio.run(khc.register_khc_grievance(c, **registration()))
    assert saved(c) == before


def test_registration_reverification_resets_otp_step(monkeypatch):
    c = ctx()
    c.deps.session_state["khc"] = {"mobile": "9876543210", "otp_step": "verified", "state_id": 10}
    respond(monkeypatch, "otp_required", {}, "Verify mobile")
    asyncio.run(khc.register_khc_grievance(c, **registration()))
    assert saved(c) == {"mobile": "9876543210", "state_id": 10}


def test_status_flow_saves_number_and_step_but_not_the_otp(monkeypatch):
    c = ctx()
    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope("status", "status_otp_sent", {})), [])
    asyncio.run(khc.check_khc_grievance_status(c, "br/240926/2"))
    assert saved(c) == {"grievance_number": "BR/240926/2", "status_otp_step": "sent"}

    fake_http(monkeypatch, khc.httpx.Response(200, json=envelope("status")), [])
    asyncio.run(khc.check_khc_grievance_status(c, "BR/240926/2", "012345"))
    assert saved(c) == {"grievance_number": "BR/240926/2"}
    assert "012345" not in json.dumps(c.deps.session_state)
