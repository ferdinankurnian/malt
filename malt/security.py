"""Path sandboxing + command whitelisting."""

import os
import json
import shlex

FILE_CAPABILITIES = ("read", "write", "create", "delete")
TERMINAL_PERMISSIONS = ("off", "allowlist", "full")

# Kept for backwards compatibility with old projects and the existing details
# popover. New code should use the capability helpers below.
PERMISSION_LEVELS = {
    "read": [
        "list_directory",
        "read_file",
        "file_info",
        "search",
    ],
    "write": [
        "list_directory",
        "read_file",
        "file_info",
        "search",
        "write_file",
        "edit_file",
        "delete_file",
        "move_file",
    ],
    "execute": [
        "list_directory",
        "read_file",
        "file_info",
        "search",
        "write_file",
        "edit_file",
        "delete_file",
        "move_file",
        "run_command",
    ],
    "admin": [
        "list_directory",
        "read_file",
        "file_info",
        "search",
        "write_file",
        "edit_file",
        "delete_file",
        "move_file",
        "run_command",
    ],
}

FILE_CAPABILITY_TOOLS = {
    "read": ["list_directory", "read_file", "file_info", "search"],
    "write": ["write_file", "edit_file"],
    "create": ["write_file", "create_directory"],
    "delete": ["delete_file", "move_file"],
}


def _legacy_policy(permission: str) -> tuple[set[str], str]:
    policies = {
        "read": ({"read"}, "off"),
        "write": ({"read", "write", "create"}, "off"),
        "execute": ({"read", "write", "create"}, "allowlist"),
        "admin": ({"read", "write", "create", "delete"}, "full"),
    }
    return policies.get(permission, policies["read"])


def project_policy(project: dict) -> tuple[set[str], str]:
    """Return normalized file capabilities and terminal permission."""
    raw_files = project.get("file_permissions")
    raw_terminal = project.get("terminal_permission")
    use_legacy = raw_files is None and raw_terminal is None
    try:
        if isinstance(raw_files, str):
            file_permissions = set(json.loads(raw_files or "[]"))
        else:
            file_permissions = set(raw_files or [])
    except (TypeError, ValueError, json.JSONDecodeError):
        file_permissions = set()

    terminal = raw_terminal if raw_terminal in TERMINAL_PERMISSIONS else None
    if use_legacy or terminal is None:
        legacy_files, legacy_terminal = _legacy_policy(project.get("permission", "read"))
        if use_legacy:
            file_permissions = legacy_files
        if terminal is None:
            terminal = legacy_terminal

    return file_permissions & set(FILE_CAPABILITIES), terminal


def allowed_tools(project: dict) -> set[str]:
    file_permissions, terminal = project_policy(project)
    tools = {
        tool
        for capability in file_permissions
        for tool in FILE_CAPABILITY_TOOLS[capability]
    }
    if terminal != "off":
        tools.add("run_command")
    return tools


def command_rules(project: dict) -> list[dict]:
    """Normalize legacy command strings and structured command rules."""
    raw = project.get("allowed_commands")
    if raw is None:
        values = list(ALLOWED_CMDS)
    else:
        values = []
        try:
            values = json.loads(raw) if isinstance(raw, str) else (raw or [])
        except (TypeError, ValueError, json.JSONDecodeError):
            values = []

    rules = []
    for value in values:
        if isinstance(value, str) and value.strip():
            rules.append({"command": value.strip(), "scope": "exact"})
        elif isinstance(value, dict) and value.get("command"):
            scope = value.get("scope", "exact")
            if scope in ("exact", "prefix", "binary"):
                rules.append({"command": value["command"].strip(), "scope": scope})
    return rules


def command_allowed(cmd: str, rules: list[dict]) -> bool:
    """Match a command against exact, prefix, or binary rules without a shell."""
    try:
        argv = shlex.split(cmd)
    except ValueError:
        return False
    if not argv:
        return False

    for rule in rules:
        try:
            rule_argv = shlex.split(rule["command"])
        except (KeyError, TypeError, ValueError):
            continue
        if not rule_argv:
            continue
        scope = rule.get("scope", "exact")
        if scope == "exact" and argv == rule_argv:
            return True
        if scope == "prefix" and argv[: len(rule_argv)] == rule_argv:
            return True
        if scope == "binary" and argv[0] == rule_argv[0] and len(rule_argv) == 1:
            return True
    return False


def policy_label(project: dict) -> str:
    files, terminal = project_policy(project)
    if files == {"read"} and terminal == "off":
        return "read only"
    if files == {"read", "write"} and terminal == "off":
        return "read & write"
    if (
        files == {"read", "write"}
        and terminal == "allowlist"
    ):
        return "developer"
    if files == set(FILE_CAPABILITIES) and terminal == "full":
        return "god mode"
    return "custom"


def legacy_permission(file_permissions: set[str], terminal: str) -> str:
    """Keep the old column meaningful for older clients and migrations."""
    if file_permissions == {"read"} and terminal == "off":
        return "read"
    if file_permissions == {"read", "write"} and terminal == "off":
        return "write"
    if file_permissions == {"read", "write"} and terminal == "allowlist":
        return "execute"
    if file_permissions == set(FILE_CAPABILITIES) and terminal == "full":
        return "admin"
    return "read"

ALLOWED_CMDS = {
    "cargo check": ["cargo", "check"],
    "cargo test": ["cargo", "test"],
    "cargo clippy": ["cargo", "clippy"],
    "cargo fmt --check": ["cargo", "fmt", "--check"],
    "git status": ["git", "status"],
    "git diff": ["git", "diff"],
    "git log": ["git", "log", "--oneline", "-20"],
}


def safe_path(root_path: str, user_path: str) -> str:
    """Resolve user_path relative to root_path, blocking escapes."""
    root_real = os.path.realpath(root_path)
    resolved = os.path.realpath(os.path.join(root_real, user_path))
    # must be root itself, or root + separator + something — plain startswith()
    # would also let "/home/x/proj-evil" match root "/home/x/proj"
    if resolved != root_real and not resolved.startswith(root_real + os.sep):
        raise ValueError("path escape blocked")
    return resolved
