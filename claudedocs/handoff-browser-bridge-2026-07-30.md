# Handoff: browser-bridge — full agent-driveable live-Brave automation layer — 2026-07-30

## Goal
Took `scripts/browser-bridge/` from "read the active tab" to a **complete
agent-driveable browser-automation layer** over Zach's REAL, logged-in Brave
(live cookies/session, not headless). ~17 PRs this arc. **All capabilities
shipped, adversarially audited, AND live-verified on real Brave on both hosts;
clawgate task #92 complete. Feature is DONE.**

## What browser-bridge IS now (the arc)
`browser` CLI → loopback token-authed rendezvous server (`server.py`,
`127.0.0.1:8788`) ← outbound long-poll ← MV3 extension (loaded unpacked per Brave
profile) → executed against a tab → result back. Capabilities shipped this arc:

- **Read/drive ops:** `html`, cheap `text [selector]` (~98% smaller than `html`),
  `eval` (one expression, MAIN world), `tabs`, `nav`, `screenshot` (CDP
  `Page.captureScreenshot` — works on background/occluded tabs, `--fullpage`),
  `click`/`type`/`key` (TRUSTED CDP `Input.*` on the top frame), `upload` (typed
  CDP `DOM.setFileInputFiles`, audit-logged), `frames` + `--frame` (cross-origin
  OOPIF read/eval), `activate` (host i3-msg foreground), `whoami` (host identity).
- **Multi-instance:** several Brave profiles per host, each with a routing key
  (label, else stable auto-id); `browser --instance <key> <op>`; the bridge never
  guesses when >1 connected.
- **Per-session tab isolation (`open`/`close`/`release`, `--tab`):** each Claude
  session owns its own tab so concurrent sessions don't clobber a shared tab.
  Known limit: sibling subagents SHARE a session id → each must `open` + thread
  its own explicit `--tab <id>`.
- **Backpressure (#178):** per-instance token-bucket rate limit + queue cap
  guarding the single serial extension connection; over-rate → HTTP 429.
- **Metadata-only telemetry:** every command emits one `activity.events` event
  (`source=browser-bridge`, `kind=cmd`; op/key/outcome/domain — NEVER eval
  source / HTML / screenshot bytes / full URLs); surfaced in `adoption-scan`.
- **Autonomous opencode browser-agent (`browser agent "<goal>"`):** offloads
  open-ended "go read/do X, report Y" browsing onto a CHEAP model
  (opencode + DeepSeek via OpenRouter, ~$0.006/task vs ~$0.75 in Claude), in its
  OWN isolated tab, returning a compact `{answer,evidence,status}`. RCE-hardened:
  a TYPED custom tool ONLY (no raw shell/bash/CDP passthrough), own-tab-scoped,
  fail-closed runtime tool-gate before each run, non-http nav-scheme hard-denial,
  process-group kill on timeout.

## Key learnings (the durable ones)
- **Live verification against real Brave is the ONLY authoritative gate.** Green
  tests and clean security audits are prerequisites, NOT verification — CI cannot
  drive a real Brave. Driving each change against the live browser caught ~11
  defects that passing tests and audits BOTH missed.
- **i3:** Chrome-side focus (`tabs.update`/`windows.update`) is INERT under a
  tiling WM → foreground host-side via `i3-msg` (the `activate` op). Background
  tabs are Chromium-throttled → a heavy SPA renders ONLY when foregrounded (reads
  of a hidden tab self-announce `hidden:true`).
- **MV3/CDP:** `chrome.scripting` can't eval a string → CDP `Runtime.evaluate`;
  cross-origin OOPIFs need `Target.setAutoAttach({flatten})`; `captureVisibleTab`
  needs the foreground → CDP `Page.captureScreenshot` for background tabs.
- **↻ reload is UNRELIABLE** — the extension's long-poll keeps the OLD service
  worker permanently alive, so clicking reload in `brave://extensions` often
  no-ops. **A full Brave quit-and-reopen is the reliable path** for any extension
  code change. (The server, not the extension, restarts on `home-manager switch`.)
- **Threat model — "autonomous cheap model on hostile pages":** typed-ops-only
  (no shell/raw-CDP surface), own-tab-scoped, untrusted input escaped (the i3-msg
  window title is page-controlled → strict escaping, `shell=False`), and
  audit-logging for exfil-capable ops (`upload`).

## Current state
- All capabilities above shipped + **live-verified on both hosts** (workbench +
  laptop). Extension at **manifest `0.2.0`**. clawgate task #92 complete.
- Deploy model: `browser-bridge` `systemd --user` service (`nix/home.nix`) runs
  the server; the `browser` skill is provisioned as a symlink at
  `~/.claude/skills/browser/` (source of truth = `scripts/browser-bridge/`).
- Discoverability (this session's docs work): a `scripts/browser-bridge/` bullet
  added to `CLAUDE.md` Layout; SKILL.md tightened (full-restart + live-verify-gate
  learnings); the `browser-bridge` project memory consolidated; a chrome-extension
  inventory written (`claudedocs/chrome-extensions-inventory.md`).

## Operational conventions (do these)
- **`browser whoami` FIRST** on any browser task — both hosts are hostname `nixos`;
  confirm host + pick the right `--instance` before acting.
- **Full Brave restart** (not ↻) after any `extension/` code change.
- **The mandatory loop for any browser change: build → audit → fix → merge → ship
  → live-verify on real Brave.** Reproduce the exact path and LOOK at the result.
  Operate changes via a feature branch + `/audit-pr`-style review (live-cookie
  surface).

## Next-session pickup
The feature is **complete and verified** — the move is USE, not more build.
- **browser-bridge STAYS in `devrc/scripts/browser-bridge/`** (operator decision) —
  do not move/extract it; discoverability was the fix, now done.
- The chrome-extension inventory exists at
  `claudedocs/chrome-extensions-inventory.md` (cleanup candidates FLAGGED, not
  deleted — see it before any extension-repo cleanup).
- Open follow-ups (deferred, none blocking):
  - `upload` sensitive-path deny-list (upload is audit-logged but has no path
    allow/deny list yet).
  - **Nested-OOPIF `eval --frame`** — `Target.setAutoAttach` is not recursive, so
    `eval --frame` on a grandchild cross-origin iframe returns `frame_not_found`
    (fails safe); `text`/`html`/`click`/`type`/`key --frame` still reach it.
  - `browser agent` domain-deny is best-effort (can't see a page's own
    client-side redirect) — server-side post-nav URL enforcement would make it
    binding.
  - Monorepo/extension consolidation deferred (several loose extension dirs — see
    the inventory).

## How to verify (the manual gate — MV3 can't be driven headlessly)
```bash
BB=~/workspace/devrc/scripts/browser-bridge/browser
$BB whoami          # host + connected instances + loaded-vs-repo extension version
$BB health          # extension_connected:true
$BB html            # on a logged-in tab → contains logged-in-only markup
# tests: pytest scripts/browser-bridge/tests  (prerequisite, NOT verification —
#        the real gate is driving it against live Brave)
```
