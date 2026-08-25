"""Negative-path checks for the AIF tools against the live provider network.

Calls the tools directly (no LLM) so each error condition can be provoked exactly.
usage: python scratch_aif_neg.py
"""
import uuid

from agents.tools import aif


class Deps:
    def __init__(self, session_id):
        self.session_id = session_id
        self.question_id = "neg-test"


class Ctx:
    def __init__(self, session_id):
        self.deps = Deps(session_id)


def case(title, fn, *args, **kwargs):
    print("=" * 70)
    print(f"CASE: {title}")
    try:
        print(f"  -> {fn(*args, **kwargs)!r}")
    except Exception as e:  # noqa: BLE001 - surfacing anything that escapes is the point
        print(f"  -> RAISED {type(e).__name__}: {e}")


fresh = lambda: Ctx(f"neg-{uuid.uuid4().hex[:8]}")  # noqa: E731

# --- input validation, no network ---------------------------------------------
case("beneficiary_id non-numeric", aif.initiate_aif_otp, fresh(), beneficiary_id="abc123")
case("beneficiary_id empty", aif.initiate_aif_otp, fresh(), beneficiary_id="")
case("beneficiary_id with spaces/symbols", aif.initiate_aif_otp, fresh(), beneficiary_id="185-195")
case("otp non-numeric", aif.verify_aif_otp, fresh(), otp="abcd", beneficiary_id="185195")
case("otp empty", aif.verify_aif_otp, fresh(), otp="", beneficiary_id="185195")
case("loan number missing", aif.check_aif_loan_status, fresh(), beneficiary_id="185195",
     loan_application_number="")
case("loan number non-numeric", aif.check_aif_loan_status, fresh(), beneficiary_id="185195",
     loan_application_number="ABC/2024")

# --- unverified session (never sent an OTP on this transaction) ----------------
case("loan status without verification", aif.check_aif_loan_status, fresh(),
     beneficiary_id="185195", loan_application_number="101154")
case("grievance status without verification", aif.check_aif_grievance_status, fresh(),
     beneficiary_id="185195")

# --- upstream rejections -------------------------------------------------------
case("unknown beneficiary", aif.initiate_aif_otp, fresh(), beneficiary_id="999999999")
case("beneficiary id of 0", aif.initiate_aif_otp, fresh(), beneficiary_id="0")

# --- digit normalisation (Devanagari 185195) -----------------------------------
print("=" * 70)
print("CASE: Devanagari digits normalise to ASCII")
print(f"  -> _numeric('१८५१९५') = {aif._numeric('१८५१९५')!r}")
print(f"  -> _numeric('  185 195 ') = {aif._numeric('  185 195 ')!r}")
print(f"  -> _numeric('١٨٥') (Arabic-Indic) = {aif._numeric('١٨٥')!r}")

# --- transaction id binding ----------------------------------------------------
print("=" * 70)
print("CASE: transaction id is stable per (session, beneficiary) and differs across both")
from agents.tools.pmkisan_scheme_status import generate_transaction_id

print(f"  same session+ben  : {generate_transaction_id('s1', '185195')}")
print(f"  same session+ben  : {generate_transaction_id('s1', '185195')}")
print(f"  diff beneficiary  : {generate_transaction_id('s1', '999999')}")
print(f"  diff session      : {generate_transaction_id('s2', '185195')}")
