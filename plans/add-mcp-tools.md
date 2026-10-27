# Plan: Add missing MCP tools

## Goal
Make malt MCP servers actually usable for real editing work, especially large files.

## Tools to add

### 1. `edit_file` (highest priority)
- Exact string search-replace
- Params: `path`, `old_string`, `new_string`, `replace_all` (optional bool, default false)
- Fail if `old_string` not found (or found >1 time when replace_all=false)
- Permission: write / execute / admin

### 2. Partial read on existing `read_file`
- Add optional `start_line` and `end_line` (1-indexed)
- If both provided → return only that range
- Keep full-file behavior when omitted
- Permission: already gated by read_file

### 3. `delete_file`
- Delete a file (or empty dir? start with file only)
- Param: `path`
- Permission: write / execute / admin

### 4. `move_file`
- Rename / move within project root
- Params: `src`, `dest`
- Permission: write / execute / admin

### 5. `search`
- Grep-like search across files
- Params: `pattern` (regex), `path` (default "."), `max_matches` (default 50), `context_lines` (default 2)
- Return matching lines with line numbers + context
- Permission: read / write / execute / admin

### 6. `file_info`
- Stat a path
- Return: size, is_dir, is_file, mtime, mode
- Param: `path`
- Permission: read / write / execute / admin

## Permission matrix (updated)

| Tool            | read | write | execute | admin |
|-----------------|------|-------|---------|-------|
| list_directory  | ✅   | ✅    | ✅      | ✅    |
| read_file       | ✅   | ✅    | ✅      | ✅    |
| file_info       | ✅   | ✅    | ✅      | ✅    |
| search          | ✅   | ✅    | ✅      | ✅    |
| write_file      | ❌   | ✅    | ✅      | ✅    |
| edit_file       | ❌   | ✅    | ✅      | ✅    |
| delete_file     | ❌   | ✅    | ✅      | ✅    |
| move_file       | ❌   | ✅    | ✅      | ✅    |
| run_command     | ❌   | ❌    | ✅      | ✅    |

## Files to change
- `malt/security.py` — update `PERMISSION_LEVELS`
- `malt/mcp_server.py` — implement all new tools + partial read
- `README.md` — update permissions table
- `AGENTS.md` — mention new tools

## Out of scope (this round)
- `apply_patch` (unified diff) — more complex, later
- recursive delete / force flags
- binary file handling beyond what exists

## Implementation notes
- All paths go through `safe_path`
- `edit_file` should be exact match (not regex) for safety
- `search` use `re` module, case-sensitive by default
- Keep error messages short and clear
