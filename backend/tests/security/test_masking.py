import pytest

from app.platform.masking import PII_ROLES, can_view_pii, mask_email, mask_name
from tests.helpers import PERSONAS, principal_for


@pytest.mark.parametrize(
    "raw,masked",
    [
        ("ada@customer.test", "a***@customer.test"),
        ("Ada.Lovelace+tag@sub.customer.test", "A***@sub.customer.test"),
        ("a@b.c", "a***@b.c"),
        ("weird@local@customer.test", "w***@customer.test"),
        ("not-an-email", "***"),
        ("@customer.test", "***"),
        ("ada@", "***"),
        ("", "***"),
    ],
)
def test_mask_email(raw, masked):
    assert mask_email(raw) == masked


def test_mask_email_never_contains_local_part_beyond_first_char():
    assert "lovelace" not in mask_email("lovelace@customer.test")


@pytest.mark.parametrize(
    "raw,masked", [("Ada Lovelace", "A*** L***"), ("  Ada  ", "A***"), ("", "***")]
)
def test_mask_name(raw, masked):
    assert mask_name(raw) == masked


@pytest.mark.parametrize("persona", sorted(PERSONAS))
def test_can_view_pii_by_role(persona):
    roles = set(PERSONAS[persona][0])
    assert can_view_pii(principal_for(persona)) == bool(roles & set(PII_ROLES))


def test_only_viewer_is_masked_among_personas():
    assert [p for p in sorted(PERSONAS) if not can_view_pii(principal_for(p))] == ["erin"]
