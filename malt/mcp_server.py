"""MCP HTTP server — one FastMCP instance per project."""

import json
import os
import shlex
import subprocess

from mcp.server.fastmcp import FastMCP

from .security import safe_path, PERMISSION_LEVELS, ALLOWED_CMDS


def create_mcp_server(project: dict, streamable_http_path: str = "/mcp") -> FastMCP:
    """Create a FastMCP server for a project with permission-gated tools.

    `streamable_http_path` is the literal HTTP path this project's server
    responds on once mounted by ServerManager (e.g. "/mcp/{project_id}") —
    see server.py for why this isn't a nested Mount.
    """
    mcp = FastMCP(
        name=f"malt-{project['name']}",
        stateless_http=True,
        streamable_http_path=streamable_http_path,
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
        is_admin = perm == "admin"
        if not is_admin:
            custom_cmds = json.loads(project.get("allowed_commands", "[]") or "[]")
            if custom_cmds:
                cmd_map = {
                    cmd: ALLOWED_CMDS[cmd]
                    for cmd in custom_cmds
                    if cmd in ALLOWED_CMDS
                }
            else:
                cmd_map = dict(ALLOWED_CMDS)

        @mcp.tool()
        def run_command(cmd: str) -> str:
            """Run a command. Admin permission: any command allowed. Others: whitelisted only."""
            if is_admin:
                try:
                    argv = shlex.split(cmd)
                except ValueError as e:
                    return f"error: invalid command syntax: {e}"
                if not argv:
                    return "error: empty command"
            else:
                argv = cmd_map.get(cmd, [])
                if not argv:
                    return f"error: command not whitelisted: {cmd}"
            try:
                result = subprocess.run(
                    argv, cwd=root, capture_output=True, text=True, timeout=60
                )
            except FileNotFoundError:
                return f"error: command not found: {argv[0]}"
            except subprocess.TimeoutExpired:
                return "error: command timed out after 60s"
            except Exception as e:
                return f"error: {e}"
            return result.stdout or result.stderr or "(no output)"

    return mcp
