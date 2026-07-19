# AGENTS.md

## What is this

GTK4/libadwaita desktop app for managing MCP (Model Context Protocol) servers across multiple projects. Python 3.12+.

## Setup

```bash
uv sync
```

**System deps required** (not in pyproject.toml): GTK4 + libadwaita development packages. On Arch: `gtk4 libadwaita`. On Debian/Ubuntu: `libgtk-4-dev libadwaita-1-dev`.

## Run

```bash
malt
```

Or: `python -m malt.main`

## Lint/typecheck

```bash
uv run ruff check malt/
uv run mypy malt/
```

No tests exist. No CI.

**Known pre-existing lint issues** (as of Jul 2026): `Adw` undefined in `project_detail.py:412`, unused `cairo` import + E402 in `project_list.py`. mypy passes clean.

## Architecture

- `malt/main.py` — app entry, window layout, wires views together
- `malt/db.py` — SQLite schema + CRUD. DB at `~/.local/share/malt/malt.db`
- `malt/mcp_server.py` — creates FastMCP server per project, permission-gated tools
- `malt/security.py` — path sandboxing (`safe_path`) + command whitelisting
- `malt/settings.py` — global config at `~/.config/malt/settings.json`
- `malt/tunnel.py` — cloudflared process manager
- `malt/models.py` — `Project` data model
- `malt/views/project_list.py` — sidebar with project cards
- `malt/views/project_detail.py` — right panel: config, server controls, logs
- `malt/ui/style.css` — custom GTK CSS

## Key patterns

- Each project gets a unique auth token, regenerable from the UI
- MCP servers run in-process via `FastMCP` from the `mcp` library
- Path sandboxing: `safe_path()` blocks directory escapes via `os.path.realpath`
- Command execution whitelisted to specific binaries defined in `security.py`
- `tunnel.py` spawns `cloudflared` subprocesses, tracked by project ID
- Database uses WAL mode, foreign keys enabled

## Gotchas

- No ruff config file — uses defaults. Check `pyproject.toml` for any overrides (currently none beyond mypy `ignore_missing_imports = true`)
- `.python-version` says 3.14, but `requires-python = ">=3.12"` — both work
- GTK imports use `gi.require_version()` before import — don't reorder
