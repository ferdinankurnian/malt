# Plan 005: Cache provider state and simplify domain setup

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving on. Touch
> only the files listed in Scope. Preserve GTK4/libadwaita conventions and
> keep all `cf` subprocesses off the GTK main loop. If the installed `cf`
> command exposes a profile syntax different from the one stated below, stop
> and report the observed help output; do not guess.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: `plans/001-release-verification-gate.md`,
  `plans/004-settings-and-activity-ui-polish.md`
- **Category**: perf / ui / integration
- **Planned at**: commit `a1cb53b`, 2026-10-27

## Why this matters

The Domains page currently re-runs provider discovery each time Settings is
opened. Although the current code starts discovery in a background thread,
each dialog owns its own state, so closing and reopening immediately shows
incorrect initial “cf CLI not installed”/“login needed” conclusions while the
new lookup is still running. The app should load provider state once at app
startup, cache the result in memory with an explicit loading/ready/error state,
and let every Settings dialog render the cached state immediately.

The current header also contains an unnecessary Cloudflare account banner.
The Add Domain dialog is visually dense and exposes tiny helper text and a
free-form subdomain field that makes users likely to enter a full hostname.
The desired flow is a clean wizard: choose a zone from a list, continue, then
enter only the subdomain with the selected zone shown as a fixed suffix.
Cloudflare CLI commands must also honor the user's configured profile, such as
the default profile or `--profile=avaid`.

## Current state

- `malt/main.py:414` builds a new Settings dialog on every open.
- `malt/main.py:487-489` creates `cf_cli_info` and `_cf_state` as dialog-local
  dictionaries.
- `malt/main.py:736-750` renders a provider banner; this includes the
  “cf CLI not installed” and account/tunnel text that should no longer occupy
  the Domains header.
- `malt/main.py:1246` loads zones during Add Domain, and
  `malt/main.py:1430-1470` loads provider state after the dialog is built.
- `malt/cfcli.py:54-147` builds `cf` commands without a profile argument.
- The installed local CLI help confirms the global option is
  `--profile <string>` ("Use a specific auth profile"), available on `cf` and
  `cf auth`; the equivalent argv token for the requested profile is
  `--profile=avaid` (or the two-token form `--profile`, `avaid`).
- `malt/settings.py:12-23` has no persisted `cf_profile` setting.
- The current Add Domain UI combines provider, account status, zone combo,
  subdomain entry, and explanatory DNS-record text in one dialog.

## Scope

**In scope**
- `malt/main.py` — app-level provider state/cache, Settings rendering, and
  Add Domain wizard
- `malt/cfcli.py` — profile-aware command construction and cached calls
- `malt/settings.py` — persist/migrate `cf_profile` and any selected profile
- `tests/` — pure tests for profile command construction, cache state, and
  hostname composition/validation
- `README.md` — document profile selection and the zone/subdomain flow

**Out of scope**
- Changing Cloudflare API semantics or adding automatic CLI installation.
- Removing ngrok support.
- Displaying the Cloudflare account email in the Domains header.
- Showing the Cloudflare account email in plaintext by default anywhere in the
  UI.
- Allowing a full hostname in the subdomain field.

## Steps

### Step 1: Add explicit provider-state cache

Create an app-owned provider state object with at least:
`status` (`loading`, `ready`, `unavailable`, `error`), detected `cf` and
ngrok metadata, auth result, tunnel result, zones, and an error message.
Start one background refresh during `MaltApp.do_activate` after the initial
window is constructed. Use a generation counter or equivalent so stale
results cannot overwrite a newer refresh.

Settings must consume this shared state immediately. On first startup it
renders “Loading…” rather than “cf CLI not installed” or “login needed”.
When the worker completes, update all open Settings instances on the GTK
thread. Closing and reopening Settings must not start a duplicate provider
lookup unless the cache is explicitly expired or the user presses Refresh.
Keep a manual refresh action that transitions back to `Loading…`.

**Verify**: tests prove one startup refresh is shared by two dialog opens,
initial state is `loading`, and a slow worker cannot update a closed dialog or
replace a newer generation.

### Step 2: Remove the Domains header account banner

Delete the account/tunnel/auth status banner from the top of the Domains page.
Keep provider state visible only where it is actionable: the Add Domain wizard,
domain row/detail statuses, and explicit Refresh/Sync actions. Do not display
account email in the page header. If provider/account context is needed inside
an actionable control, render the email masked by default (for example,
`a••••@example.com`) with a clearly labeled reveal button. Revealing is
explicit, local to that view, and must not persist to settings, logs, or
activity history. Do not put the raw email into a tooltip, accessibility label,
log message, or status string while it is masked.

For domain rows and Add Domain, map states explicitly:
`loading` → “Loading…”, `unavailable` → “cf CLI not installed”, auth failure →
“Login required”, ready/authenticated → usable zones/tunnel state. Never map
unknown/loading to unavailable.

**Verify**: UI/source test or focused helper test asserts all four mappings and
that the header contains no account banner. Add a pure masking test covering
short/long local parts and malformed/empty values; assert the raw email is not
present in the default rendered text and appears only after reveal.

### Step 3: Make `cf` profile support first-class

Add a persisted `cf_profile` setting with a clear default that means “use the
CLI default profile”. Build all relevant `cf` argv through one helper that
adds the selected profile in the exact syntax supported by the installed CLI:
the local `cf --help` and `cf auth --help` both expose the global
`--profile <string>` option, and `--profile=avaid` is a valid single argv
token for the requested profile. Apply it consistently to
version/auth/zones/tunnels/DNS/config and tunnel-run/quick-start commands.
Do not append a profile to unrelated `cloudflared` commands.

Expose profile selection in the appropriate Settings/provider controls. At
minimum, support the default profile and an explicitly entered profile name;
do not silently use a different profile after a failed lookup. If profile
discovery is supported by `cf`, use it only after confirming the installed
CLI's actual command contract.

**Verify**: unit tests assert default argv has no profile flag, explicit
`avaid` argv contains `--profile=avaid` exactly once, and every `cf` wrapper
uses the helper. Do not execute authenticated profile commands in tests;
mock subprocess argv instead.

### Step 4: Replace Add Domain with a two-step wizard

Implement a clean two-step flow:

1. **Choose zone** — show zones as a readable selectable list, with loading,
   empty, error, and retry states. Include a clear Next action disabled until a
   zone is selected.
2. **Choose subdomain** — show an input group with a single editable
   subdomain field on the left and a non-editable suffix label on the right,
   for example `[ mcp                 ][ .iydheko.dev ]`. Explain in one
   concise sentence that only the prefix is entered. Compose and validate the
   final hostname as `<subdomain>.<zone>`; reject whitespace, URL schemes,
   slashes, and a second dot/full hostname in the editable field.

Keep Cancel/back navigation obvious. Do not show raw DNS-record inventories or
tiny explanatory paragraphs in the main flow. Any advanced DNS detail belongs
behind an optional secondary link or the existing domain status detail.

**Verify**: tests cover zone selection gating, valid `mcp` composition,
empty/invalid/full-hostname rejection, and back/next state transitions.
Manual smoke test confirms the list and suffix group are readable at the
supported dialog width.

### Step 5: Validate cache and profile behavior end-to-end

Run the full test suite and checks. Manually open Settings twice in quick
succession, confirm no second provider subprocess starts, confirm the first
state says “Loading…”, and confirm the eventual state reflects the configured
profile. Test both default profile and `avaid` with a real local CLI only if
the operator has already configured them; never add credentials to the repo.

**Verify**: `uv run pytest tests/`, `uv run ruff check malt/`, and
`uv run mypy malt/` all exit 0.

## Test plan

- Provider state reducer: loading/ready/unavailable/error mappings.
- Startup cache: one refresh shared across dialog instances; explicit refresh
  increments generation; stale completion is ignored.
- `cf` argv: default profile, named profile, no duplication across wrappers.
- Hostname validation: subdomain-only input and deterministic composition.
- Wizard state: no Next without a selected zone; Back preserves selection;
  Add persists only a validated full hostname.

## Done criteria

- [ ] Provider discovery starts once at app startup and is shared by Settings.
- [ ] Reopening Settings does not re-run provider discovery while cached state
  is valid.
- [ ] Loading is shown during unresolved provider state; it never appears as
  “cf CLI not installed” prematurely.
- [ ] Domains header has no account/email banner.
- [ ] All `cf` calls honor the configured profile syntax.
- [ ] Add Domain is a readable Zone list → Next → Subdomain-with-suffix wizard.
- [ ] Full hostname input is rejected or prevented.
- [ ] Tests, Ruff, and mypy pass.

## STOP conditions

- The installed `cf` CLI rejects the requested profile syntax or uses a
  different profile mechanism.
- App startup cannot safely launch a background worker without GTK lifecycle
  leaks; report the constraint and preserve current behavior.
- Existing persisted settings contain an incompatible profile shape.
- Zone data cannot distinguish loading from an empty successful result.

## Maintenance notes

Keep provider cache ownership above individual dialogs. Any new Settings
surface must subscribe to the shared state instead of launching its own
subprocess. Profile handling must remain centralized in `cfcli.py`; reviewers
should reject inline `["cf", ...]` construction that bypasses the profile
helper. 
