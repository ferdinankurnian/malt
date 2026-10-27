# Plan 004: Make settings and activity details responsive

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving on. Touch
> only the files listed in Scope. Preserve existing GTK4/libadwaita patterns.
> If a GTK API is unavailable in the supported environment, stop and report
> instead of replacing it with an untested workaround.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: `plans/001-release-verification-gate.md`
- **Category**: perf / ui
- **Planned at**: commit `a1cb53b`, 2026-10-27

## Why this matters

Opening Settings currently performs Cloudflare discovery synchronously on the
GTK main thread. The settings code calls `cf_cli.list_zones()` and
`cf_cli.auth_status()` while building the dialog; each `cf` invocation can cost
about three seconds according to `malt/cfcli.py`, so the window visibly hangs.
The activity detail dialog also presents every field as one vertical stack,
making long `read_file` results and `edit_file` old/new strings difficult to
compare. The release polish should make the dialog open immediately, make
comparisons side-by-side, strengthen the running indicator to a flat LED-like
green, and make fallback project avatars more vivid.

## Current state

- `malt/main.py:414` starts `_on_settings`; the settings UI is constructed
  immediately.
- `malt/main.py:1246` loads Cloudflare zones in a background helper only after
  some UI flow has already been built, while `malt/main.py:1432` calls
  `cf_cli.auth_status()` during settings setup. Audit the full method before
  changing it; all Cloudflare calls must be off the GTK main thread.
- `malt/cfcli.py:1-35` documents that each `cf` call can cost roughly three
  seconds and already has TTL caching. Preserve caching but do not rely on it
  for first-open responsiveness.
- `malt/views/project_detail.py:1778-1859` builds the activity detail dialog
  as one vertical `Gtk.Box`; `Result` is appended after arguments, and
  `old_string` / `new_string` are rendered as ordinary detail fields.
- `malt/ui/style.css:26-28` defines `.success` as `#26a269`, which is muted
  rather than bright LED green.
- `malt/icons.py:83-99` defines `_DARK_PALETTE` for fallback letter avatars.
  The current colors are intentionally dark/muted and produce the dull
  sidebar avatars shown in the reported screenshots.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Typecheck | `uv run mypy malt/` | exit 0 |
| Lint | `uv run ruff check malt/` | exit 0 |
| Tests | `uv run pytest tests/` | all tests pass |

## Scope

**In scope**
- `malt/main.py` — settings construction/loading behavior
- `malt/cfcli.py` — only if cache/background boundaries need a small helper
- `malt/views/project_detail.py` — activity detail layout
- `malt/ui/style.css` — flat success LED color
- `malt/icons.py` — fallback avatar palette
- `tests/` — regression tests for pure helpers and any testable settings
  scheduling behavior
- `README.md` only if a user-visible settings behavior needs documentation

**Out of scope**
- Rewriting the settings data model or Cloudflare CLI integration.
- Adding gradients, shadows, glow effects, or animations to the green status
  indicator. The request is a brighter color only.
- Changing endpoint/auth/log data semantics.
- Changing icon image files or custom uploaded icon handling.

## Steps

### Step 1: Make Settings open without blocking GTK

Separate dialog creation from remote/provider discovery. Construct and present
the dialog with cached/local values immediately. Show a neutral loading state
for provider status, zones, tunnels, and authentication. Schedule background
workers for `cf_cli.auth_status()`, `cf_cli.list_zones()`, and any equivalent
Cloudflare calls, then marshal results back to GTK using the repository's
existing GLib/thread pattern. Never call `cf` subprocesses from the GTK
callback or while constructing widgets.

Prevent stale background results from updating a closed/reopened settings
dialog. Keep actionable failure text when `cf` is missing, logged out, or
times out; do not silently turn a failed lookup into a successful connected
state.

**Verify**: a mocked settings-open test proves the dialog is presented before
the slow provider functions return; a second test proves late results do not
touch a closed dialog. Manual smoke check: opening Settings is immediate even
when provider calls sleep.

### Step 2: Add side-by-side activity detail layout

In `_show_activity_detail`, keep the left column for event metadata and
arguments (tool, timestamp, path, line range, replace-all, and other small
fields). Put content-heavy values on the right in a vertically scrollable
column: `Result` for all events, and `Old string` / `New string` for
`edit_file`. Use equal or intentionally weighted columns so long strings get
usable width. Keep each text view selectable and preserve existing truncation
and error styling. For events without content-heavy values, retain a clean
right-side result panel rather than leaving an awkward empty column.

The layout must remain usable at narrow window sizes: use a responsive
orientation or a minimum-width fallback that stacks columns below the
threshold, while keeping side-by-side layout at normal desktop width.

**Verify**: add/adjust UI-facing tests or pure event-layout helper tests where
possible; manually inspect both a `read_file` event and an `edit_file` event.
Assert that result and old/new content are in the right column and metadata
remains in the left.

### Step 3: Brighten the running status indicator

Change only the `.success` foreground color in `malt/ui/style.css` to a
high-luminance green suitable for a dark GTK theme, such as a flat
`#39ff88`-class value chosen to remain legible. Do not add gradients, box
shadows, text shadows, borders, or animations. Verify the same success class is
used by the running dot and does not unintentionally recolor unrelated UI.

**Verify**: stylesheet check confirms the success rule contains one flat
foreground color and no shading properties; manually confirm the running dot
is visibly brighter in the sidebar and active-project UI.

### Step 4: Replace muted fallback avatar palette

Replace `_DARK_PALETTE` with a small deterministic palette of brighter,
distinct colors that still provides white-letter contrast on the dark theme.
Keep the existing MD5/project-id selection algorithm so a project's color
stays stable across launches. Do not alter custom/auto image resolution.

**Verify**: add a pure test for deterministic color selection and contrast
bounds; manually inspect at least five projects with no image icon and confirm
colors differ without becoming fluorescent or unreadable.

### Step 5: Run release gates

Run the complete Plan 001 test suite and the repository checks. If UI tests
cannot run headlessly because GTK is unavailable, keep pure helper tests
passing and document the exact manual smoke checks rather than deleting
coverage.

**Verify**: `uv run pytest tests/`, `uv run ruff check malt/`, and
`uv run mypy malt/` all exit 0.

## Test plan

- Settings: dialog presentation is not blocked by provider subprocesses;
  late worker results are ignored after dialog close; provider failure renders
  an explicit unavailable/error state.
- Activity detail: `read_file` keeps line metadata left and result right;
  `edit_file` keeps metadata left and old/new/result right; long content
  remains scrollable/selectable; narrow mode does not clip content.
- Avatar palette: same project identity gives same color; at least several
  palette entries have sufficient brightness/contrast for white text.
- CSS: success color is flat and only affects the intended class.

## Done criteria

- [ ] Settings opens without waiting for Cloudflare/provider subprocesses.
- [ ] Slow or failed provider discovery cannot update a closed dialog.
- [ ] Activity details use a responsive two-column layout for content-heavy
  values and preserve readable narrow-window behavior.
- [ ] Running status is a bright flat green with no added shading.
- [ ] Fallback avatars use a brighter deterministic palette.
- [ ] `uv run pytest tests/`, `uv run ruff check malt/`, and
  `uv run mypy malt/` pass.

## STOP conditions

- GTK callbacks still require synchronous provider calls after the refactor.
- A proposed layout breaks at the repository's supported minimum window size.
- The only way to brighten the indicator requires adding visual effects beyond
  a color change.
- Existing activity event data cannot distinguish `old_string`, `new_string`,
  and `result`; stop and report rather than changing the log schema silently.

## Maintenance notes

Keep all slow external/provider work off the GTK main loop. Any future
settings provider must expose a loading, success, and failure state. Review
the activity dialog whenever a new event payload adds a large content field;
large values belong in the right content column, not the metadata column.
