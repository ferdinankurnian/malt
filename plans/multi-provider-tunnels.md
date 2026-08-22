# Plan: Multi-provider tunnels + per-project domains

## Goal
Make domain/tunnel setup clear and flexible so web agents (Grok, Claude, etc.) can reliably reach malt MCP endpoints. Support cloudflare + ngrok, with domains selectable per project.

## Why
Current flow is opaque: one global `tunnel_hostname`, assumes cloudflared is already configured, no install guidance, no alternative provider. Users don't know how to get a public URL.

## Design decisions

### Providers
- **cloudflare** (default, first-class)
  - quick tunnel → random `*.trycloudflare.com` (zero config)
  - named tunnel → custom hostnames via user's `~/.cloudflared/config.yml`
- **ngrok** (optional)
  - uses account dev domain / configured domain
  - requires authtoken
  - free tier limits (20k req/mo, 1GB) → document clearly, not primary path

### Domains model
- Domains are **global resources** (settings)
- Each **project picks one domain** (or none / quick mode)
- URL stays path-based: `https://{domain}/mcp/{project_id}?token=...`
- One local MCP port; routing by path stays the same

### Tunnel process
- Still **one tunnel process** for the app (not per project)
- cloudflare named: rebuild ingress from all hostnames currently used by running projects
- cloudflare quick: only when no named domains needed / fallback
- ngrok: one endpoint; warn if multiple distinct domains requested

## Data model

### Settings (`~/.config/malt/settings.json`)
```json
{
  "tunnel_provider": "cloudflare",
  "domains": [
    {
      "id": "uuid",
      "hostname": "mcp.iydheko.site",
      "provider": "cloudflare",
      "mode": "named"
    },
    {
      "id": "uuid",
      "hostname": "xxx.ngrok-free.app",
      "provider": "ngrok",
      "mode": "dev"
    }
  ],
  "default_mcp_port": 3100,
  "url_visible_by_default": false
}
```

Remove single `tunnel_hostname` string. Migrate existing value → one cloudflare named domain entry.

### Project (db)
- Add `domain_id` (nullable text) — references settings domain id
- Keep `tunnel_enabled` or fold into "has domain selected + server running"
- If `domain_id` null → use provider quick mode when starting (cloudflare only)

## UI

### Settings → Tunnel / Domains tab
1. **Provider status cards**
   - cloudflare: binary found? version? link/guide install (`yay -S cloudflared` + short steps)
   - ngrok: binary found? authtoken present? guide (`ngrok config add-authtoken …`)
   - no auto-install — user installs themselves

2. **Domain list**
   - list of configured domains (hostname, provider, mode)
   - add domain:
     - cloudflare named: hostname input (user must already have DNS + tunnel config)
     - cloudflare quick: not stored as fixed domain (ephemeral at runtime)
     - ngrok: detect/show assigned dev domain, or input if paid domain
   - remove domain (block if any project still references it)

3. **Default provider** for new projects / quick fallback

### Project detail
- Dropdown: Domain (from list, filtered by available providers)
- Option: "Quick tunnel (random URL)" when provider is cloudflare
- URL display uses selected domain
- Start fails clearly if provider binary missing or domain invalid

## Tunnel manager changes (`tunnel.py`)

### Interface
```python
class TunnelManager:
    def start(self, domains: list[DomainConfig], port: int) -> bool: ...
    def stop(self) -> bool: ...
    def is_running(self) -> bool: ...
    def public_base_url(self, domain_id: str | None) -> str | None: ...  # after start
```

### cloudflare
- named: generate ingress yml from active hostnames → `cloudflared tunnel --config ... run`
- quick: `cloudflared tunnel --url http://localhost:{port}` → parse stdout for trycloudflare URL
- reuse existing named-tunnel detection logic where possible

### ngrok
- `ngrok http {port} --log=stdout` (or config domain)
- parse public URL from API (`localhost:4040/api/tunnels`) or stdout
- require authtoken; surface clear error if missing

### Detection helpers
- `which cloudflared` / `which ngrok`
- ngrok: check `~/.config/ngrok/ngrok.yml` for authtoken

## Install guides (in-app, short)

### cloudflared (Arch)
```
yay -S cloudflared
# optional named tunnel: follow cloudflare zero trust docs
# point DNS CNAME to tunnel, put hostname in malt domains list
```

### ngrok (Arch)
```
yay -S ngrok
ngrok config add-authtoken <token from dashboard>
```

Link out to official docs; don't duplicate long tutorials.

## Onboarding / setup guide

Lightweight, skippable — not a forced multi-step wizard.

### When it shows
- First run / empty state: no domains configured **and** user never successfully started a tunnel
- Settings → Tunnel tab: always reachable via "setup guide" / "how to connect a domain"
- Project detail: if user hits Start without a usable domain/provider → short nudge + link to guide

### What it is
- One panel / dialog with provider choice (cloudflare recommended, ngrok optional)
- Short numbered steps per path, with **live checks** (binary present? authtoken present?)
- Skip anytime; don't re-show on every app launch once dismissed or once a domain exists

### Paths

**A. Cloudflare quick (fastest)**
1. Install `cloudflared` (show arch command + link)
2. Add a project, leave domain on "Quick tunnel"
3. Start → malt shows the random `*.trycloudflare.com` URL

**B. Cloudflare named (fixed domain)**
1. Install `cloudflared`
2. Create named tunnel + credentials (link official CF zero trust docs)
3. Point DNS (CNAME) at the tunnel
4. Add hostname in malt domain list
5. Assign domain on project → Start

**C. Ngrok**
1. Install `ngrok`
2. `ngrok config add-authtoken …` (detect success)
3. Add/use dev domain in malt
4. Assign on project → Start
5. Caption: free tier limits (20k req / 1GB) — fine for light use only

### Rules
- No account login inside malt
- No DNS/API management inside malt
- No auto-install of binaries
- Prefer cloudflare copy as default path
- Keep text short; deep detail lives in linked docs

## Migration
- On load settings: if old `tunnel_hostname` exists and no `domains` key → create one cloudflare domain entry
- Projects without `domain_id` → null (quick behavior)

## Files to touch
- `malt/settings.py` — new schema + migration + onboarding dismissed flag
- `malt/db.py` / `models.py` — `domain_id` on projects
- `malt/tunnel.py` — multi-provider, ingress rebuild, URL discovery
- `malt/main.py` — settings UI (Domains tab rewrite) + onboarding entry points
- `malt/views/project_detail.py` — domain picker + URL from selection + start-time nudge
- `malt/views/` — optional small setup-guide widget/dialog
- `README.md` / `AGENTS.md` — document providers + setup

## Out of scope (this round)
- auto-install binaries
- more providers (localtunnel, bore, etc.)
- per-project separate local ports
- cloudflare Access / ngrok traffic policy UI
- managing cloudflare DNS from malt
- multi-ngrok endpoints beyond free-tier comfort
- forced full-screen product tour

## Implementation order
1. Settings schema + migration + domain list UI (no tunnel change yet)
2. Project `domain_id` + picker in detail view
3. Rewrite `TunnelManager` for cloudflare named multi-host + quick
4. Add ngrok provider
5. Status/detection + install guides in settings
6. Onboarding empty-state + setup guide dialog (skippable)
7. Docs

## Notes
- Prefer cloudflare as default everywhere in UI copy
- Be honest about ngrok free limits in UI (tooltip / caption)
- Quick tunnel URL is ephemeral — show it after start, don't pretend it's a saved domain
- Keep path routing `/mcp/{id}`; domains only change the host
