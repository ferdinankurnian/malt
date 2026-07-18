"""Path sandboxing + command whitelisting."""

import os

PERMISSION_LEVELS = {
    "read": ["list_directory", "read_file"],
    "write": ["list_directory", "read_file", "write_file"],
    "execute": ["list_directory", "read_file", "write_file", "run_command"],
    "admin": ["list_directory", "read_file", "write_file", "run_command"],
}

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
    resolved = os.path.realpath(os.path.join(root_path, user_path))
    if not resolved.startswith(root_path):
        raise ValueError("path escape blocked")
    return resolved
