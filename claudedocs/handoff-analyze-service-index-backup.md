# Handoff: analyze-service index — backup, restore-verification, key escrow — 2026-08-24

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
- `#765` — **the escrow-verifier** (`scripts/analyze-service-index/escrow-verify.py`),
  136 tests. Squash-merged as `9300a01a`. It also amends the **already-shipped**
  `restore-verify.py`: `decrypt()` now publishes a machine-readable `cause` (a closed set,
  `KeyError` on an unpublished value) and unlinks a stale `plain` before invoking `age`.
- `#673` — **MERGED** (`4dd14e68`), GUARD 9 / `scripts/testlib/nogit_plugin.py`. It is in
  `origin/main` and its banner prints on every pytest run
  (`gitenv(session) … mode=enforce(auto)`). 🔴 An earlier revision of this doc called it
  "deliberately unmerged" — that was true when first written and false ~28 h later. It is
  the load-bearing safety fact behind the fixture-wipe section below, so check it by
  content (`git log --diff-filter=A -- scripts/testlib/nogit_plugin.py`), never from prose.
- `#822` — **identity PROVENANCE** (`f6516771`, 2026-08-25). A mismatch now says WHICH
  FILE it compared and refuses to call it diagnosed when an env var chose the path.
- `#851` — **the `--decrypt-check` PREFLIGHT + `check-escrow.sh`** (`a58a261d`,
  2026-08-26). Refuses before any `bw` call when the interpreter cannot finish.
- `#896` — **F5/F6: a run that cross-checked NOTHING now exits `40`** (`add34a14`,
  2026-08-27). ⚠ **Merged and on the LAPTOP only — see "Deployment is SPLIT" below.**

**Closed WITHOUT merging:** `#689`, `#676` — superseded, kept as the record.

### 🔴 Deployment is SPLIT — `#896` is NOT on the workbench

The units `ExecStart` straight from `%h/workspace/devrc/scripts/...` (`nix/home.nix:3719`),
so for these scripts **git currency IS deployment** — there is no store copy to switch to.

- **laptop** — shipped, `add34a14`, 499 managed artifacts resolve / 0 dangling, and
  **verified by symptom**: `--print-plan` renders `40=NOTHING-CROSS-CHECKED`, zero `FAILS`.
- **workbench** — 🔴 **still running the PRE-`#896` code.** Measured 2026-08-27:
  `grep -c "NOTHING_CROSS_CHECKED: 40"` on its working tree returns **0**. Its checkout was
  on another session's branch (`feat/rig-control-toggle-and-timers`), and `ship.sh` would
  have fast-forwarded it and *landed the checkout back on main*, taking that branch out from
  under live work — so it was deliberately **not** shipped. F5 is still live on that host.
  **Closing condition:** that session's branch lands or is abandoned and the checkout is back
  on `main`; then `scripts/ship.sh` and confirm the `grep -c` above returns **1**.

🔴 **Both shipped and VERIFIED BY SYMPTOM on each host, not by the deploy reporting
success.** `ship.sh` → converged + verified, 2 hosts compared, both at `a58a261d`. Then, on
each: a wrong shell exits **34** in ~2s with the vault untouched (workbench measured
`rc=34`; laptop's true `rc=34` — its first reading showed `rc=0`, which was `head`'s status
through a pipe, not python's), and `--print-plan --identity <the default>` prints
`chosen by: the --identity flag` with **no** "NOT the default" warning, while a genuine
redirect still warns.

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
4. **Nothing re-checked the ESCROW.** `#765` does — `escrow-verify.py` answers "is the
   escrowed key still there, still byte-identical, and does it still *work*". Byte equality
   and working are different claims: `--decrypt-check` writes the **escrowed** bytes to a
   throwaway 0600 identity and decrypts a real artifact with them.
   🔴 **CLOSED 2026-08-25: rc=0, byte-IDENTICAL and DECRYPT-CHECKED.** The escrowed
   bytes opened a real timer-produced artifact and restored 40 commits. An earlier attempt
   that day did NOT settle it — it compared against a client key an env var had redirected
   it to — so the run that counts is the one with `--identity` pinned. See "The one link".

### Verified live, end to end
10 scopes, 201 commits compared, restored from the bucket → `age -d` → `git clone` →
`fsck` → cross-checked against the live store. Store **byte-identical afterwards** — a
sha256 over a `path size mtime` manifest, identical before and after a full run — and zero
remotes on all 10 scopes. The artifacts verified were the ones the **TIMER** produced
(object stamp `20260823T094821Z`, one second after its `LastTrigger`), not a hand-triggered
run. The commit count and the store's file count both advance hourly; the *byte-identity*
and *zero-remotes* claims are the durable ones.

### 🔴 OUTSTANDING — measured 2026-08-27, nothing blocked on an agent

**DR gaps (need a human):**
1. 🟡 **The laptop's `bw` — HALF CLOSED 2026-08-27.** `serverUrl` was `null`; it is now set
   (`bw config server` run on the laptop, verified: `{"serverUrl":"…","lastSync":null,
   "status":"unauthenticated"}`). The remaining half is the part no agent can do: **`bw
   login`** — email + master password + **2FA**.
   **It is teed up and waiting:** a detached tmux session on the laptop is sitting at the
   master-password prompt, email pre-filled.
   ```sh
   ssh -t zach@10.42.0.100 tmux attach -t bw-login     # -t is required, or no prompt renders
   ```
   Driven by `~/bw-login-teeup.sh` **on the laptop** (not in this repo). The quoting lives in
   a file on purpose — hand-typed one-liners for this cost three master-password entries on
   2026-08-25/26, every failure a paste/quoting fault rather than a mistake about the task.
   ⚠ If the session is gone, re-create it rather than hand-typing:
   `tmux new-session -d -s bw-login "nix-shell -p bitwarden-cli jq --run '~/bw-login-teeup.sh'"`.
   After login, the script prints the follow-on that **doubles as gap 2's real check**.
   If the workbench is the machine you lose, the laptop is the machine you are standing at.
2. **The web-vault paste path is still untested.** Everything verified went through `bw`
   **on the machine that already holds the key**. The real disaster path — open the note in
   the web vault on another device, paste into a file — can silently mangle whitespace and
   nothing here exercises it.
   🔴 **Do NOT check it with "189 bytes / 3 lines" — that check is VACUOUS, measured
   2026-08-27.** A freshly generated, completely unrelated age key is *also* exactly 189
   bytes / 3 lines (control run: pubkey sha `a3f415beca5861cf` vs the real key's
   `288c4d24cfdb5aa1`). This is this doc's own "189 vs 189 is not corroboration" lesson,
   which was applied to the escrow byte-comparison and NOT to these paste instructions —
   one rule, two places, wrong at one. Use the PUBLIC half instead:
   ```sh
   nix-shell -p age --run 'age-keygen -y /tmp/paste.key' | sha256sum | cut -c1-16
   # MUST print 288c4d24cfdb5aa1 — prints the public half only, no secret material
   shred -u /tmp/paste.key
   ```
   It catches both mangling modes: altered key material moves the sha, and broken framing
   makes `age-keygen` fail outright. ⚠ Pipe it **exactly** as written — `printf '%s'` strips
   the trailing newline and yields `be0206821107ea94`, a FALSE mismatch on a good key
   (measured). And `e3b0c442…` is sha256 of **empty input**: the command did not run, which
   is not the same as a failed check.
3. **`server NOT PINNED`** on the successful run — `--expect-server` / `ASIB_ESCROW_SERVER`
   unset, so it trusted whatever `bw config server` said. The session cross-check DID run
   and matched (weaker, but real). Set `ASIB_ESCROW_SERVER` to close it.

**Repo hygiene:**
4. Two MERGED branches still on the remote: `fix/escrow-identity-provenance` and
   `fix/decrypt-check-minio-preflight`. 🔴 Deliberately NOT deleted — the first carries
   `bde25889`, the commit whose client paths the operator chose to LEAVE in history
   (2026-08-25). Deleting that branch eventually makes it unreachable, which is arguably
   desirable but is **not** what was decided. The second is unambiguous cleanup.
5. The base clone carries two untracked files that predate this work
   (`nix/system/apply-nebula-443.sh.LOCAL-preserved-2026-08-02`,
   `scripts/dl-router/tests/load_test_store.sh`) — in no commit and no backup.

**Known and deliberately unfixed:**
6. 🔴 **The late-precondition class is NARROWED, not closed.** `kubectl`, `git`,
   reachability to the MinIO tenant and `--store` readability all still fail **after** the
   vault round-trip, and the advertised shell provisions none of them — they resolve only
   because `nix-shell -p` is impure. Under `--pure` or on a fresh host an operator who
   follows the rc-34 remedy verbatim gets past the preflight and dies later. `age` was
   fixed because it is one PATH lookup with no network; the rest is design work. Written
   into #851 under "What this does NOT close".

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

## 🔴 The `#765` lessons — five rounds, and a premise that was measured wrong THREE ways

Kept because each cost a round, and none is a logic bug.

- 🔴 **A premise "measured once" is not measured.** The escrow classifier keyed on *"corrupt
  ciphertext leaves no plaintext file"*. True only for a corrupt **header** — payload
  corruption and truncation leave a partial or zero-length file. The consequence was a
  **tampered backup reported as "THE ESCROW IS FINE — age reported success"**, exit 31,
  while quoting age's own `file may be corrupted or tampered with` in the same sentence.
  Three separate attempts to measure this were each wrong differently: one sampled a single
  offset and generalised; one used stdout redirection instead of the `--output` invocation
  the code actually makes (they differ); one used `printf '\xff'` on a byte that was
  **already `0xff`**, so the mutation never happened and the reading was an artefact of a
  control that did not run. **XOR the byte and assert it changed.** Final matrix, age v1.3.1,
  176 runs: wrong key → ABSENT; header corrupt → ABSENT; payload corrupt → PRESENT (0 or
  partial); truncated → PRESENT; valid encryption of nothing → rc 0, PRESENT size 0.
- 🔴 **A substring assertion cannot tell a true message from a confident wrong one.** A test
  asserting `"THE ESCROW IS FINE" in msg` **passed unchanged on the tampered-artifact run**,
  certifying a false sentence. Pin whole normalised strings for anything machine-readable.
- 🔴 **`plain_present` did not witness what its branch claimed.** `returned == False` means
  `decrypt()` *raised* — yet the branch's own comment read "age exited 0". Classification now
  comes from a closed `cause` set published by the module that knows, not from the filesystem
  and not from parsing stderr. **`DECRYPT-FAILED` deliberately asserts NEITHER cause**: wrong
  key and damaged header are indistinguishable from outside, and saying so beats guessing.
- **A handover check that shares the step you doubt is not a control.** The old
  `DECRYPT-FAILED` advised re-running with the on-disk identity — reachable only when the
  escrowed bytes are byte-identical to it, so the same experiment with the same input.
- **Version boundaries: measure, don't assume.** `Path.exists()` stops raising
  `PermissionError` in **3.14**, *not* 3.13 — a guard written to `>= (3,13)` would have gone
  red on 3.13, reintroducing the failure it was added to prevent. The test now keys on a
  runtime regime probe with **no version literal**; green on 3.12.14, 3.13.15 and 3.14.7.
- **One rule, two places, wrong at one.** `--work-dir` hands the tool a directory someone
  else populated. That reasoning was applied to the throwaway identity and not to `plain`,
  so a stale plaintext from an aborted run made a **wrong key** read as `ARTIFACT-CORRUPT`
  ("do NOT rotate"). One `unlink` removed the class.
- **A layer nobody can observe failing is a layer nobody knows is gone.** `O_EXCL|O_NOFOLLOW`
  survived mutation because `_shred` removed the symlink first; `os.fchmod` survived because
  no test set a hostile umask. Both are now measured independently.

## Ranked follow-ups (none blocking)

1. 🔴 **The host-label asymmetry itself.** `backup.py --print-plan` by hand also reports
   `host: nixos`, so a hand-run of the *backup* would write a phantom second host prefix.
   Deliberately out of scope for #737 — changing labelling affects artifacts already in
   the bucket and retention pruning.
2. ✅ **F5 — CLOSED by `#896` (`add34a14`, 2026-08-27).** A wrong/absent `--store` used to
   exit **0** with everything "self-consistency only" — reproduced live at **13** scopes,
   not the 10 an earlier revision recorded. It now exits **`40 NOTHING-CROSS-CHECKED`**, its
   own code, distinct from the generic `1`. **F6 closed in the same PR.** ⚠ Live on the
   laptop only — see "Deployment is SPLIT".
3. **Nothing runs the verifier on a schedule.** **UNBLOCKED** — (2) has landed. It needs
   network *and* the age key, and the backup unit's containment took several measured
   `systemd-run --user` rounds. Not attempted rather than claimed.
   🔴 **When it lands, wire `40` as an alert DISTINCT from `1`, or the whole point of
   `#896` is discarded at the consumer.** `1` means "a check FAILED — your backups may be
   broken"; `40` means "the artifacts restored fine, nothing was compared". Collapsing them
   back into "non-zero = bad" re-creates exactly the conflation `#896` spent five audit
   rounds separating. A timer that pages on `1` and merely records `40` is the shape.
4. **F4** — a structurally truncated artifact (1 of 40 commits) verifies green;
   `--max-lag-days` reads the key stamp, never the content.
5. **F9** — no SIGTERM handling around the plaintext window (Python runs no `finally` on
   SIGTERM). Matters precisely because (2) is a timer.
6. ✅ **F6 — CLOSED by `#896`.** "no local store" was three facts in one; `why_no_live_scope`
   now returns one of **five** distinct `WHY_*` kinds with its own sentence, and the refusal
   names the mechanisms each is *consistent with* while declaring the list **NOT closed**.
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

## `#896` filed, NOT fixed — each with its closing condition

Recorded so nobody re-derives them, and so none becomes an object nobody can close.

1. 🔴 **A mode-`000` scope directory exits `1` with a TRACEBACK instead of `40`** —
   `live_scope_path` (`restore-verify.py:974`) raises `PermissionError` *before*
   `why_no_live_scope` runs. Pre-existing, and it is on the disaster path.
   *Closes when:* the probe is permission-safe and a mode-`000` scope reaches the `40`
   message with its own observation, pinned by a test that `chmod`s a fixture scope.
2. 🟢 **`--store` pointing at a regular FILE** reports "the store directory DOES NOT EXIST".
   *Closes when:* `store_state` distinguishes "exists but is not a directory", pinned like
   the other five states.
3. 🟢 **A scope path that is a regular file** classifies as `scope-not-present`.
   **Deliberately left** — literally true, and inoculated by the "AT LEAST … NOT closed"
   wording. Recorded only so it is not re-derived as a bug.
4. 🔴 **`test_git_repo_isolation.py` is a LOAD-SENSITIVE flake** — its `_GIT_ENV` omits
   `hermetic_git.MAINTENANCE_OFF` (five other modules here merge it), so a background
   `git gc --auto` outlives `_mkrepo`'s commit and the co-tenant probe sees it. It went red
   once in the sandbox tier under load 13–18 while a gate run and a 32-mutant sweep
   saturated the box; controls at both `origin/main` and the branch alone were rc=0.
   ⚠ **It can bite Tekton, which runs on shared hardware.** Not fixed here: unrelated to the
   diff, and in a file another session may own.
   *Closes when:* that merge lands and the test passes in the sandbox tier while a second
   gate run loads the box.
5. 🟢 **The AST bypass guard is narrower than its message** — `return _why(a) if c else
   _why(b)` (an `IfExp`) and a nested helper's `return` are rejected though legitimate. All
   failures are **fail-closed** and self-diagnosing (`ast.unparse` prints the offender), so
   the direction is safe. *Closes when:* the message gains "…or does not do so as a direct
   `_why(...)` call", or the guard accepts the wider shape.

## `#896` — what five audit rounds actually cost, and why the ladder is not theatre

The PR shipped after **five rounds plus a clean sixth**. Every round found something real,
and **four found a defect the PREVIOUS round's fix had introduced**:

- **R1→R2**: the first fix made a wrong `--store` exit `1` — the same code as "your backups
  are broken". Correct direction, wrong instrument: `escrow-verify.py` sits in the same
  directory with **nine** distinct codes precisely so a timer can act on the number.
- **R2→R3**: the round-2 commit message asserted *"🔴 THE #851 CONTROL RAN"*. It ran against
  ONE surface; the `--print-plan` prose added **in the same commit** carried the same claim
  unpinned, and an auditor flipped it to *"40 IS a verdict against the backups"* with **342
  tests green**. 🔴 **The #851 trap, reproduced inside the fix written to close it.** A pin
  that covers one instance of a class reads as covering the class.
- **R3→R4**: the "both mechanisms" list was a CLOSED disjunction excluding two reachable
  states (symlinked scope, `.git`-less dir), and its tail then asserted *"the live side is
  gone"* — false for both. Round 1's vaguer *"or fix it"* had been **correct**; the fix made
  it worse. A remedy also pointed at `--print-plan` "the store path this run used", which
  prints the **default** — manufacturing the diagnosis the message refuses to assert.
- **R4→R5**: the ordering guard added in R4 was **vacuous on the dimension its own docstring
  named** — reverting to sentence-sorting survived all 349 tests, because that fixture's two
  scopes sorted identically under both keys. The code comment even recorded the coincidence.
- **R5→R6**: the "no branch bypasses `_why`" assert was **SPELLED, not structural** (it
  matched lines starting `return `), and two lint-clean bypassing spellings survived a green
  165-test suite. There is **no Python linter anywhere in the gate** — verified zero hits for
  ruff/flake8/pylint/pycodestyle across `gate.sh`, `run-tests.sh` and `flake.nix` — so
  nothing else refused them either.

🔴 **Three instrument failures were caught by controls, not by reasoning**, and each would
have produced a confident wrong number:
- a mutation sweep reported **29/31** where both survivors were HARNESS defects — one mutant
  aimed at `len(by_scope)`, already narrowed by `--scope`, so it was blind for the very
  reason the finding it tested described;
- a mutant whose anchor was a **guessed line wrap** matched 0 times and scored SURVIVED
  silently. **Assert every replacement count == 1**, and dry-run anchors before the sweep;
- `nix build --rebuild`, used to attribute a red test, needs a valid prior output and
  **errored before testing anything** — rc=1 twice, which reads as "confirms pre-existing".
  Reading the log rather than the exit code caught it.

**The stop rule that matters:** a *verdict* of "safe to merge" is not the signal — the
FINDINGS are. Round 4's audit said safe-to-merge **and** reported a real vacuous guard in the
same breath. The ladder ended on the first round that found nothing, and no round was run to
confirm it.

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
- 🔴 **15 untracked files on the workbench are in NO commit and NO backup** (measured
  2026-08-24 by `drift-check.sh`): a handoff doc, a preserved `nix/system/apply-*.sh`, and an
  entire `scripts/discord-embed-ext/` browser extension — manifest, service worker, icons.
  Any routine `git checkout` by any session deletes them silently. Not mine to commit; commit
  them or copy them aside.
- **A pre-existing flake on `main`, measured not guessed**: `test_subsystem_touch.py::
  test_the_LENGTH_bound_is_not_vacuous…` fails ~1 run in 300–600. **600 runs on a pristine
  `origin/main` reproduced it twice.** Mechanism: `git rev-parse --disambiguate` lists every
  object with the prefix — trees and blobs, not just commits — so ~8 objects against 4096
  gives P ≈ 0.17%/run. Durable fix: a longer prefix, or assert the sha is *among* the
  expansion rather than its sole member. **Run the control before calling a red gate a flake**
  — and note the background-task notification for that red run reported "exit code 0", which
  was the pipeline's status through `tail`, not the gate's.
- ~125 registered agent worktrees are accumulating, in **three** places: `.claude/worktrees/`
  (~69), `/tmp` (~35) and **`~/workspace/devrc-*` (~20)** — the last is easy to miss. The
  count moves by the hour. "Stale" is asserted, not measured: `git worktree list --porcelain`
  reports **0 prunable**, so git considers all of them live. Some may hold unpushed work;
  **diff before removing any.**

## How to verify
```bash
# 🔴 THE ESCROW — one command, no quoting to get wrong. Runs a LOCKED DRY PASS
# first (proves shell + interpreter + argv + host label, spends nothing, because
# escrow-verify.py never prompts), and only then asks for the password.
~/workspace/devrc/scripts/analyze-service-index/check-escrow.sh
~/workspace/devrc/scripts/analyze-service-index/check-escrow.sh --plan   # no bw, no network, no key

# the restore path, for real (10 scopes, from the bucket)
nix-shell -p 'python3.withPackages(p:[p.minio])' --run \
  "python3 ~/workspace/devrc/scripts/analyze-service-index/restore-verify.py \
   --host workbench-\$(cat /etc/machine-id)"

# the timer actually fired
systemctl --user list-timers analyze-service-index-backup.timer --all
systemctl --user show analyze-service-index-backup.service -p Result

# the PREFLIGHT is live on this host: a shell WITHOUT the package must exit 34 in
# ~2s having contacted nothing. MEASURED on both hosts 2026-08-26.
nix-shell -p bitwarden-cli jq --run \
  'python3 ~/workspace/devrc/scripts/analyze-service-index/escrow-verify.py --decrypt-check \
   --identity ~/workspace/homelab-talos/.secrets/age.key --host workbench-$(cat /etc/machine-id)'
# 🔴 read the exit code WITHOUT a pipe — `| head` reports HEAD's status, and that
# is exactly how a laptop rc=34 first read as rc=0 on 2026-08-26.

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

## The one link — 🔴 CLOSED 2026-08-25, rc=0

**MEASURED, rc=0.** The escrow is intact AND it works:

```
escrow OK — the Secure Note matches ~/workspace/homelab-talos/.secrets/age.key
IDENTICAL (189 escrowed bytes vs 189 on disk); session cross-check RAN;
DECRYPT-CHECKED: the ESCROWED bytes decrypted
<host>/civitai/20260825T094001Z.bundle.age (scope civitai) and restored
40 commit(s) over 1 ref(s)
```

🔴 **Two separate claims, and the second is the one that matters.** Byte equality says the
two copies agree; the decrypt says the copy *in the vault* — not the one on disk — opens a
real artifact. The artifact was the TIMER's own output from that morning, not a hand-made
fixture. Disk loss no longer takes the key with the store, and that is now measured rather
than assumed.

It also settled the two assumptions **never previously measured against the real vault
item**: the note is `type == 2`, and `notes` is present in `bw list items` output. A wrong
guess on either exits 19 or 20 long before the decrypt, so reaching rc=0 proves both.

⚠ **Still soft:** `server NOT PINNED` — `--expect-server` / `ASIB_ESCROW_SERVER` were
unset, so the run trusted whatever `bw config server` said. The session cross-check DID
run and matched, which is a weaker but real check. Pin the server to close it.

⚠ **Still untested, and unchanged:** the disaster path that actually matters — reading the
note from the **web vault on another device** and pasting it into a file. That path can
mangle whitespace and no run here exercises it. And the laptop's `bw` is still not logged
in (`rc=13` when measured), so recovering from that host needs a fresh `bw login` first.

**How it was run — use this, not a hand-typed one-liner:**

```bash
scripts/analyze-service-index/check-escrow.sh          # locked dry pass, then unlock
scripts/analyze-service-index/check-escrow.sh --plan   # no bw, no network, no key
```

🔴 **The one-liner form is retired because it failed three times in a row, each time
costing a master-password entry** — once by omitting the `python3.withPackages(...)`
argument, once by losing the script-path token (which lands the operator in a Python
REPL), and once by losing the identity-path token (`--identity: expected one argument`).
All three are quoting/paste failures, not mistakes about the task. A ~300-character line
carrying a nix expression, a `--run` string and three nested command substitutions is not
something to hand a person to paste; the quoting now lives in a file.

### The earlier run, kept because its lesson is durable

**An earlier attempt on 2026-08-25 did NOT settle the question** — it compared the escrow
against the wrong file. Details in "The env
redirect" below. So the claim below is unchanged: everything above the vault boundary is
still synthetic — the real note fetch, the real byte comparison against the real `age.key`,
and `--decrypt-check` against MinIO are covered only by an injected fake.

🔴 **Do NOT re-escrow on the strength of that run.** What it proved is that the escrow
differs from a **CivitAI client key**, which is not a fact about the escrow at all.

The command below is the corrected one — it pins the identity and provisions `minio`.
Both differences are load-bearing, and each was a separate failure on 2026-08-25:

```bash
nix-shell -p bitwarden-cli jq 'python3.withPackages(p:[p.minio])' --run \
  'export BW_SESSION="$(bw unlock --raw)"; env -u SOPS_AGE_KEY_FILE -u ASIB_AGE_IDENTITY python3 ~/workspace/devrc/scripts/analyze-service-index/escrow-verify.py --decrypt-check --identity ~/workspace/homelab-talos/.secrets/age.key --host workbench-$(cat /etc/machine-id)'
```

Validated to the vault boundary (`rc=12 VAULT-LOCKED` in 3s), so argv, `--identity`, the
host label and the imports are all known-good; only the step the password opens is untried.

### 🔴 The env redirect — what the 2026-08-25 run actually measured

The run returned **`rc=22 BYTES-DIFFER-MATERIALLY`, "189 escrowed bytes vs 189 on disk"**,
and its remedy said to re-escrow from the on-disk identity. Following that advice would
have **overwritten a good escrow with a client's age key**. Two mechanisms fit that output
and they demand opposite actions:

- **A** — the escrow is stale/damaged → re-escrow.
- **B** — the *on-disk* side was the wrong file → change nothing.

**It was B, measured.** `resolve_identity()` reads `ASIB_AGE_IDENTITY` → `SOPS_AGE_KEY_FILE`
→ the default. The command ran in an **interactive** shell (`!`/`.zshrc`), which had
`SOPS_AGE_KEY_FILE` pointed into a **client repo**; the Bash tool's non-interactive zsh does
not, which is why the same command behaved differently for the agent and the human.

🔴 **"189 vs 189" is not corroboration — it is what two DIFFERENT keys look like.** Every
age identity file is the same size (fixed-width `# created:`, `# public key:` and key
lines). Equal byte counts on both sides carry no information about whether the keys match.

Public-key hashes, compared without printing key material — all four are **distinct**
identities. Three client checkouts carry their own key at a `…/.secrets/sops/age.key` path;
they are not enumerated here (this repo is public) and which one it landed on does not
change the conclusion:

| identity | pubkey sha (first 16) |
|---|---|
| `homelab-talos/.secrets/age.key` (the real one) | `288c4d24cfdb5aa1` |
| client checkout A | `7e3367f5a3a5d327` |
| client checkout B | `6643aa0a698317cf` |
| client checkout C | `8e6095ea38340cef` |

Reproduce with `age-keygen -y <file> | sha256sum` — it prints the PUBLIC half only, so the
comparison never handles secret material.

**The on-disk homelab key is confirmed correct, independently of the vault.**
`restore-verify.py --identity ~/workspace/homelab-talos/.secrets/age.key` → **rc=0**, all
10 artifacts decrypted, 214 commits compared, `fsck OK`, 0 self-consistency-only, against
artifacts stamped `20260824T094214Z` — the timer's own output. Two independent sources
agree that path is the intended one: the systemd unit pins `SOPS_AGE_KEY_FILE` to it
(`nix/home.nix:3312`), and it opens the bucket.

**Both defects are fixed in this branch** (`fix/escrow-identity-provenance`): the advertised
nix-shell is derived from a package ledger pinned against what the decrypt path imports, and
a mismatch now names *what chose* the on-disk path — refusing to call the mismatch diagnosed
when an env var did. `--print-plan` discloses a redirect before any password is spent.
Matrix: both red at `a8089a51`, green at HEAD, demonstrated by execution.

🔴 **A run from the WRONG SHELL now refuses BEFORE the vault, exit 34.** Measured
2026-08-25, twice: `--decrypt-check` unlocked the vault and *then* died on a missing
`minio` — after the master password had been typed. The first fix corrected which shell is
advertised; it did not change *when* the run discovers it is in the wrong one, so it
happened again. `preflight_decrypt_imports()` now runs before any `bw` call and names the
interpreter that came up short:

```
DECRYPT-DEPS-MISSING: --decrypt-check needs minio, which <the profile python3>
cannot import. NOTHING HAS BEEN CHECKED and the vault was NOT contacted ...
```

Only the `python3.withPackages(p:[p.minio])` shape resolves a `python3` that has it — the
bare shell and `-p bitwarden-cli jq` both resolve the ambient profile's, which does not.
The old failure surfaced as `scripts/mail-actions/_minio.py`'s own `SystemExit`, which
names a different program (`extract.py archive-invoices`) and a different package set;
accurate for its original caller, misleading here. That shim is unchanged — escrow-verify
simply no longer reaches it.

It also settles two assumptions **never measured against the real vault item**: that a
Secure Note has `type == 2`, and that `notes` is present in `bw list items` output. A wrong
guess is loud, not silent — exit 19 prints the observed type. Locked/unauthenticated exit
12/13 with an actionable message and **cannot hang**: every `bw` call runs with stdin on
`/dev/null` precisely so an unattended run fails fast instead of blocking on an invisible
prompt. `SECRETS.md` carries the full exit-code table with per-code "do not rotate" guidance.

**Measured on BOTH hosts 2026-08-24, and they differ — correctly.** Workbench → `rc=12
VAULT-LOCKED`; laptop → **`rc=13 VAULT-UNAUTH`**. Two points, two distinct codes, each
accurate: this is the first *live* exercise of the unauthenticated branch, which had only
ever been synthetic. 🔴 **The DR fact hiding in that second reading: the laptop's `bw` is
not logged in at all.** If the workbench is the machine you lose, recovering the escrow from
the laptop needs a fresh `bw login` there first — email plus master password plus 2FA, not
just the master password. Worth doing before you need it.

**And the retrieval path that actually matters in a disaster.** The escrow was verified
through the `bw` CLI **on this machine**. If this machine is gone you would read that note
from the **web vault on another device** and paste it into a file — a path that can silently
mangle whitespace. Worth doing once at leisure: open the note in the web vault, paste it
into a scratch file, then verify it with the **public-key** check in "OUTSTANDING" item 2 —
`age-keygen -y <file> | sha256sum` must begin `288c4d24cfdb5aa1`. 🔴 **Not a byte count:
every age identity file is 189 bytes / 3 lines, so an unrelated key passes that.**

⚠ `bw` is **not installed** — run it as `nix-shell -p bitwarden-cli jq --run '…'`, and add
`'python3.withPackages(p:[p.minio])'` for `--decrypt-check`, which reaches MinIO through a
**lazy** `minio` import and so dies mid-run, after the password, in a shell without it. Its
server was repointed from the internal LAN name to the externally-trusted one
(`bw config server` shows the current value; the old one survives as a historical
`byServer` entry). The LAN endpoint serves a cert-manager **self-signed placeholder**
(`CN=selfsigned-ca`, empty subject) that Node rejects outright
(`UNABLE_TO_VERIFY_LEAF_SIGNATURE`), so the CLI cannot talk to it at all. Fixing that cert
is separate homelab breakage, unrelated to this work. Endpoints deliberately not named
here — this repo is public.
## Gotchas / decisions / dead-ends — 2026-08-25/26

🔴 **A fix to a message's CONTENT is not a fix to its TIMING.** `#822` corrected which
shell `escrow-verify.py` advertises and left the master password still being spent before
the failure surfaced. **Four adversarial audit rounds missed it**, because every round was
framed as *"is this sentence true?"* and none asked *"does this refusal come early
enough?"*. The reviewers inherited the framing. When an audit-fix round closes, ask what
axis the rounds did NOT vary.

🔴 **The same failure three times is a signal about the FORM, not the contents.** Getting
one successful `--decrypt-check` took three attempts and three master-password entries, and
every failure was QUOTING: the `withPackages` argument omitted (`ModuleNotFoundError`), the
script-path token lost (dropped into a **Python REPL**), the identity-path token lost
(`--identity: expected one argument`). Rewriting the one-liner each time was the wrong
response — a ~300-character line carrying a nix expression, a `--run` string and three
nested command substitutions is not something to hand a person to paste. The quoting now
lives in `check-escrow.sh`.

🔴 **`bw unlock --raw` can exit ZERO having printed NOTHING** — no TTY, EOF, or a cancelled
prompt. So `BW_SESSION=$(bw unlock --raw) && …` lets a doomed run through: the empty session
exports fine and the verifier then reports **VAULT-LOCKED**, which reads as *a vault
problem* rather than *an unlock that never happened*. Two faults, one message.
`check-escrow.sh` checks the emptiness explicitly and names it.

🔴 **Five instrument failures in one session, each producing a confident wrong verdict, none
visible in a diff.** Validate the instrument before reading its answer:
- a mutation sweep whose sandbox copied only `scripts/` — the UNMUTATED baseline failed 19
  tests, so every mutant "died" for that reason and reported a clean 5/5 measuring nothing;
- a sandbox copied with `rsync -a`, carrying **valid** `__pycache__`, so it executed
  bytecode from the ORIGINAL tree. 🔴 `PYTHONDONTWRITEBYTECODE=1` does **not** prevent this
  — it stops *writing*, not *reading*;
- background task notifications reporting **"exit code 0"** for a FAILED `nix build`,
  because a trailing `echo` owned the pipeline's status (hit three times; fixed by ending
  the command with `exit $rc`);
- `$?` read after `| head`, turning a laptop `rc=34` into `rc=0`;
- `bw unlock --raw` above.
Every sweep here now runs an unmutated baseline FIRST and requires it GREEN, asserts no
`.pyc` reached the sandbox, and includes a **no-op control that must SURVIVE** — otherwise
a harness that reddens everything is indistinguishable from real coverage.

🔴 **An unguarded fix is a comment, not a guarantee.** Round 3 of #822's audit found the
round-2 fix could be reverted verbatim with all 450 tests green. Same shape in #851: the
clause that mattered most — *"the vault was NOT contacted"* — was pinned only by
substrings, so flipping it to *"the vault WAS contacted"*, a falsehood about whether the
operator's credential had been used, passed the entire suite. **When the artifact under
test is prose, pin the whole normalised string.**

⚠ **`bde25889` decision (2026-08-25):** it added three client repo paths to a PUBLIC repo;
`1543df0a` redacted the tree. The operator chose **leave the history, squash-merge only** —
so `main` never carried them (verified: 0 occurrences, `main` gained ONE commit) while the
branch commit stays visible. That makes **squash-merge load-bearing**, not conventional,
and all three merge methods are enabled on this repo.
