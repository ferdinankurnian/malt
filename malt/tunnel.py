"""cloudflared process manager."""

import subprocess


class TunnelManager:
    """Manages cloudflared tunnel processes per project."""

    def __init__(self):
        self._processes: dict[str, subprocess.Popen] = {}

    def start(self, project_id: str, hostname: str, port: int = 3100) -> bool:
        if self.is_running(project_id):
            return False
        try:
            proc = subprocess.Popen(
                [
                    "cloudflared",
                    "tunnel",
                    "--url",
                    f"http://localhost:{port}",
                    "--hostname",
                    hostname,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            self._processes[project_id] = proc
            return True
        except FileNotFoundError:
            return False

    def stop(self, project_id: str) -> bool:
        proc = self._processes.pop(project_id, None)
        if proc is None:
            return False
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        return True

    def is_running(self, project_id: str) -> bool:
        proc = self._processes.get(project_id)
        return proc is not None and proc.poll() is None

    def stop_all(self) -> None:
        for pid in list(self._processes):
            self.stop(pid)
