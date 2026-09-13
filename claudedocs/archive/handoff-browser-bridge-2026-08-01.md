# Handoff: browser-bridge — context op, enriched envelopes, annotated text — 2026-08-01

## Goal
Ship three small features that reduce token cost and improve page-identification
capability: a `context` op (metadata without DOM read), enriched `text`/`html`
envelopes (domain/path/searchParams/tabId), and `text --annotated` (structured
element extraction). Manifest 0.7.0. Doc-only changes in this session.

## What shipped (manifest 0.7.0)

| Feature | What | Why |
|---|---|---|
| **`context` op** | Returns `{url, domain, path, searchParams, title, tabId}` — no DOM read. Tab-scoped, no required fields | Cheaper than `text`/`html` for page identification; avoids unnecessary DOM access |
| **Enriched `text`/`html` envelopes** | Every `text` and `html` result now includes `domain`, `path`, `searchParams`, and `tabId` alongside `url`/`title` | Backward-compatible (additive). Callers that only use `url`/`title` are unchanged |
| **`text --annotated`** | Structured element extraction: each element has `{text, path, tag, attrs, precedingText, followingText}`. `attrs` includes `id`, `class`, `href`, `src`, `alt`, `title`, `name`, `placeholder`, `type`, `role`, `aria-label`, `data-testid`, `data-cy`, `data-e2e`. Byte-capped | Replaces flat `innerText` with structured data for selector-aware agent workflows |

## ALLOWED_OPS sync hazard

🔴 **`server.py` and `protocol.js` must stay in lockstep on the ALLOWED_OPS set.**

The op names defined in `protocol.js` (`OPS` object) and the op dispatch in
`server.py` (`OP_TO_SERVER` mapping) are separate codepaths that must agree. A
new op added to one but not the other causes either an `unknown_op` error from
the extension or a silent dispatch failure from the server.

This is not enforced by any compile-time check. The unit tests cover each path
independently, but a drift between them is a runtime-only failure. `context` was
added to both in this change — verify `protocol.js`'s `OPS` and `server.py`'s
`OP_TO_SERVER` both include it before deploying.

## Deployment notes

- **Extension**: deployed to `~/.local/share/browser-bridge-ext/` via
  `home-manager switch` (`home.activation.browserBridgeExtension` in `nix/home.nix`).
  Real copy (`cp -rL` → `mv -T --exchange`), not nix-store symlinks. Brave must
  load from this path, NOT the repo directory.
- **server.py**: nix-managed symlink at `~/.config/browser-bridge/server.py`.
  Restarted automatically by `home-manager switch` (X-Restart-Triggers).
- **MV3 sticky workers**: the extension's long-poll keeps the old service worker
  alive. After any extension code change, ↻ in `brave://extensions` is
  **UNRELIABLE** — a full Brave quit-and-reopen is the reliable path. Use
  `browser --instance <label> ping` to confirm the new build is loaded (new op
  name = deterministic build tell; old build → `unknown_op`).
- **Flake trap**: `git add` any new files before `home-manager switch`. Flakes
  only see git-tracked files; untracked new files are silently omitted from the
  deployed tree.

## Known limitations

- **`context` is tab-scoped**: needs an active/owned tab. With no tab, it falls
  back to the active tab (like other tab-scoped ops). This is by design — the
  op has no selector or JS injection, so it only works on a tab with a
  committed document.
- **`--annotated` attr extraction is best-effort**: some attributes may be absent
  from the element's `attrs` object if the element doesn't have them. The list
  is a fixed set (see SKILL.md); future attributes can be added without breaking
  callers.

## Next steps (ranked)

1. **`--annotated` for frames** — the most requested follow-up. The element
   extraction logic in `protocol.js` is frame-agnostic; only the dispatch gate
   blocks it. Needs `chrome.scripting` injection into the frame context and
   a frame-aware selector resolver.
2. **`resolve` op** — given a CSS selector + optional context, return a stable
   element path. Would let agents pre-compute selectors before navigating.
3. **Deeper attr extraction** — `data-*` attributes beyond the fixed set, or
   computed styles. Low priority; the current set covers most agent use cases.
4. **`context` in the autonomous agent** — currently not in the agent's op set
   (the agent already gets page context via other means). Could be useful if
   the agent needs cheap page identification without a DOM read.

## How to verify

```bash
BB=~/workspace/devrc/scripts/browser-bridge/browser
$BB whoami                          # ORIENT FIRST — host + instances + loaded version
$BB --instance <key> context        # → {url, domain, path, searchParams, title, tabId}
$BB --instance <key> text           # → enriched envelope with domain, path, searchParams, tabId
$BB --instance <key> text --annotated  # → structured element extraction
$BB --instance <key> text --annotated --frame <id>  # → annotated elements from inside the frame
# Confirm ping shows 0.7.0:
$BB --instance <key> ping           # → extensionVersion:"0.7.0", ops includes "context"
```
