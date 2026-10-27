import pytest

from malt.masking import compose_hostname, mask_email, validate_subdomain


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("a@example.com", "a@example.com"),
        ("ab@example.com", "a•@example.com"),
        ("alice@example.com", "a••••@example.com"),
        ("", ""),
        ("not-an-email", ""),
        ("@example.com", ""),
    ],
)
def test_mask_email(value, expected):
    assert mask_email(value) == expected


def test_subdomain_composition_and_validation():
    assert compose_hostname("mcp", "iydheko.dev") == "mcp.iydheko.dev"
    for invalid in ("", " ", "https://mcp", "mcp/path", "mcp.iydheko.dev"):
        with pytest.raises(ValueError):
            validate_subdomain(invalid)
