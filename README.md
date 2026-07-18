# malt

Manage multiple project MCP servers from one dashboard.

A GTK4/libadwaita desktop app that lets you run and manage [Model Context Protocol](https://modelcontextprotocol.io/) servers for multiple projects.

## Features

- **Multi-project management** — create, edit, and delete projects from a sidebar
- **Server controls** — start/stop MCP servers per project
- **Token auth** — each project gets a unique auth token, regenerable anytime
- **Allowed commands** — whitelist specific CLI tools per project (execute/admin permission)
- **Tunnel support** — expose local MCP servers via cloudflared tunnels
- **Status indicators** — green/grey dot in sidebar shows server state
- **Keyboard shortcut** — press `Esc` to deselect and return to empty state

## Requirements

- Python 3.12+
- GTK4 + libadwaita (system packages)
- `libgtk-4-dev`, `libadwaita-1-dev` (Debian/Ubuntu) or `gtk4`, `libadwaita` (Arch)

## Installation

```bash
# Clone
git clone git@github.com:ferdinankurnian/malt.git
cd malt

# Install with uv (recommended)
uv sync

# Or with pip
pip install -e .
```

## Usage

```bash
malt
```

1. Click **+** to add a project (name, root path, permission level)
2. Select a project from the sidebar
3. Click **Start** to launch its MCP server
4. Copy the endpoint URL and use it in your MCP client

### Permissions

| Level    | Tools                                                      |
|----------|------------------------------------------------------------|
| `read`   | list_directory, read_file                                  |
| `write`  | list_directory, read_file, write_file                      |
| `execute`| list_directory, read_file, write_file, run_command         |
| `admin`  | list_directory, read_file, write_file, run_command, manage |

## Project Structure

```
malt/
├── main.py              # App entry point, window layout
├── db.py                # SQLite schema + CRUD
├── models.py            # Project data model
├── settings.py          # App settings (tunnel hostname, default port)
├── security.py          # Allowed commands, permission levels
├── mcp_server.py        # MCP server factory
├── tunnel.py            # Cloudflared tunnel manager
├── ui/
│   └── style.css        # Custom GTK CSS
└── views/
    ├── project_list.py  # Sidebar with project cards
    └── project_detail.py # Detail panel (config, server controls, logs)
```

## License

MIT
