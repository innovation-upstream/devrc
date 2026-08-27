---
# No clawgate-task — $CLAUDE_CODE_SESSION_ID was unset; the board was not asked.
---
# Handoff: browser-bridge-architecture-trace — 2026-08-27

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Trace and document the browser-bridge extension's full architecture (three-actor command channel) and the devrc skill+flows pattern (SKILL.md → reference/ → flows/) as observed in the codebase. Research-only session, no code changes.

## State now
- Branch: `main`, clean working tree, no uncommitted changes
- No PRs opened or modified this session
- No deploys or builds in flight
- Clawgate task: **not resolved** (no session ID)

### What was produced
- Complete architecture trace of browser-bridge: the three actors (CLI → server.py → MV3 extension), the HTTP long-poll transport, multi-instance routing, security model, op set (18 ops), deployment chain (home-manager atomic swap to `~/.local/share/browser-bridge-ext/`), and the build-marker freshness mechanism (#324)
- Identification of the skill+flows pattern: the three-layer SKILL.md (always-on routing surface, ~1% context budget) → reference/ (durable FACTS, 0 cost until triggered) → flows/ (PROCEDURES, never auto-fire, named by hooks or SKILL.md table rows)
- Concrete mapping of how `browser` implements this: `SKILL.md` + 13 reference files (including `sites/` sub-registry) + no flows/ (browser ops are direct, not procedural)
- Contrast with `clawgate` which has both reference/ (11 files) and flows/ (2 files: `task-authoring.md`, `task-pickup.md`)

### Key files traced
| File | Role |
|---|---|
| `scripts/browser-bridge/browser` | CLI entrypoint (bash, 2325 lines) |
| `scripts/browser-bridge/server.py` | Loopback rendezvous server (Python stdlib, 3592 lines, port 8788) |
| `scripts/browser-bridge/extension/service_worker.js` | MV3 background worker (chrome.* glue) |
| `scripts/browser-bridge/extension/protocol.js` | Pure protocol logic (testable with node --test, no chrome.*) |
| `scripts/browser-bridge/extension/manifest.json` | MV3 manifest, version 0.8.1 |
| `scripts/browser-bridge/extension/build_id.js` | Generated BUILD_MARKER literal (#324) |
| `scripts/browser-bridge/extension/options.js` | Persist port/token/label to chrome.storage.local |
| `scripts/browser-bridge/opencode/tools/browser_tool_impl.mjs` | opencode browser-agent's typed tool (no shell, RCE fix from PR #180) |
| `scripts/browser-bridge/SKILL.md` | Always-on skill routing surface |
| `scripts/browser-bridge/reference/*.md` | 13 durable-fact files (spa-wake, auth-pages, emulation, etc.) |
| `scripts/browser-bridge/reference/sites/_index.json` | Per-host registry (host-suffix matching, longest-wins) |
| `nix/home.nix:526-745` | Extension deployment (atomic dir exchange) |
| `nix/home.nix:2389-2421` | systemd user service for server.py |

## Open investigations — live diagnosis state
(No unresolved investigations — this was a read-only research session.)

## Next steps (ranked)
1. No action items — this was a documentation/understanding session. The traces produced are reference material, not work items.

## Gotchas / decisions / dead-ends
- The `reference/` vs `flows/` distinction is defined in `CLAUDE.md:127-131`: reference holds facts you verify against; flows holds procedures you execute. A flows file does not auto-fire — something must name it (a SKILL.md table row, or a hook that names the path).
- browser-bridge has no `flows/` directory because its ops are direct (command → result), not procedural multi-step workflows. clawgate has flows because task authoring and pickup are multi-phase procedures enforced by hooks.
- The `sites/` sub-registry under `reference/` is a special case: `_index.json` maps host suffixes to filenames, matched on label boundaries (not substring), longest-wins. The server emits `site_notes` on matching result envelopes.
- The BUILD_MARKER (`build_id.js`) is a generated literal, not a runtime computation — the only signal that describes running code rather than load directory. Two profiles on one directory can report identical version/id while running different code (#324).

## How to verify
No verification needed — this was a read-only research session with no code changes.
