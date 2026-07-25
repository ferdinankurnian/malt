"""cloudflared process manager.

One tunnel for the whole app, not one per project. If the user's cloudflared
config already contains the requested hostname, reuse that named tunnel with a
small generated config that points the hostname at Malt's current local port.
"""

from pathlib import Path
import subprocess


class TunnelManager:
    """Manages the single cloudflared tunnel process for the app's hostname."""

    def __init__(self):
        self._proc: subprocess.Popen | None = None

    def start(self, hostname: str, port: int = 3100) -> bool:
        if self.is_running():
            return True
        try:
            self._proc = subprocess.Popen(
                self._command(hostname, port),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            return True
        except FileNotFoundError:
            return False

    def stop(self) -> bool:
        if self._proc is None:
            return False
        self._proc.terminate()
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._proc = None
        return True

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _command(self, hostname: str, port: int) -> list[str]:
        named = self._named_tunnel_config(hostname, port)
        if named:
            return ["cloudflared", "tunnel", "--config", str(named), "run"]
        return [
            "cloudflared",
            "tunnel",
            "--url",
            f"http://localhost:{port}",
            "--hostname",
            hostname,
            "--http-host-header",
            f"localhost:{port}",
        ]

    def _named_tunnel_config(self, hostname: str, port: int) -> Path | None:
        source = Path.home() / ".cloudflared" / "config.yml"
        if not source.exists():
            return None

        text = source.read_text()
        tunnel = self._top_level_value(text, "tunnel")
        credentials = self._top_level_value(text, "credentials-file")
        if not tunnel or not credentials or f"hostname: {hostname}" not in text:
            return None

        path = Path.home() / ".cache" / "malt" / f"cloudflared-{hostname}.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(
                [
                    f"tunnel: {tunnel}",
                    f"credentials-file: {credentials}",
                    "ingress:",
                    f"  - hostname: {hostname}",
                    f"    service: http://127.0.0.1:{port}",
                    "    originRequest:",
                    f"      httpHostHeader: \"localhost:{port}\"",
                    "  - service: http_status:404",
                    "",
                ]
            )
        )
        return path

    def _top_level_value(self, text: str, key: str) -> str | None:
        prefix = f"{key}:"
        for line in text.splitlines():
            if line.startswith(prefix):
                return line[len(prefix) :].strip().strip('"')
        return None
