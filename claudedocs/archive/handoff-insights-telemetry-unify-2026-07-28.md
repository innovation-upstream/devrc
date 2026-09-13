# Insights ↔ telemetry — session 2026-07-28

**Supersedes `handoff-insights-telemetry-unify-2026-07-11.md`.** That doc's priority
list was stale in all three items by the time it was picked up; this one records what
was actually true on 2026-07-28 and what the extraction produced.

## Why the old handoff misled

| Old claim (07-11) | Reality on 07-28 |
|---|---|
| Layer B: 8 sessions extracted, ~90 backlog | **148 already extracted** — batches ran 07-21 (57) and 07-24 (83) |
| Layer A: 373 `session-summary` rows | **27,061 rows / 702 sessions**, +~1,800/day |
| "Dominant finding: config-as-code for storage/CDN" | **Superseded** — now a ×1 tail item |

No code touched Layer A/B between 07-11 and 07-28; the intervening sessions were
operating runs only. **Lesson: a handoff doc's *findings* section ages as fast as the
data behind it. Re-run the report before acting on a remembered conclusion.**

## The dominant finding at 148 sessions — and its twist

52 of 148 sessions carried a `config_gap`; **33 named the headless drafter's allowlist**
(`gh pr list` ×23, multi-op `&&` ×15, `$CIVITAI` ×12, `git branch` ×10, kubectl ×7).
The largest toil category — `context-gathering` ×~20 — was the downstream symptom
(brute-force `git log --grep` archaeology as a substitute for "is this merged?").

**The twist: it was already fixed.** All 33 gap sessions fall in 07-10 → 07-24, and 29
strictly before **PR #157** (2026-07-24, "harden headless runtime") + **#159**
(deterministic `ticket-status` probe). The report couldn't know — the extraction
batches predated the fix, so *no post-fix session had been extracted*.

That inverted the value of the "just extract more" option: the pending sessions were
**the verifier for #157/#159**, not more of the same.

## What the extraction actually proved

Extracted 8 sessions (run `20260728T231415Z-07c4`), all post-07-20, → Layer B now
**156 sessions**. Six of the eight were the drafter's own per-ticket `claude -p` runs
from today's 08:02 pass (note: `ground_truth` timestamps are **UTC** — `13:03Z` =
`08:03 CDT`).

**Verdict: PR #157 did NOT close the gap.** Session `1d05e840` recorded `gh pr view`
still blocked. Root cause found and confirmed against the transcript:

- `drafter-prompt.md:99-100` mandates `gh -R civitai/civitai pr list|view|checks …`
- `drafter.sh:198` allowlisted only `Bash(gh pr list*)` / `view*` / `checks*`
- `-R <repo>` sits between binary and subcommand → **prefix pattern can never match**

Proof it was the pattern and not the environment: six sibling `git -C … log` calls in
the same session matched `Bash(git -C * log*)` and ran fine.

### Deterministic baseline (all 81 drafter transcripts)
**192 of 1,419 Bash calls rejected (13.5%)**, split by cause:
- **106 true allowlist gaps** — 77 of them `gh -R … pr …` (73%), 11 `git branch -a/--merged`, 4 `KUBECONFIG=… kubectl`
- **86 shape-guard rejections** — model violating the prompt's own no-chaining contract. *Not* allowlist bugs; must not be "fixed" by loosening the allowlist.

Blocked rate by run-day: 14.2% (07-21) → 12.2% (07-25) → 8.0% (07-28) — #157 helped
but never approached zero, because the largest cause was untouched.

## What shipped

**PR #177** (merged, `cba98a9`) — allowlist the `gh -R` form; add read-only
`git -C * branch --merged*` / `branch -a` / `branch --all`; new
`tests/test_allowlist_covers_prompt_shapes.py` (a port of Claude Code's Bash matcher +
prompt-shape extraction) asserting every prompt-mandated shape is allowlisted. 87 tests.
- Adversarial audit caught that `Bash(gh -R * pr view*)` admitted **gh write verbs**
  via substring smuggle → repo pinned to `civitai/civitai` + `civitai/talos-infra`.
- Audit also caught the anti-drift test could be evaded (fenced blocks stripped before
  extraction; placeholder-bearing spans dropped) → both closed.
- The author correctly **refused** an instruction to add `Bash(git -C * branch -a*)`:
  `git branch -a -m old new` renames (verified rc=0). Shipped exact `branch -a` instead.

**PR #179** (merged `d5c46c2`, shipped to both hosts) — closes **two confirmed
pre-existing RCEs**. 102 tests. Two adversarial-audit rounds; both rounds found real
blockers that were fixed before merge.

**Live state verified on the exact file the timer runs**
(`~/workspace/devrc/scripts/task-spec-drafter/drafter.sh` — the unit runs the working
tree directly, so `git pull` is the deploy): 71 allow entries, node pinned, **0
mid-pattern wildcards**, 13 deny entries, runtime `CIVITAI_REPO` guard, `bash -n` OK.
Next run **2026-07-29 08:03:13 CDT**.

## 🔴 The RCE (pre-existing since 2026-06-23, NOT introduced by #177)

`Bash(git -C * log*)`'s mid-pattern `*` matches **across spaces**, so git's global
`-c` options can be inserted between path and verb. Reproduced by execution:

```
git -C <repo> -c diff.external='sh -c "id > /tmp/M" --' log -p --ext-diff
→ executed; uid=1000(zach) groups=wheel,docker
```

Threat model: untrusted ClickUp ticket text → prompt injection → code execution as
`zach` (docker group ⇒ root) on a host holding a **production kubeconfig**, unattended
at 08:02 daily. No evidence of exploitation — this is a capability finding.

**Fix = structural, not blocklist:** pin the `-C` path to the three literal repos the
drafter actually uses (civitai 782 calls, datapacket-talos 16, homelab-talos 4), so the
verb must follow the literal path immediately. Verified premise: git **refuses `-c`
after the subcommand** (rc=128).

Two further holes found while fixing, both verified by execution:
1. **`git grep -O'<cmd>'` executes** — a *post*-subcommand flag, so pinning does not
   close it → the `grep` verb was **dropped** (native `Grep` tool + `git log --grep/-S`).
2. **`git log --output=<path> --pretty=format:'<text>'` = arbitrary-path truncating
   file write** (aimed at `~/.zshenv` ⇒ RCE). A prefix *allow* pattern cannot forbid a
   suffix → added a **`--disallowedTools` deny layer**.

Also corrected: `drafter.sh`'s header claimed the pass runs under
`--permission-mode plan`. **It does not.** That materially misstated the safety posture.

### A SECOND RCE of the same class — `node` (found by the #179 audit)

`Bash(node *query.mjs get*)` (plus `comments*` / `search*`) has the mid-glob exactly
where node's `-e` lives. Verified by execution:

```
node -e '<arbitrary JS>' query.mjs get 1   → matches the pattern; node runs the -e
                                              payload and ignores the trailing file
```

Arbitrary JS as `zach`. **Pre-existing**, same mechanism as the git `-c` hole, and it
survived the first cut of #179 — which meanwhile asserted "no mid-pattern wildcard,
test-guarded." Fixed by pinning to the literal `query.mjs` path.

**Generalise the lesson:** a `*` is safe at the END of a `Bash(...)` pattern and
dangerous in the MIDDLE. Audit every mid-pattern wildcard for what can be inserted at
the `*`. (Recorded in memory as `claude-allowlist-glob-matches-spaces`.)

### The deny layer is a speed bump, not a boundary

`--disallowedTools` does correctly override `--allowedTools` (verified from the shipped
bundle: `SKe`/`wko`/`gvd`/`r8y` all consult deny first). But a substring deny is
**bypassable by quoting** — the regex needs a literal space before the flag and only
the first token is de-quoted:

```
git -C R log --output=OUT --pretty=format:x -1     → denied
git -C R log '--output=OUT' --pretty=format:x -1   → EXECUTED, file written
```

Treat it as raising the bar, never as closure.

## Still open (honest residual)

- ~~Not verified against a live run.~~ **✅ VERIFIED 2026-07-29** against the 08:00:20
  → 08:10:48 run (`Result=success`). **Blocked rate 13.5% baseline → 2.7%**; **zero
  `gh -R … pr …` blocks** (pass criterion met); and **no over-restriction** —
  git 55 ran/2 blocked, gh 9/1, kubectl 11/0, node 22/0, ticket-status 11/0.
  The 3 remaining blocks are benign: `gh … issue view` (never allowlisted — a real but
  small new gap), `git … tag -l` (deliberately out of remit), and `git -C … find .`
  (malformed — git has no `find` subcommand; model error, not an allowlist issue).

  Re-run this any time to recompute the same table:

  ```bash
  python3 - <<'PY'
  import json,glob,os,collections,re,datetime
  pure=collections.Counter(); chained=collections.Counter(); byday=collections.Counter(); tot=collections.Counter()
  for f in glob.glob(os.path.expanduser('~/.claude/projects/-home-zach/*.jsonl')):
      day=datetime.date.fromtimestamp(os.path.getmtime(f)).isoformat(); tools={}
      for line in open(f,errors='replace'):
          try: d=json.loads(line)
          except: continue
          c=(d.get('message') or {}).get('content')
          if not isinstance(c,list): continue
          for b in c:
              if not isinstance(b,dict): continue
              if b.get('type')=='tool_use' and b.get('name')=='Bash':
                  tools[b.get('id')]=b.get('input',{}).get('command',''); tot[day]+=1
              if b.get('type')=='tool_result':
                  t=b.get('content')
                  if isinstance(t,list): t=' '.join(x.get('text','') for x in t if isinstance(x,dict))
                  if 'requires approval' not in str(t).lower(): continue
                  cmd=tools.get(b.get('tool_use_id'),''); byday[day]+=1
                  (chained if re.search(r'[|;]|&&|\$\(|`',cmd) else pure)[' '.join(cmd.split()[:4])]+=1
  for d in sorted(tot): print(f"{d}  bash={tot[d]:4d}  blocked={byday[d]:3d}  {100*byday[d]/max(tot[d],1):.1f}%")
  print("\nTRUE allowlist gaps:", sum(pure.values()))
  for c,n in pure.most_common(8): print(f"  {n:3d}  {c}")
  print("\nchained (shape-guard, NOT allowlist):", sum(chained.values()))
  PY
  ```

  **Pass criteria:** `gh -R … pr …` disappears from the TRUE-gaps list entirely.
  **Watch for the opposite failure — OVER-restriction.** The realistic residual risk is
  now a legitimate read being blocked, which is unanswerable headless. Eyeball the
  digest (`~/.claude/task-spec-drafter/latest.md`) for
  `verification: "unverified — all reads failed/blocked"`. If git reads all fail, the
  first suspect is a `CIVITAI_REPO` outside the three pinned paths (the new runtime
  guard should `exit 1` loudly rather than fail silently).
- **The allowlist is reduced, not proven safe.** Known-remaining surface, all
  pre-existing and none claimed closed:
  - `Bash(kubectl get*)` + prod `KUBECONFIG` ⇒ `kubectl get secret -A -o yaml`
    exfiltrates **production secrets** into model context, and thence into the digest
    email. **Largest single remaining item.**
  - `Bash(cat*)` / `Bash(grep*)` ⇒ arbitrary file read (`~/.claude/.credentials.json`,
    kubeconfigs, `~/.ssh/*`).
  - `Bash(jq*)` ⇒ `$ENV` / `input_filename` env + path disclosure (no exec, no write).
  - `git … diff --no-index <a> <b>` reads any two files (subsumed by `cat*`).
  The durable fix is moving raw git/node behind `ticket-status`-style wrappers rather
  than continuing to enumerate flags.
- ~~**🔴 Permission blast radius**~~ — **CLOSED by PR #185** (merged `da6b9af`,
  shipped both hosts, 2026-07-29). See "PR #185" below. What remains is the
  *credential*, not the string matching: the pass still holds a cluster-admin cert.

<details><summary>Original finding (kept for the record)</summary>
  `claude -p` **unions** the CLI allowlist with `~/.claude/settings.json` — 248 allow
  entries, `deny: []`, `ask: []`. So the headless drafter also holds `Bash(curl:*)`,
  `Bash(ssh:*)`, `Bash(git add:*)`, `Bash(git commit:*)` and
  `kubectl apply/delete/scale` against homelab **and** workbench kubeconfigs. This is
  what turns an RCE into full prod access. `drafter.sh`'s careful read-only design is
  **not a ceiling**.
</details>

- **clawgate `PermissionRequest` hook** has `matcher: None`, `timeout: 180`, vs
  `DRAFTER_TIMEOUT=240` — one non-allowlisted call at 08:00 can burn most of a ticket's
  budget waiting for an approval nobody is awake to give.
- **`shell-env-nudge` PostToolUse hook** pushes the drafter toward `$CIVITAI`, which
  `drafter-prompt.md:38` explicitly forbids. Contradictory guidance to a headless agent.

## PR #185 — the settings.json union (merged `da6b9af`, shipped 2026-07-29)

**The problem.** `claude -p` UNIONS its `--allowedTools` with `~/.claude/settings.json`
(248 allow, `deny: []`, `ask: []`). So closing the git/node doors achieved less than it
looked: `Bash(python3:*)` is a PREFIX rule matching `python3 -c '<code>'`, and python3
is on the unit's PATH — **arbitrary execution had been wide open the whole time**, as
had `docker run --privileged` (⇒ root), `curl`, `wget`, `nc`, `ssh`, `git commit`,
`sudo lsof`, and (found by audit) `sops` (decrypts secrets), `sqlite3` (`.shell`),
`find -exec`, `xargs`, `sort --compress-program`, `dig`, `env`.

**The fix.** `DRAFTER_DENIED_TOOLS` 13 → **253 fields** across grouped
`DRAFTER_DENY_<GROUP>` vars, all whole-binary `Bash(name:*)` PREFIX form. Whole-binary
denies matter: the `--output` flag-substring deny from #179 fell to quoting
(`'--output=x'`), but the first token is de-quoted by `rae()`, so a binary deny holds.
Deny beats allow in every path (`SKe`/`wko`/`gvd`/`r8y`) and is evaluated with
`stripAllEnvVars` + wrapper unwrapping + `xargs` broadening + per-sub-command
compound checks — so `KUBECONFIG=… kubectl delete`, `sudo python3 -c`, `xargs python3`
and `git log && python3 -c` are all covered without leading wildcards.

**🔴 the audit caught, that the tests hid.** The first cut denied kubectl per-verb —
29 `Bash(kubectl <verb>:*)` PREFIX rules, which only match **verb-first**. Any global
flag before the verb missed all 29, and `Bash(kubectl:*)` then allowed it against
**production**:

| | |
|---|---|
| `kubectl delete pod -n prod api-0` | denied |
| `kubectl -n prod delete deploy web` | **ALLOWED** |
| `kubectl --kubeconfig=<prod> delete ns prod` | **ALLOWED** |

`-n <ns>` before the verb is the most ordinary kubectl idiom there is. The tests missed
it because every kubectl case was built as `f"kubectl {verb}"` — while the same file
already ran this exact attack class against `git` via `_GLOBAL_OPTION_INJECTIONS`.

**Fix = `scripts/task-spec-drafter/kubectl-ro`** (the `ticket-status` pattern): deny
`kubectl` wholesale, allow ONE pinned wrapper that validates in code we control —
parses flags-before-verb properly, permits only `get`/`logs`/`describe`/`top`, refuses
identity/endpoint overrides and Secret objects.

**A second 🔴, found only by exercising the wrapper at runtime:** `--raw` takes an
arbitrary API path, so `get --raw /api/v1/namespaces/x/secrets` read **every Secret in
the client's production cluster** past the object-level block — and `is_secret_token`
couldn't catch it because `${1%%/*}` on a leading-slash path strips to empty. `--raw`
is now refused outright and the secret check walks every path segment. **No pattern
test could have found this** — the allow/deny patterns are identical either way. That
is why the suite now exercises the wrapper as a subprocess against a stub kubectl.

**Two kill-switches closed:** appending `DRAFTER_DENIED_TOOLS=""`, or flipping the
wiring conditional `-n`→`-z`, each disabled the whole layer *with the suite green*.
`--disallowedTools` is now passed unconditionally, the optional-array pattern is banned
by test, and an integrity guard aborts on an empty/short value while probing the
critical denies by name. `${VAR:-}` not `${VAR-}`.

Tests **102 → 119**. Live state verified on the deployed files: 70 allow entries, 0
mid-pattern wildcards, one kubectl allow (`Bash($SELF_DIR/kubectl-ro *)`), 253 deny
fields, wrapper behaviour re-checked against a stub.

**Honesty note on "0 false positives":** true for the 07-29 corpus, and partly because
that run used no `find`. Over the full 8-day window **55/1,172 (4.7%) would now be
denied, all `find`**. Kept deliberately (`find -exec` is RCE; `Glob`/`Grep` remain; the
prompt already steers to `Grep`/`rg`) — but it is a real capability reduction, and the
README recipe now mines the full window instead of one morning.

## 2026-07-30 — the loop closes, and corrects one of its own calls

Four PRs merged + shipped to both hosts.

**#208 — Layer A emit-on-settle (the 07-11 item, finally closed).** Each tick a
transcript takes one branch: `unchanged` · `settled` (idle ≥ `CLAUDE_SUMMARY_SETTLE_MINUTES`,
default **20**) · `first-seen` · `interim` (bounded backstop, `CLAUDE_SUMMARY_INTERIM_HOURS`,
default **4**) · `active`. Settle-only was rejected deliberately: an in-flight session or
a mid-session reboot would vanish from the report entirely, which is worse than
duplication. A 12h agent run now yields ~5 rows instead of ~145. Read contract
unchanged (append-only, `argMax` per session). New invariant
`session_summary_rows_bounded` is scoped to **`ingested_at`**, so it measures the
current emitter and passes as soon as the fix deploys rather than waiting on the TTL.
Settle (20m) sits deliberately under `SUMMARY_ORPHAN_GRACE_HOURS = 2`.
*Verified independently against the LIVE v1 state file (420 entries): 420/420 migrate,
0 lost, unchanged transcripts skip on the first post-deploy tick (no migration storm),
changed-and-idle emits the final rollup once.*

**✅ VERIFIED LIVE on the first two post-deploy ticks:**
```
12:27:44  scanned=420 emitted=8  [interim=8 unchanged=412]
12:32:45  scanned=420 emitted=4  [active=7 interim=4 unchanged=409]
```
`active=7` is the proof — seven CHANGED transcripts suppressed because they were
already stamped and still live; under the old code all seven would have re-emitted a
full rollup. Tick 2 saw 11 changed and emitted 4. Stamps accumulate correctly (8 then
12 of 420), so persistence works. The residual `interim` emits are the one-time
post-migration stamping of the 420-session backlog and decay as it converges; steady
state is genuine settles plus bounded 4h interims. Confirm the aggregate after ~a day
with the `count()/uniq(session)` query in item 1b.

**#207 — `git tag -l`: reversing my own call.** #185 excluded `git tag` as "low volume,
outside the remit". **That was wrong and the telemetry proved it.** Extraction over four
drafter runs converged on one gap: `ticket-status` prints `deploy_status: unknown —
release-chain resolution is out of scope`, and the only substitute
(`git tag -l "v5.0.21*" --sort=-version:refname`) was refused — which is what drove the
long compensating archaeology chains. Safe because `-l` structurally pins list mode
(verified: `tag -l -d`, `tag -l --delete`, `tag --list -d` all rc=129, tag intact; bare
`git tag -d` DOES delete, so `tag*` stays forbidden).

**#205** — the unattended pass opts out of the clawgate `PermissionRequest` hook
(`CLAWGATE_REMOTE_APPROVAL=off`, the hook's own documented switch). A permission prompt
at 08:00 is unanswerable by construction. Interactive sessions untouched.

**civitai/talos-infra #753 (DRAFT, not merged)** — least-privilege read-only SA for the
drafter. ⚠ **In that repo merging IS deploying** (Flux tracks `trunk`, `prune: true`,
`interval: 1m` → live in ~1–2 min; upside: reverting the merge deletes the objects).
Grant is `get`/`list`/`watch` only on pods, pods/log, events, cronjobs, jobs,
deployments (+ an optional `metrics` block for `top`, which the evidence shows was used
**0 times**). **No secrets** — deliberately, since `kubectl-ro` refuses Secret reads and
the credential must not grant what the wrapper refuses. Scope mined from the real 07-29
/07-30 runs. **Sufficiency is UNVERIFIED** — nothing tested against the API server;
step 0 of its runbook is an `auth can-i --as` matrix. Failure mode is silent: too-tight
RBAC makes the drafter go quiet (`verification: "unverified"`), not crash.

**Layer B: 164 sessions** (+8). Findings worth carrying forward:
- **No GC for agent git worktrees** — confirmed: **47 `worktree-agent-*` branches**,
  oldest 2026-06-24, and **zero have commits not on `origin/main`**. Safe to delete.
- A **guard-integrity detector** — flag checks that fail identically across consecutive
  PRs or that can pass vacuously (empty test dir exiting 0, stale floor, grep matching
  its own echoed input). Two independent sessions converged on "our guards pass
  vacuously", which is exactly what the #185 audit found.
- Nothing in CI builds the published install paths, so an advertised Nix flake had
  silently broken for all users.

## Layer A re-emit storm — RESOLVED by #208 (kept below for the original numbers)

**97.4% of `session-summary` rows are superseded duplicates** (26,359 of 27,061),
20.4 MiB payload for ~700 sessions, avg 38.5 rows/session, worst 486, +~1,800/day.
Reads stay correct via `argMax(…, ingested_at)`; it is pure waste that compounds.
Fix: **emit-on-settle** (emit once idle N min) and/or a **ClickHouse TTL** on
superseded rollups.

## Next session — priority order (re-derive before trusting this)

1. ~~Verify #185~~ — **✅ VERIFIED 2026-07-30** against the 08:04:52 → 08:21:15 run
   (`Result=success`, no guard aborts). **Blocked rate 0.6% (1/154)**, the full arc
   being 13.5% baseline → 2.7% (#177/#179) → 0.6% (#185). **No over-restriction:**
   `kubectl-ro` 7 ran / 0 blocked (the wrapper serves real cluster reads), `node`
   44/0, `gh` 12/0, `git` 67/1, `ticket-status` 23/0. The one block was
   `git … branch -a --sort=-committerdate | head -20` — a PIPE, so a shape-contract
   rejection, not an allowlist gap (and `branch -a` is exact-form-only by design,
   per #177). 22 queue records, 1 `unverified`.
1b. **Verify #208 on live data after ~a day:**
   `SELECT count()/uniq(session) FROM activity.events WHERE kind='session-summary' AND ingested_at > now() - INTERVAL 24 HOUR` — expect a small number, not 38.5. The
   `session_summary_rows_bounded` invariant should pass. The 26,359 historical dupes are
   NOT cleaned (they age out under the 180d TTL; a one-off cleanup is a separate
   homelab-side call). Also open: `scripts/collector/opencode/session_tailer.py` has the
   identical per-tick re-emit design and was left alone.

2. **🔴 Least-privilege the prod credential — DRAFTED, DECISION PENDING (PR #753).** `PROD_KUBECONFIG`
   points at `admin@civit-datapacket-talos` (**cluster-admin, client certificate**).
   `kubectl-ro` is a wrapper in front of an admin cert; three distinct shell-reaching
   paths were found and closed in two days, so assume a fourth. Durable fix: a
   read-only ServiceAccount + ClusterRole (get/list/watch on pods, deployments,
   cronjobs, jobs, events, logs) on the datapacket prod cluster, and point
   `PROD_KUBECONFIG` at it. **Not started — it's client production infra in another
   repo (`datapacket-talos`) and needs an RBAC review. Zach's call.**
3. **Layer A re-emit storm** — bounded, clearly scoped, untouched since 07-11.
4. **Keep extracting** — ~49 pending. Now that the drafter gap is closed, the next
   dominant finding is unknown; that's the point of continuing.
5. Small: the clawgate `PermissionRequest` hook timeout, and the `shell-env-nudge`
   contradiction (both above).

### The meta-lesson from this session
Every fix here was found by exercising the real thing, and every one of the four
security findings was missed by the tests that were supposed to cover it:
- the `gh -R` gap — invisible until a post-fix session was extracted;
- the `git -c` RCE — invisible until someone ran the exploit;
- the kubectl flag-ordering bypass — the tests only built `f"kubectl {verb}"`;
- the `--raw` Secret read — the allow/deny patterns are identical either way.

**Pattern-level reasoning kept agreeing with itself and being wrong.** Prefer
structural fixes (pin the literal prefix; validate in a wrapper you control;
least-privilege the credential) over enumerating strings, and test the artifact's
runtime behaviour, not your model of it.

## Operating notes
- Reader creds + the `sops -d --input-type yaml` gotcha: the `activity` skill.
- Extraction flow: `cli.py status|prepare|write`, monster sessions (>~250 chunks) get
  their own subagent and are SAMPLED; small ones batch. Do **not** bias extractors
  toward an expected finding — it destroys the verification value.
- Parallel agents in this repo **need `isolation: "worktree"`** — a mid-task
  `git checkout` on the shared tree disrupted an agent this session.
