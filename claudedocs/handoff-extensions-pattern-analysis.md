# Handoff: extensions-pattern-analysis — 2026-08-27

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

⚠ Measured 2026-08-27: the `devrc` scope holds 3 entries (`signal`, `obs`, `testlib`)
and **nothing on extensions**; `--search 'extension deploy'` returns no match.

## Goal
Verify the 2026-08-26 extensions-deployment analysis against the live `nix/home.nix`
activation blocks. Done. The verification is finished and the doc carrying it was
**rejected**; what survives as work is one live source-side defect it uncovered.

## State now
- Branch / PR: **PR #903 CLOSED WITHOUT MERGE** (2026-08-27T05:16:08Z, by ZacxDev),
  reason given on the PR: *"Analysis corrections doc. The discord-embed-ext work is
  complete — this document adds no actionable code."* Not merged: `mergedAt=null`,
  `mergeCommit=null`, and `claudedocs/handoff-extensions-pattern-analysis.md` is
  **absent from `origin/main`** (verified by content, not by ancestry).
- The work is **not lost**: branch `docs/handoff-extensions-pattern-analysis` still
  exists on origin at `973ddf90`, 10 commits, one 401-line file. Local worktree at
  `~/workspace/devrc-extensions-handoff`.
- `claim-work extensions-pattern-analysis-1` — **RELEASED**.
- Nothing deployed. No `home-manager switch` was run at any point this session.
- ⚠ The analysis itself was never in `claudedocs`: the 2026-08-26 session left it at
  `/tmp/opencode/handoff/extensions-pattern-analysis.md`, and `/resume` was pointed at
  a `claudedocs/` path that **has never existed** in this repo's history.

### Verified against source (all re-derived at `origin/main`, `nix/home.nix` 3,873 lines)
The three-tier architecture is substantially right. Six original claims were not:
- 🔴 **Tier 1 is two mechanisms, not one.** `mv -T --exchange` is browser-bridge ONLY.
  `home.nix:759`: *"This copy is deliberately weaker than browser-bridge's: no
  `--exchange` two-attempt TOCTOU dance."* discord-embed-ext moves the old tree aside
  then moves the new one in, so **its target is briefly absent by design**.
- `force = true`: **9** assignments, not 14 (14 counts *mentions*; all 5 comment
  mentions say it is absent or insufficient). `grep -cE '^\s*force = true;'`.
- `reference/`: 12 `.md` + a `sites/` dir (13 entries, 14 tracked), not 15 files.
- `claim-work` is not a skill — one `mkOutOfStoreSymlink` to `~/.local/bin/claim-work`
  (`home.nix:1207`; 1206 is the `home.file` key).
- browser-bridge block is **594-746**, not 594-739. discord is 773-857.
- `rm -df` does not appear in `home.nix`; the command is `rm -rf`.

### Gate results — per tier, because they disagreed
| tier | result |
|---|---|
| sandbox `nix build .#checks…` (the tier Tekton gates on) | PASS |
| `tekton/devrc-pytests` + `tekton/devrc-nodetests` on #903 | both **success** |
| node, dev host | PASS — 39 files, 1292 tests, floor 1239 |
| merged-tree doc/content gates (integration branch off main) | PASS — 480 tests |
| pytest, dev host | 17,220/17,225 — **3 failures, PRE-EXISTING** |

🔴 The 3 failures are **not attributable to any change made here** — control run on an
unmodified `origin/main` worktree reproduced 2 of 3 identically
(`test_ordinary_growth_needs_no_hand_edit`, `test_the_collapse_guard_is_what_makes_
that_test_red`); the third, `test_a_green_real_run_says_pass_with_exit_zero`, passes in
isolation and is parallelism-dependent. An open `fix/unbreak-pytests-gate` worktree
exists on this box independently.

## Open investigations — live diagnosis state

### browser-bridge `chmod -R` can rewrite a symlink target's modes — two unguarded sites
**This is the only finding of the session that touches live behaviour rather than prose,
and it is the reason not to let the closed PR be the end of it.**

- **Symptom + exact repro:** with a symlink at
  `~/.local/share/browser-bridge-ext.new.<pid>` or `.old.<pid>` (the sweep glob at
  `home.nix:613` covers **both**), a `home-manager switch` rewrites the modes of
  whatever that symlink points at. Reproduce without a switch by running the loop body
  against a fixture:
  ```bash
  S=$(mktemp -d); mkdir -p "$S/victim"; : > "$S/victim/f"
  chmod 555 "$S/victim"; chmod 444 "$S/victim/f"
  mkdir -p "$S/dst-parent"; ln -s "$S/victim" "$S/dst-parent/ext.old.999999"
  stat -c %a "$S/victim" "$S/victim/f"          # 555 444
  chmod -R u+rwX "$S/dst-parent/ext.old.999999" 2>/dev/null || true
  stat -c %a "$S/victim" "$S/victim/f"          # 755 644   <-- the defect
  rm -rf "$S"
  ```
- **Observed (with values):** target directory `555 → 755`, its file `444 → 644`.
  Byte-for-byte the signature `home.nix:780-786` already documents 🔴 **for the discord
  block**, which guards against it and browser-bridge does not.
- **The two sites**, both `2>/dev/null || true` so the damage is silent:
  - `home.nix:623` `chmod -R u+rwX "$bbOld"` (in the sweep) — `[ -e "$bbOld" ]` at 614
    **follows** a symlink, so it is swept rather than skipped.
  - `home.nix:627` `chmod -R u+rwX "$bbTmp"` (before the `rm -rf` at 628) — a
    *pre-existing* `$bbTmp` is skipped by the `$$` test at **620** and routes straight
    here; 627 runs **before** 628, so the chmod fires first.
- **Contrast:** `deeScrub` (`home.nix:787-792`) diverts symlinks at 789
  (`if [ -L "$1" ]; then rm -f "$1"; return 0; fi`) before ever chmod-ing.
  browser-bridge's only `[ ! -L ]` is the **destination** test at 662 — that is one
  site, not a property of the block.
- **Ruled out:**
  - *A third unguarded site.* browser-bridge has five `chmod -R` (623, 627, 630, 685,
    744); 630 follows `cp -rL` at 629, 685's operand comes from the `mv` at 678 whose
    source passed 662, 744's is block-owned. **Exactly 623 and 627.**
  - *The `rm -rf` compounding it.* Both following `rm -rf` calls take the **link**, not
    the target (no trailing slash). The mode rewrite is the whole of the damage.
  - *Reachable by ordinary operation.* No. 662's `[ -d ] && [ ! -L ]` means the `mv` at
    678 only ever produces a directory, and `cp -rL` at 629 likewise. **Needs an
    operator artefact — latent, not live.**
- **Leading hypothesis:** an oversight, not a deliberate asymmetry. The sweep
  commentary at 601-612 enumerates its accepted limits (a reused pid, a non-numeric
  suffix) and this is not among them.
- **Next probe:** none needed — it is measured. The open question is the *remedy*, and
  it is a decision, not an observation: add `deeScrub`'s `[ -L ]` diversion at both
  sites, or document the limit at both. **Whichever is chosen must cover 623 AND 627** —
  601-612 documents the sweep only, so a comment added there does not reach 627.

### Also unexplained in source: the own-pid sweep asymmetry
browser-bridge spares its own pid (`home.nix:620`); discord **deliberately does not**,
under a 🔴 comment at 806-812 warning that sparing it makes `mv -T` fail with
`Directory not empty` and abort the switch. discord's stated reason — *"We create ours
further down, after this sweep"* — applies verbatim to browser-bridge, whose `bbTmp` is
created at 629, after the sweep at 613-625. **One side is explained; the other is not.**
Establish which before extracting any shared helper.

## Next steps (ranked)
1. **Fix the `chmod -R` symlink gap at `nix/home.nix:623` and `:627`** —
   repo `devrc`, file `nix/home.nix`, plus a test. Add `deeScrub`'s `[ -L ]` diversion
   at both sites. 🔴 Live hardened deploy path: needs a `home-manager switch` to verify,
   so the blast radius is both hosts. **Asked and unanswered as of this handoff** — the
   user was offered this vs. a one-line issue and the session ended before a reply.
2. **If (1) is declined, file it as a GitHub issue instead** so the measurement outlives
   branch `docs/handoff-extensions-pattern-analysis` — repo `devrc`, no files touched.
3. **Decide what happens to branch `docs/handoff-extensions-pattern-analysis`**
   (`973ddf90`, still on origin). Options: leave it, cherry-pick only the
   `## Open` source findings somewhere durable, or delete it. Deleting loses the only
   written record of the six corrections above beyond this doc.
4. **Do NOT extract the shared deploy helper.** Recorded as a decision, not a task —
   see Gotchas.

## Gotchas / decisions / dead-ends
- 🔴 **The audit ladder is the story of this session.** Nine rounds via `/audit-pr`.
  Rounds 1-8 each found something, and **five of those were defects introduced by my own
  previous fix round**. Round 9 came back clean, which ended it — a clean round is the
  stop condition and no round 10 was run to confirm it.
  - R1: the correction doc reproduced the exact error class it was written to correct.
  - R2: the R1 retraction over-corrected into a new false absolute. **Measured:
    `rm -rf <link>/` — with a trailing slash — DOES follow the link and empties the
    target, rc=0, silent.** Without the slash it takes the link. `rm -f <link>/` fails
    loudly (`Is a directory`, rc=1, nothing removed), so the silent case is `-r`-specific.
  - R7: the doc simultaneously asserted the `[ ! -L ]` guard existed and that it was
    missing.
  - R8: a markdown **lazy continuation** put a "must cover BOTH sites" closing condition
    *inside* the one-site bullet. Invisible in source, visible only when rendered.
- **A pronoun near a list is unsafe in a long technical doc** — the "stolen antecedent"
  defect recurred four separate times (R4, R5, R7, R8). Repeat the enumeration instead
  of writing "those three".
- **Don't extract a shared deploy helper for the two extensions.** `home.nix:754-758`
  sets the trigger at a *third* extension, and there isn't one. More importantly the two
  blocks diverge in the swap **and** the own-pid policy, and only one side of the latter
  is explained — unifying them today erases a distinction nobody has justified.
- **Don't `/analyze-service` an extensions entry into the subsystem index** for this.
  Deliberate call: an index entry is a second copy to keep true, and this session is a
  demonstration of what that costs.
- ⚠ `gh pr view --json merged` is not a field; use `mergedAt`/`mergeCommit`. And a
  squash merge never makes the branch head an ancestor of main — verify by **content**.

## How to verify
```bash
R=~/workspace/devrc
# the corrections, cheapest first
git -C $R diff --stat HEAD origin/main -- nix/home.nix   # empty ⇒ tree copy authoritative
grep -cE '^\s*force = true;' $R/nix/home.nix             # 9, not 14
ls $R/scripts/browser-bridge/reference/*.md | wc -l      # 12
grep -n -- '--exchange' $R/nix/home.nix                  # browser-bridge only (566/650/669) + 759

# the open defect — the fixture repro is in Open investigations above; these are the sites
sed -n '623p;627p' $R/nix/home.nix                       # two unguarded chmod -R
sed -n '789p'      $R/nix/home.nix                       # deeScrub's guard, for contrast
```
🔴 Nothing in this session was verified under a real `home-manager switch`. The deploy
paths' behaviour is asserted by in-source measurement comments, not re-measured — a
weaker claim than "verified", stated deliberately.
