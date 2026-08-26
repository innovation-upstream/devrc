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
- Branch `main`, clean apart from two files another session is editing
  (`claudedocs/close-the-loop/STATE.md`, `claudedocs/the-algorithm-applied-2026-06-17.md`).
- **The name is Cairn**, decided 2026-08-26 after a presentation that went well. It names the
  whole three-noun layer — **sessions · claimable tasks · subsystems** — not just the index.
  Chosen over Almanac / Muster / Atlas: one syllable, no collision with the crowded `claw`
  family (clawgate, kubeclaw, openclaw, fuzzyclaw, clawdbot), and semantically exact — a cairn
  is a marker left on a trail so whoever comes next knows the way, which is what a
  subsystem-index entry is.
- **Seven PRs merged this session**, each verified by content on `origin/main` after squash
  (a squash never makes the branch head an ancestor, so ancestry lies):

  | PR | squash | what |
  |---|---|---|
  | #816 | `f881856b` | awaiting-predicate contract — the consolidation was a REGRESSION, shipped a pinned contract instead |
  | #819 | `e18e55d7` | transcript-search consolidation — **7 real bugs**, each watched red at base |
  | #829 | `fed5c5c8` | the explainer page + `scripts/present/` generator |
  | #855 | `1d67f5e8` | xdist unblock — `main` was red for EVERY PR |
  | #872 | `30acd174` | three-noun restructure (sessions · claimable tasks · subsystems) |
  | #862 | `2d4b2980` | workbench-local serving + daily regeneration |
  | #882 | `d78af6c8` | three real-process flakes, each reproduced on demand and mutation-verified |

- **The explainer page is live and deployed** (not just merged): `present-serve.service` active,
  `present-regen.timer` armed, both `serverMode`-gated. Serving `HTTP 200` at
  `http://192.168.50.250:8900/`, 117,561 B, `X-Present-State: fresh`. `/sanitized` serves the
  redacted variant. Next regen 05:06 daily.
- 🔴 **Workbench-only, deliberately.** Port 8900 is NOT in `/etc/nixos/configuration.nix`'s
  `allowedTCPPorts`. Measured from the laptop: `22 OPEN, 443 OPEN, 8899 CLOSED, 8900 CLOSED` —
  and 8899 is `initiatives-viewer` listening on the identical address, which is why following
  that precedent warned nobody. LAN/nebula reach is deferred behind a named trigger: *someone
  who is not on the workbench actually needs to read it*.

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
1. **Tell task #360's session the CLI should be named `cairn`.** #360 is IN FLIGHT in another
   session right now and its criterion 2 specifies a CLI at `scripts/store`. If that ships
   unnamed, the rename becomes a follow-up against freshly-merged code. Repo: `devrc`.
2. **Fix the sanitizer leak** — `scripts/present/sanitize.py`, evidence above. Repo: `devrc`.
   Verify with the control pair, not a bare zero: sanitized vs full counts for `naida`, `vetr`,
   `auditloop` alongside `civitai`/`zacx.dev`.
3. **Convert the six remaining positional audit reads** —
   `scripts/tests/test_subsystem_store_api.py`, closing condition above. Repo: `devrc`.
4. **Diagnose the Tekton non-fire** — EventListener logs + GitHub webhook delivery log.
   Repo: `homelab-talos` (Tekton lives on homelab).
5. **Ratify or close clawgate #359**, superseded by #360. Board decision, no repo.

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
