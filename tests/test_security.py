import json

import pytest

from malt.security import allowed_tools, command_allowed, project_policy, safe_path


def test_safe_path_allows_root_and_child(tmp_path):
    assert safe_path(str(tmp_path), ".") == str(tmp_path)
    assert safe_path(str(tmp_path), "src/file.txt") == str(tmp_path / "src/file.txt")


def test_safe_path_blocks_parent_and_symlink_escape(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    link = tmp_path / "link"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        safe_path(str(tmp_path), "../outside")
    with pytest.raises(ValueError):
        safe_path(str(tmp_path), "link/secret.txt")


def test_command_allowed_scopes_and_malformed_commands():
    assert command_allowed("git status", [{"command": "git status", "scope": "exact"}])
    assert command_allowed("cargo test --lib", [{"command": "cargo test", "scope": "prefix"}])
    assert command_allowed("python -m pytest", [{"command": "python", "scope": "binary"}])
    assert not command_allowed("git status --short", [{"command": "git status", "scope": "exact"}])
    assert not command_allowed("git 'unterminated", [{"command": "git", "scope": "binary"}])


def test_project_policy_normalizes_capabilities():
    project = {
        "file_permissions": json.dumps(["read", "write", "unknown"]),
        "terminal_permission": "allowlist",
    }
    files, terminal = project_policy(project)
    assert files == {"read", "write"}
    assert terminal == "allowlist"
    assert "run_command" in allowed_tools(project)
