"""Global settings storage."""

import json
from pathlib import Path

SETTINGS_PATH = Path.home() / ".config" / "malt" / "settings.json"

DEFAULTS = {
    "tunnel_hostname": "mcp.iydheko.site",
    "default_mcp_port": 3100,
}


def _path() -> Path:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    return SETTINGS_PATH


def load() -> dict:
    p = _path()
    if p.exists():
        with open(p) as f:
            data = json.load(f)
        merged = dict(DEFAULTS)
        merged.update(data)
        return merged
    return dict(DEFAULTS)


def save(settings: dict) -> None:
    p = _path()
    with open(p, "w") as f:
        json.dump(settings, f, indent=2)


def get(key: str):
    return load().get(key, DEFAULTS.get(key))


def set(key: str, value) -> None:
    settings = load()
    settings[key] = value
    save(settings)
