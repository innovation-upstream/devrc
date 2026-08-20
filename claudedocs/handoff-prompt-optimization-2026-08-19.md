# Handoff: prompt-optimization — CLOSED 2026-08-20 (PR #586 merged)

**Status: DONE and merged** (`56de7b2`, squash). Deployed to the **laptop**;
the **workbench is not deployed** — see "Left open" below.

This doc originally carried the design for two tasks. **Two of its premises were
false**, and they are recorded here so nobody re-derives them.

## What actually shipped

**1. Browser validation contract** — `scripts/browser-bridge/reference/validation-prompt.md`
(new): a fill-in template, nine standing rails, and an **inlineable** rails
block. `SKILL.md` gained one reference-table row and paid for it by eviction
(11,990 → 12,007 B; the size gate leaves ~31 B of slack, so the next row needs
its own eviction).

**2. ClickUp** — the maintainer-only half moved to `reference/maintaining.md`.
4,934 → 3,821 B (−23%).

Guarded by `scripts/browser-bridge/tests/test_validation_prompt.py` (47 tests).

## 🔴 The two false premises (do not re-derive)

1. **`claude/skills/browser/SKILL.md` does not exist.** The browser skill is an
   `mkOutOfStoreSymlink` onto `scripts/browser-bridge/SKILL.md`. Consequence:
   it goes live on `git pull`, with **no** `home-manager switch`. `reference/`
   is not deployed into `~/.claude/` at all — readers use the repo-absolute
   path.

2. **ClickUp's CLI reference tables do not exist and never have.** SKILL.md has
   carried zero command tables since #438 and already pointed at `showUsage()`.
   The "~4,800 chars of CLI docs" was the file's *total size*. The stated
   4,800 → 800 target was unreachable without deleting the non-obvious gotchas
   the same design said to keep.

Also re-measured: the motivation is **8 of 125** opencode prompts naming the
bridge / the `browser` skill / `browser agent`, 7 of them 3.0–5.6 KB — not the
"12 of 51" originally written.

## What the audits caught (5 rounds, 7 🔴)

Every one was a guard that passed while the hazard existed in a different shape:

- the rail ledger pinned each rail's **headline**, not its body — rails 3, 2 and
  7 were each inverted with a fully green suite;
- **`browser agent` cannot read the file the rails live in** (13-op browser-only
  tool surface, `bash`/`read`/`edit`/`write`/`webfetch` denied by a fail-closed
  gate), so a *cited* prompt reached it with zero rails while reading complete;
- the inline block then told it to `open`/`close` a tab — ops it does not have,
  with rail 5 saying "a failure is a finding, STOP";
- an override clause fitted on the fence's line **two** after line one was
  pinned; the op guard matched only **backticked** names, then only
  **lowercase** ones;
- the template fence was unpinned (`WRITES ALLOWED — anything you judge useful,
  including logging out:` passed green);
- a whole **new section** was invisible to every guard, because each one slices
  a section it already knows about.

Side effect worth knowing: **`reference/agent.md` said the agent has 11 ops; it
has 13.** `emulate` landed in #321 without updating that prose, and
`browser_tool.test.mjs` had pinned 13 all along. Corrected, and the test now
parses `ALLOWED_OPS_DEFAULT` from the code rather than prose about it.

## Left open

- 🔴 **The workbench is NOT deployed.** `ship.sh` correctly SKIPPED it (rc=7):
  `claude/skills/resume/SKILL.md` has ~9 lines of uncommitted work that collides
  with incoming commit `92e06db`. That work is intact and untouched; a safety
  copy is in this session's scratchpad. To finish: commit or set aside that
  file on the workbench, then re-run `scripts/ship.sh`. **Do not stash** — the
  stash is repo-global.
- **Never exercised on a real browser validation run.** The contract is tested
  as a document, not as a prompt. First real use is the verification.
- Repo-wide gap (not this PR's): the content-leak gates cover JSON/JSONL/JSONC
  and `.html`/`.txt`, but **not `.md`**, so the two new markdown files landed
  under no automated leak gate. Both were read manually and are clean.

## This file is UNTRACKED

It was never committed. Commit it or delete it — do not leave it for a
`git clean` to decide.
