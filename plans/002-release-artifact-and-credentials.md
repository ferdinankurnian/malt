# Plan 002: Remove credential leakage and define a distributable release

> **Executor instructions**: Follow this plan step by step. Never copy or
> print secret values. Touch only the files listed in Scope. Stop and report
> if a credential has been committed or if rotation cannot be completed by the
> operator.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: `plans/001-release-verification-gate.md`
- **Category**: security / packaging / docs
- **Planned at**: commit `a1cb53b`, 2026-10-27

## Why this matters

The working tree contains untracked `.cloudflare/cache/cloudflare-account.json`.
It is not covered by `.gitignore` and appears to be a Cloudflare account
credential cache. Even if it is never committed, it can be accidentally
included in a release archive or shared patch. The project also declares
version `0.1.0`, has no `LICENSE` file, no release workflow, and only documents
clone-and-editable-install usage; that is not a reproducible distributable
release.

## Current state

- `.gitignore` ignores Python caches, build artifacts, virtualenvs, and `*.db`,
  but not `.cloudflare/`.
- `pyproject.toml:5-25` declares the package and console script but no license
  metadata, package-data declaration, or release automation.
- `README.md:28-46` requires system GTK/libadwaita packages and documents only
  source installation.
- The current branch is `feature/ui-updates` with many modified and untracked
  files; the release must not be cut from this dirty state.

## Scope

**In scope**
- `.gitignore`
- `pyproject.toml`
- `README.md`
- `LICENSE` (create, using the repository's intended MIT license)
- `.github/workflows/ci.yml` or release workflow, if Plan 001 did not create it
- exact untracked credential path `.cloudflare/cache/cloudflare-account.json`
- package metadata/assets needed for a source or wheel release

**Out of scope**
- Changing Cloudflare API behavior.
- Printing, committing, or pasting any credential contents.
- Publishing to PyPI, GitHub Releases, or another registry without explicit
  operator authorization.

## Steps

### Step 1: Remove and ignore local credential state

Remove the exact untracked credential cache path from the working tree, add
`.cloudflare/` to `.gitignore`, and verify it is ignored. Check git history and
the index for credential files without displaying their contents. If the
credential was ever committed or shared, stop and report that it must be
rotated/revoked by the operator.

**Verify**: `git status --short --untracked-files=all` contains no
`.cloudflare` path; `git check-ignore -v .cloudflare/cache/cloudflare-account.json`
shows the new ignore rule.

### Step 2: Make package metadata release-grade

Add explicit license metadata, a supported Python classifier, and package-data
configuration for runtime files under `malt/ui/` and any required package
assets. Bump the version only after the current feature work is committed and
reviewed; do not invent a public version number in this plan.

**Verify**: run the package metadata inspection command supported by the
chosen build backend and confirm the wheel contains `malt/ui/style.css` and
imports the console entry point.

### Step 3: Document fresh-machine installation and caveats

Update README with the actual release install path, GTK/libadwaita runtime
dependencies (not only development package names), tunnel binary/auth
requirements, local-only binding behavior, and a warning that public tunnels
expose the selected project permissions. State that auth tokens are
capability URLs and must be treated as secrets. Add a short release checklist.

**Verify**: every command in the installation section is syntactically valid
and matches `pyproject.toml`; no README step assumes a developer checkout.

### Step 4: Verify a clean artifact

Build a wheel and sdist in an isolated or ignored build directory, inspect
their file lists, and install the wheel into a temporary virtual environment.
Run `malt --help` or an equivalent import/entry-point check without launching
GTK if the environment is headless.

**Verify**: both artifacts build successfully, contain no `.cloudflare`,
`.db`, `.env`, or secret-cache files, and the temporary install resolves the
`malt` console script.

## Done criteria

- [ ] No credential cache is present in the worktree or release artifacts.
- [ ] `.cloudflare/` is ignored.
- [ ] License and package metadata are explicit.
- [ ] Wheel/sdist contain runtime CSS and no local state.
- [ ] README describes a fresh-machine install and security caveats.
- [ ] Release is not published automatically or from a dirty tree.

## STOP conditions

- Any secret is found in git history or an artifact.
- The build backend omits runtime files or imports GTK at metadata time.
- Packaging requires adding credentials or network calls.

## Maintenance notes

Before every release, inspect `git status`, build both artifacts, inspect their
file lists, and rotate any credential that was ever exposed. Keep local
Cloudflare state outside the repository permanently.
