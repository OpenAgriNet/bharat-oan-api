import pytest
from pydantic_ai import ModelRetry

from agents.tools.pmkisan_grievance import _validate_pmkisan_registration_number


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("AB123456789", "AB123456789"),
        ("ab123456789", "AB123456789"),
    ],
)
def test_normalizes_valid_registration_number(value, expected):
    assert _validate_pmkisan_registration_number(value) == expected


@pytest.mark.parametrize("value", ["A1234567890", "ABC12345678", "AB12345678", "AB12345678X", "12123456789"])
def test_rejects_malformed_registration_number(value):
    with pytest.raises(ModelRetry, match="2 letters followed by 9 digits"):
        _validate_pmkisan_registration_number(value)


@pytest.mark.parametrize("value", ["", "  "])
def test_rejects_missing_registration_number(value):
    with pytest.raises(ModelRetry, match="Please provide the PM-KISAN Registration Number"):
        _validate_pmkisan_registration_number(value)
