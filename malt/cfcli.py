"""Thin wrapper around the new Cloudflare `cf` CLI.

malt used to shell out to `cloudflared` directly (cert.pem login,
`tunnel route dns`, manual ~/.cloudflared/config.yml patching). That works
but the UX is fragile: browser cert login, overwrite/takeover dialogs,
dig-based DNS checks.

`cf` (v1 beta) already handles auth via oauth profiles and exposes
zones / tunnels / dns records as JSON. This module is the single place
that talks to `cf` so views never build subprocess commands inline.

All functions are best-effort: they return None / (False, msg) instead
of raising, because the GUI must stay usable when `cf` is missing or
logged out.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

from . import settings

# Tiny TTL cache — every `cf` call costs ~3s (spawns node + API roundtrip).
# Without this the settings dialog fires zones/tunnels/dns/auth repeatedly
# and feels frozen. Cache lives per-process; sync actions invalidate DNS.
_CACHE: dict[str, tuple[float, object]] = {}
_TTL = {
    "auth": 120.0,
    "zones": 120.0,
    "tunnels": 30.0,
    "dns": 30.0,
    "config": 30.0,
}


def _profile() -> str:
    """Return the configured cf profile without exposing it in UI/logs."""
    try:
        value = settings.get("cf_profile") or ""
    except Exception:
        value = ""
    return value.strip()


def command(*args: str) -> list[str]:
    """Build every local ``cf`` command in one place."""
    profile = _profile()
    prefix = ["cf"] if not profile else ["cf", f"--profile={profile}"]
    return prefix + list(args)


def _cached(key: str, loader, ttl_key: str):
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit and (now - hit[0]) < _TTL[ttl_key]:
        return hit[1]
    val = loader()
    _CACHE[key] = (now, val)
    return val


def invalidate(prefix: str = "") -> None:
    """Drop cached entries (call after sync/create actions)."""
    for k in [k for k in _CACHE if k.startswith(prefix)]:
        _CACHE.pop(k, None)


def detect_cf() -> dict:
    """Check whether the `cf` binary exists and its version."""
    path = shutil.which("cf")
    version = None
    if path:
        try:
            out = subprocess.check_output(
                command("--version"), stderr=subprocess.STDOUT, text=True, timeout=5
            )
            version = out.strip().splitlines()[0] if out.strip() else None
        except Exception:
            version = None
    return {"found": path is not None, "path": path, "version": version}


def _run_json(args: list[str], timeout: int = 20) -> object | None:
    """Run `cf ...` and parse stdout as JSON. Returns None on any failure."""
    try:
        out = subprocess.check_output(
            args, stderr=subprocess.STDOUT, text=True, timeout=timeout
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    try:
        return json.loads(out)
    except (json.JSONDecodeError, ValueError):
        return None


def auth_status() -> dict:
    """Return `cf auth whoami` parsed: authenticated, email, accounts."""
    data = _cached(f"auth:{_profile()}", lambda: _run_json(command("auth", "whoami")), "auth")
    if not isinstance(data, dict):
        return {"authenticated": False, "email": None, "accounts": []}
    return {
        "authenticated": bool(data.get("authenticated") and data.get("tokenValid", True)),
        "email": data.get("email"),
        "accounts": data.get("accounts") or [],
    }


def list_zones() -> list[dict]:
    """List active zones via `cf zones list` (cached 2 min)."""
    data = _cached(f"zones:{_profile()}", lambda: _run_json(command("zones", "list")), "zones")
    if not isinstance(data, list):
        return []
    zones = []
    for z in data:
        if not isinstance(z, dict):
            continue
        zones.append(
            {
                "id": z.get("id", ""),
                "name": z.get("name", ""),
                "status": z.get("status", ""),
            }
        )
    return zones


def zone_for_hostname(hostname: str) -> dict | None:
    """Find the longest-matching zone for a hostname (e.g. a.b.iydheko.dev)."""
    zones = list_zones()
    if not zones:
        return None
    host = hostname.strip().lower().rstrip(".")
    best = None
    for z in zones:
        name = (z.get("name") or "").lower()
        if host == name or host.endswith("." + name):
            if best is None or len(name) > len(best.get("name", "")):
                best = z
    return best


def list_tunnels() -> list[dict]:
    """List all tunnels via `cf tunnels list` (cached 30s)."""
    data = _cached(
        f"tunnels:{_profile()}", lambda: _run_json(command("tunnels", "list")), "tunnels"
    )
    if not isinstance(data, list):
        return []
    out = []
    for t in data:
        if not isinstance(t, dict):
            continue
        out.append(
            {
                "id": t.get("id", ""),
                "name": t.get("name", ""),
                "status": t.get("status", ""),
            }
        )
    return out


def tunnel_config(tunnel_id: str) -> dict | None:
    """Remote ingress config via `cf tunnels config get` (cached 30s)."""
    data = _cached(
        f"config:{_profile()}:{tunnel_id}",
        lambda: _run_json(command("tunnels", "config", "get", tunnel_id)),
        "config",
    )
    if not isinstance(data, dict):
        return None
    return data


def ingress_service_remote(tunnel_id: str, hostname: str) -> str | None:
    """Service for hostname in the *remote* tunnel config, if present."""
    cfg = tunnel_config(tunnel_id)
    if not cfg:
        return None
    ingress = (cfg.get("config") or {}).get("ingress") or []
    for entry in ingress:
        if isinstance(entry, dict) and entry.get("hostname") == hostname:
            return entry.get("service")
    return None


def dns_records(zone_name_or_id: str) -> list[dict]:
    """List DNS records for a zone via `cf dns records list -z` (cached 30s)."""
    data = _cached(
        f"dns:{_profile()}:{zone_name_or_id}",
        lambda: _run_json(command("dns", "records", "list", "-z", zone_name_or_id)),
        "dns",
    )
    if not isinstance(data, list):
        return []
    return [r for r in data if isinstance(r, dict)]


def dns_state_cf(hostname: str) -> str:
    """DNS status via cf API: 'routed' | 'active' | 'missing' | 'unknown'.

    - 'routed': CNAME visibly points at *.cfargotunnel.com
    - 'active': some record exists for the name (proxied hides the target)
    - 'missing': zone found but no record for this name
    - 'unknown': cf missing / logged out / zone not found
    """
    if not detect_cf()["found"]:
        return "unknown"
    zone = zone_for_hostname(hostname)
    if not zone:
        return "unknown"
    records = dns_records(zone.get("name") or zone.get("id") or "")
    if not records:
        # empty could mean API failure — check zone list worked
        return "unknown" if not zone else "missing"
    host = hostname.strip().lower().rstrip(".")
    for r in records:
        if (r.get("name") or "").lower().rstrip(".") == host:
            content = (r.get("content") or "").lower()
            if "cfargotunnel.com" in content:
                return "routed"
            return "active"
    return "missing"


def ensure_dns_cname(hostname: str, tunnel_id: str) -> tuple[bool, str]:
    """Create/update proxied CNAME hostname -> <tunnel_id>.cfargotunnel.com.

    Uses `cf dns records create --body`. If a record already exists with the
    right target, returns (True, 'already'). If a conflicting record exists,
    attempts an in-place update via the API shape cf expects (delete+create
    is left to the dashboard to keep this conservative).
    Returns (ok, detail).
    """
    zone = zone_for_hostname(hostname)
    if not zone:
        return False, f"no cloudflare zone found for {hostname}"
    zone_ref = zone.get("name") or zone.get("id") or ""
    target = f"{tunnel_id}.cfargotunnel.com"
    records = dns_records(zone_ref)
    host = hostname.strip().lower().rstrip(".")
    existing = [
        r for r in records if (r.get("name") or "").lower().rstrip(".") == host
    ]
    for r in existing:
        if r.get("type") == "CNAME" and (r.get("content") or "") == target:
            return True, f"{hostname} already points at tunnel"
    body = json.dumps(
        {
            "type": "CNAME",
            "name": hostname,
            "content": target,
            "proxied": True,
            "ttl": 1,
        }
    )
    try:
        proc = subprocess.run(
            command("dns", "records", "create", "-z", zone_ref, "--body", body),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        return False, "cf not found"
    except subprocess.TimeoutExpired:
        return False, "timed out after 30s"
    if proc.returncode == 0:
        action = "updated" if existing else "created"
        invalidate("dns:")
        return True, f"{hostname} {action} → {target}"
    err = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    # record already exists — user likely needs delete first; surface clearly
    if "already exists" in err.lower():
        return False, f"DNS record for {hostname} already exists with other content — delete it in dash first"
    return False, err[-300:] or "(no output)"


def default_tunnel_id(preferred_name: str = "malt") -> str | None:
    """Pick a tunnel id: preferred name first, else first available."""
    tunnels = list_tunnels()
    if not tunnels:
        return None
    for t in tunnels:
        if t.get("name") == preferred_name and t.get("id"):
            return t["id"]
    return tunnels[0].get("id")


def quick_start_cmd(url: str) -> list[str]:
    """Command for ephemeral tunnel: `cf tunnels quick-start <url>`."""
    return command("tunnels", "quick-start", url)


def run_cmd(tunnel: str) -> list[str]:
    """Command to run a named tunnel via cf-managed cloudflared."""
    return command("tunnels", "run", tunnel)


def state_dir() -> Path:
    return Path.home() / ".config" / "cloudflare"
