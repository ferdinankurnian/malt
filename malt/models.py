"""Data models for projects."""

import json


class Project:
    """A project managed by malt."""

    def __init__(
        self,
        project_id: str = "",
        name: str = "",
        root_path: str = "",
        permission: str = "read",
        allowed_commands: list[str] | None = None,
        token: str = "",
        mcp_port: int = 3100,
        tunnel_enabled: bool = False,
        domain_id: str | None = None,
        icon_mode: str = "auto",
        icon_path: str | None = None,
    ):
        self.id = project_id
        self.name = name
        self.root_path = root_path
        self.permission = permission
        self.allowed_commands: list[str] = allowed_commands or []
        self.token = token
        self.mcp_port = mcp_port
        self.tunnel_enabled = tunnel_enabled
        self.domain_id = domain_id
        self.icon_mode = icon_mode or "auto"
        self.icon_path = icon_path

    @classmethod
    def from_db_row(cls, row: dict) -> "Project":
        cmds = (
            json.loads(row["allowed_commands"]) if row.get("allowed_commands") else []
        )
        return cls(
            project_id=row["id"],
            name=row["name"],
            root_path=row["root_path"],
            permission=row["permission"],
            allowed_commands=cmds,
            token=row["token"],
            mcp_port=row.get("mcp_port", 3100),
            tunnel_enabled=bool(row.get("tunnel_enabled", 0)),
            domain_id=row.get("domain_id"),
            icon_mode=row.get("icon_mode") or "auto",
            icon_path=row.get("icon_path"),
        )
