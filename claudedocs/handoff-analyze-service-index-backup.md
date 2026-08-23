# Handoff: analyze-service index — backup, restore-verification, key escrow — 2026-08-23

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
Evaluate whether the `/analyze-service` index store earns its upkeep, act on the findings,
and close the one failure mode in it that is unrecoverable (no off-machine backup).

## State now — the backup loop is CLOSED

**Merged AND shipped to both hosts** (`ship.sh`, verified by content, 0 dangling / 0 stale):
- `#637` `#650` `#653` `#668` — earlier rounds (entry self-description, mutants, a
  `<<<<<<< HEAD` marker living in `main` inside a docstring, the `prune-index` skill).
- `#703` — encrypted offsite backup. Daily systemd-user timer.
- `#681` — `skill-audit.py` headroom blindness.
- `#737` — **the restore-verifier** (`scripts/analyze-service-index/restore-verify.py`),
  138 tests. Squash-merged as `592eef27`; verified by CONTENT, not ancestry.

**Not merged:** `#673` — superseded, deliberately unmerged, kept as the record.

### The three things that were open at the last handoff, and are now closed

1. 🔴 **The age key had no escrow.** DONE 2026-08-23. Escrowed into Vaultwarden as a
   Secure Note named `age.key — SOPS + analyze-service-index backups`, and **verified
   byte-identical** (`cmp` against the copy read back from the server after a sync —
   189 bytes, 3 lines, trailing newline intact). Disk loss no longer takes the key with
   the store.
2. **The timer had never fired.** It has now, unattended: `LAST = Sun 2026-08-23
   04:48:20 CDT, Result=success`. Schedule is `OnCalendar=04:30` +
   `RandomizedDelaySec=1800`, so it lands 04:30–05:00 — *not* a fixed time.
3. **Nothing verified the artifact as STORED.** `#737` does, by restoring it.

### Verified live, end to end
10 scopes, 201 commits compared, restored from the bucket → `age -d` → `git clone` →
`fsck` → cross-checked against the live store. Store byte-identical afterwards (936
files), zero remotes on all 10 scopes. The artifacts verified were the ones the TIMER
produced, not a hand-triggered run.

## 🔴 Two gotchas that will bite you immediately

**The bare command cannot see the unit's artifacts, BY DESIGN.**
```bash
restore-verify.py                                    # ALWAYS rc=1 — looks under nixos/
restore-verify.py --host workbench-<machine-id>      # the real one
```
The unit sets `ASIB_HOST=workbench-%m` (systemd expands `%m` to `/etc/machine-id`); a
hand-run has `ASIB_HOST` unset and falls back to the hostname, which is `nixos` on
**both** machines. The script now says so explicitly and names the prefix that does hold
artifacts — it will not tell you your backups are missing. **Follow-up #1 below is to fix
the asymmetry itself.**

**`git bundle verify` does NOT detect corruption.** Re-measured this session on git
2.55.0 with a real bundle, both controls:

| bundle | `git bundle verify` | `git clone` (restore) |
|---|---|---|
| 1 byte flipped mid-packfile | **rc=0**, *"records a complete history"* | **rc=128**, `inflate returned -5` |
| intact | rc=0 | rc=0 |

The intact row is the positive control — it proves `clone` *can* succeed, so rc=128
discriminates rather than a harness that always fails. Both halves are pinned by tests,
and `bundle` is absent from the script's read-only allowlist, so substituting it back
fails at the call site.

## Ranked follow-ups (none blocking)

1. 🔴 **The host-label asymmetry itself.** `backup.py --print-plan` by hand also reports
   `host: nixos`, so a hand-run of the *backup* would write a phantom second host prefix.
   Deliberately out of scope for #737 — changing labelling affects artifacts already in
   the bucket and retention pruning.
2. **Nothing runs the verifier on a schedule.** A timer is the obvious follow-up; it needs
   network *and* the age key, and the backup unit's containment took several measured
   `systemd-run --user` rounds. Not attempted rather than claimed.
3. **F5** — a wrong/absent `--store` exits **0** with everything "self-consistency only".
   Exit code is all a timer reads, so close this *before* (2).
4. **F4** — a structurally truncated artifact (1 of 40 commits) verifies green;
   `--max-lag-days` reads the key stamp, never the content.
5. **F9** — no SIGTERM handling around the plaintext window (Python runs no `finally` on
   SIGTERM). Matters precisely because (2) is a timer.
6. **F6** — "no local store" is printed for a store that exists but has no scope repos.
7. **F7** — `MinioDownloader` inherits `put()`/`remove()` from the producer, unrefused.
8. **F8** — `kubectl port-forward` leaks if `MinioArchive.__enter__` itself times out
   (pre-existing; `backup.py:646` is identical).
9. **B-10** — `uncovered_local_scopes`'s `>` vs `>=` boundary is a **known-unpinned
   boundary**, same shape as the staleness one that WAS closed. Labelled, not hidden.
10. B-5, B-7, B-8, B-9 — cosmetic; listed in the #737 body.
11. **Run `prune-index` against the store.** Built, shipped, validated, and **still never
    used for its purpose.** Last verdict: 5 entries over the 12,288 B hard cap, 11 over
    target, 32 evictable `RESOLVED`, 1 `NO HOME`, 22 broken pointers, 5 scopes with no
    README, 31 OPEN bullets protected.
12. Second A/B against a doc-poor repo (tests whether "selection, not knowledge"
    generalises past n=1).

## The A/B result — what the index is actually worth
Controlled A/B on `datapacket-talos/storage-resolver`, pre-registered 10-question answer
key, both arms on a clean `origin/trunk` worktree.
- **~90% of the entry is recoverable from the repo itself.** Only `kubectl wait -l
  app=storage-resolver` hangs was index-only.
- The control arm **matched or beat** the index arm on 6 of 10 questions.
- **The index's one clean win was recency-ordered SELECTION**: the control confidently
  asserted the pre-2026-08-20 auth control (`401`) because it read the 08-19 docs and
  stopped. The correction (`403 SignatureDoesNotMatch`) is in the repo, in two 08-20 docs
  it never opened.
- Cost: 25 vs 26 tool calls, 123k vs 148k tokens (~17% saved). Not a step change.
- 🔴 **n=1.** One subsystem in an unusually doc-rich repo.
- **My own answer key was wrong in two places** — it repeated the entry's incomplete
  `CLEANED=` advice. The artifact under test corrupted the instrument measuring it.

## Still open — the fixture-wipe incident
Diagnosed and repaired, NOT closed.
- **Mechanism, reproduced on git 2.55.0:** `GIT_DIR=<victim>/.git git -C <innocent> branch
  -m PWNED` renames the **victim's** branch. **`GIT_DIR` silently overrides an explicit
  `git -C`.** Every fixture binds `-C`/`cwd=` correctly — audited twice — and that
  property confers no safety.
- **`git rev-parse --git-common-dir` from a linked worktree resolves to the real clone's
  `.git`. A worktree is not containment.**
- **Isolation means a standalone clone with `origin` removed.** The single most useful
  sentence from the whole incident, and it was used all session with zero damage.
- **Still open:** sessions keep starting tier runs in trees that share the base clone.
- Probe: `for p in $(pgrep -f 'run-tests.sh|gate.sh'); do git -C "$(readlink /proc/$p/cwd)"
  rev-parse --path-format=absolute --git-common-dir; done` — anything equal to
  `~/workspace/devrc/.git` is the hazard.

## Lessons from #737 worth keeping (four rounds, three carried the next defect)

- 🔴 **The suite is hermetic BY CONSTRUCTION and therefore blind to environment binding.**
  Every test injects `this_host`/`this_machine_id`, so `main()`'s *derivation* of them is
  never exercised — and that is exactly where two consecutive fixes were wrong. Both left
  the suite fully green while silently suppressing all 10 cross-checks at rc=0. **Only the
  live run caught them.** Now closed by two in-process `main()` tests, each built so only
  one half of the predicate can pass it.
- 🔴 **A substring assertion cannot tell a true message from a confident wrong one.** A
  `--keep-work-dir` test asserted `"PLAINTEXT history" in stderr` and **passed**, certifying
  a sentence that was false on the very run it tested. Pin kind and count, not a phrase.
- 🔴 **An EMPTY RESULT cannot distinguish two mechanisms.** The empty-prefix message
  enumerated three causes and omitted the real one; the exit-**0** variant said *"this host
  has never run the backup"* while every artifact sat one prefix over — a green all-clear at
  the worst possible moment. Name the rival mechanism, or say you have not diagnosed it.
- **Bound your re-gating.** `main` moved 9 commits during one round. Gate once against a
  **named SHA**, then judge whether what landed can *interact*; do not chase the tip. A gate
  result is always a claim about the tree it ran on.
- 🔴 **`ProtectHome = "read-only"` makes `$HOME` READABLE**, not inaccessible. Fixed to
  `tmpfs` in #703; measured.

## Repo/CI facts re-measured 2026-08-23 (these churn — re-verify, don't trust this doc)
- `required_status_checks.contexts = ["tekton/devrc-nodetests"]`, `enforce_admins: true`,
  **0 rulesets**. So one check now BLOCKS a merge — but it is the **node** tier.
  `devrc-pytests` is **not** required, meaning the only blocking check cannot see a line of
  a Python-only PR. **Run the gate yourself and say which command.**
- A repo-local `core.hooksPath` reappeared mid-session pointing at `githooks/` (the #322
  precondition). Re-measure `git config --local --get core.hooksPath` at the moment you act.
- Pushes died `SIGPIPE 141` twice *after* the pre-push hook printed `RESULT: PASS`, remote
  unchanged both times — caught only by `git ls-remote`, not by the ✅.
- ~130 stale agent worktrees are accumulating under `.claude/worktrees/` and `/tmp`. Some
  may hold unpushed work; **diff before removing any.**

## How to verify
```bash
# the restore path, for real (10 scopes, from the bucket)
nix-shell -p 'python3.withPackages(p:[p.minio])' --run \
  "python3 ~/workspace/devrc/scripts/analyze-service-index/restore-verify.py \
   --host workbench-\$(cat /etc/machine-id)"

# the timer actually fired
systemctl --user list-timers analyze-service-index-backup.timer --all
systemctl --user show analyze-service-index-backup.service -p Result

# the index store's own verdict
python3 ~/workspace/devrc/scripts/subsystem-audit.py

# nobody is running the tier against the real clone
for p in $(pgrep -f 'run-tests.sh|gate.sh'); do
  git -C "$(readlink /proc/$p/cwd)" rev-parse --path-format=absolute --git-common-dir
done   # none may equal ~/workspace/devrc/.git
```

## The one link still untested
The escrowed key was verified through the `bw` CLI **on this machine**. In a real disaster
you would read that note from the **web vault on another device** and paste it into a file —
a path that can silently mangle whitespace. Worth doing once at leisure: open the note at
`https://vw.zacx.dev`, paste it into a scratch file, confirm 189 bytes / 3 lines.

⚠ The `bw` CLI now points at `https://vw.zacx.dev`, not `vault.homelab.lan` — that LAN
endpoint serves a cert-manager **self-signed placeholder** (`CN=selfsigned-ca`, empty
subject) that Node rejects outright (`UNABLE_TO_VERIFY_LEAF_SIGNATURE`). Fixing the LAN
cert is separate homelab breakage, unrelated to this work.
