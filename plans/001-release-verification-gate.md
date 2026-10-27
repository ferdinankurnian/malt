# Plan 001: Establish a release verification gate

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving on. Touch
> only the files listed in Scope. If a STOP condition occurs, stop and report.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: none
- **Category**: tests / dx
- **Planned at**: commit `a1cb53b`, 2026-10-27

## Why this matters

The repository currently has no tests and no CI, while the current branch
changes database migrations, permission policy, MCP tool exposure, HTTP auth,
and tunnel lifecycle in one large uncommitted feature set. `uv run mypy malt/`
passes, but `uv run ruff check malt/` fails on `malt/views/project_list.py:11`.
Without a repeatable gate, a release can regress authorization or server
startup without detection.

## Current state

- `malt/security.py` contains `safe_path`, permission normalization, and
  command matching; these are pure functions and should be the first unit-test
  targets.
- `malt/db.py` owns SQLite schema creation, migrations, project CRUD, token
  regeneration, and logs.
- `malt/mcp_server.py` conditionally registers tools based on project policy.
- `malt/server.py` owns `_TokenAuthMiddleware` and `ServerManager`.
- There are no `test_*.py` files and no CI workflow.
- Verification commands observed during recon:
  - `uv run mypy malt/` → exit 0.
  - `uv run ruff check malt/` → fails with E402 at
    `malt/views/project_list.py:11`.

Match the repository's existing standard-library style; do not introduce a
test framework beyond the project dependencies unless the executor first
updates `pyproject.toml` and `uv.lock`.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Typecheck | `uv run mypy malt/` | exit 0 |
| Lint | `uv run ruff check malt/` | exit 0 |
| Tests | `uv run pytest` | exit 0 with all tests passing |

## Scope

**In scope**
- `tests/test_security.py` (create)
- `tests/test_db.py` (create)
- `tests/test_server_auth.py` (create)
- `malt/views/project_list.py` only if needed to fix the existing E402 lint
- `pyproject.toml` and `uv.lock` only if adding pytest is necessary

**Out of scope**
- UI redesigns, tunnel provider behavior, and unrelated refactors.
- Real network calls, real Cloudflare credentials, or the user's home DB.

## Steps

### Step 1: Add security regression tests

Cover `safe_path` for a normal child, `..` escape, symlink escape, and the
root itself. Cover `command_allowed` for exact, prefix, binary, malformed
syntax, and non-matching rules. Cover `project_policy`/`allowed_tools` for
read, write, allowlist, full terminal, and malformed JSON inputs.

**Verify**: `uv run pytest tests/test_security.py` → all tests pass.

### Step 2: Add database tests with an isolated DB path

Use pytest's temporary-path fixture and monkeypatch `malt.db.DB_PATH`.
Test `init_db` on a fresh database, legacy-column migration, create/list/get,
update field filtering, token regeneration, cascade deletion of logs, and
log retrieval ordering/limits. Never use `~/.local/share/malt/malt.db`.

**Verify**: `uv run pytest tests/test_db.py` → all tests pass and the real home
database is unchanged.

### Step 3: Add HTTP auth tests

Exercise `_TokenAuthMiddleware` with a minimal ASGI downstream app. Assert
unknown project → 404, missing/wrong token → 401, valid bearer/query/path
token reaches the downstream app, and OPTIONS returns 204. Include a test that
the canonical downstream path is restored for `/mcp/{project_id}/{token}`.

**Verify**: `uv run pytest tests/test_server_auth.py` → all tests pass without
starting uvicorn or opening a socket.

### Step 4: Make the lint gate pass

Fix only the existing E402 at `malt/views/project_list.py:11` while preserving
the required `gi.require_version()` ordering. Do not mask the error with a
new broad noqa unless the import ordering is genuinely required by GTK.

**Verify**: `uv run ruff check malt/` → exit 0; `uv run mypy malt/` → exit 0.

### Step 5: Add one-command CI

Add a GitHub Actions workflow that installs system GTK/libadwaita packages,
runs `uv sync`, then runs ruff, mypy, and pytest on Python 3.12. Keep the
workflow read-only with respect to the repository and do not add release
publishing yet.

**Verify**: validate YAML syntax with the repository's available tooling and
run the same three commands locally; all exit 0.

## Test plan

The tests above are the release gate. They must test policy outcomes and HTTP
status/path behavior, not merely that functions return without exceptions.

## Done criteria

- [ ] `uv run pytest` exits 0 with security, DB, and auth tests present.
- [ ] `uv run ruff check malt/` exits 0.
- [ ] `uv run mypy malt/` exits 0.
- [ ] CI runs the same three gates on Python 3.12.
- [ ] No test reads or writes the real home database or external services.

## STOP conditions

- GTK imports cannot be installed in the verification environment.
- A test requires changing production behavior outside the listed scope.
- Existing code differs materially from the current-state description.

## Maintenance notes

Any future permission, token, path, database migration, or middleware change
must extend these tests before release. Keep network/tunnel tests mocked and
separate from the deterministic unit suite.
