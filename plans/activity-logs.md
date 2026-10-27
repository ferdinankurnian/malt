# Plan: Activity logs (structured UI)

## Goal
Make project activity readable at a glance. Keep raw text logs for debugging, add an “activity” view with icons + one-line summaries, and a detail modal on click.

## Depends on
**Must ship after `project-detail-layout.md`.**
Logs live in the main column of the redesigned detail page; don’t build activity UI on the old single-column layout.

## Why
Current logs are a wall of tool-call strings. Fine for debugging, bad for “what did the agent just do?”

## Two view modes

Toggle on the logs header (segmented button or dropdown):

1. **Activity** (default) — structured list
2. **Raw** — existing plain text log output, unchanged behavior

## Activity row

One row per event:
- **Icon** by tool/kind:
  - read_file / list_directory / file_info / search → document/search style
  - write_file / edit_file → edit/pencil
  - delete_file → trash
  - move_file → move/rename
  - run_command → terminal
  - server start/stop / auth / errors → status icons
- **Summary line** e.g. `edit_file → plans/project-icons.md`
- **Timestamp** (relative or compact clock)
- Optional subtle status tint (ok / error)

Click row → **detail modal**.

## Detail modal

Show:
- Tool name
- Arguments (formatted, not one giant escaped string)
- Result or error message
- For `edit_file`: short before/after or truncated old/new if available
- Timestamp + duration if we have it

Keep modal scannable — no need for a full diff viewer in v1.

## Data shape

String-only logs are painful to parse reliably. Prefer emitting **structured events** alongside or instead of free-text lines.

Suggested event (json, stored or logged as one json line):

```json
{
  "ts": "2026-08-10T17:00:00+07:00",
  "kind": "tool",
  "tool": "edit_file",
  "ok": true,
  "args": {"path": "plans/x.md", "replace_all": false},
  "summary": "edit_file → plans/x.md",
  "result": "ok (1 replacement(s))",
  "error": null
}
```

Server/UI:
- Activity mode reads structured events
- Raw mode can still show human lines derived from the same events (or keep dual-write during transition)

If structured emission is too big for one pass: v1 activity can regex/heuristic-parse existing log lines for known tools, then harden with structured events in a follow-up. Prefer structured early if touch `server` / tool dispatch anyway.

## UI placement

In redesigned detail main column (from project-detail-layout):
- Logs header: title + entry count + mode toggle + Clear
- Body: either activity list (ListBox/ListView) or raw TextView

## Files likely to touch
- `malt/views/project_detail.py` — toggle, activity list, detail modal
- `malt/server.py` / `mcp_server.py` — emit structured events when tools run
- `malt/db.py` — optional: store events in sqlite instead of only in-memory/text buffer (nice later, not required v1)
- `malt/ui/style.css` — row icons, density
- `AGENTS.md` — note log modes

## Out of scope
- Full unified diff viewer
- Filtering/search in activity list (can add later)
- Real-time collaboration / export
- Redesigning log storage for long-term analytics

## Implementation order
1. Finish `project-detail-layout.md` (logs region exists and is primary)
2. Decide event source: structured emit vs parse-existing
3. Activity list UI + icons + summary rows
4. Detail modal
5. Mode toggle (activity default, raw preserved)
6. Structured events at tool boundary (if not done in 2)
7. Polish empty/error states

## Notes
- Activity default, raw always available — don’t remove the chaos view, tame it
- Summary text should be stable and short; dump detail only in modal
- Failed tool calls must be obvious (icon + error row + modal error body)
