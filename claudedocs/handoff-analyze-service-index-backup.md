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
- `#673` — **MERGED** (`4dd14e68`), GUARD 9 / `scripts/testlib/nogit_plugin.py`. It is in
  `origin/main` and its banner prints on every pytest run
  (`gitenv(session) … mode=enforce(auto)`). 🔴 An earlier revision of this doc called it
  "deliberately unmerged" — that was true when first written and false ~28 h later. It is
  the load-bearing safety fact behind the fixture-wipe section below, so check it by
  content (`git log --diff-filter=A -- scripts/testlib/nogit_plugin.py`), never from prose.

**Closed WITHOUT merging:** `#689`, `#676` — superseded, kept as the record.

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
`fsck` → cross-checked against the live store. Store **byte-identical afterwards** — a
sha256 over a `path size mtime` manifest, identical before and after a full run — and zero
remotes on all 10 scopes. The artifacts verified were the ones the **TIMER** produced
(object stamp `20260823T094821Z`, one second after its `LastTrigger`), not a hand-triggered
run. The commit count and the store's file count both advance hourly; the *byte-identity*
and *zero-remotes* claims are the durable ones.

## 🔴 Two gotchas that will bite you immediately

**The bare command cannot see the unit's artifacts, BY DESIGN.**
```bash
restore-verify.py                                    # ALWAYS rc=1 — looks under nixos/
restore-verify.py --host workbench-<machine-id>      # the real one
```
The unit sets `ASIB_HOST=workbench-%m` (systemd expands `%m` to `/etc/machine-id`). A
hand-run resolves `host_label()` through `ASIB_HOST` → **`ACTIVITY_HOST`** →
`socket.gethostname()` — and the hostname is `nixos` on **both** machines (measured on
each, not inferred from one). So with neither env var set it always lands on a
permanently-empty prefix; "ALWAYS rc=1" is a claim about that clean environment, not an
absolute. The script now says so explicitly and names the prefix that does hold artifacts —
it will not tell you your backups are missing. **Follow-up #1 below is to fix the asymmetry
itself.**

**`git bundle verify` does NOT detect corruption.** Re-measured this session on git
2.55.0 with a real bundle, both controls:

| bundle | `git bundle verify` | `git clone` (restore) |
|---|---|---|
| 1 byte flipped mid-packfile | **rc=0**, *"records a complete history"* | **rc=128**, `error: index-pack died` |
| intact | rc=0 | rc=0 |

Pin `index-pack died`, **not** the `inflate returned -N` detail: flipping a byte at 10
different mid-packfile offsets gave `index-pack died` at 10/10 but `-5` at only 2/10
(`-3` at 8/10). The specific inflate code is position-dependent; the death is not.

The intact row is the positive control — it proves `clone` *can* succeed, so rc=128
discriminates rather than a harness that always fails. Both halves are pinned by tests,
and `bundle` is absent from the script's read-only allowlist, so substituting it back
fails at the call site.

## Ranked follow-ups (none blocking)

1. 🔴 **The host-label asymmetry itself.** `backup.py --print-plan` by hand also reports
   `host: nixos`, so a hand-run of the *backup* would write a phantom second host prefix.
   Deliberately out of scope for #737 — changing labelling affects artifacts already in
   the bucket and retention pruning.
2. **F5** — a wrong/absent `--store` exits **0** with everything "self-consistency only".
   Reproduced live: `--store <empty dir>` → rc=0, "10 self-consistency only". **Exit code
   is all a timer reads, so this MUST land before (3)** — shipping the timer first is
   exactly the harm F5 describes.
3. **Nothing runs the verifier on a schedule.** A timer is the obvious follow-up; it needs
   network *and* the age key, and the backup unit's containment took several measured
   `systemd-run --user` rounds. Not attempted rather than claimed. **Blocked on (2).**
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
    used for its purpose.** Verdict **measured 2026-08-23** (88 entries / 10 scopes): 2
    over the 12,288 B hard cap, 14 over the 6,144 B target, 30 evictable `RESOLVED`, 11
    broken pointers, 0 scopes without a README, every ref resolving to exactly one entry,
    41 OPEN bullets in 31 entries protected — plus 5 `ACKNOWLEDGED` over cap, excluded from
    the verdict but not from the store. 🔴 **These drift within a day** (the store
    autocommits hourly); an earlier revision of this doc carried a set that was wrong in
    *both* directions on all seven figures. Re-run `scripts/subsystem-audit.py` and read
    its numbers — never quote these.
12. Second A/B against a doc-poor repo (tests whether "selection, not knowledge"
    generalises past n=1).

## The A/B result — what the index is actually worth
Controlled A/B on `datapacket-talos/storage-resolver`, pre-registered 10-question answer
key, both arms on a clean `origin/trunk` worktree.
- **~90% of the entry is recoverable from the repo itself** — sampled 10 load-bearing
  facts; only `kubectl wait -l app=storage-resolver` hangs was index-only.
- The control arm **matched or beat** the index arm on 6 of 10 questions.
- **The index's one clean win was recency-ordered SELECTION**: the control confidently
  asserted the pre-2026-08-20 auth control (`401`) because it read the 08-19 docs and
  stopped. The correction (`403 SignatureDoesNotMatch`) is in the repo, in two 08-20 docs
  it never opened.
- Cost: 25 vs 26 tool calls, 123k vs 148k tokens (~17% saved). Not a step change.
- 🔴 **n=1.** One subsystem in an unusually doc-rich repo.
- **My own answer key was wrong in two places** — it said one image-pin site where there
  are three, and it repeated the entry's incomplete `CLEANED=` advice. The artifact under
  test corrupted the instrument measuring it.

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
- 🔴 **Still open, and measured live 2026-08-23:** the corrected probe found **7 processes**
  whose git-common-dir is `~/workspace/devrc/.git` — including another session running
  `./scripts/gate.sh --tier both` from `.claude/worktrees/agent-…`, and a second from a
  scratchpad worktree. Both *thought* they were isolated. This is not historical; it is
  happening while you read this.
  ⚠ **Do not kill what the probe finds.** Those are other sessions' agents, and the damage
  from killing one reads exactly like a code defect in whatever branch it was testing. Report
  it, or fix the tree you control.
- Probe: see "How to verify" below. 🔴 It must skip `$$` — `pgrep -f` matches the probe's
  OWN shell, so the naive form reports a guaranteed hit from inside the repo and the real
  offender becomes indistinguishable from the artefact. Measured 2026-08-23: the corrected
  probe found **one genuine foreign `run-tests.sh`** whose common-dir was
  `~/workspace/devrc/.git`, so this is live, not theoretical.

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
- ~125 registered agent worktrees are accumulating, in **three** places: `.claude/worktrees/`
  (~69), `/tmp` (~35) and **`~/workspace/devrc-*` (~20)** — the last is easy to miss. The
  count moves by the hour. "Stale" is asserted, not measured: `git worktree list --porcelain`
  reports **0 prunable**, so git considers all of them live. Some may hold unpushed work;
  **diff before removing any.**

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

# nobody is running the tier against the real clone.
# `| while read` — NOT `for p in $(...)`: zsh does not word-split, so the for-loop
# form iterates ONCE over the whole PID list and silently checks nothing.
# `[ "$p" = "$$" ]` — pgrep -f matches this very shell; without the skip the probe
# always reports a hit from inside the repo and can never come back clean.
pgrep -f 'run-tests.sh|gate.sh' | while read -r p; do
  [ "$p" = "$$" ] && continue
  cwd=$(readlink "/proc/$p/cwd" 2>/dev/null) || continue
  printf '%s %s\n' "$p" "$(git -C "$cwd" rev-parse --path-format=absolute \
                            --git-common-dir 2>/dev/null)"
done   # none may equal ~/workspace/devrc/.git
```

## The one link still untested
The escrowed key was verified through the `bw` CLI **on this machine**. In a real disaster
you would read that note from the **web vault on another device** and paste it into a file —
a path that can silently mangle whitespace. Worth doing once at leisure: open the note in
the web vault, paste it into a scratch file, confirm 189 bytes / 3 lines.

⚠ `bw` is **not installed** — run it as `nix-shell -p bitwarden-cli jq --run '…'`. Its
server was repointed from the internal LAN name to the externally-trusted one
(`bw config server` shows the current value; the old one survives as a historical
`byServer` entry). The LAN endpoint serves a cert-manager **self-signed placeholder**
(`CN=selfsigned-ca`, empty subject) that Node rejects outright
(`UNABLE_TO_VERIFY_LEAF_SIGNATURE`), so the CLI cannot talk to it at all. Fixing that cert
is separate homelab breakage, unrelated to this work. Endpoints deliberately not named
here — this repo is public.
