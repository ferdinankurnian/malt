"""Pure formatting and validation helpers used by provider UI."""

from __future__ import annotations

import re


def mask_email(value: object) -> str:
    """Mask an email for default UI display without retaining its raw value."""
    email = value.strip() if isinstance(value, str) else ""
    if "@" not in email:
        return ""
    local, domain = email.rsplit("@", 1)
    if not local or not domain or "." not in domain:
        return ""
    if len(local) == 1:
        masked = local
    elif len(local) == 2:
        masked = local[0] + "•"
    else:
        masked = local[0] + "•" * min(4, len(local) - 1)
    return f"{masked}@{domain}"


def validate_subdomain(value: str) -> str:
    """Validate and normalize a prefix entered before a selected zone."""
    prefix = value.strip().lower()
    if not prefix:
        raise ValueError("subdomain is required")
    if "://" in prefix or "/" in prefix or any(ch.isspace() for ch in prefix):
        raise ValueError("enter only a subdomain prefix")
    if "." in prefix or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", prefix):
        raise ValueError("enter only a single subdomain prefix")
    return prefix


def compose_hostname(subdomain: str, zone: str) -> str:
    """Compose a hostname from a validated prefix and zone."""
    normalized_zone = zone.strip().lower().strip(".")
    if not normalized_zone or "." not in normalized_zone:
        raise ValueError("a valid zone is required")
    return f"{validate_subdomain(subdomain)}.{normalized_zone}"
