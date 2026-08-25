"""Exercise every verified-session path with one single-use OTP."""
import sys

from agents.tools import aif

OTP = sys.argv[1]
BEN = "185195"
SESSION = sys.argv[2]


class Deps:
    session_id = SESSION
    question_id = "verified-test"


class Ctx:
    deps = Deps()


def case(title, fn, **kwargs):
    print("=" * 70)
    print(f"CASE: {title}")
    print(f"  -> {fn(Ctx(), **kwargs)}")


case("verify OTP", aif.verify_aif_otp, otp=OTP, beneficiary_id=BEN)
case("grievance status (verified)", aif.check_aif_grievance_status, beneficiary_id=BEN)
case("loan status, unknown application number", aif.check_aif_loan_status,
     beneficiary_id=BEN, loan_application_number="1")
case("beneficiary swap on verified transaction", aif.check_aif_grievance_status,
     beneficiary_id="106545")
case("re-use verified session (no second OTP)", aif.check_aif_grievance_status,
     beneficiary_id=BEN)
print("=" * 70)
print(f"SESSION: {SESSION}")
