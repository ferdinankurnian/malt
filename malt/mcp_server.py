"""MCP HTTP server — one FastMCP instance per project."""

import json
import os
import subprocess

from mcp.server.fastmcp import FastMCP

from .security import safe_path, PERMISSION_LEVELS, ALLOWED_CMDS


def create_mcp_server(project: dict) -> FastMCP:
    """Create a FastMCP server for a project with permission-gated tools."""
    mcp = FastMCP(
        name=f"malt-{project['name']}",
        stateless_http=True,
    )

    root = project["root_path"]
    perm = project["permission"]
    allowed_tools = PERMISSION_LEVELS.get(perm, [])

    @mcp.tool()
    def list_directory(path: str = ".") -> str:
        """List directory contents."""
        dir_path = safe_path(root, path)
        entries = os.listdir(dir_path)
        return "\n".join(sorted(entries)) if entries else "(empty)"

    if "read_file" in allowed_tools:

        @mcp.tool()
        def read_file(path: str) -> str:
            """Read file contents."""
            file_path = safe_path(root, path)
            with open(file_path) as f:
                return f.read()

    if "write_file" in allowed_tools:

        @mcp.tool()
        def write_file(path: str, content: str) -> str:
            """Write content to file."""
            file_path = safe_path(root, path)
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w") as f:
                f.write(content)
            return "ok"

    if "run_command" in allowed_tools:
        custom_cmds = json.loads(project.get("allowed_commands", "[]") or "[]")
        if custom_cmds:
            cmd_map = {
                cmd: ALLOWED_CMDS[cmd] for cmd in custom_cmds if cmd in ALLOWED_CMDS
            }
        else:
            cmd_map = dict(ALLOWED_CMDS)

        @mcp.tool()
        def run_command(cmd: str) -> str:
            """Run a whitelisted command."""
            argv = cmd_map.get(cmd)
            if not argv:
                raise ValueError(f"command not whitelisted: {cmd}")
            result = subprocess.run(
                argv, cwd=root, capture_output=True, text=True, timeout=60
            )
            return result.stdout or result.stderr or "(no output)"

    return mcp
