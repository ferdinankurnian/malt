"""Global settings storage."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

SETTINGS_PATH = Path.home() / ".config" / "malt" / "settings.json"

DEFAULTS: dict[str, Any] = {
    "tunnel_provider": "cloudflare",
    "domains": [],
    "default_mcp_port": 3100,
    "url_visible_by_default": False,
    "sidebar_width": 260,
    "onboarding_dismissed": False,
    "tunnel_ever_started": False,
}


def _path() -> Path:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    return SETTINGS_PATH


def _migrate(data: dict[str, Any]) -> dict[str, Any]:
    """Migrate legacy tunnel_hostname → domains list."""
    if "domains" not in data:
        data["domains"] = []

    old_host = data.pop("tunnel_hostname", None)
    if old_host and isinstance(old_host, str) and old_host.strip():
        host = old_host.strip()
        already = any(
            d.get("hostname") == host for d in data["domains"] if isinstance(d, dict)
        )
        if not already:
            data["domains"].append(
                {
                    "id": uuid.uuid4().hex[:12],
                    "hostname": host,
                    "provider": "cloudflare",
                    "mode": "named",
                }
            )

    if "tunnel_provider" not in data:
        data["tunnel_provider"] = "cloudflare"
    if "onboarding_dismissed" not in data:
        data["onboarding_dismissed"] = False
    if "tunnel_ever_started" not in data:
        data["tunnel_ever_started"] = False

    return data


def load() -> dict[str, Any]:
    p = _path()
    if p.exists():
        with open(p) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
        data = _migrate(data)
        merged = dict(DEFAULTS)
        merged.update(data)
        # Ensure domains is always a list
        if not isinstance(merged.get("domains"), list):
            merged["domains"] = []
        return merged
    return dict(DEFAULTS)


def save(settings: dict[str, Any]) -> None:
    p = _path()
    # Never persist legacy key
    out = {k: v for k, v in settings.items() if k != "tunnel_hostname"}
    with open(p, "w") as f:
        json.dump(out, f, indent=2)


def get(key: str) -> Any:
    data = load()
    if key == "tunnel_hostname":
        # Backward-compat for callers that still read the old key
        # (e.g. project_detail until redesigned).
        domains = data.get("domains") or []
        if domains:
            return domains[0].get("hostname", "")
        return ""
    return data.get(key, DEFAULTS.get(key))


def set(key: str, value: Any) -> None:
    settings = load()
    if key == "tunnel_hostname":
        # Map legacy writes onto domains list
        host = (value or "").strip() if isinstance(value, str) else ""
        domains = list(settings.get("domains") or [])
        if host:
            if domains:
                domains[0] = {
                    **domains[0],
                    "hostname": host,
                    "provider": domains[0].get("provider", "cloudflare"),
                    "mode": domains[0].get("mode", "named"),
                }
            else:
                domains.append(
                    {
                        "id": uuid.uuid4().hex[:12],
                        "hostname": host,
                        "provider": "cloudflare",
                        "mode": "named",
                    }
                )
        settings["domains"] = domains
    else:
        settings[key] = value
    save(settings)


def list_domains() -> list[dict[str, Any]]:
    return list(load().get("domains") or [])


def get_domain(domain_id: str | None) -> dict[str, Any] | None:
    if not domain_id:
        return None
    for d in list_domains():
        if d.get("id") == domain_id:
            return d
    return None


def add_domain(
    hostname: str,
    provider: str = "cloudflare",
    mode: str = "named",
) -> dict[str, Any]:
    hostname = hostname.strip()
    if not hostname:
        raise ValueError("hostname required")
    settings = load()
    domains = list(settings.get("domains") or [])
    for d in domains:
        if d.get("hostname") == hostname:
            raise ValueError(f"domain already exists: {hostname}")
    entry = {
        "id": uuid.uuid4().hex[:12],
        "hostname": hostname,
        "provider": provider,
        "mode": mode,
    }
    domains.append(entry)
    settings["domains"] = domains
    save(settings)
    return entry


def remove_domain(domain_id: str) -> bool:
    settings = load()
    domains = list(settings.get("domains") or [])
    new_domains = [d for d in domains if d.get("id") != domain_id]
    if len(new_domains) == len(domains):
        return False
    settings["domains"] = new_domains
    save(settings)
    return True


def domain_in_use(domain_id: str) -> bool:
    """Check whether any project references this domain."""
    try:
        from . import db

        for row in db.list_projects():
            if row.get("domain_id") == domain_id:
                return True
    except Exception:
        return False
    return False
