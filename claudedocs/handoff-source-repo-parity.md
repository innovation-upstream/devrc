# Handoff: source-repo parity (rc 17 / rc 18) — 2026-08-18

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo /home/zach/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

Sibling doc, different topic: `handoff-drift-deadman-notification-delivery.md` (2026-08-12) covers
the deadman's *alert delivery*. This one covers *source-repo parity*, a check it did not have.

## Goal
The laptop shipped a `clawgatectl` binary with two subcommands missing, wearing the version string of
one that had them. Find why, then make the class detectable rather than patching the instance.

## State now

**Root cause (measured, not inferred).** devrc builds some `nix/pkgs/**` derivations from a **local
working tree of another repo** (`${workspace}/…`). Two repos, two delivery mechanisms, only one
automatic:
- the **version literal** lived in devrc (`clawgatectl.nix`), stamped via `ldflags -X main.buildVersion`
- the **code** lives in homelab-talos, read live off disk
- `ship.sh` is scoped to `$HOME/workspace/devrc` (line 176) and **nothing converges the source repos**

Timeline, 4 minutes wide:
```
2026-08-14 17:44  homelab-infra 978c549c (#323)  added task status + task comment,
                                                 set client.go buildVersion = "0.7.95"
2026-08-14 17:48  devrc 1b790b3 (#483)           bumped the nix literal 0.7.87 -> 0.7.95
```
The laptop's checkout was frozen at `6e9055f7` (08-13), 24 commits behind. Its `client.go` **correctly
said `0.7.87`** and the nix ldflag **overwrote that truth with `0.7.95`**. Result:
`clawgatectl task status <id> in_progress` printed help and **exited 0** — a silent no-op against a
ritual the `clawgate` skill makes mandatory. `drift-check` was fully green on that laptop throughout.

**Shipped, deployed to both hosts, verified live:**

| PR | what |
|---|---|
| devrc #536 | `clawgatectl.nix` reads its version out of the compiled source (never a literal; unparseable ⇒ no binary, not a failed switch). `drift-check.sh` **rc 17** = a package's `srcDir` **subtree** is behind/ahead its own upstream, per host. |
| devrc #542 | **rc 18** — a scope that stays UNMEASURED escalates after N consecutive runs (rc 13 ladder). `NOUPSTREAM`/`NOCOUNT` structural (default 4), `FETCHFAILED` transient (default 12), `ABSENT` never. |
| devrc #539 | `clawgate` skill: the row warned of *absence*; the real failure was a *present* binary that lied. |
| tmux-fuzzyclaw #1 | `d665323` docs commit that existed on zero remotes. |
| tmux-fuzzyclaw #2 | `6f11533` — 1,493 lines that lived on one disk for 4.5 months. |

Both devrc hosts at `85cf466`, switched, neither skipped. `drift-check` measures under systemd
`--user` (the ssh-agent concern did **not** materialise). All four built-source scopes across both
hosts read CURRENT: `hosts-reporting=2 scopes=4 unmeasured=0 escalated=0`.

**First genuine catch:** after merging tmux-fuzzyclaw #1/#2, rc 17 fired because those merges left the
laptop building 2-commit-stale source. Fast-forwarded it; rc 17 cleared. The check caught a regression
created hours after it shipped.

## Open investigations — live diagnosis state

### `drift-check` exits rc 15 (host parity) — pre-existing, was masked by rc 17
- **Symptom + exact repro:** `systemctl --user start drift-check` → `ExecMainStatus=15`, unit `failed`,
  `OnFailure` toast fires. It was present all session, outranked by rc 17 (`severity()` 67 vs 35), and
  became the verdict only once source drift cleared.
- **Observed (with values):**
  ```
  [parity] DRIFT — settings.json top-level KEY SETS differ (names only; no values shown):
  [parity]   only on workbench: extraKnownMarketplaces
  [parity] DRIFT — enabledPlugins differ:
  [parity]   enabled only on workbench: cloudflare@cloudflare
  [workbench] FACT installed-plugins cloudflare@cloudflare gopls-lsp@… hytale-modding@hytale-modding-marketplace pyright-lsp@… typescript-lsp@…
  [laptop]    FACT installed-plugins gopls-lsp@… pyright-lsp@… typescript-lsp@…
  ```
  `hytale-modding@hytale-modding-marketplace` is **installed on workbench, enabled nowhere**.
- **Ruled out:** not source drift (all 4 scopes CURRENT, `stale=0 unmeasured=0`); not a regression from
  #536/#542 (rc 15 predates both — it is the existing host-parity check, untouched by this work); not
  the allowlisted per-host keys (`theme`, `effortLevel`, `voice` print as `IGNORED`, correctly).
- **Leading hypothesis:** genuine, intentional divergence — Cloudflare tooling installed on the
  workbench only. This is a **preference question, not a defect**, which is why it was left alone.
- **Next probe:** decide, don't measure. Either `claude plugin install cloudflare` on the laptop, or add
  `extraKnownMarketplaces` + that plugin to the parity allowlist in `scripts/drift-check.sh` with the
  reason inline (the allowlist is an **enumeration, not a pattern** — unknown keys are drift by default).
  🔴 Until resolved the unit fails and toasts on every 6-hourly run.

### `~/.local/bin/fuzzyclaw` shadows the nix-deployed binary — decision not taken
- **Symptom + exact repro:** `command -v fuzzyclaw` → `/home/zach/.local/bin/fuzzyclaw`, **not**
  `~/.nix-profile/bin/fuzzyclaw`.
- **Observed (with values):**
  ```
  /home/zach/.local/bin/fuzzyclaw        built 2026-03-30 22:21:32   10 commands, NO statusline   reports "dev"
  /home/zach/.nix-profile/bin/fuzzyclaw  -> /nix/store/whxg223k…-tmux-fuzzyclaw-2.0.0
                                                                     11 commands, HAS statusline  reports "2.0.0"
  ```
  Build date `2026-03-30` is the same date as the oldest edit in the rescued work.
- **Ruled out:** devrc's ldflag is **not** inert — a control build with devrc's exact
  `-X …/cmd.Version=2.0.0` prints `2.0.0`, and the store binary prints `2.0.0` when invoked by full
  path. An earlier reading of `dev` was the *shadowing copy*, not the nix one.
- **Leading hypothesis:** hand-installed once in March per the README's own
  "`go build` → `cp fuzzyclaw ~/.local/bin/`" instructions, then never rebuilt. Consequence: **every
  feature in tmux-fuzzyclaw #2 has never executed on this machine.**
- **Next probe:** none needed — it is a decision. `rm ~/.local/bin/fuzzyclaw` makes the nix-managed
  binary take effect and starts running 4.5 months of unexercised code. Deliberately left to the operator.

## Next steps (ranked)
1. **Resolve rc 15** — install the plugin on the laptop, or allowlist with a stated reason. The unit
   fails and toasts every 6h until then.
2. **Review tmux-fuzzyclaw #2's content.** It merged on compile-time evidence only (build/vet/test/help
   all rc 0). Nobody has read the 1,493 lines, and nothing has exercised them.
3. **Decide on `~/.local/bin/fuzzyclaw`** (above). Do this *after* 2.
4. Consider whether `test_clawgatectl_version.py`'s pin should also fire when `tmux-fuzzyclaw`'s
   `cmd/version.go` grows a real version — that is the documented trigger to apply #536's fix there.

## Gotchas / decisions / dead-ends
- **rc 17 escalating on WHOLE-REPO staleness was wrong and was fixed before merge.** Measured: the
  workbench sat 18 commits behind with **0** touching `containers/clawgate`; over 14 days that repo took
  98 commits of which only 32 could reach any built artefact. Whole-repo would have been a
  permanently-red gate. The verdict is a **pathspec-limited** count against the branch's **own** upstream;
  repo-wide numbers print beside it as information.
- **Do NOT append a fingerprint to `buildVersion`.** `client.go:277` compares `h.Version == buildVersion`
  by exact string equality — any suffix fires the skew note on every command forever.
- **`ship.sh` converging source repos was considered and REJECTED.** Both source repos are routinely
  dirty (14 and 20 paths), so an ff-only pass would skip both and would not have prevented the incident.
  Detection over convergence; keep `ship.sh` narrow.
- **`tmux-fuzzyclaw.nix`'s `version = "2.0.0"` literal is deliberate**, not the same bug. `cmd/version.go`
  has only `var Version = "dev"`, "2.0.0" appears nowhere in that repo, no git tags — no source of truth
  to read. `test_clawgatectl_version.py` pins that finding so it fails the day one appears.
- **`--delete-branch` on a stacked parent would have destroyed tmux-fuzzyclaw #2.** Merged #1 without it,
  confirmed #2 still `OPEN`, then retargeted #2 to `main`. After the squash, #2 showed both commits;
  verified `git diff origin/main origin/rescue/… -- CLAUDE.md` was **empty** before merging.
- **After `--delete-branch`, the local checkout was left on a branch with no upstream** — which reads as
  UNMEASURED and, with rc 18 live, would have escalated in ~4 runs. Moved it back to `main`.
  🔴 That checkout **is devrc's build input**; never leave it on a deleted or upstream-less branch.
- **Two instruments lied and were discarded, not their readings:** `strings <binary> | grep -cE '^2\.0\.0$'`
  returned 0 for a binary *known* to print `2.0.0`; `grep -c 'footgun'` returned 0 on a file whose
  case-insensitive match count is 12. Both caught by running a positive control first.

## How to verify
```bash
# 1. the original failing path — must hit the wire, not exit 0 silently
ssh zach@10.42.0.100 'clawgatectl task status 999999 in_progress'   # expect rc 4, "not found"

# 2. both hosts expose the same command surface
diff <(clawgatectl task --help) <(ssh zach@10.42.0.100 'clawgatectl task --help')

# 3. the deadman, as a deployed unit (NOT ./scripts/drift-check.sh from a worktree)
systemctl --user start drift-check
journalctl --user -u drift-check -n 60 -o cat | grep -E 'srcblind|BUILT SOURCE'
# expect: hosts-reporting=2 scopes=4 unmeasured=0 escalated=0   <- zero over a REAL denominator
```
🔴 A `scopes=0` or `hosts-reporting=0` triple is withheld by design and prints `NOT EVALUATED` instead —
if you see a clean-looking zero with no denominator, the check measured nothing.
