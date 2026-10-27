"""Multi-provider tunnel process manager.

One tunnel process for the whole app (not per project). Supports:
- cloudflare named tunnels via `cf` (preferred) or `cloudflared` fallback
- cloudflare quick tunnels via `cf tunnels quick-start` (preferred)
- ngrok (dev domain / configured domain)

Legacy callers may still use start(hostname, port). Prefer start_domains().
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class DomainConfig:
    id: str
    hostname: str
    provider: str  # "cloudflare" | "ngrok"
    mode: str  # "named" | "quick" | "dev"


def detect_cf() -> dict[str, Any]:
    from . import cfcli as cf_mod

    return cf_mod.detect_cf()


def detect_cloudflared() -> dict[str, Any]:
    path = shutil.which("cloudflared")
    version = None
    if path:
        try:
            out = subprocess.check_output(
                ["cloudflared", "--version"],
                stderr=subprocess.STDOUT,
                text=True,
                timeout=5,
            )
            version = out.strip().splitlines()[0] if out.strip() else None
        except Exception:
            version = None
    return {"found": path is not None, "path": path, "version": version}


def detect_ngrok() -> dict[str, Any]:
    path = shutil.which("ngrok")
    has_token = False
    config_path = Path.home() / ".config" / "ngrok" / "ngrok.yml"
    # Also check legacy location
    legacy = Path.home() / ".ngrok2" / "ngrok.yml"
    for p in (config_path, legacy):
        if p.exists():
            text = p.read_text(errors="ignore")
            if "authtoken" in text:
                has_token = True
                break
    return {"found": path is not None, "path": path, "has_authtoken": has_token}


class TunnelManager:
    """Manages the single tunnel process for the app."""

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._provider: str | None = None
        self._quick_url: str | None = None
        self._active_domains: list[DomainConfig] = []
        self._stdout_thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ── Public API ──────────────────────────────────────────────

    def start(self, hostname: str, port: int = 3100) -> bool:
        """Legacy API: start a cloudflare tunnel for one hostname."""
        domains = [
            DomainConfig(
                id="legacy",
                hostname=hostname,
                provider="cloudflare",
                mode="named",
            )
        ]
        return self.start_domains(domains, port)

    def start_domains(self, domains: list[DomainConfig], port: int = 3100) -> bool:
        if self.is_running():
            # If already running with same set of hostnames, keep it.
            current = {(d.hostname, d.provider) for d in self._active_domains}
            wanted = {(d.hostname, d.provider) for d in domains}
            if current == wanted:
                return True
            self.stop()

        if not domains:
            # Pure quick tunnel fallback (cloudflare)
            return self._start_cloudflare_quick(port)

        providers = {d.provider for d in domains}
        if len(providers) > 1:
            # Prefer cloudflare if mixed; ngrok is single-endpoint
            domains = [d for d in domains if d.provider == "cloudflare"] or domains[:1]

        primary = domains[0].provider
        if primary == "ngrok":
            ok = self._start_ngrok(domains[0], port)
        else:
            named = [d for d in domains if d.mode != "quick"]
            if named:
                ok = self._start_cloudflare_named(named, port)
            else:
                ok = self._start_cloudflare_quick(port)

        if ok:
            self._active_domains = list(domains)
            try:
                from . import settings as settings_mod

                settings_mod.set("tunnel_ever_started", True)
            except Exception:
                pass
        return ok

    def stop(self) -> bool:
        with self._lock:
            if self._proc is None:
                self._quick_url = None
                self._active_domains = []
                self._provider = None
                return False
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
            self._quick_url = None
            self._active_domains = []
            self._provider = None
            return True

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def public_base_url(self, domain_id: str | None = None) -> str | None:
        """Return https base URL for a domain (or quick URL if none)."""
        if not self.is_running():
            return None
        if domain_id:
            for d in self._active_domains:
                if d.id == domain_id:
                    if d.mode == "quick" and self._quick_url:
                        return self._quick_url.rstrip("/")
                    return f"https://{d.hostname}"
        if self._quick_url:
            return self._quick_url.rstrip("/")
        if self._active_domains:
            d = self._active_domains[0]
            return f"https://{d.hostname}"
        return None

    # ── Cloudflare ──────────────────────────────────────────────

    def _start_cloudflare_named(self, domains: list[DomainConfig], port: int) -> bool:
        # Preferred: `cf tunnels run <tunnel>` — uses cf-managed cloudflared
        # with the same local credentials. Ingress + DNS are managed via the
        # cf API (see cfcli + cloudflare.ensure_ingress), so the tunnel just
        # needs to run; no generated config file anymore.
        try:
            from . import cfcli as cf_mod
            from . import settings as settings_mod

            if cf_mod.detect_cf()["found"] and cf_mod.auth_status().get(
                "authenticated"
            ):
                tunnel_ref = (
                    settings_mod.get("cf_tunnel_name") or "malt"
                ).strip() or "malt"
                tunnel_id = (settings_mod.get("cf_tunnel_id") or "").strip()
                # Prefer stored id, fall back to name (cf resolves both).
                ref = tunnel_id or tunnel_ref
                if cf_mod.list_tunnels():
                    # Ensure local ingress covers the requested hostnames so a
                    # locally-managed tunnel actually routes them.
                    try:
                        from . import cloudflare as cf_legacy

                        for d in domains:
                            cf_legacy.ensure_ingress(d.hostname, port)
                    except Exception:
                        pass
                    if self._spawn(cf_mod.run_cmd(ref), provider="cloudflare"):
                        return True
                    # fall through to cloudflared fallback below
        except Exception:
            pass

        config = self._named_tunnel_config([d.hostname for d in domains], port)
        if config is None:
            # Fall back to first hostname via quick-ish legacy command
            host = domains[0].hostname
            cmd = [
                "cloudflared",
                "tunnel",
                "--url",
                f"http://localhost:{port}",
                "--hostname",
                host,
                "--http-host-header",
                f"localhost:{port}",
            ]
        else:
            cmd = ["cloudflared", "tunnel", "--config", str(config), "run"]
        return self._spawn(cmd, provider="cloudflare")

    def _start_cloudflare_quick(self, port: int) -> bool:
        try:
            from . import cfcli as cf_mod

            if cf_mod.detect_cf()["found"]:
                ok = self._spawn(
                    cf_mod.quick_start_cmd(f"http://localhost:{port}"),
                    provider="cloudflare",
                    parse_quick=True,
                )
                if ok:
                    return True
        except Exception:
            pass
        cmd = [
            "cloudflared",
            "tunnel",
            "--url",
            f"http://localhost:{port}",
        ]
        ok = self._spawn(cmd, provider="cloudflare", parse_quick=True)
        return ok

    def _named_tunnel_config(self, hostnames: list[str], port: int) -> Path | None:
        source = Path.home() / ".cloudflared" / "config.yml"
        if not source.exists():
            return None

        text = source.read_text()
        tunnel = self._top_level_value(text, "tunnel")
        credentials = self._top_level_value(text, "credentials-file")
        if not tunnel or not credentials:
            return None

        # Require at least one hostname to appear in the user's config
        if not any(f"hostname: {h}" in text for h in hostnames):
            return None

        path = Path.home() / ".cache" / "malt" / "cloudflared-malt.yml"
        path.parent.mkdir(parents=True, exist_ok=True)

        lines = [
            f"tunnel: {tunnel}",
            f"credentials-file: {credentials}",
            "ingress:",
        ]
        for host in hostnames:
            lines.extend(
                [
                    f"  - hostname: {host}",
                    f"    service: http://127.0.0.1:{port}",
                    "    originRequest:",
                    f"      httpHostHeader: \"localhost:{port}\"",
                ]
            )
        lines.append("  - service: http_status:404")
        lines.append("")
        path.write_text("\n".join(lines))
        return path

    # ── ngrok ───────────────────────────────────────────────────

    def _start_ngrok(self, domain: DomainConfig, port: int) -> bool:
        info = detect_ngrok()
        if not info["found"]:
            return False
        if not info["has_authtoken"]:
            return False

        cmd = ["ngrok", "http", str(port), "--log=stdout"]
        if domain.hostname and domain.mode != "dev":
            # Paid / reserved domain
            cmd.extend(["--domain", domain.hostname])

        ok = self._spawn(cmd, provider="ngrok")
        if ok:
            # Discover public URL via local API
            url = self._ngrok_public_url()
            if url:
                self._quick_url = url
                # If domain was placeholder, update hostname for public_base_url
                if domain.mode == "dev" and not domain.hostname:
                    domain.hostname = url.removeprefix("https://").removeprefix(
                        "http://"
                    )
        return ok

    def _ngrok_public_url(self, timeout: float = 8.0) -> str | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                    "http://127.0.0.1:4040/api/tunnels", timeout=1
                ) as resp:
                    data = json.loads(resp.read().decode())
                for t in data.get("tunnels", []):
                    pub = t.get("public_url") or ""
                    if pub.startswith("https://"):
                        return pub
                for t in data.get("tunnels", []):
                    pub = t.get("public_url") or ""
                    if pub:
                        return pub
            except Exception:
                pass
            time.sleep(0.4)
        return None

    # ── Process helpers ─────────────────────────────────────────

    def _spawn(
        self,
        cmd: list[str],
        provider: str,
        parse_quick: bool = False,
    ) -> bool:
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except FileNotFoundError:
            self._proc = None
            return False

        self._provider = provider
        self._quick_url = None

        if parse_quick:
            self._stdout_thread = threading.Thread(
                target=self._watch_quick_url,
                args=(self._proc,),
                daemon=True,
            )
            self._stdout_thread.start()
            # Give it a moment to print the URL
            for _ in range(25):
                if self._quick_url or not self.is_running():
                    break
                time.sleep(0.2)
            if not self._quick_url:
                # A live child is not sufficient: quick tunnels are only
                # usable once the provider has published a public URL.
                if self.is_running():
                    self._proc.terminate()
                    try:
                        self._proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self._proc.kill()
                self._proc = None
                self._provider = None
                return False

        return self.is_running()

    def _watch_quick_url(self, proc: subprocess.Popen) -> None:
        """Parse cloudflared stdout for trycloudflare.com URL."""
        pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                m = pattern.search(line)
                if m:
                    with self._lock:
                        self._quick_url = m.group(0)
                    break
        except Exception:
            pass

    def _top_level_value(self, text: str, key: str) -> str | None:
        prefix = f"{key}:"
        for line in text.splitlines():
            if line.startswith(prefix):
                return line[len(prefix) :].strip().strip('"')
        return None
