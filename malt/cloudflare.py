"""Cloudflare tunnel helpers — now fronted by the `cf` CLI.

malt manages hostnames at two layers outside its own settings.json:

1. DNS — a proxied CNAME for the hostname pointing at the tunnel
   (now via ``cf dns records create``, was ``cloudflared tunnel route dns``)
2. ``~/.cloudflared/config.yml`` — an ingress entry routing the
   hostname to the local MCP port (still local-managed, patched surgically).

Auth is via `cf auth` (oauth) instead of cert.pem browser login.
Everything here stays conservative:

- never silently overwrite DNS or an ingress entry owned by someone else
- always back up config.yml before writing
- malt-owned ingress blocks are tagged with ``# managed by malt``
"""

from __future__ import annotations

import shutil
import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

CONFIG_PATH = Path.home() / ".cloudflared" / "config.yml"
CERT_PATH = Path.home() / ".cloudflared" / "cert.pem"
MANAGED_MARKER = "# managed by malt"

__all__ = [
    "CONFIG_PATH",
    "CERT_PATH",
    "cert_exists",
    "cf_available",
    "cf_auth_ok",
    "active_tunnel",
    "tunnel_name",
    "ingress_service",
    "config_has_hostname",
    "dns_state",
    "route_dns",
    "route_dns_cf",
    "classify_route_error",
    "ensure_ingress",
    "sync_domain_cf",
    "domain_status",
]


def cf_available() -> bool:
    try:
        from . import cfcli as cf_mod

        return bool(cf_mod.detect_cf()["found"])
    except Exception:
        return False


def cf_auth_ok() -> bool:
    try:
        from . import cfcli as cf_mod

        return bool(cf_mod.auth_status().get("authenticated"))
    except Exception:
        return False


def active_tunnel() -> dict | None:
    """Preferred tunnel from settings, else auto-detect via cf."""
    try:
        from . import cfcli as cf_mod
        from . import settings as settings_mod

        stored_id = (settings_mod.get("cf_tunnel_id") or "").strip()
        stored_name = (settings_mod.get("cf_tunnel_name") or "").strip() or "malt"
        tunnels = cf_mod.list_tunnels()
        if not tunnels:
            return None
        if stored_id:
            for t in tunnels:
                if t.get("id") == stored_id:
                    return t
        for t in tunnels:
            if t.get("name") == stored_name:
                return t
        return tunnels[0]
    except Exception:
        return None


def cert_exists() -> bool:
    return CERT_PATH.exists()


def api_request(token: str, method: str, path: str, payload: dict | None = None) -> dict:
    """Call the Cloudflare API using a user-provided API token."""
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4{path}",
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            result = json.loads(response.read().decode())
    except (OSError, urllib.error.HTTPError) as e:
        detail = getattr(e, "read", lambda: b"")()
        try:
            detail = json.loads(detail.decode()).get("errors", [{}])[0].get("message")
        except Exception:
            detail = str(e)
        raise RuntimeError(f"Cloudflare API: {detail}") from e
    if not result.get("success"):
        errors = result.get("errors") or [{}]
        raise RuntimeError(f"Cloudflare API: {errors[0].get('message', 'request failed')}")
    return result


def api_zones(token: str) -> list[dict[str, str]]:
    result = api_request(token, "GET", "/zones?per_page=100&status=active")
    return [{"id": z["id"], "name": z["name"]} for z in result.get("result", [])]


def api_route_dns(token: str, zone_id: str, hostname: str, tunnel_id: str) -> None:
    """Create/update the proxied CNAME used by a named Cloudflare tunnel."""
    target = f"{tunnel_id}.cfargotunnel.com"
    existing = api_request(token, "GET", f"/zones/{zone_id}/dns_records?type=CNAME&name={hostname}")
    records = existing.get("result", [])
    record = {"type": "CNAME", "name": hostname, "content": target, "ttl": 1, "proxied": True}
    if records:
        api_request(token, "PUT", f"/zones/{zone_id}/dns_records/{records[0]['id']}", record)
    else:
        api_request(token, "POST", f"/zones/{zone_id}/dns_records", record)


def tunnel_id() -> str | None:
    """Get the UUID from credentials-file in the local named-tunnel config."""
    if not CONFIG_PATH.exists():
        return None
    try:
        text = CONFIG_PATH.read_text()
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("credentials-file:"):
            return Path(line.split(":", 1)[1].strip()).stem
    return None


def tunnel_name() -> str | None:
    """Return the ``tunnel:`` value from config.yml, if present."""
    if not CONFIG_PATH.exists():
        return None
    try:
        text = CONFIG_PATH.read_text()
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("tunnel:"):
            return line[len("tunnel:") :].strip().strip('"') or None
    return None


def ingress_service(hostname: str) -> str | None:
    """Return the ``service:`` for a hostname in config.yml, if present."""
    if not CONFIG_PATH.exists():
        return None
    try:
        lines = CONFIG_PATH.read_text().splitlines()
    except OSError:
        return None
    for i, line in enumerate(lines):
        code = line.split("#", 1)[0].strip()
        if code == f"- hostname: {hostname}":
            # scan the block until the next ingress entry
            for follow in lines[i + 1 :]:
                stripped = follow.split("#", 1)[0].strip()
                if stripped.startswith("- hostname:") or stripped.startswith(
                    "- service:"
                ):
                    break
                if stripped.startswith("service:"):
                    return stripped[len("service:") :].strip()
    return None


def config_has_hostname(hostname: str) -> bool:
    return ingress_service(hostname) is not None


def dns_state(hostname: str) -> str:
    """Best-effort DNS check: 'routed' | 'active' | 'missing' | 'unknown'.

    Prefers the `cf` API (accurate even for proxied records), falls back
    to `dig` when cf is unavailable.
    """
    try:
        from . import cfcli as cf_mod

        if cf_mod.detect_cf()["found"]:
            state = cf_mod.dns_state_cf(hostname)
            if state != "unknown":
                return state
    except Exception:
        pass
    dig = shutil.which("dig")
    if dig is None:
        return "unknown"
    try:
        cname = subprocess.run(
            [dig, "+short", "CNAME", hostname],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    text = ((cname.stdout or "").strip().lower())
    if "cfargotunnel.com" in text:
        return "routed"
    # Proxied (orange-cloud) records flatten to A/AAAA — check any answer.
    try:
        any_rec = subprocess.run(
            [dig, "+short", hostname],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if (any_rec.stdout or "").strip():
        return "active"
    return "missing"


def route_dns(
    tunnel: str, hostname: str, overwrite: bool = False
) -> tuple[bool, str]:
    """Run ``cloudflared tunnel route dns``. Returns (ok, output)."""
    cmd = ["cloudflared", "tunnel", "route", "dns"]
    if overwrite:
        cmd.append("--overwrite-dns")
    cmd.extend([tunnel, hostname])
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60
        )
    except FileNotFoundError:
        return False, "cloudflared not found"
    except subprocess.TimeoutExpired:
        return False, "timed out after 60s"
    output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    return proc.returncode == 0, output or "(no output)"


def classify_route_error(output: str) -> str:
    """Classify a failed route-dns output."""
    low = output.lower()
    if "authentication error" in low or "unauthorized" in low:
        return "auth"
    if "already exists" in low or "overwrite" in low or "already routed" in low:
        return "exists"
    if (
        "no such zone" in low
        or "zone not found" in low
        or "not found in your account" in low
        or "failed to add" in low
        and "zone" in low
    ):
        return "no_zone"
    if "cannot find zone" in low or "no zone" in low:
        return "no_zone"
    return "other"


def ensure_ingress(hostname: str, port: int) -> tuple[bool, str, str]:
    """Insert/update an ingress block for hostname. Returns (ok, action, detail).

    action is one of 'added' | 'updated' | 'already'. Never touches
    non-matching ingress entries (e.g. other hostnames on other ports).
    """
    if not CONFIG_PATH.exists():
        return (
            False,
            "missing",
            f"{CONFIG_PATH} does not exist — run `cloudflared tunnel login` first",
        )
    try:
        text = CONFIG_PATH.read_text()
        lines = text.splitlines()
    except OSError as e:
        return False, "error", f"cannot read config: {e}"

    if "ingress:" not in text:
        return (
            False,
            "error",
            "config.yml has no ingress: section — refusing to guess",
        )

    want_service = f"http://127.0.0.1:{port}"
    existing = ingress_service(hostname)
    if existing == want_service:
        return True, "already", f"{hostname} already points at {want_service}"

    # backup before any write
    try:
        backup = CONFIG_PATH.with_suffix(".yml.bak")
        backup.write_text(text + ("" if text.endswith("\n") else "\n"))
    except OSError as e:
        return False, "error", f"cannot write backup: {e}"

    if existing is not None:
        # surgical update of just this hostname's block
        out: list[str] = []
        i = 0
        touched = False
        while i < len(lines):
            line = lines[i]
            code = line.split("#", 1)[0].strip()
            if code == f"- hostname: {hostname}":
                out.append(line)
                i += 1
                # consume block until next entry, rewriting service + host header
                saw_service = False
                saw_header = False
                # keep managed marker right after hostname line
                if not (out and out[-1].rstrip().endswith(MANAGED_MARKER)):
                    pass  # marker lives on service line instead; see below
                while i < len(lines):
                    nxt = lines[i]
                    stripped = nxt.strip()
                    if stripped.startswith("- hostname:") or stripped.startswith(
                        "- service:"
                    ):
                        break
                    if stripped.startswith("service:"):
                        out.append(f"    service: {want_service} {MANAGED_MARKER}")
                        saw_service = True
                    elif stripped.startswith("httpHostHeader:"):
                        indent = nxt[: len(nxt) - len(nxt.lstrip())]
                        out.append(f'{indent}httpHostHeader: "localhost:{port}"')
                        saw_header = True
                    else:
                        out.append(nxt)
                    i += 1
                if not saw_service:
                    out.append(f"    service: {want_service} {MANAGED_MARKER}")
                if not saw_header:
                    # ensure originRequest block exists
                    out.append("    originRequest:")
                    out.append(f'      httpHostHeader: "localhost:{port}"')
                touched = True
                continue
            out.append(line)
            i += 1
        if not touched:
            return False, "error", "hostname found but block could not be updated"
        CONFIG_PATH.write_text("\n".join(out) + "\n")
        return True, "updated", f"{hostname} repointed: {existing} → {want_service}"

    # insert new block before the catch-all, else append at end
    block = [
        f"  - hostname: {hostname} {MANAGED_MARKER}",
        f"    service: {want_service}",
        "    originRequest:",
        f'      httpHostHeader: "localhost:{port}"',
    ]
    catch_idx = next(
        (
            idx
            for idx, line in enumerate(lines)
            if line.strip() == "- service: http_status:404"
        ),
        None,
    )
    if catch_idx is None:
        lines.extend(block)
    else:
        lines[catch_idx:catch_idx] = block
    CONFIG_PATH.write_text("\n".join(lines) + "\n")
    return True, "added", f"{hostname} → {want_service}"


def route_dns_cf(hostname: str) -> tuple[bool, str]:
    """Ensure DNS via `cf dns records create`. Returns (ok, detail)."""
    tun = active_tunnel()
    if not tun or not tun.get("id"):
        return False, "no cloudflare tunnel found — run `cf tunnels list`"
    try:
        from . import cfcli as cf_mod

        return cf_mod.ensure_dns_cname(hostname, tun["id"])
    except Exception as e:
        return False, str(e)


def sync_domain_cf(hostname: str, port: int) -> tuple[bool, str]:
    """One-shot sync for cf-managed flow: DNS (cf API) + local ingress.

    Returns (ok, detail). DNS failure aborts before touching config.yml.
    """
    if not cf_available():
        return False, "cf not installed — yay -S cloudflare-cf || bun i -g @cloudflare/cf"
    if not cf_auth_ok():
        return False, "not logged in — run `cf auth login` in a terminal"
    tun = active_tunnel()
    if not tun or not tun.get("id"):
        return False, "no tunnel — create one with `cf tunnels create --name malt`"
    ok_dns, dns_detail = route_dns_cf(hostname)
    if not ok_dns:
        return False, dns_detail
    ok_cfg, _action, cfg_detail = ensure_ingress(hostname, port)
    if not ok_cfg:
        return False, cfg_detail
    # Persist the tunnel choice so `cf tunnels run` uses the same one.
    try:
        from . import settings as settings_mod

        settings_mod.set("cf_tunnel_id", tun["id"])
        if tun.get("name"):
            settings_mod.set("cf_tunnel_name", tun["name"])
    except Exception:
        pass
    return True, f"{hostname} synced ✓ ({dns_detail}; {cfg_detail})"


def domain_status(hostname: str, port: int) -> dict[str, str | bool | None]:
    """Combined status for one hostname: config + dns + cf auth.

    Returns dict with keys: cert_ok, cf_ok, tunnel, in_config,
    config_service, config_ok, dns (routed|missing|unknown).
    """
    service = ingress_service(hostname)
    return {
        "cert_ok": cert_exists() or cf_auth_ok(),
        "cf_ok": cf_auth_ok(),
        "tunnel": (active_tunnel() or {}).get("name") or tunnel_name(),
        "in_config": service is not None,
        "config_service": service,
        "config_ok": service == f"http://127.0.0.1:{port}",
        "dns": dns_state(hostname),
    }
