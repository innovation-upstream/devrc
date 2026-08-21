# Handoff: prompt-optimization — CLOSED 2026-08-21 (#586 #605 #617 #621 #644)

**Status: DONE, merged, deployed to BOTH hosts, and VERIFIED BY REAL USE.**
Landed across five PRs — #586 (the contract + the clickup slim), #605 (this
doc), #617, #621 and #644 — each shipped with `scripts/ship.sh` and confirmed on
workbench and laptop.

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

## Verified by real use (2026-08-21, PR #644)

The contract has now been RUN, twice, against a public read-only target. Ground
truth read from `tool-audit.jsonl`, not the returned envelope — because the
contract itself says `steps_used` is untrustworthy:

    run 1   whoami -> nav -> wake -> text x4                        9 ops, ok
    run 2   whoami -> nav -> text x2 -> wake -> text x2 -> eval x2   9 ops, ok

- **ZERO `open`/`close`/`activate`/`upload`/`tabs` attempts** in either run, so
  the round-2 fix (rails 2 and 8 no longer instructing ops `browser agent` does
  not have) holds against a live model and not merely against a pinned string.
- **The first call was `whoami` both times**, and run 2's evidence quotes
  "whoami: personal - other instance, no extension_stale" — rail 1 steered
  behaviour including its second half. The rails are not decoration.
- **Rail 7 fired for real**: run 1's read came back `hidden`, the deterministic
  auto-wake ran (`auto_wake_ok woke:true settleMs:1500`), and the re-read had
  content.
- 0 leftover tabs in the operator's profile either time.
- `steps_used` claimed **5 against 9** real op calls BOTH times — a fresh
  reproduction of `reference/agent.md` guardrail 3.

**What first use found that 47 document-guards could not:** the template's
`INSTANCE:` line read as optional prose, I left it out, and the run died at
`browser-agent: failed to open a tab` before a single model token was spent.
Two profiles are connected, so `--instance` is required — and the wrapper forces
the tab via env BEFORE the model runs, so a profile named in the goal text can
never satisfy it. Fixed in #644 and re-verified live afterwards.

## Left open

- **The `.md` leak-gate gap is NARROWER than this doc first claimed.** The
  hostname and public-IP gates enumerate `git ls-files` with **no extension
  filter**, which is why #619's client subdomain was caught inside a `.md`.
  Only `test_no_captured_text.py` (JSON/JSONL/JSONC) and
  `test_no_captured_markup.py` (`.html`/`.txt`) are extension-limited. So the
  real gap is CAPTURED TEXT — message bodies, prompts, transcripts, or a model's
  summaries of them — in `.md`. `claudedocs/` is exactly where handoffs quoting
  real conversations land. Extending it is a design call, not a one-liner:
  `.md` is mostly legitimate prose, so the false-positive rate is the whole
  question.
- **Path-rot coverage for the two mkOutOfStoreSymlink skills.** `browser` and
  `dl-router` deploy from `scripts/`, so they sit outside
  `test_doc_path_rot.py`'s `CORPUS_DIRS = ("claude", "CLAUDE.md")` — 6,337 lines
  ungated. Extending it found exactly one real rot (fixed in #621); the
  remaining 47 hits are rule 1c firing on the bare `reference/<file>.md`
  spelling that subtree uses deliberately, 28 of them in a byte-ceilinged
  SKILL.md. Three options are laid out in #621; it needs a rule decision, and
  the gate explicitly refuses the baseline shortcut.

## Shipped

Both hosts converged and verified at each merge. The workbench skip recorded in
an earlier draft of this doc is RESOLVED — it was another session's uncommitted
`claude/skills/resume/SKILL.md`, which landed on its own; `ship.sh` then
fast-forwarded and switched both hosts.
