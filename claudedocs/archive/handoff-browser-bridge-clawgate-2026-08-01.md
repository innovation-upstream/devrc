# Handoff: browser-bridge v0.7.0 + clawgate extension v1.4.0

**Date:** 2026-08-01
**Session:** element references enrichment

---

## What shipped

### browser-bridge v0.7.0 (devrc)
- **`browser context`** — page metadata (domain, path, searchParams, title, tabId) without DOM read
- **Enriched `text`/`html`** — every read now includes domain, path, searchParams, tabId
- **`text --annotated`** — structured element extraction: CSS path, identifying attrs (id, class, href, data-*, aria-*), adjacent text (precedingText/followingText)
- server.py `ALLOWED_OPS` + `TAB_SCOPED_OPS` updated
- Manifest 0.6.0 → 0.7.0
- 85 tests passing (64 protocol + 21 annotated)
- Deployed + validated on laptop + workbench

### clawgate extension v1.4.0 (homelab-talos)
- Element picker references enriched with page context + adjacent text
- Format: `` `#sel` — tag "name" (domain.com/path · prev: "text" · next: "text") ``
- Built locally in content.js (no browser-bridge dependency)
- `buildEnrichment(el)` reads `window.location` + walks DOM siblings
- `new URLSearchParams(loc.search)` instead of `loc.searchParams` (isolated world fix)
- Token configurable in extension options (optional)
- 596 tests passing
- Deployed on workbench (Default profile, unpacked)

### Documentation
- browser-bridge SKILL.md, README.md, CLAUDE.md, extension README.md, errors.md
- clawgate SKILL.md — new "Resolving element references" section
- clawgate extension README.md — enriched format documented
- Memory files updated (browser-bridge.md, MEMORY.md)
- Handoff docs: `handoff-browser-bridge-2026-08-01.md`, this file

---

## Key learnings

1. **Content scripts don't hot-reload** — even after Brave restart. Must remove + re-add unpacked extension to force Chrome to drop cached content scripts. Simple reload only works AFTER the first remove+re-add cycle.

2. **`window.location.searchParams` not iterable in MV3 content script isolated world** — use `new URLSearchParams(loc.search)` instead.

3. **browser-bridge `tab` doesn't resolve instances** — `tab` routes within an instance; `target` resolves which instance. Must query `/instances` and match by active tab URL.

4. **Annotated selectors differ from clawgate selectors** — can't match by selector; must use page context (domain+path) or local DOM.

5. **server.py sync hazard** — `ALLOWED_OPS` must stay in sync with protocol.js. Test exists but must be run explicitly.

6. **Extension deploy paths**: browser-bridge at `~/.local/share/browser-bridge-ext/` (nix), clawgate unpacked from repo. Both require manual `brave://extensions` reload.

---

## Commits

- `e673e6b` — feat(browser-bridge): context op, enriched read envelopes, text --annotated
- `4643cf1` — docs(browser-bridge): add context op, enriched envelopes, text --annotated
- `d631930` — docs: add element reference resolution guide to clawgate skill
- `69a9c2ef` — docs: add enriched element reference format to extension README (v1.4.0)
- `df750474` — feat(clawgate-ext): element refs enriched via browser-bridge annotated data (1.4.0)

---

## Open threads / next steps

- Could add `browser resolve "<description>"` op using annotated DOM for natural language element finding
- `context` op not in the autonomous agent's op list — consider adding if agent needs page metadata
- clawgate enrichment could optionally call browser-bridge for richer annotated data when available (currently fully local)
- Consider CI check that `server.py ALLOWED_OPS == protocol.js ALLOWED_OPS`

---

## Deploy notes

- **browser-bridge**: rsync extension to `~/.local/share/browser-bridge-ext/` on each host, reload in brave://extensions
- **clawgate extension**: ⚠ **superseded 2026-08-12** — Brave now loads it from the dedicated worktree `~/workspace/clawgate-extension/containers/clawgate/extension` (branch `clawgate-ext-local`), on **both hosts** and in **every profile that has it**, not from the `homelab-talos` base clone and not just `Default`. Deploy + verification procedure: the `clawgate` skill's `reference/extension.md`.
- **server.py**: if updated, must kill + restart the server process (nix symlink → `~/.config/browser-bridge/server.py`)
