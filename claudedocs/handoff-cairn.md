# Handoff: cairn — 2026-08-26

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
The agent-facing layer of devrc now has a name — **Cairn** — and a direction: keep iterating
locally, then decouple it into something deployable that a **team instance** can read and write.
This doc records what shipped, what the name applies to, and the coupling points that block a
team instance, each measured rather than assumed.

## State now
- Branch `main`, 1 behind origin at snapshot time; two files dirty from another session
  (`claudedocs/close-the-loop/STATE.md`, `claudedocs/the-algorithm-applied-2026-06-17.md`).
- **The name is Cairn** (2026-08-26). It names the whole three-noun layer — **sessions ·
  claimable tasks · subsystems** — not just the index. Chosen over Almanac / Muster / Atlas:
  one syllable, no collision with the crowded `claw` family, and a cairn is a marker left on a
  trail so whoever comes next knows the way — which is what a subsystem-index entry is.
- **Eight PRs merged**, each verified by content on `origin/main` after squash (a squash never
  makes the branch head an ancestor, so ancestry lies):

  | PR | squash | what |
  |---|---|---|
  | #816 | `f881856b` | awaiting-predicate contract — the consolidation was a REGRESSION; shipped a pinned contract |
  | #819 | `e18e55d7` | transcript-search consolidation — **7 real bugs**, each watched red at base |
  | #829 | `fed5c5c8` | the explainer page + `scripts/present/` generator |
  | #855 | `1d67f5e8` | xdist unblock — `main` was red for EVERY PR |
  | #872 | `30acd174` | three-noun restructure |
  | #862 | `2d4b2980` | workbench-local serving + daily regeneration |
  | #882 | `d78af6c8` | three real-process flakes, reproduced on demand and mutation-verified |
  | #889 | `1f3d854c` | this handoff doc |

- **The explainer page is deployed, not merely merged**: `present-serve.service` active,
  `present-regen.timer` armed, both `serverMode`-gated. `HTTP 200` at
  `http://192.168.50.250:8900/`, `X-Present-State: fresh`. `/sanitized` serves the redacted
  variant. Regenerates 05:06 daily.
- 🔴 **Workbench-only, deliberately.** 8900 is NOT in `/etc/nixos/configuration.nix`'s
  `allowedTCPPorts`. From the laptop: `22 OPEN, 443 OPEN, 8899 CLOSED, 8900 CLOSED` — and 8899
  is `initiatives-viewer` on the identical address with the identical gap, which is why
  following that precedent warned nobody. LAN/nebula reach is deferred behind a named trigger:
  *someone not on the workbench actually needs to read it*.
- **Board state:** #359 `complete` by supersede (not by criteria — none of its five were
  validated); **#360 `in_progress` in another session** — `98843002…`, project `homelab-talos`,
  tmux `scratch15:@334`, peer `homelab-talos-02`; **#366 `open`**, the supersede decision card.
- **#360's session has been told the CLI should ship as `cairn`**, not `scripts/store`, via
  `SendMessage`. Also passed on: the team-instance direction reframes its criterion 6 token
  from per-*host* to per-*person*, and `sensitivity: client-confidential` becomes an
  authorization boundary rather than a transit question.

## Open investigations — live diagnosis state

### ~~The sanitized export leaks three scope names~~ — DIAGNOSED AND FIXED
🔴 **The leading hypothesis in the previous handoff was WRONG, and acting on it would not
have fixed the leak.** It read: *short, word-like scope names fall below the length ladder;
the fix is a scope allowlist keyed on the store's own scope set.* Measured 2026-08-26:

- **They were not scopes at all.** The store's scope set is `civitai`, `civitai-*`,
  `claude-pool`, `cli`, `datapacket-talos`, `devrc`, `flipt-state`, `homelab-infra`,
  `homelab-talos`, `kubeclaw`, `storage-resolver`. `naida`, `vetr` and `auditloop` are absent,
  so the scope class removed **zero** of the leaked occurrences — the one that *did* vanish in
  each pair (`vetr.com`, `auditloop.zacx.dev`) went via the **host** rule.
- **The length ladder was a red herring.** `auditloop` is 9 chars and would have been matched
  aggressively *if it had been a scope*.
- **The store's scope set was already the substitution source** (`sanitize.build()` →
  `measurements.by_key("index.store")`). It is a curated index, not an inventory of names that
  must not leak.
- **The real class:** all three survivors sat in the `skills.inventory` row — human-authored
  prose harvested out of `claude/skills/*/SKILL.md`. Substitution redacts identifier CLASSES it
  has been shown; a harvested sentence has no class at all.

🔴 **Widening the name list was tried and REJECTED ON MEASUREMENT, not on taste.** Sourcing
names from the index store + the skill directories + all 149 directories under `~/workspace`
still left `naida` **fully intact** — the directory is `naida-ai` and the leak was a bare
`naida` in a sentence — while corrupting ordinary English: `"run the test suite from a scratch
dir"` → `"run the scope-108 suite from a scope-104 dir"`. **A name list is fail-OPEN by
construction**; it closes exactly the names something happened to be called after, and an
entitlement boundary may not be.

**The fix (this PR):** a measurer DECLARES what each column holds
(`Measurement.column_kinds`): `""` ordinary, `"name"` a local identifier substituted *inside
that cell only*, `"prose"` **withheld** in a sanitized build. Withholding is driven by the
declaration, never by a name list, so it still happens on a host where every name source is
empty. An unknown kind is withheld too — the fail-closed direction.

- **Verified** — every client name to 0, with the mechanism proven live, not a bare zero:
  `naida 2→0`, `vetr 4→0`, `auditloop 4→0`; controls `civitai 5→0`, `datapacket 2→0`,
  `kubeclaw 2→0`, `zacx.dev 3→0`; page still renders **37 rows, 37 withheld cells, 77 name
  stand-ins**; ordinary prose uncorrupted (`test` 23 in both builds).
- **Residual, stated honestly:** devrc's *own* subsystem names still appear — in file paths
  (`scripts/repo-cos/tests`), systemd unit names (`repo-cos.timer`) and `content.py`'s static
  narrative, none of which pass through a declared column. That is correct: the page is *about*
  devrc. It would become a leak if a script were ever named after a client.
- 🔴 **Two `SANITIZE DEGRADED` lines still print, and both are honest.**
  *"hostname indistinguishable from a word"* is the nodename; *"scope matched in its exact form
  only"* is the 3-char scope `cli`. Both are deliberate declines, counted in the legend. They
  are **not** the leak and were never related to it.
- **Separately worth a decision (not fixed here):** `claude/skills/*/SKILL.md` is tracked in
  this **public** repo, so those client names are already published in git. The sanitizer is now
  correct; the repo-level exposure is a different question.

### Tekton silently did not fire for one push
- **Symptom + exact repro:** pushed `4b9e692a` to a PR branch; both required checks sat
  `pending` for 45 min with no PipelineRun ever created.
- **Observed:** `kubectl get pipelinerun -n tekton-ci -o jsonpath=…` over all runs matched
  **no** row for that revision, while runs for other shas existed and completed normally. A
  later push of the same tree under a fresh sha ran and passed.
- **Ruled out:** the documented `timeouts.tasks` stall — that leaves a PipelineRun that exists
  and never reports; here nothing was created at all.
- **Leading hypothesis:** a dropped webhook delivery. Unconfirmed.
- **Why it matters:** from GitHub's side this is indistinguishable from a slow run, and both
  checks are required with `enforce_admins: true`, so the PR is unsatisfiable until someone
  reads the cluster. Only a fresh push clears it.
- **Next probe:** check the EventListener pod logs for that delivery id, and whether GitHub's
  webhook delivery log shows a non-2xx for it.

### Six remaining positional audit reads — same class as a fixed flake
- **Observed:** `scripts/tests/test_subsystem_store_api.py` has **38** audit-list reads, all
  after the `with` block and none waiting; **12** make more than one auditable request and
  **7** read positionally. #882 fixed one of the seven.
- **Leading hypothesis:** the other six are latent instances of the same race — a list appended
  to by `ThreadingHTTPServer` handler threads, read by index.
- 🔴 **`await_audit()` guarantees the lines EXIST, not their ORDER** — that is why flake 2 fired
  despite already calling it. Any conversion must assert a multiset or select by identity.
- **Closing condition (stated, mechanical):** a PR converting all 11 sites and showing each red
  under a forced `_audit` delay.
- **Rejected deliberately:** `daemon_threads = False` — measured to work, but several tests
  leave a request unfinished on purpose and an unbounded join in a required check is a blind
  trade.

## Next steps (ranked)
1. ~~**Fix the sanitizer leak**~~ — **DONE**, see the diagnosis above. The proposed fix was
   measured not to work and was replaced: declaration-driven **withholding** of harvested
   prose, plus name substitution confined to declared identifier cells.
2. ~~**Build `cairn who <task>`**~~ — **DONE**, PR #917 (squash `c39abe31`), shipped to both
   hosts. Three things the chain description below did NOT carry, each measured and each
   changing the design:
   - 🔴 **A tmux window is TRANSIENT; a transcript is DURABLE, and collapsing them is the trap.**
     The worked example below *no longer resolves* — #360's window is gone while its 6 MB
     transcript sits where it was written. A window-keyed resolver answers "nobody" for almost
     every task older than current uptime. So each session yields TWO independent findings.
   - **The join key is not always a uuid** — 39 of 41 live windows carried uuids, 2 carried
     `ses_…` tokens. A shape-validating join silently matches nothing and reports a clean
     "no live window".
   - **`session-manager --lean` omits `pane_id`/`window_id`/`codename`** — three of the four
     things the command prints. Pinned by a test, since `--lean` is the obvious "optimisation".

   Five states are kept distinct because they all print near-nothing: `resolved` ·
   `no-sessions-recorded` (a UI-filed task genuinely has none — exit 0) ·
   `sessions-recorded-but-none-located` · `task-not-found` (7) · `bad-task-id` (2) ·
   `clawgate-unreachable` (8). The pair that matters is *"the answer is no"* vs *"there was no
   answer"*. 🔴 **An unmeasured live half is never rendered as "no window"** — if any host goes
   unmeasured the absence is UNMEASURED, and transcripts are still reported.
3. **Convert the six remaining positional audit reads** —
   `scripts/tests/test_subsystem_store_api.py`. One PR covering all 11 sites, each shown red
   under a forced `_audit` delay. Repo: `devrc`. 🔴 **Do this AFTER #360 lands** — that card may
   restructure the file substantially.
4. **Instrument the Tekton non-fire rather than chase it** — a check that flags "PR has required
   checks pending with no PipelineRun for that sha". Bounded and useful whether or not the root
   cause is ever found; chasing one dropped webhook is not. Repo: `homelab-talos`.
5. **#366** — the supersede decision card, `open`. Repo: `homelab-talos`,
   `containers/clawgate/`.

## Gotchas / decisions / dead-ends
- 🔴 **The store-api retirement was proposed and CLOSED by the operator** (devrc #849,
  homelab-infra #404, both `mergedAt=null`). The pivot: clawgate task **#360** makes the hosted
  store the single datastore and ships the missing phase-2 CLI + phase-3 append write path.
  Closing those two PRs is literally #360's criterion 1. **Do not re-propose retirement** — the
  store was unused because the *client* was never built, not because the need was absent.
- **The "must never gain a git remote" rule is bad and is being removed** (operator ruling
  2026-08-25, `claudedocs/proposal-subsystem-store-homelab.md:23`). It is absolute where its own
  stated intent was narrower — no *third party*. Replacement must keep the third-party
  prohibition while permitting a remote on a host you own.
- **Three flakes were fixed by reproducing them first**, not by re-running: `2/50 → 100/100`,
  `1/1 forced red → green`, `4/180 → 0/180` with lock sightings `43/48 → 0`. A flake that merely
  stops failing is not fixed — passing is what a flake already does.
- **A `sleep` in disguise was masking one of them**: `httpd.shutdown()` polls on a 0.5 s
  interval, burning half a second before the assertion while ordering nothing.
- **Two tiers, repeatedly.** The nix sandbox caught defects the dev-host tier structurally
  cannot see, three times: a `git ls-files` call with `check=True` (no `.git` in the sandbox), a
  hardcoded `== 2` that was really a claim about one machine, and a shebang that resolves only
  on the dev host. Always run both and name the tier.
- **`nix build` writes an empty marker out-path** and a `| tail` swallows the build status —
  read the derivation log, never the exit code. This bit me directly (`BUILD_RC=0` beside
  `RESULT: FAIL`).
- **A defect in `main` can masquerade as your PR's.** #849 and #872 were each blocked by
  failures they did not cause (an xdist ID collision; a real-process race). The control that
  settles it is running the same target on a clean checkout of `main` — cheap, and it twice
  stopped an agent hunting a phantom in the wrong subsystem.

- **Why the name happened at all, and why the direction changed — recorded here rather than
  under a REPLACE heading so a later status update cannot drop it.** Zach presented the system
  on 2026-08-26 and **it went well**; the three outcomes were: keep iterating locally, move
  toward decoupling it into something deployable that a **team instance** teammates read and
  write into, and give it a name simple enough to refer to. **Cairn** was chosen over Almanac /
  Muster / Atlas — one syllable, no collision with the crowded `claw` family (clawgate,
  kubeclaw, openclaw, fuzzyclaw, clawdbot), and semantically exact: a cairn is a marker left on
  a trail so whoever comes next knows the way, which is what a subsystem-index entry is. The
  team-instance half is the load-bearing part — it turns `sensitivity: client-confidential` from
  a transit question into an **authorization** question, which nothing in the system has today.
- 🔴 **The task→session→window→transcript chain RESOLVES today, but takes four tools and a jq
  join — no single command does it.** Demonstrated end to end on #360:
  `clawgate task get 360 | jq .sessions` → session `98843002…` (clawgate embeds sessions on task
  reads, with a `role` of `created`/`read`/`worked`) → `session-manager --json`, key
  `claude_session_id` → `pane_id` `%334` → tmux `scratch15:@334` on workbench → `ListAgents`
  peer name `homelab-talos-02` → transcript
  `~/.claude/projects/-home-zach-workspace-homelab-talos/98843002….jsonl`. 🔴 **`ListAgents`
  labels peers by a short ref that is NOT the session uuid** (`5e2e92`, not `988430`), so the
  window↔uuid join must go through `session-manager`, not through the peer name. The dispatch-pod
  case is the same lookup with a different backend (`devpod-<agent-name>` instead of a pane).
- 🔴 **clawgate ALREADY HAS supersede, and it was unreachable on the only real supersede.**
  `POST /tasks/merge` implements it properly (nothing deleted, loser → `complete` + comment,
  winner gains the tag union). Measured 2026-08-26: session route → **409** (refuses when either
  task is `in_progress` or `complete`, and the successor was in_progress *because* it
  superseded), `/api/tasks/merge` → **405** (no machine counterpart), `clawgatectl` → no verb.
  **The hand-rolled workaround produced a WORSE record**: a `claude-code` comment instead of the
  `user`-authored audit comment merge writes, which `taskCommentAuthor` structurally cannot mint.
  Filed as **#366**. Do not file "build supersede" — it exists.
- 🔴 **No clawgate status means "superseded".** Statuses are exactly
  `open`/`in_progress`/`ready_for_review`/`complete`. #359 is `complete`, which is false — none
  of its criteria were validated — and the truth survives only because a human wrote a comment.
  That is the part most likely to break a team instance, where nobody reads every comment.
  Dismissal is not the alternative: it **deletes** the task.
- **`/handoff`'s clawgate resolver lands in the no-worked case for a session that FILES cards.**
  `created` is terminal upstream and outranks `worked`, so a session that created a task *and*
  commented on it *and* flipped its status still reports `created`. Both handoffs this session
  hit exit 6 and recorded no field — correct, but worth knowing before you go looking for a bug.
- **A decision task is not an implementation task.** #366 was deliberately scoped to produce a
  written decision with named checkers, not code — because writing acceptance criteria before
  the design is settled is what produced #359's criteria being rewritten mid-flight.

## How to verify
```bash
# the page is live and current on the workbench
curl -s -D- -o /dev/null http://192.168.50.250:8900/ | grep -i '^x-present'
#   expect: X-Present-State: fresh   (and X-Present-Stale: 0)

# it carries the three-noun structure
curl -s http://192.168.50.250:8900/ | grep -c 'Where the three touch'   # expect 1

# workbench-only is a fact, not an assumption — run FROM THE LAPTOP
ssh zach@10.42.0.100 '(echo >/dev/tcp/192.168.50.250/8900) 2>/dev/null && echo OPEN || echo CLOSED'
#   expect: CLOSED   (and 22/443 OPEN, proving the probe works)

# the sanitizer, with BOTH controls — a bare zero cannot tell a working
# sanitizer from one wired to nothing, so check the page still has content
# 🔴 `grep -o | wc -l`, NEVER `grep -oc`. With GNU grep, `-c` counts matching
# LINES and overrides `-o`, so the positive controls below return 1 instead of
# 37/77 and the fix reads as failed. This host's `grep` is a ugrep WRAPPER where
# `-oc` does count occurrences — which is exactly why the wrong form looked fine
# when it was written. The piped form is right under both.
python3 -m scripts.present.generate --sanitize -o /tmp/san.html   # 2 DEGRADED lines, both honest
grep -o -i naida /tmp/san.html   | wc -l   # expect 0  — was 2 before the fix
grep -o -i civitai /tmp/san.html | wc -l   # expect 0  — control: substitution works
grep -o WITHHELD /tmp/san.html   | wc -l   # >0 — positive control: the page is NOT empty
grep -o 'name-[0-9][0-9]' /tmp/san.html | wc -l   # >0 — identifiers renamed, not dropped
grep -o -i ' test ' /tmp/san.html | wc -l  # equal in BOTH builds — prose NOT corrupted

# cairn who — the task -> session -> window -> transcript join
cairn who 360            # expect rc 0; each session shows BOTH a window line and a transcript line
cairn who 99999999       # expect rc 7  — task-not-found
cairn who not-a-number   # expect rc 2  — bad-task-id (clawgate ANSWERED with a 400)
#   🔴 rc 7 and rc 8 are the pair that matters: "the answer is no" vs "there was no answer".
#   A session whose window is gone still resolves via its transcript — that is the design,
#   not a degraded result.
```
🔴 **Do not pin the last four to a literal count here.** They move whenever a skill or an rc
code is added, and a stale number in a doc reads as a failed fix. Compare the sanitized build
against the full one from the same commit instead.
