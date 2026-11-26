"""The v2 grievance category gate, which no other test can reach.

pmkisan_submit_grievance returns the disabled message on its first line, so
every check inside it is dead code today and will run for the first time on
the day lodging is re-enabled. These tests exercise the gate directly so that
day is not the first time anyone finds out what it does.
"""
import pytest

from agents.tools.pmkisan_grievance import GRIEVANCE_TYPES
from agents.tools.v2.pmkisan_grievance import (
    _CATEGORY_CODES,
    _PUBLISHED_CATEGORIES,
    category_for,
)

# The thirteen v1 types with no published heading. Listed out rather than
# derived, so adding a mapping without updating the migration note fails here.
UNMAPPED = {
    "INCORRECTLY_MARKED_AS_DECEASED",
    "INCORRECTLY_MARKED_AS_DECEASED_PREVIOUS_LANDOWNER",
    "INCORRECTLY_MARKED_AS_INELIGIBLE",
    "VOLUNTARY_SURRENDER_BY_MISTAKE",
    "INCORRECTLY_MARKED_UNDER_SUSPECTED_CASES",
    "PROBLEM_WITH_UPDATING_LAND_DETAILS",
    "PENDING_APPROVAL_FOR_INELIGIBILITY_REVOCATION",
    "PENDING_APPROVAL_FOR_SURRENDER_REVOCATION",
    "PENDING_APPROVAL_FOR_UPDATION_OF_MISSING_INFORMATION",
    "PENDING_ACTION_SUSPECTED_SPOUSE_OR_MINOR",
    "PENDING_ACTION_SUSPECTED_INCORRECT_LAND_OR_PREVIOUS_OWNER",
    "CORRUPTION",
    "URGENT",
}


def test_every_v1_type_is_either_mapped_or_knowingly_refused():
    assert set(_CATEGORY_CODES) | UNMAPPED == set(GRIEVANCE_TYPES)
    assert not set(_CATEGORY_CODES) & UNMAPPED


@pytest.mark.parametrize("grievance_type", sorted(_CATEGORY_CODES))
def test_a_mapped_type_yields_a_published_code(grievance_type):
    code, refusal = category_for(grievance_type)
    assert code in _PUBLISHED_CATEGORIES
    assert refusal == ""


@pytest.mark.parametrize("grievance_type", sorted(UNMAPPED))
def test_an_unmapped_type_is_refused_rather_than_misfiled(grievance_type):
    code, refusal = category_for(grievance_type)
    assert code == ""
    assert refusal
    # The farmer is told what IS accepted, not just that this is not.
    for heading in _PUBLISHED_CATEGORIES.values():
        assert heading in refusal


def test_corruption_is_never_given_a_code():
    """The specific misfiling this gate exists to prevent.

    "Payment related" is the nearest-sounding heading and routing a corruption
    report to the payments desk is worse than refusing it.
    """
    code, refusal = category_for("CORRUPTION")
    assert code == ""
    assert "helpline" in refusal.lower()


def test_an_unknown_label_is_refused_too():
    code, refusal = category_for("NOT_A_REAL_TYPE")
    assert code == ""
    assert refusal
