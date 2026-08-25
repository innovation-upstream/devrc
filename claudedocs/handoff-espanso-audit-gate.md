# Handoff: espanso-audit-gate — 2026-08-21

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Run `/espanso-audit`, tune the snippets from measured usage — and, after the audit's own
advice turned out to be built on a false premise, make the correction enforceable rather
than written down.

## State now
- Branch: `main`, clean, HEAD == `origin/main`. **Nothing in flight.**
- **Three PRs merged and on `main`:**
  - **#592** `d5657c2` — espanso snippets. `:pdt` `:cgt` added, the four `:ssh*` relabelled,
    `("ask", ":acq")` pinned. **Deployed to workbench + laptop**, both resolving the same
    store path (`xszax4l0…-base.yml`), `espanso` + `keylog.service` active on both.
  - **#603** `1fd04d8` — `CLAUDE.md` claimed "CI gates both suites". There is no CI. Replaced
    with a machine-checked `<!-- merge-gate: none -->` marker +
    `scripts/tests/test_ci_claim_matches_reality.py`.
  - **#641** `f8fbdf0e` — `espanso-usage.py --gate` / `--diff-config`.
- **Verified against the real path?** The *artifact* yes, byte-identical on both hosts. The
  **keystroke path NO** — see "How to verify". Espanso erases trigger and expansion when it
  fires, so nothing in the toolchain can reach it.
- Since the merges, `main` gained `9b671a96` (Zach's `:roo` snippet). It is on `main` **and**
  in the deployed config, and `--gate` on main's config vs the deployed one is **PASS**.

## Open investigations — live diagnosis state
None. Everything opened this session was closed or merged. The single unverified claim is the
keystroke check below, which is not an investigation — it needs a human at the keyboard.

## Next steps (ranked)
1. **Type the three triggers** (30 seconds, see "How to verify"). If `Ctrl+Space` → `lap`
   still shows two rows, the central claim of #592 is wrong.
2. **Re-run `/espanso-audit`** — it acted on **3 of 42** ADD-CANDIDATES. The remaining 39 are
   the audit you actually asked for. It now runs against `--gate`.
3. Consider whether `--replay`'s ranking should weight *characters saved* × occurrences —
   `"merge it"` (28×, 8 chars) currently outranks a 48-char phrase at 3×, and that ranking is
   what led to dismissing the short candidates without measuring.

## Gotchas / decisions / dead-ends

🔴 **`--lint`'s "can never fire from the search UI" is about TELEMETRY, not espanso.** espanso
lists every match as a picker row; the user arrows to one. `_attribute` returning `None` on
≥2 matches only means the fire is logged with `trigger=None` (`_close_search`,
`espanso_detect.py:228`, emits a row either way). **This premise being read literally is what
caused everything else in this session.** The tool's own strings now say so.

🔴 **A snippet with NO label is worse off, not better** — espanso falls back to showing its
raw `replace` text as the picker row.

🔴 **FOUR fatal-rule designs for `--gate` were each walked by a one-line edit**, and the
pattern is the lesson: every one tried to infer *intent* from the config, and intent is not
in the config. Removing `nebula` from a label is byte-identical whether you retired the word
or destroyed the only way to find that snippet.
| rule | walked by |
|---|---|
| a query reaches nothing | pruning — red on the skill's own primary action |
| snippet reaches nothing at all | keeping **one** word |
| lost with no gain | adding **one junk** word |
| any magnitude threshold | staying under it |
The shipped rule stops guessing: every lost query is fatal, deliberate ones are stated with
`--accept word,word`. A prune needs none — the word went with the snippet.

🔴 **`--replay` "0 regressions" is a claim about the OBSERVED search stream**, not the config.
It replays only terms actually typed in the window. It reported 0 while a real regression was
live. Always also run `pytest scripts/collector/keylog/tests/test_espanso_detect.py`.

🔴 **`'ask'` ⊂ `'task'`.** A `:cgt` labelled "task" silently takes `:acq`'s 58-fire term.
`_token_matches` is a **substring** test over trigger + label words + search_terms, so
`'bench'` ⊂ `'workbench'` and `'la'` ⊂ `'nebula'` too. Pinned now, but the class is live.

🔴 **`.git/hooks` cannot tell you whether the pre-push gate is installed.** `githooks/` ships
a real blocking one; it installs by pointing `core.hooksPath` elsewhere. Use
`git config --get core.hooksPath`. Also: several workspace clones carry a **repo-local**
`core.hooksPath` (workbench `devrc` and `homelab-talos` do, the laptop's `devrc` does not) —
local beats global, so `githooks/install.sh` alone can be inert. Per-clone, environmental,
not a devrc setting.

**Dead ends, so they are not re-tried:** collapsing the four `:ssh*` to nebula-only (all four
endpoints are in live use, LAN ahead of nebula — `activity.events`, 13-day window: laptop-LAN
4, workbench-LAN 3, workbench-nebula 1, laptop-nebula 0); grading attribution loss as fatal
(permanently-red gate); `blinded`/all-or-nothing and lost-with-no-gain as fatal axes (both
walked, above).

**Process, measured this session:** a `[] or [...]` mutant evaluates to the non-empty list and
mutates nothing — written **three times**, scoring phantom mutants SURVIVED each time. Declare
each mutant's expected verdict and make setup failure loud.

## How to verify
```bash
# the tool, from main, against the live config — must be PASS
nix-shell -p python3Packages.pyyaml --run \
  "python3 ~/workspace/devrc/scripts/session-analysis/espanso-usage.py --gate \
   \$(readlink -f ~/.config/espanso/match/base.yml)"

# the suites
nix build .#checks.x86_64-linux.pytests --no-link    # RESULT: all good
```
**The part no command can do — type these:**
- `:sshll` → `ssh zach@192.168.50.155`; then `Ctrl+Space` → `lap` must show **ONE** row
- `Ctrl+Space` → `nebula` must still show **TWO** rows (correct, not a bug)
- `:alo` → your wording, unchanged by the reconcile
