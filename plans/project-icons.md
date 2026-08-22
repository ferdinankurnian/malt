# Plan: Project icons

## Goal
Give each project a visible icon in the sidebar (and detail header) so projects are easier to scan. Support auto-detect from common favicon paths, custom upload, or fallback letter avatar.

## Why
Sidebar is name-only today. With many projects, icons make switching faster and less error-prone.

## Design decisions

### Icon sources (per project)
1. **auto** (default) — scan project root for common favicon/icon files
2. **custom** — user-picked image file (copied into malt data dir)
3. **none** — letter avatar from project name (first character)

### Auto scan paths (in order, first hit wins)
- `favicon.ico`
- `favicon.png`
- `favicon.svg`
- `public/favicon.ico`
- `public/favicon.png`
- `public/favicon.svg`
- `static/favicon.ico`
- `static/favicon.png`
- `assets/favicon.ico`
- `assets/favicon.png`
- `app/favicon.ico`
- `src/favicon.ico`
- `src/assets/favicon.ico`

No remote fetch. No scraping homepage URLs. Local files only — predictable and private.

### Custom icons
- User picks file via GTK file dialog (png, svg, ico, webp, jpg)
- Copy into `~/.local/share/malt/icons/{project_id}.ext` (not a reference to original path — survives moves)
- On switch back to auto/none, delete stored custom file if present

### Display
- Sidebar project card: ~20–24px icon left of name
- Detail header: slightly larger next to title
- **Active projects dropdown** (header “n Project Active”): same icon + name + status dot as sidebar rows — keep consistent
- Letter avatar: simple circle/square with initial + muted color derived from project id/name
- Broken/missing file under auto → fall back to letter avatar quietly

## Data model

### Project (db)
```text
icon_mode   TEXT  DEFAULT 'auto'   -- 'auto' | 'custom' | 'none'
icon_path   TEXT  NULL             -- relative name under icons dir when custom; unused otherwise
```

Optional cache (not required v1):
- resolved path / mtime for auto — can re-scan on project select or app start

### Settings
No new global settings. Icons are per-project only.

## UI

### Sidebar (`project_list.py`)
- Load icon for each card (Gdk.Texture / Gtk.Image from file or generated avatar)
- Keep layout tight; icon + name + status dot

### Project detail
- Icon preview near name
- Menu/button **Change icon**:
  - Use auto (scan)
  - Upload image…
  - No icon (letter)
- When auto selected, optional caption: path found or “no favicon found”

### File dialog
- Filter: images (`*.png`, `*.svg`, `*.ico`, `*.webp`, `*.jpg`, `*.jpeg`)
- On accept → copy + set `icon_mode=custom` + save db

## Implementation notes

### Resolving auto icon
```python
def find_auto_icon(root: Path) -> Path | None:
    candidates = [...]
    for rel in candidates:
        p = root / rel
        if p.is_file():
            return p
    return None
```

### Loading for GTK
- Prefer `Gdk.Texture.new_from_filename` / paintable for raster
- SVG: GTK supports via gdk-pixbuf/rsvg where available; if load fails → avatar fallback
- ICO: usually works via pixbuf; if not, skip to next candidate or avatar

### Letter avatar
- First alphanumeric char of name, uppercased; fallback `?`
- Background color: stable hash of project id → HSL with fixed S/L so it stays readable in light/dark

### Performance
- Resolve icons once when building/updating list, not every frame
- Re-resolve auto icon when root path changes or user picks “auto” again

## Files to touch
- `malt/db.py` — columns + migration for existing rows
- `malt/models.py` — fields on Project
- `malt/views/project_list.py` — render icons on cards
- `malt/views/project_detail.py` — preview + change-icon actions
- `malt/icons.py` (new, optional) — resolve auto, copy custom, letter avatar helper
- `README.md` / `AGENTS.md` — short mention

## Out of scope
- Fetching favicon from remote URLs
- Icon packs / built-in emoji picker
- Per-theme alternate icons
- Animated icons
- Syncing icon when files appear later without refresh (manual re-select auto is enough for v1)

## Implementation order
1. DB + model fields + simple letter avatar helper
2. Sidebar rendering (avatar only first is fine)
3. Auto scan + load file icons
4. Custom upload + storage under `~/.local/share/malt/icons/`
5. Detail panel controls + captions
6. Docs

## Notes
- Prefer local-only discovery; don’t surprise users with network calls
- Custom copy-on-upload avoids broken icons when project folder moves
- Keep fallbacks silent — never block project select on bad icon file
