"""MCP HTTP server — one FastMCP instance per project."""

import functools
import json
import os
import re
import shlex
import subprocess
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

from . import db
from .security import safe_path, PERMISSION_LEVELS, ALLOWED_CMDS

_MAX_LOG = 2000


def _truncate(s: str, n: int = _MAX_LOG) -> str:
    if len(s) <= n:
        return s
    return s[:n] + f"… ({len(s) - n} more chars)"


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
    project_id = project["id"]
    allowed_tools = PERMISSION_LEVELS.get(perm, [])

    def log_call(tool_name: str, arguments: dict, response: str, success: bool) -> None:
        # redact huge bodies (write_file content, etc.)
        args_for_log = {}
        for k, v in arguments.items():
            if isinstance(v, str) and len(v) > 200:
                args_for_log[k] = v[:200] + f"… ({len(v) - 200} more)"
            else:
                args_for_log[k] = v
        try:
            db.add_log(
                project_id,
                tool_name,
                json.dumps(args_for_log, ensure_ascii=False),
                _truncate(response),
                success,
            )
        except Exception:
            pass  # never break the tool because of logging

    def logged(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            name = fn.__name__
            # merge positional into kwargs for logging when possible
            try:
                result = fn(*args, **kwargs)
                ok = not (isinstance(result, str) and result.startswith("error:"))
                log_call(name, kwargs, str(result), ok)
                return result
            except Exception as e:
                log_call(name, kwargs, str(e), False)
                raise

        return wrapper

    @mcp.tool()
    @logged
    def list_directory(path: str = ".") -> str:
        """List directory contents."""
        dir_path = safe_path(root, path)
        entries = os.listdir(dir_path)
        return "\n".join(sorted(entries)) if entries else "(empty)"

    if "read_file" in allowed_tools:

        @mcp.tool()
        @logged
        def read_file(
            path: str, start_line: int | None = None, end_line: int | None = None
        ) -> str:
            """Read file contents. Optionally limit to a line range (1-indexed, inclusive)."""
            file_path = safe_path(root, path)
            with open(file_path) as f:
                if start_line is None and end_line is None:
                    return f.read()
                lines = f.readlines()
            total = len(lines)
            start = (start_line or 1) - 1
            end = end_line if end_line is not None else total
            if start < 0:
                start = 0
            if end > total:
                end = total
            if start >= total:
                return f"(start_line {start_line} beyond end of file, {total} lines)"
            selected = lines[start:end]
            numbered = [f"{i + start + 1}|{line}" for i, line in enumerate(selected)]
            return "".join(numbered)

    if "file_info" in allowed_tools:

        @mcp.tool()
        @logged
        def file_info(path: str) -> str:
            """Get file/directory metadata (size, type, mtime, mode)."""
            target = safe_path(root, path)
            st = os.stat(target)
            mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
            kind = "directory" if os.path.isdir(target) else "file"
            return (
                f"path: {path}\n"
                f"type: {kind}\n"
                f"size: {st.st_size}\n"
                f"mtime: {mtime}\n"
                f"mode: {oct(st.st_mode)[-3:]}"
            )

    if "search" in allowed_tools:

        @mcp.tool()
        @logged
        def search(
            pattern: str,
            path: str = ".",
            max_matches: int = 50,
            context_lines: int = 2,
        ) -> str:
            """Search for a regex pattern in files under path. Returns matching lines with context."""
            base = safe_path(root, path)
            try:
                regex = re.compile(pattern)
            except re.error as e:
                return f"error: invalid regex: {e}"

            matches: list[str] = []
            count = 0

            def scan_file(fpath: str, rel: str) -> None:
                nonlocal count
                try:
                    with open(fpath, errors="ignore") as f:
                        lines = f.readlines()
                except (OSError, UnicodeError):
                    return
                for i, line in enumerate(lines):
                    if count >= max_matches:
                        return
                    if regex.search(line):
                        start = max(0, i - context_lines)
                        end = min(len(lines), i + context_lines + 1)
                        block = []
                        for j in range(start, end):
                            prefix = ">" if j == i else " "
                            block.append(f"{prefix} {j + 1}|{lines[j].rstrip()}")
                        matches.append(f"{rel}:\n" + "\n".join(block))
                        count += 1

            if os.path.isfile(base):
                scan_file(base, path)
            else:
                for dirpath, _, filenames in os.walk(base):
                    for name in filenames:
                        if count >= max_matches:
                            break
                        fpath = os.path.join(dirpath, name)
                        rel = os.path.relpath(fpath, root)
                        scan_file(fpath, rel)
                    if count >= max_matches:
                        break

            if not matches:
                return "(no matches)"
            header = f"{count} match(es)" + (" (truncated)" if count >= max_matches else "")
            return header + "\n\n" + "\n\n".join(matches)

    if "write_file" in allowed_tools:

        @mcp.tool()
        @logged
        def write_file(path: str, content: str) -> str:
            """Write content to file (full overwrite)."""
            file_path = safe_path(root, path)
            parent = os.path.dirname(file_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(file_path, "w") as f:
                f.write(content)
            return "ok"

    if "edit_file" in allowed_tools:

        @mcp.tool()
        @logged
        def edit_file(
            path: str,
            old_string: str,
            new_string: str,
            replace_all: bool = False,
        ) -> str:
            """Replace exact string in a file. Fails if old_string not found (or found multiple times when replace_all=False)."""
            file_path = safe_path(root, path)
            with open(file_path) as f:
                content = f.read()

            count = content.count(old_string)
            if count == 0:
                return "error: old_string not found"
            if count > 1 and not replace_all:
                return f"error: old_string found {count} times (set replace_all=true to replace all)"

            if replace_all:
                new_content = content.replace(old_string, new_string)
            else:
                new_content = content.replace(old_string, new_string, 1)

            with open(file_path, "w") as f:
                f.write(new_content)
            return f"ok ({count} replacement(s))"

    if "delete_file" in allowed_tools:

        @mcp.tool()
        @logged
        def delete_file(path: str) -> str:
            """Delete a file. Refuses directories."""
            target = safe_path(root, path)
            if os.path.isdir(target):
                return "error: path is a directory (delete_file only works on files)"
            if not os.path.exists(target):
                return "error: path does not exist"
            os.remove(target)
            return "ok"

    if "move_file" in allowed_tools:

        @mcp.tool()
        @logged
        def move_file(src: str, dest: str) -> str:
            """Move or rename a file/directory within the project root."""
            src_path = safe_path(root, src)
            dest_path = safe_path(root, dest)
            if not os.path.exists(src_path):
                return "error: source does not exist"
            parent = os.path.dirname(dest_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            os.rename(src_path, dest_path)
            return "ok"

    if "run_command" in allowed_tools:
        is_admin = perm == "admin"
        custom_cmds: list[str] = []
        if not is_admin:
            custom_cmds = json.loads(project.get("allowed_commands", "[]") or "[]")

        @mcp.tool()
        @logged
        def run_command(cmd: str) -> str:
            """Run a command. Admin: any command. Execute: only exact-matched allowed commands."""
            cmd = cmd.strip()
            if not cmd:
                return "error: empty command"

            if is_admin:
                try:
                    argv = shlex.split(cmd)
                except ValueError as e:
                    return f"error: invalid command syntax: {e}"
            elif custom_cmds:
                if cmd not in custom_cmds:
                    return f"error: command not whitelisted: {cmd}"
                try:
                    argv = shlex.split(cmd)
                except ValueError as e:
                    return f"error: invalid command syntax: {e}"
            else:
                argv = ALLOWED_CMDS.get(cmd, [])
                if not argv:
                    return f"error: command not whitelisted: {cmd}"

            if not argv:
                return "error: empty command"

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
