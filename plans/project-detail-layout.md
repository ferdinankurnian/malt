# Plan: Project detail layout redesign

## Goal
Make the project detail page feel more serious and scannable. Move config out of the main flow into a narrow read-only meta column; keep endpoint, connect help, and logs as the primary content.

## Why
Current layout is a single tall stack of forms (path, permission, commands, endpoint, logs). Feels unfinished and hard to focus on the thing that matters: the public MCP URL and activity.

## Layout

### Structure (wide window)
```
┌──────────┬─────────────────────────────┬──────────────┐
│ Projects │  icon + title     Stop Copy ⋮│  meta panel  │
│  list    │─────────────────────────────│  (narrow)    │
│          │  MCP Endpoint               │  path        │
│          │  Connect (how-to + snippet) │  permission  │
│          │  Logs                       │  domain      │
│          │                             │  status      │
└──────────┴─────────────────────────────┴──────────────┘
```

### Narrow window
Stack meta panel below main content (or collapse meta into a disclosure). Don't force two columns when width is tight.

## Left / main column

### Header
- Project icon (left of title) — see `project-icons.md`
- Title
- Actions: Start/Stop, Copy URL, overflow ⋮
  - ⋮ menu: Edit icon…, Delete project, (future actions)

### MCP Endpoint
- URL display (show/hide, selectable)
- Regen Token
- Copy stays in header too (redundant ok — primary action)

### Connect
- Short explanation: use this URL in an MCP client; keep token private
- Snippet example (JSON or generic client config) using current URL
- Keep short; link out only if needed later

### Logs
- Full remaining height
- Clear + existing log behavior
- Primary visual weight of the page

## Right / meta column (read-only)

Width ~260–300px. Display only — no inline editors.

Fields (v1):
- **Root path** — truncated + tooltip full path
- **Permission** — badge/label + info button
  - info popover/dialog: tools available for that mode (and note about execute whitelist)
- **Domain** — hostname or “Quick tunnel” / “—”
- **Status** — running/stopped (+ tunnel state if useful)

Top of panel: **pencil** button → opens edit modal.

### Not shown in meta
- Allowed commands list (only inside edit modal when permission is execute)
- Icon (lives next to title / via ⋮)

## Edit modal

Opened from pencil (or future “Edit project”).

Fields:
- Root path
- Permission
- Domain picker (once multi-provider domains land)
- Allowed commands — **only visible when permission == execute**

Save writes db + refreshes meta column + URL if domain changed.
Cancel discards.

Icon editing is **not** in this modal — via ⋮ → Edit icon (see project-icons plan).

## Permission info button

Popover or small dialog listing tools for current mode, matching `PERMISSION_LEVELS` / README matrix. For execute: mention whitelist; for admin: any command.

## Interaction rules
- Meta is always read-only overview
- All mutations go through modal or ⋮ actions
- Start still blocked clearly if tunnel/provider not ready (ties to tunnel plan)
- Esc / cancel closes modal without save

## Files to touch
- `malt/views/project_detail.py` — major layout rewrite (main + meta + modal)
- `malt/main.py` — only if window/header wiring needs adjust
- `malt/ui/style.css` — column widths, meta card, snippet block
- Possibly small helper for connect snippet string
- `README.md` — optional screenshot/layout note later

## Depends on / coordinates with
- `project-icons.md` — icon beside title + ⋮ edit icon
- `multi-provider-tunnels.md` — domain field in meta + modal picker

Can ship layout first with domain as plain text from current settings hostname, then plug real domain picker when tunnel plan lands.

## Out of scope
- Redesigning project list sidebar beyond icon support
- Full settings redesign
- Fancy analytics on meta panel
- Inline editing of path/permission

## Implementation order
1. Split detail view into main + meta boxes (meta hard-coded read-only from current project fields)
2. Move path/permission out of main stack into meta; drop commands from overview
3. Add pencil → edit modal (path, permission, commands if execute)
4. Add permission info popover
5. Add Connect section with snippet
6. Wire icon slot in header (placeholder until icons plan)
7. Responsive: stack meta under main when narrow
8. Polish CSS

## Notes
- Prefer GitHub-style density: meta quiet, main content loud
- Logs must not shrink to a stamp — give them flex height
- Truncate long paths/URLs with tooltip, don't wrap the meta column into a mess
