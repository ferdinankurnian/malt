# Plan 003: Harden MCP server and tunnel lifecycle behavior

> **Executor instructions**: Follow this plan only after Plan 001 is DONE.
> Touch only the files listed in Scope. Preserve the local single-user
> architecture; stop and report if the change requires a public multi-tenant
> auth model.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: HIGH
- **Depends on**: `plans/001-release-verification-gate.md`
- **Category**: correctness / security
- **Planned at**: commit `a1cb53b`, 2026-10-27

## Why this matters

The app publishes project folders over MCP and can expose them through a public
tunnel. That makes lifecycle and error visibility release-critical. The code
rebuilds the whole Starlette app whenever a project starts/stops, which drops
in-flight sessions by design, while tunnel setup has several broad
best-effort catches that can report success-like state after a failed ingress
or provider operation. Public users need an unambiguous “running and reachable”
state, and changing permissions must never leave an old server policy active.

## Current state

- `malt/server.py:152-180` stops and recreates uvicorn whenever the project set
  changes; the module comment explicitly says in-flight sessions are dropped.
- `malt/server.py:57-69` allows preflight before token validation and returns
  wildcard CORS headers. This may be required by MCP clients, but must be
  covered by the auth threat model and tests.
- `malt/views/project_detail.py:1089-1113` restarts a running project after
  edits, but uses the default port rather than the project's stored
  `mcp_port` and only appends a UI error after a restart failure.
- `malt/tunnel.py:193-206` and `:226-239` swallow provider/setup exceptions and
  fall back; `_spawn` only checks process liveness, not endpoint readiness.
- `malt/mcp_server.py:306-336` runs full-terminal commands when policy allows
  them, but does not add a resource limit or structured audit event beyond the
  database log.

## Scope

**In scope**
- `malt/server.py`
- `malt/tunnel.py`
- `malt/views/project_detail.py`
- `malt/mcp_server.py`
- tests added under `tests/` for the behavior
- `README.md` for user-facing lifecycle/security caveats

**Out of scope**
- Multi-tenant hosting, remote identity providers, or a new auth protocol.
- Removing the intentional admin/full-terminal feature.
- Replacing uvicorn/FastMCP.

## Steps

### Step 1: Characterize lifecycle behavior

Using the Plan 001 test style, test project start/stop/restart with mocked
uvicorn and tunnel processes. Assert that failed process startup is surfaced,
stopping joins the old thread, and a project edit restarts with the project's
effective configured port and current policy/token.

**Verify**: targeted lifecycle tests pass and no real subprocess or socket is
created.

### Step 2: Make tunnel readiness explicit

Separate “child process started” from “public URL/ingress is ready”. Preserve
provider fallback, but return a failure with a user-visible reason when all
providers fail or a named ingress cannot be confirmed. Do not swallow an
exception without recording the provider, operation, and reason in the
existing UI/log path.

**Verify**: mocked tests cover missing binary, child exits immediately, quick
tunnel URL timeout, and named tunnel ingress failure; each yields a failed
state and actionable log.

### Step 3: Verify policy changes cannot leave stale servers

Ensure edits to root path, file permissions, terminal permission, command
rules, token, or port cannot leave the previous MCP instance serving. If
restart fails, mark the project stopped and do not claim the endpoint is
active.

**Verify**: tests change each policy class while running and assert the old
server is not reachable/registered after a failed restart.

### Step 4: Document exposure boundaries

Update README to state that localhost is the default bind address, a tunnel
makes the endpoint internet-reachable, the token is a bearer capability, and
`admin`/full-terminal grants arbitrary commands under the project root.
Document the intentional session-drop behavior during project-set changes.

**Verify**: README matches actual bind/auth/lifecycle behavior and does not
promise zero-downtime restarts.

## Done criteria

- [ ] Lifecycle tests pass without real network/process dependencies.
- [ ] Failed tunnel/server startup is visible as failure, not just a dead
  process or silent fallback.
- [ ] Running edits restart with current policy and configured port.
- [ ] Auth/CORS behavior is covered by tests and documented as intentional.
- [ ] `uv run pytest`, `uv run ruff check malt/`, and `uv run mypy malt/` pass.

## STOP conditions

- Correct behavior requires changing the documented local single-user model.
- Provider APIs cannot expose a reliable readiness signal without a larger
  redesign; report the gap instead of claiming readiness.
- A test would need real Cloudflare/ngrok credentials.

## Maintenance notes

Any new tunnel provider, auth transport, permission capability, or server
restart path must add a deterministic lifecycle test. Reviewers should inspect
whether every success indicator corresponds to a live local server and a
confirmed public route.
