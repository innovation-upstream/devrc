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

### The sanitized export leaks three scope names
- **Symptom + exact repro:** `python3 -m scripts.present.generate --sanitize -o <path>` prints
  two `🔴 SANITIZE DEGRADED` lines — *"hostname indistinguishable from a word"* and *"scope
  matched in its exact form only"* — and the output retains real scope names.
- **Observed (with values):** counts in sanitized vs full output —
  `naida 2 vs 2` (**wholly unsanitized**), `vetr 3 vs 4`, `auditloop 3 vs 4`.
  Positive control on the same run proves the mechanism works: `civitai 0 vs 5`,
  `datapacket 0 vs 2`, `kubeclaw 0 vs 2`, `zacx.dev 0 vs 3`.
- **Ruled out:** the sanitizer being broken generally — four identifier classes go to zero.
  Ruled out an operator-identity leak: username, email and FQDNs all sanitize to 0.
- **Leading hypothesis:** short, word-like scope names (`naida`, `vetr`, `auditloop`) fall below
  the length ladder / exact-form rule in `scripts/present/sanitize.py`, which was deliberately
  narrowed so that rewriting the English acronym `CLI` (a real scope) stopped corrupting prose.
  The fix is likely a scope allowlist keyed on the store's own scope set rather than a length
  heuristic.
- **Why it matters beyond tidiness:** "the portable export serves the off-workbench reader" is
  the argument that justified deferring LAN/nebula reach. If the export is not clean, that
  argument is weaker than stated. With a **team instance** it stops being cosmetic entirely and
  becomes an entitlement boundary.
- **Next probe:** `sed -n '/def .*scope/,/^def /p' scripts/present/sanitize.py` and read the
  length ladder; then check whether `measure`'s scope set can drive substitution directly.

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
1. **Fix the sanitizer leak** — `scripts/present/sanitize.py`. Drive substitution from the
   store's own scope set (which `measure` already resolves) instead of a length/word heuristic.
   Verify with the control pair, never a bare zero. Repo: `devrc`. **Do this before the team
   instance** — the heuristic *becomes* the entitlement boundary the moment someone who is not
   Zach reads a page.
2. **Build `cairn who <task>`** — the task→session→window→transcript resolver. Every hop exists;
   only the join is missing (see the Gotchas block below for the exact chain). Repo: `devrc`.
   This is the first capability that is *about* Cairn rather than inherited from devrc.
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

# the sanitizer leak, with its positive control
python3 -m scripts.present.generate --sanitize -o /tmp/san.html   # prints 2 DEGRADED lines
grep -oic naida /tmp/san.html      # expect 2  — leaked
grep -oic civitai /tmp/san.html    # expect 0  — control: the mechanism works
```
