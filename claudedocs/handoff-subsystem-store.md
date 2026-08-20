# Handoff: subsystem-store — 2026-08-18 (🔴 THE STORE IS PUBLIC; cutover done)

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo ~/workspace/devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it — the
`devrc/subsystem-index` entry describes the very tooling below. 🔴 RECALL, NOT LIVE
OBSERVATION: every line is a pointer to VERIFY, never a current reading, and it may describe
a gotcha already fixed. `scope-absent`/`scope-empty` means nothing recorded yet: ordinary,
not an error, not a clean bill of health. Non-blocking — if it exits non-zero, print the
stderr line and carry on. **Two measured sessions skipped this when it was only reachable via
`/resume`; it is here because reading this doc is the one thing both of them did first.**

## Goal
A durable store for subsystem data + history with clean Claude Code integration, **reachable
from anywhere**. The local half is built, deployed and verified at the consumer on both hosts:
the read half is cheap enough to run every `/resume`, searchable, and survives a malformed
entry. The server half reached its cutover on 2026-08-18: **`store.zacx.dev` is live on the public
internet**, proxied through Cloudflare and gated on two things — a bearer token, and a required
`CF-Connecting-IP` header. See
"State now — 🔴🔴 THE STORE IS PUBLIC" (the SECOND such block; the first is historical).

⚠ Two earlier revisions each closed with a framing the next day falsified — *"what remains is
measurement, not a build"*, then a phase table that outlived its own cutover. **The standing risk in
this doc is a stale state claim read as current**, which is why the superseded block below is
labelled rather than deleted.

## ⚠ SUPERSEDED — this block describes 2026-08-16. Jump to "State now — THE STORE IS PUBLIC".

🔴 **This doc has TWO "State now" sections and this is the OLD one.** It is kept because the
open-investigations below reference it, but every exposure claim in it is now false: the store went
public on 2026-08-18 (`#329`). The authoritative block is further down and titled
**"State now — 🔴🔴 THE STORE IS PUBLIC"**. If you read only one, read that one.

- **devrc `main` at `6bc6518`; homelab-infra `trunk` at `4f6ced02`.** ← both long superseded.
- ~~**A pod serves the store on homelab, cluster-internal.** No ingress, no public exposure, no DNS,
  no Authelia rule — that is phase 1.5 and it has NOT been done.~~ **All four halves of that
  sentence are now wrong**: there IS an ingress, DNS resolves via Cloudflare, and there is
  deliberately no Authelia (a forward-auth 302 is unusable from a CLI). The ClusterIP is still 8102.
- **The design doc is `claudedocs/proposal-subsystem-store-homelab.md`.** ⚠ Its header states what
  was built vs still proposed **as of phase 1** — it has not been updated for the cutover, so treat
  its phase table as historical too.

### Shipped this session (9 PRs)

| PR | repo | what |
|---|---|---|
| `#507` | devrc | two stranded rule lessons rescued (mutation-isolation; Loki partial results) — shipped + verified at the consumer on both hosts |
| `#508` | devrc | `.envrc` + `.opencode/` were unignored in a PUBLIC repo |
| `#509` | devrc | the migration proposal |
| `#510` | devrc | the laptop's logitech pair — applied on that host 2026-08-13, never committed |
| `#512` | devrc | **phase 1: the HTTP API over the existing reader** + 1,282 lines of tests |
| `#516` | devrc | the proposal header still said "nothing built" after phase 1 shipped |
| `#326` | homelab-infra | **phase 1 manifests** — ns, Deployment, Service, PVC, SOPS secret |
| `#327` | homelab-infra | the checker's rc 1 meant two things — a crash could spell a verdict |

`#503`/`#504`/`#505` (previous session) remain shipped + verified at the consumer on both hosts.

### 🔴 What phase 1 was NOT (historical — several of these have since been closed)
Read this before trusting the 2026-08-16 block above as coverage:
- ~~**Nothing has been tested off-mesh.**~~ **CLOSED 2026-08-18** — tested over the public internet
  via Cloudflare: 200 authed / 401 unauthed / 404 on `/`, client IP intact.
- ~~**The `(B-required)` hardening is not built**: no rate-limit, no lockout, no split read/write
  tokens. **Token rotation has never been exercised once.**~~ **MOSTLY CLOSED 2026-08-18** — the
  rate limiter, the lockout and the mandatory client-IP all ship in `0.2.0` and are live; rotation
  was exercised end to end (`#344`+`#345`). ⚠ **Split read/write tokens were NOT built** and remain
  outstanding. The Cloudflare WAF rule (layer 1) **was missing entirely and has since been created**
  — see the RESOLVED block below, including why it is the shallowest of the three layers.
- **88 of the 89 API tests are invariant guards, not regressions** — `server.py` did not
  exist at base, so "red at base" is a collection error and proves nothing. The evidence
  that they bite is the 22-mutant sweep, not the red-at-base matrix.
- **`seed.sh --push` has no hermetic test** (needs a cluster); it was exercised live 3×.
- **`--mode=full` / `--page` over HTTP** were never byte-compared against the pod; only the
  default digest was.

### `#505` in one paragraph
An entry proposed a one-line remedy at **15:00:18** on 2026-07-24; the remedy landed at **15:02:21** — 2m03s later — and the entry served it as outstanding for **22 days**. `/handoff` step 4 runs MID-session, so the writer is gone by the time the work finishes. Openness is now a typed prefix, not prose, because a prose detector is walkable by rewording. Six populations, one precedence source (`JournalBullet.openness_population`); `--validate` reports four of them and its VERDICT is deliberately unchanged (an entry with unfinished business is well-formed; failing it would be a permanently-red gate). Index rows gain `🔴 N OPEN`, conditional so the common case is byte-identical.

## Open investigations — live diagnosis state

### The worktree-path bucket — MEASURED 2026-08-15, and the framing was wrong
🔴 **`--session` is THIN, not dead. "Structurally empty here because of the worktree rule" is
REFUTED.** Over 14 recent devrc sessions, using the repo's own `collect_session_paths`: 12 did
file work, **only 1 had an empty in-cwd set**, and **41 of 232 paths (17.7%) landed under cwd** —
independently matching the 13.9% over 3,913 paths recorded below, from a different sample. What
IS true is the threshold: only **5 of 12** reached the 2 paths a nomination needs, so the window
usually cannot nominate even when it is not empty. `--pr`/`--commit` stay primary for work that
LANDED; `--session` stays worth running.

🔴 **How this nearly went the other way.** Three agent reports of "0 under cwd, N outside" were
about to be written into the store as a structural fact about this host. They are the tail: 1 in
12. The scan takes two minutes and the entry it would have created would have outlived the
session that got it wrong — which is the entire premise of the store. **Do not promote an
anecdote to a structural claim without the scan.**

⚠ **The CLI cannot reproduce this.** `--session` refuses any transcript not written in the last
30 minutes ("this is not the session that is running now"), which is correct for a writer and
fatal for a retrospective. Measure through `collect_session_paths(..., max_age_seconds=inf)`,
which reads the path distribution without pretending to be those sessions.

- **Original symptom (the tail case, not the norm):** a session whose edits all land in a
  throwaway worktree gets `status=looked-at-nothing` from `--session`. Seen **4×** on 08-13;
  most recent: 0 paths under cwd, **23 outside**, all worktree paths (civitai scope).
- **Observed:** `#459` does **NOT** fix this. Its window reports paths lexically under
  `--repo`; a worktree at `/tmp/…/wt-x/src/foo.ts` is not. Measured bucket: **143 of 945**
  outside-cwd paths were temp worktrees, of which only **32 still existed on disk** — and 38
  such worktrees were deleted the same day. The correct source today is `--pr` / `--commit`,
  which is what all 4 sessions used.
- 🔴 **Ruled out — my own motivating number was WRONG.** I claimed 112 cleanly-attributable
  cross-repo paths. Actual derivation: 185 loose → −97 same-repo-from-a-worktree → 88
  different top-level dir → −58 `devrc-*` **sibling worktree directories of devrc itself**
  (`devrc-fix443`, `devrc-clickup`, `devrc-clawgate-ext`, `devrc-cutguard`, `devrc-458fix`,
  `devrc-fix444`) → **30 genuinely cross-project** (24 `homelab→civit`, 6 `homelab→devrc`).
  Corroborated independently at 33. The bug was `repo_of()` treating every top-level dir under
  `~/workspace` as a separate project — a label claiming more than its predicate tested.
  **The 112 is RETRACTED; do not re-derive it.**
- **Leading hypothesis:** the practical win from `#459` is mostly the **97 same-repo-from-a-
  worktree** paths, not the 30 cross-project ones. Worktree→repo mapping is fragile by
  construction — nothing in a transcript maps a worktree back to its repo, and the worktree is
  usually already gone.
- **Next probe (verbatim):** before building anything, measure the yield —
  `python3 scripts/lib/subsystem_touch.py --repo <r> --session <uuid>` on a worktree-only
  session, and read `changed_paths_outside_cwd` against what `--pr` recovers for the same work.

### The doc hook FIRED on its first exposed continuation — n=1, and that 1 is contaminated
- **Symptom:** the index read is skipped by sessions started from a kickoff block.
- 🔴 **Every earlier zero was measured PRE-EXPOSURE — the fix had n=0, not n=2.** `#457` put
  the block in the doc at **20:36Z**. Reconstructing each session's actual `Read` *result* and
  grepping it for `Run this first`: `d5db63c4` read the doc 3× (17:54Z, 18:02Z, 19:34Z) — all
  before 20:36Z, hook absent from every one. `e98279bd`'s later read (22:27Z) was
  `offset:140, limit:95` and never saw the top of the file. **Exposure is a property of the
  READ, not of the session's start time — check the tool_result, not the clock.**
- **Observed (2026-08-13, session `4a7d5bf8`, the FIRST exposed continuation):** read the doc
  at turn 1 → ran `subsystem_recall.py --repo` as its **first Bash call**, before any other
  work. `Skill` calls **0** (so `/resume` never loaded — the prefix is still inert as prompt
  text). Attribution is clean: `subsystem_recall` appears in **zero** always-loaded surfaces
  (`~/.claude/{CLAUDE,RULES,PRINCIPLES}.md`, `devrc/CLAUDE.md`, `MEMORY.md`); the only surface
  naming it is the `/resume` skill body, which was never loaded. **The doc was the sole path.**
- 🔴 **Contaminated — do not read this as an adoption rate.** That session's kickoff said
  *"measure whether the doc hook actually fires"*, which primes the behaviour being measured.
  It establishes the mechanism is **capable** of firing unaided by `/resume`; it does **not**
  establish that a session with an ordinary kickoff will run it. **n=1 uncontaminated: 0.**
- **Ruled out:** that the `/resume` prefix alone suffices (`#446`'s claim, retracted in
  `#457`). A subagent receives the kickoff as prompt TEXT — no CLI slash-command parsing.
- 🔴 **Ruled out — counting MENTIONS is not the measurement.** A grep showed `recall=3
  touch=60` and read as success; parsing actual `tool_use` calls gave **0** recall executions.
  The 60 were the agent *editing* `subsystem_touch.py`.
- 🔴 **Parsing `tool_use` is NOT sufficient either — three false-positive modes survive it,**
  found only because the probe reported **5** for a session known to have run it **once**:
  (a) **substring containment** — `test_subsystem_recall.py` contains `subsystem_recall.py`,
  so every `pytest` run and every `git add` of the test file counted; (b) **heredoc bodies** —
  `python3 - <<'PY' … PY` reads its script from *stdin*, so the body is DATA, yet splitting the
  command on newlines turns each mentioning line into a fake invocation; (c) `python3 -m
  pytest <path>` / `python3 -c`. Require an **exact basename match** on a token that is the
  script argument, strip heredoc bodies first, and reject `-c`/`-m`/`-`.
- **Instrument validation that the numbers rest on** — three controls, all watched:
  negative (6 mentions, 0 executions) · false-positive (5 mentions in the shapes above, 0) ·
  positive (4 distinct invocation shapes, 4/4). Plus a live control: the measuring session's
  own ground truth of exactly 1 read back as exactly 1. **The pre-fix probe passed the
  positive control and would still have been wrong** — it had no false-positive control.
  The probe itself lives only in that session's scratchpad and is **gone**; the recipe above
  is the durable form. Land it under `scripts/lib/` if this gets measured a third time.
- **Observed (2026-08-13, a DISPATCHED agent, ordinary kickoff naming only the doc and
  next-step 3):** tool call 1 = `Read` the doc, tool call 2 = **execute
  `subsystem_recall.py --repo`**, before any task work. `Skill` calls **0**. So the kickoff
  reaching a subagent as plain prompt text — the thing that made `#446` inert — does **not**
  stop the read happening, because the instruction now travels in the doc the agent reads
  anyway. Counted from the agent's own transcript, not its self-report: it *claimed* it ran
  the index read first, and the claim happened to be true, but the claim is not the evidence.
- 🔴 **The remaining contamination is STRUCTURAL and a staged probe cannot remove it.** This
  doc describes the experiment, and `Read` returns the whole file — so any agent dispatched
  here can see it may be the subject. Two exposed continuations, two fired, neither clean:
  the first was told by its prompt, the second could read about itself. **Do not stage a
  third and call it uncontaminated.** Measure organically instead: parse the next few real
  continuations. The instrument recipe is above; it is three controls and twenty lines.

### The two hosts run different opencode, and five doc lines are false while they do
- **Symptom:** `ship.sh` rc=7 — workbench SKIPPED, laptop converged. The dev-host
  `scripts/gate.sh --tier pytest` is RED on the workbench and will stay red until it converges:
  `test_engine_is_the_version_every_measurement_is_keyed_to` compares the binary against
  `PINNED_VERSION` and gets `assert '1.18.4' == '1.18.16'`.
- **Observed (values):** `readlink -f $(command -v opencode)` → workbench
  `/nix/store/64n428…-opencode-1.18.4`, laptop `/nix/store/rcrzfd71…-opencode-1.18.16`;
  `flake.lock` pins 1.18.16. `ship.sh` named the three blocking files verbatim
  (`scripts/run-tests.sh`, `scripts/tests/test_agent_ledger.py`,
  `scripts/tests/test_session_manager.py`) and left the host **exactly as found** — the
  protective outcome, by design.
- **Ruled out:** that this is a nix error. The laptop's first switch failed the same way
  (`converge exited 9`) on **pre-existing FOREIGN files** — `~/.config/opencode/agent/review.md`
  and `plugin/guard.js`, read-only with 1969 mtimes, stale pre-#469 store copies. Preserved to
  `~/foreign-opencode-preserved-2026-08-13`, diffed (they differed only by #469's edits), removed,
  re-switched clean.
- **Blocked on, not broken:** the blocker has since cleared — that session committed and merged —
  so `ship.sh` would now SUCCEED at `git checkout main` and switch a live session off
  `feat/agent-activity-ledger`. That is why it was not re-run. **Decide before running it.**
- 🔴 **Consequence to fix the moment it converges:** five DEPLOYED-STATE lines in
  `scripts/browser-bridge/{README.md,reference/agent.md}` say "Both hosts run 1.18.4". True today,
  false the instant the workbench switches, and the `HISTORICAL_VERSION_CLAIMS` ledger **exempts
  them**, so nothing will catch it. One-word fix to 1.18.16 once both hosts agree.
- **Next probe (verbatim):** `scripts/ship.sh` then, on the workbench,
  `readlink -f $(command -v opencode) && opencode --version` — a deploy reporting success is a
  claim about the DEPLOY, not the consumer.

### The doc hook fires; the uncontaminated sample is still n=0
- **Observed:** two exposed continuations, two fired. A CLI session read the doc at turn 1 and ran
  `subsystem_recall.py` as its first Bash call; a **dispatched agent** with an ordinary kickoff did
  the same at tool calls 1→2, with **0 `Skill` calls** — the case `#446` failed.
- 🔴 **Both contaminated, differently, and the residue is STRUCTURAL:** the first session's prompt
  named the measurement; the second could read about the experiment because this doc describes it
  and `Read` returns the whole file. A staged probe cannot fix that. **Do not stage a third and
  call it clean** — parse the next few organic continuations instead.
- **Instrument (reusable, and the reason to trust the numbers):** parse `tool_use` calls, never
  string mentions — and three false-positive modes survive naive parsing: `test_<name>.py` contains
  `<name>.py`; heredoc bodies (`python3 - <<'PY'`) are DATA yet split into fake invocations;
  `-m pytest`/`-c`. Require an exact basename match, strip heredocs, reject `-c`/`-m`/`-`.

### RESOLVED 2026-08-15 — the opencode host split (kept for the trail, do not re-open)
Both hosts are on 1.18.16, verified at the consumer (values above). The five DEPLOYED-STATE
doc lines went false exactly as predicted, and the version ledger **caught it**: updating
them orphaned all five exemptions and the suite went red. 🔴 The prediction in the previous
revision — "the ledger exempts them, so nothing will catch it" — was WRONG, and usefully so:
the exemptions themselves are asserted, so the shrink direction fires. The category earned
its separate name: *a historical record never stops being true; a deployed-state claim stops
the day you ship.*

### One test failed ONCE and I could not reproduce it — three mechanisms eliminated
- **Symptom:** during the `#496` merge resolution, the combined run of
  `test_handoff_doc.py + test_subsystem_touch.py` failed at
  `test_the_LENGTH_bound_is_not_vacuous_git_WOULD_have_expanded_it`,
  `scripts/tests/test_subsystem_touch.py:5841`: `assert expanded == [sha]`, where
  `expanded = _run_git(repo, "rev-parse", f"--disambiguate={sha[:3]}").split()`.
- **Ruled out — prefix collision** (my first theory, and the plausible one): a 3-hex prefix
  matching more than one object. **0 of 550** trials, measured at TWO repo shapes — 0/250 on a
  minimal repo, then 0/300 on the REAL fixture (`_init_repo` + `_commit`, ~9 reachable
  objects) after the first probe used the wrong shape.
- **Ruled out — order dependence:** `pytest-randomly` is NOT installed (checked
  `importlib.util.find_spec`), so collection order is deterministic; the same order passed
  on re-run.
- **Ruled out — a swallowed git failure:** `_run_git` asserts `proc.returncode == 0`
  (`test_subsystem_touch.py:184`), so a transient git failure surfaces there, not at 5841.
- **Ruled out — merge-induced:** passes in isolation, passes in the same combined pair, and
  40/40 in a loop on the merged tree. The authoritative nix gate is green.
- **Leading hypothesis:** none that survives. Say so rather than picking one.
- **Next probe (verbatim):** if it recurs, capture the FULL failure block before re-running —
  `nix-shell -p python3Packages.pytest python3Packages.pyyaml --run "python3 -m pytest
  scripts/tests/test_subsystem_touch.py scripts/tests/test_handoff_doc.py -q -rs" >
  /tmp/f.log 2>&1` — and read `expanded`'s actual value. Every elimination above assumed the
  assertion compared `[sha]` against a LONGER list; a shorter or different one points
  elsewhere and none of this work applies.

### 🔴 A leaked credential in the store has been OPEN for 33 days and nothing was tracking it
- **Symptom:** the new `--validate` advisory surfaced the store's ONE pre-existing hand-written `OPEN:` bullet. It is a credential rotation + SOPS re-encrypt, dated **2026-07-14**, in the `datapacket-talos` scope. 🔴 **Deliberately not named here — this repo is PUBLIC and the entry is client-confidential.** Read it with `subsystem_recall.py --scope datapacket-talos --search "rotate"`.
- **Observed:** `subsystem_touch.py --scope datapacket-talos --validate` → `28 of 28 parse, 0 malformed`, then `🔴 1 declared OPEN:` naming the file, plus `⚠ 2 unmarked` (the forgejo remedy and an unpinned image tag).
- **Ruled out:** that the marker convention was invented by `#505` — a past session had ALREADY hand-written this bullet as `- OPEN: …` with no tooling asking for it. The schema formalises a shape the corpus invented on its own.
- **Next probe (verbatim):** rotate the credential, re-encrypt the SOPS file, then rewrite that bullet as `- RESOLVED <sha>: …` in the SAME edit so the badge clears. **Operator's call whether an agent touches the credential.**

### The doc hook — still n=0 uncontaminated, and THIS session cannot be the sample
- **Observed:** this session ran `subsystem_recall.py` at `/resume` step 4, i.e. because the SKILL told it to. `Skill` calls were non-zero (the `resume` skill loaded), so attribution to the doc is structurally impossible here.
- **Unchanged:** parse the next few ORGANIC continuations; do not stage a probe.

### `#329` WAS the on-switch — MERGED 2026-08-18 (kept for the evidence trail)
- **State: MERGED** as `d27b0cc1`. Rebased onto current trunk first — it was **23 commits behind**, and
  its only check was a day-old `COULD NOT RUN` evaluating a stale tree. Post-rebase the gate ran green
  (including the new relay-guard leg), `merge-tree` exited 0, and the merged region of
  `kustomization.yaml` — the file `#330` also rewrote — was read directly, because a clean textual
  merge is not a clean merge.
- 🔴 **MEASURED + FIXED 2026-08-18 — the answer was THE INTERNET, and the hole was already live in
  `trunk` before `#329`.** The production node's host firewall is `k0s/host-firewall/relay-firewall.sh`
  (homelab-infra), a hand-maintained **deny-list**. It installs a dedicated chain in the `raw` table,
  reached via a jump early in the packet path, restricted **only to the outward-facing NIC**, and
  carrying a single explicit drop rule per relay port. `ufw` inactive, `filter INPUT` policy **ACCEPT**, Calico `cali-from-host-endpoint`
  **empty** — that chain is the only thing filtering, and **`8102` was not in it**. `#330` added the
  nginx `listen 0.0.0.0:8102` block and Flux reconciled it into the live ConfigMap; nothing added the
  matching DROP. Closed by homelab-infra **`#337`** (merged, applied to the node, verified below).
- **The evidence, because "closed" and "no listener" look identical from outside.** Two distinct
  failure signatures, measured off-mesh from the workbench against **both** public IPv4s on the node's
  public interface (`ip route get` confirms the probe leaves via the ISP, not nebula), with `tcpdump`
  running on the node so the host side is observed too:
  - guarded port with a live socket (`9090`, `8113`) → **timeout**; SYN arrives, host emits **nothing**
  - `:8102`, and unbound controls `8200`/`22222` → **refused**; SYN arrives, **host emits an RST 71 µs
    later**. An RST is produced only after `raw` *and* `filter INPUT` accepted the packet and TCP found
    no socket — so the port was open at the firewall and shut only for want of a listener.
  - `22`, `6443` (deliberately public) → open. That pair is the instrument's positive control.
- 🔴 **It was armed, not theoretical — the trigger was a pod restart.** The live gateway pod started
  **2026-08-02**, predating the ConfigMap change, and mounts `nginx.conf` via **`subPath`**, which is
  frozen at pod creation and never updates or reloads (live `grep 8102 /etc/nginx/nginx.conf` inside
  the container: **0**). So the only thing keeping the port shut was pod age. Any recreation arms it —
  including the DaemonSet's **own `tunnel-watchdog`**, which `DELETE`s its pod after 6 consecutive
  tunnel health-check failures. Widest reading: **a `subPath` ConfigMap mount makes "what Flux
  reconciled" and "what the process is running" independent facts — never read one off the other.**
- **What the direct hop bypasses.** `internet → node:8102 → nginx → nebula → store pod` skips
  Cloudflare, Traefik and the `subsystem-store-ratelimit` middleware **entirely** — none of them are
  in that path. Each nebula gateway passes `CF-Connecting-IP` through untouched, and that is an
  intentional choice recorded in
  `clusters/{homelab,production}/apps/nebula/gateway/gateway-nginx-config.yaml`, whose comment says
  the value has to make it across this hop and must not be rewritten there. A request taking that
  route therefore reaches the store wearing the trusted-peer address
  `10.244.0.123` with its header **read**. The live image is still `0.1.0`, so `#520`'s "trust the
  header only from a trusted peer" fix is **not running**: a direct caller could forge the header the
  per-client lockout is keyed on. The bearer token was the only remaining gate.
- **Ruled out:** that homelab Traefik would be denied by the NetworkPolicy. It is never in the path —
  the IngressRoute lives in the **production** cluster and targets Endpoints `10.0.0.2:8102`, so
  traffic arrives via the homelab nebula gateway (hostNetwork, same node, allowed).
- **Ruled out — that the guard would break the intended edge path.** It is public-interface-only, and
  the production Traefik runs on the *other* node, reaching `10.0.0.2:8102` over the **private**
  interface. Control watched live: `auditloop.zacx.dev` returns **302** through **guarded** port
  `8113` while `8113` is unreachable from off-mesh. Re-confirmed after the fix (auditloop + clawgate
  both 302).
- **Verified after applying `#337`:** node script sha == `origin/trunk` sha; 39 DROP rules (was 38);
  `dport 8102` present in v4 **and** v6; the `PREROUTING` jump intact; `:8102` flipped
  **refused → timed out on both public IPv4s**, while `8200`/`22222` stayed **refused** (so the guard
  is still port-scoped, not a blanket drop) and `22`/`6443` stayed open. The reachability proof for a
  port that *does* have a listener is `8113`, which is dropped by an identical rule in the same chain
  — `raw/PREROUTING` runs before routing, so socket existence is irrelevant to it.
- 🔴 **The structural defect, which is the durable half.** `PORTS` is a deny-list maintained by hand in
  a *different file, in a different directory, applied by a third manual step* (`scp` + `systemctl`;
  it is **not** Flux-reconciled). Nothing diffs it against the `listen 0.0.0.0:<port>` set in
  `gateway-nginx-config.yaml`. Adding a relay port to nginx therefore **publishes it** until someone
  remembers. `8102` is the proof it fails. `#337` records this in the README, and **homelab-infra
  `#338` now pins the two sets**: `scripts/check-relay-guard.py` asserts
  `{listen 0.0.0.0:<port>} − DELIBERATELY_UNGUARDED == {PORTS}`, with the allowlist an enumeration
  carrying a reason per entry (today exactly `{25: MX}`) so an unknown unguarded port fails by
  default. 28 controls, the headline one being **RED AT BASE** — run against the guard at
  `11f67175^` it exits 1 and names `8102`, so it is a regression test for a defect that happened,
  not an invariant guard. Its `rc 2` (nothing examined) and `rc 3` (checker could not run) exist
  because `rc 1` means "a port is exposed" and neither an empty parse nor a crash may spell that.
- 🔴 **What `#338` does NOT do, and both halves are still open work.** (a) **Nothing runs it
  automatically.** GitHub Actions is **billing-blocked repo-wide** on homelab-infra — every run dies
  in ~13s having executed zero steps, so a workflow there would be a permanently-red gate, worse
  than none — and it is not on the Tekton `clawgate-ci` path. Three options, none chosen: a Tekton
  pipeline, an rc in devrc's `drift-check.sh` (which already runs 4×/day as a passive deadman), or
  leave it manual and say so. (b) **It compares two FILES in one repo and cannot see the node** —
  `relay-firewall.sh` is not Flux-reconciled, so the repo can read green while the machine itself
  lags behind. Checked by
  hand on 2026-08-18 (`cmp`: node == `origin/trunk`; live kernel 39 rules, `dport 8102` present, unit
  active), which is a reading, not a control.
- 🔴 **REACHABLE ≠ EXPOSED — and reporting them as one class overstated the finding.** 12 further
  services listening on the wildcard address responded to off-mesh probes, and my first write-up
  lumped all of them into one exposure group. Testing each in turn, exactly **one** proved genuinely
  reachable:
  - `9100` node_exporter — **2,258 metric lines, no auth at all.** 🔴 **CLOSED 2026-08-18** by
    homelab-infra **`#339`**: filesystem mounts, interfaces, kernel version, systemd unit names,
    served to anyone who asked. Now dropped in v4 and v6; a plain `curl` times out where it
    previously returned metrics.
  - `10250` kubelet — `401` on `/metrics` **and** `/pods`; `9443` k0s — `404`. Reachable, gated.
  - `853`/`5353` dnsdist — a real `LoadBalancer` (`nebula/dns-over-tls`), **deliberately public.**
  - `9091` calico, `9120` MetalLB, `10249`/`10256` kube-proxy, `30301` a NodePort, `179` bird BGP —
    still to review. Nothing in the production cluster declares `nodePort: 30301`, so identify what
    holds it first; `179` peers over the private net, so it is likely droppable.
- **The safe-to-close pattern `9100` established, worth reusing on the rest.** Find the consumer, read
  which address it *actually* dials, and satisfy yourself that address is not the outward-facing
  one — **before** touching
  the guard. In this case Prometheus resolved the exporter to the two nodes' private-network
  addresses on the node-exporter port, reporting healthy for each, with
  `count(up)=25` as the query-shape control so the reading is a measurement and not an empty result
  wearing a healthy face. Afterwards, check your later sample against `ActiveEnterTimestamp` as the
  unit itself reports it — guard
  applied one minute before the first of two consecutive scrapes, each of which came back healthy.
  🔴 **"Still up" is
  worthless unless you also hold an earlier baseline plus a clock value showing your second sample
  was taken once the change had landed.**
- ⚠ **NOT A FLEET CLAIM, and this is the gap the `9100` work opened.** `relay-firewall` runs on
  **`diffsona` only**. `tryonhaulcentral-k8s` has no `RELAY-GUARD` at all, and Prometheus scrapes a
  node_exporter at `10.0.0.4:9100`, so the same unauthenticated daemon runs there. Its public exposure
  is **unmeasured**: the repo records no public address for it, and reaching it needs either a host-key
  decision (diffsona's `known_hosts` entry for `10.0.0.4` is stale — last written 2025-11-20, key since
  changed) or a pod scheduled onto it. **Closing `9100` on one node reduces exposure; it does not close
  the fleet.**
- **Still unmeasured:** the **homelab** gateway's `:8102` (behind home NAT — a router port forward
  would be the equivalent hole), and the off-mesh probe from a production-cluster pod asserting
  `store.zacx.dev` resolves to a **Cloudflare** address (a hairpin or cluster DNS would otherwise hand
  you a confident false pass). That second one only becomes runnable once `#329` is merged.

### Keep-alive audit-log misattribution — real, reported, NOT fixed
- **Symptom:** on a keep-alive connection whose **second** request line is malformed, `self.headers`
  and `self.path` still hold the **first** request's values, so the audit line reads:
  `ip=203.0.113.7 peer=trusted … path=/api/v1/recall/sc auth=fail result=401` — the *previous*
  request's path and `CF-Connecting-IP`.
- **Observed:** measured on one connection during `#520`'s mutation sweep, while explaining why a
  `send_error`-reset mutant was equivalent.
- **Not a bypass:** the request is still refused. It is log **fidelity** — on the log this design calls
  "the only thing that can answer it" if a leak is ever suspected.
- **Why unfixed:** predates the branch; closing it means changing `_raw_path`'s base behaviour.
- **Next probe:** decide whether it earns its own PR. Recorded in the source and in `#520`'s body.

### `--exclude` over-reports the under-cwd counter
- **Symptom:** `subsystem_touch.py` exclusion filters `paths` but **not** the `under_cwd` counter, so the
  printed note over-reports after an exclusion (measured: counter stays 3 while `paths` drops to 2).
- **Next probe:** one-line fix plus a test that pins counter and path-set together.

### ✅ RESOLVED 2026-08-19 — layer 1 did NOT exist; it does now, and it is the SHALLOWEST of the three
- **Answered.** The question below was open for a day and the answer was **no**: there was no rate
  limiting configuration on the zone at all. Not a rule that failed to match — the `http_ratelimit`
  phase had **no entrypoint ruleset**, API error `10003`. The legacy `/rate_limits` surface returned
  **410 Gone**, and the one custom firewall rule on the zone was an unrelated `skip` naming a
  different host. So `#329`'s header claimed three guards and there were **two**, from cutover until
  this was closed.
- **How it was measured, and why the zero is trustworthy:** the zone-wide ruleset list returned
  **4** rulesets, so the API was reachable and correctly scoped — the absence in the ratelimit phase
  was real, not a query wired to nothing. `store.zacx.dev` was confirmed `proxied: true`, so traffic
  does traverse Cloudflare and the WAF *could* act; nothing was configured for it to do.
- 🔴 **What now exists, and the caveat that matters more than its existence.** A rule scoped to
  `http.host eq "store.zacx.dev"`, action `block`, counting on `(ip.src, cf.colo.id)`. Design intent
  was 20/s sustained with a 1-hour ejection. **The zone's plan refused both knobs**: `period` is
  entitled to `10` seconds only (not 60), and `mitigation_timeout` to `10` seconds only (not 3600).
  Final shape: **200 requests / 10s, block for 10s.**
- 🔴 **So layer 1 is a THROTTLE, not a gate.** A flooder that trips it is blocked for ten seconds and
  then gets a fresh budget; sustained abuse is rate-limited to ~20/s rather than stopped. Against an
  unbounded firehose reaching Traefik that is still the difference that matters, but **"three
  layers" now means three of unequal depth, and this is the shallowest.** A real ejection needs a
  paid plan — a decision, not a defect.
- ⚠ **It is still declared nowhere in any repo.** Cloudflare WAF rules are not declarative here, so
  nothing in git can assert its continued existence and no gate will notice if it is deleted. The
  only check is a read against the Cloudflare API. Treat its presence as unverified on any future
  session that has not re-read it.
- **Ruled out, and still true:** that the Traefik middleware covers this. It does not — that is
  layer 2, avg 10/s burst 20, and it sits *inside* the origin. It is also bypassed entirely by the
  direct node hop, on which layer 1 does not exist either.

### 🔴 The cluster's control plane crosses the public internet, by configuration
- **Symptom:** two control-plane protocols use the nodes' PUBLIC addresses even though both nodes
  share a private network (`10.0.0.0/24`) they already use for etcd, kubelet and metrics.
- **Observed:** `konnectivity-agent` on both nodes carries
  flags pointing it at the Traefik load-balancer's public address on the konnectivity port, and the
  agent on the peer machine dials out using an address of its own that is publicly routable. Calico's
  IPv6 address annotation, on each of the two nodes, likewise holds
  that machine's **publicly routable `/64`**, and `birdcl6 show protocols` reports
  `Mesh_<peer> BGP … Established` since 2025-11-21.
- **Ruled out:** that this is only IPv4-private. The v4 half *is* private
  (`IPv4Address = 10.0.0.x`, session `10.0.0.2:179 ← 10.0.0.4`) — which is exactly what made a
  v4-only check produce a confident, wrong "179 is safe to close".
- **Consequence already acted on:** `179` and `8132` are deliberately NOT in `RELAY-GUARD`; closing
  either would break pod-network routing or the API tunnel. Recorded in
  `k0s/host-firewall/README.md`.
- **Leading hypothesis:** Calico IPv6 autodetection picked the public `/64`, and konnectivity was
  configured against the LB address for convenience. Both are probably movable to `10.0.0.x`.
- **Next probe:** confirm whether the private network carries IPv6 at all before assuming Calico can
  be repointed — if it does not, the v6 mesh has nowhere else to go and the question becomes whether
  to disable the v6 mesh rather than move it. konnectivity is TLS; BGP is not.

### `render-diff` errors on other people's PRs
- **Symptom:** `gitops-validate` run `gitops-validate-7t5fm` (2026-08-18T22:37) reported
  `COULD NOT RUN: render-diff` on commit `d3a68308` — not my change; `relay-guard` passed there.
- **Not investigated.** Flagged only because a `COULD NOT RUN` leg is a broken guard, and this gate
  is the repo's real pre-merge check.

### `30301` is a NodePort nothing declares
- **Observed:** kube-proxy holds `:30301` on the production node, but no Service in the cluster
  declares that nodePort — the seven real ones are `30276 30587 31612 31754 32327 32341 32503`.
- **Deliberately left open:** guarding it is almost certainly harmless, and "almost certainly" about
  a listener nobody can account for is a reason to identify it first.

### Four audit findings, measured and unfixed — ranked, all reproducible today
A read-only audit of the whole subsystem-knowledge surface produced ten findings; six are fixed
(above). These four remain, in descending value. Each was **run, not read**.

- **`service_recon`'s `recent changes` collapses the pathspec to its shallowest ancestor.**
  Measured over 7 services: **21 of 56 shown commits (37.5%)** touch a located file; three services
  scored **0 of 8**. `{scripts, scripts/tests}` prunes to `{scripts}`, which in devrc is ~the whole
  repo. 🔴 **The `MULTI-DIRECTORY` guard is ANTI-CORRELATED with the damage** — pruning drives the
  directory count *down*, so the two worst cases print no warning while two clean 8/8 cases do.
  **Next probe:** run the same `git log` a second time restricted to the located files and print
  `N of 8 shown commits touch a located file`.
- **`recall --ref <name>` misses in-scope and reports it as a fact about the whole store.**
  `--ref minio` from devrc says *"Nothing recorded under that name yet"*; `minio` is recorded in two
  other scopes. The whole index is already loaded in that process. Same shape as `#598`, on the read
  path an agent reaches for by hand. **Next probe:** on `ref-absent`, check the loaded index for the
  ref in other scopes and either name them or say `not recorded in ANY of the N scopes`.
- **The `NEAR-MISS` legend describes the opposite of the live population.** The caveat says a
  near-miss means unfinished business you cannot see; **100% (2/2)** of the live near-misses are
  attempted **`RESOLVED —`** lines — bullets trying to declare something *closed*. The regex already
  captures which word was attempted (`_NEAR_MISS_MARKER`); `near_miss_marker` throws it away and
  returns a bool. **Next probe:** keep the captured word, render `🔴 2 NEAR-MISS (RESOLVED)`.
- **`resume/SKILL.md` restates store measurements that have gone stale** — asserts "53 entries,
  8 OPEN"; measured 2026-08-20: **65 entries, 12 OPEN**. Fourth stale-figure instance in one day.
  **Next probe:** delete the digits, keep the imperative, point at `--validate` which computes them.

### Two handoff commits are stranded, right now
`0d1a616b2c8e` (2026-08-15) and `50a13e60550b` (2026-08-13) exist on **zero remote branches** —
verified against the live remote with `branch -r --contains`, not stale remote-tracking refs.
`#599` stops the *next* one being stranded; it deliberately does not rescue these. Recovering them
is an operator call: `git branch <topic> <sha> && git push -u origin <topic>`, confirm from another
host, then decide whether the content is still wanted.

### ✅ RESOLVED 2026-08-20 — the two broken markers, and the third defect fixing them exposed
**Both were an em-dash `RESOLVED —`.** ⚠ The "colon-less `OPEN`" this line named through several
revisions **never existed** — the audit finding four paragraphs up ("100% (2/2) of the live
near-misses are attempted `RESOLVED —` lines") was the accurate one, and the two claims sat in this
same doc disagreeing. Each bullet now reads `RESOLVED <sha>:` naming the merge sha of the PR that
landed it, each confirmed on that repo's default branch **before** being written: a bare `RESOLVED:`
parses, but only trades the `NEAR-MISS` badge for `UNVERIFIABLE` (controlled — all four forms run
through `openness_population`).

🔴 **The half worth keeping is what the fix exposed.** One of those two bullets carried a SECOND
marker, typed several lines into the bullet body instead of at its head. `_bullet_openness` reads a
bullet's opening line and nothing else, so a correctly-spelled marker further down is simply
unreachable — that declaration showed on no openness surface at all, and had only ever raised a
badge **by accident**, through the broken `RESOLVED —` sitting above it. **So fixing the marker
would otherwise have SILENCED a real open action** — re-verified against that repo the same day,
still open. It is now its own top-level `OPEN:` bullet, and the index row reads `🔴 1 OPEN` where it
read `🔴 2 NEAR-MISS`.
**Input to `#574`'s shape population: this is a THIRD shape, not a near-miss.** A near-miss is a
marker mis-spelled where the parser looks; this is a marker spelled correctly where the parser never
looks — so it raises neither badge, and no surface today reads past a bullet's opening line.

**The loop ran end to end** — badge → `--validate` → edit → re-validate — and the 2→0 near-miss move
is a PAIR, not a bare zero: the pre-fix file was replayed through `--store <copy>` on the same
command and printed `🔴 2 NEAR-MISS`, so the clean row is a measurement, not a wiring failure.

## Next steps (ranked)

0. ~~Verify the Cloudflare WAF rate rule exists~~ — **DONE 2026-08-19, answer was NO.** Layer 1 now
   exists as a 10s throttle, declared in no repo. See the RESOLVED block above.
1. 🔴 **DO NOT split read/write tokens yet — a TRIGGER, not a task.** Unchanged and still correct:
   the read-only posture is structural (`do_POST = do_PUT = do_PATCH = do_DELETE = _reject_write`),
   three tests pin it, and the split belongs in the phase-3 PR that adds a write route.
2. ~~Fix the two broken markers in `datapacket-talos/tekton`~~ — **DONE 2026-08-20.** Both were
   `RESOLVED —`, not the `RESOLVED —` + `OPEN` pair this doc claimed; and fixing them exposed a
   third shape (a marker on a CONTINUATION line, invisible to every surface) that would have been
   silenced by the fix. See the RESOLVED block above before designing item 3's shape population.
3. **The four audit findings** (above), in the order listed. All measured, all reproducible, none
   urgent. 🔴 The `NEAR-MISS`-legend one is now measured over a live population of **zero** — the
   2/2 it was derived from are the two just fixed. The finding stands; its sample no longer exists,
   so do not re-derive the percentage from today's store.
4. **Exercise the store under adversarial traffic.** Unchanged: the lockout, rate limiter and WAF
   have only seen well-formed probes — and layer 1's 10s mitigation window makes its behaviour under
   abuse a live question rather than an assumption.
5. **The store's own roadmap:** the entry-fidelity audit skill, and the "does a generic journal
   belong" question. A design decision to make, not code to ship — do not start it as filler work.
6. **PARKED by decision 2026-08-19:** move konnectivity + Calico v6 onto the private network.
   Root cause known and unchanged (`IP_AUTODETECTION_METHOD` pins v4 to the private CIDR; **no
   `IP6_AUTODETECTION_METHOD` exists**, so v6 autodetect takes the first global address). Blocked on
   a prerequisite: the private NIC carries only link-local v6, so a ULA must be assigned to both
   nodes first. Neither k0sctl config pins `spec.k0s.version`, so `k0sctl apply` may attempt a k0s
   upgrade — fix that before applying anything that way. BGP has **no MD5 auth on either family**.

## Gotchas / decisions / dead-ends

### New 2026-08-16 (phase 1)
- 🔴 **A CRASH can spell a VERDICT when an exit code means two things.** Python exits 1 on any
  uncaught exception; the phase-1 checker used rc 1 for "an exposure object is present". Run
  without PyYAML, `ModuleNotFoundError` exited 1 and the negative control printed
  `✓ Middleware → rc=1` **while examining nothing**. Fixed by `#327` (rc 3 = the checker could
  not run). Widest reading: **any process whose failure code collides with one of its verdict
  codes**. Found only because a run happened to lack a dependency — so ask what your rc=1
  *else* means.
- 🔴 **`gh pr view --json mergeable` is NOT the check status, and `CLEAN` before checks
  register is byte-identical to `CLEAN` after they pass.** An agent polled inside that window
  and reported a PR clean whose gitleaks leg then failed. **Read `gh pr checks` too**, and
  quote it. Corollary: `CLEAN` on a repo with **no** CI configured (devrc has no
  `.github/workflows`) is a much weaker claim than `CLEAN` on one that ran a pipeline — say
  which you mean.
- 🔴 **A scanner run with the wrong flags is an instrument error wearing a zero's clothes.**
  `gitleaks detect` without `--no-git --config --baseline-path` scans git *history*, so it
  structurally cannot see a finding in a working-tree file, and returned a confident
  "0 findings in my new files". **Copy the pipeline's flags verbatim**, then positive-control
  it: reintroduce the offending string and watch the count move.
- 🔴 **A fix's own explanatory comment can reintroduce the hazard it documents.** The first
  draft of the gitleaks fix *quoted the offending URL to explain it*, turning 1 finding into
  2. Caught only by re-scanning; reasoning said "a comment is inert."
- 🔴 **Widening an allowlist to clear a red gate is the failure mode, not the fix** —
  `.gitleaks.toml` says so in its own header. The fix was to the fixture. Then prove the
  fixture still fails for its ORIGINAL reason (mutation: drop `Middleware` from
  `FORBIDDEN_KINDS`, watch it go red), or you have traded a red gate for a vacuous one.
- **`kubectl diff -k` shows a SOPS secret as differing even when it matches** — the manifest
  holds ciphertext, the cluster holds plaintext. Decrypt with the age key and compare hashes
  before concluding Flux would swap it. `sops -d --extract` needs `SOPS_AGE_KEY_FILE`, and
  `.secrets/age.key` is gitignored so it does **not** come with a worktree — point it at the
  base clone's copy.
- ⚠ **`yq -r` on a missing path returns the literal string `null`, not empty** — an
  `[ -z "$x" ]` guard does not catch it, and the resulting sha256
  (`74234e98afe7498f…`) reads as a genuine mismatch. Check for `"null"` explicitly.
- **Adopting hand-applied objects into Flux was a no-op here, and that was verified, not
  assumed**: same pod, same `startTime`, `restarts=0`; Deployment generation moved 4→5 from
  Flux adding its own labels, which is metadata and does not roll the pod template.


- 🔴 **Never read an exit status through a pipe.** `cmd | tail; echo $?` gives tail's status.
  This reported `ship.sh` as green when it was **rc=12** — and that ignored rc=12 was a real
  broken managed artifact which sat for 15h and later **failed a switch outright (rc=9)**.
- 🔴 **`nix build` returns a CACHED result silently** — 0-byte log, exit 0, `grep FAILED`
  matches nothing. `nix log <drv>` is the real output for a cached drv. `--rebuild` forces a
  re-run but **errors on a drv never built** *and* on one whose previous build failed. A valid
  cached output is itself proof the suite passed (`flake.nix:268` fails the derivation).
- 🔴 **A pytest failure does NOT print `FAILED` here** — the runner uses `-q -rs`. Look for
  `=== FAILURES ===`, the summary line, and `FAIL  <dir>  (… failed=N …)`. Runner lines are
  prefixed `devrc-pytests>`, so **never anchor a grep at `^`**.
- 🔴 **`rev-list origin/main..HEAD` is NOT a merged-ness test.** A squash merge never makes the
  head an ancestor — it flagged **all 52** worktree branches as unmerged work. Classify by PR
  state, or by `git diff origin/main <head>` being empty. This misled me **3×** today.
- 🔴 **Measure "did this branch land" by branch-ADDED lines present in main
  (`merge-base..head`), never `git diff main head`** — the latter is dominated by what *main*
  added since the fork and reported 100–500 changed files for branches that touched 3.
  Validate the instrument first: positive control (a known-merged head → 100%), negative
  control (a never-merged head → 2%). **A low score is not proof of lost work** — rewording,
  restructuring and retired paths all score low; read every head under ~95%.
- 🔴 **A LOCAL tag is not a backup, and removing a DETACHED worktree makes its commits
  GC-able.** Tag before a destructive sweep, **push the tags, then read them back from the
  remote** — `git ls-remote --tags origin` is the check, not `git tag -l`.
- 🔴 **Re-check the branch IMMEDIATELY BEFORE a write, and gate on it** — printing it in the
  same command is not checking it. I ran `checkout --` + `merge --ff-only` on another
  session's branch that way; `--ff-only` refusing rather than destroying is what saved it.
- 🔴 **A failed `home-manager switch` is usually a pre-existing FOREIGN path.** A **dangling**
  symlink still blocks `mkdir` ("File exists"). Inspect → record its target → remove → re-switch.
- 🔴 **Front matter is parsed LINE BY LINE.** An `aliases: [...]` wrapped over two physical
  lines reads as an unterminated bare string and **used to kill the entire scope**. `#449`
  made the reader degrade; **always run `--validate <path>` in the same turn as a write**.
- **Mutation found what reading did not.** 3 of the 4 highest-value findings on `#459` came
  from mutants while the gate stayed green: a surviving `total > cap` → `>=`, a truncation
  note whose **outright deletion survived the whole suite**, and an unkillable clause.
- **A green gate certifies nothing broke; it never says a guard is reachable or a boundary
  covered.** `#442` shipped 3 pagination defects past 3,679 green tests because the largest
  real scope (26) is far below the 100 cap — the feature's own boundary was unreachable from
  real data.
- **`browser-bridge failed=1` was NOT load** — 1.45× wall time, not the ~15× the rule needs.
  The decisive argument is structural: no import or exec edge from the changed modules.
- 🔴 **The store must never gain a remote.** `devrc` is PUBLIC. The policy file governing a
  scope is whichever the probe names on its `policy:` line — do not go looking for another,
  and never create a scope README yourself.

- 🔴 **A trailing `echo` destroys the exit status exactly like a pipe.** `cmd > log 2>&1; echo
  "RC=$?"` makes the COMPOUND return the echo's 0 — the harness reported `ship.sh` as **exit code
  0** when the log said `SHIP_RC=7`. Reading the log content is what caught it. Same lesson as
  `| tail`, new shape.
- 🔴 **`cp -a` PRESERVES mtime; plain `cp`, `git clone` and `git checkout -- <path>` RESET it;
  `git add`/`git commit` preserve it.** Measured 2026-08-14. A docstring asserted the opposite and
  was shipped.
- 🔴 **`--session` refuses any transcript older than 30 minutes**, so the CLI structurally cannot
  measure historical sessions. Go under it: `collect_session_paths(repo, session=…,
  max_age_seconds=math.inf)` reads the path distribution without pretending to be that session.
- 🔴 **`\b1\.18\.4` never matches `v1.18.4`** — `v` and `1` are both word characters, so there is
  no boundary. A version-consistency guard passed green while a `v`-prefixed claim was stale.
- **A grep is an instrument: give it a positive control.** A case-wrong pattern (`raw` vs `RAW`)
  reported content missing from `origin/main` that was present; a typo'd test path made pytest say
  "no tests ran", which is an instrument error, not a zero.
- **Audit rounds, honestly:** across `#481` and `#485` every round but the last found a real defect
  in the PRECEDING fix — a feature that would have shipped INERT (both call sites deletable green),
  a guard pinned by a header string that stayed green while its arguments were corrupted, a
  suppression gate that was DEAD CODE, and a caveat that was simply false. **Five consecutive rounds
  contained a false claim inside the sentence doing the correcting.** Budget for it.
- 🔴 **The recurring error was one thing: stating a claim beyond what was measured.** Never caught
  by re-reading; always caught by running something. The `#494` measurement REFUTED the
  recommendation that motivated it — three agent reports of "0 paths under cwd" were one turn from
  becoming a structural claim in the store, and the real rate was **1 in 12**.

- 🔴 **`git fetch` is NOT a safe read in a shared checkout, and `FETCH_HEAD` is NOT a private
  scratch ref.** Measured: `fetch` writes `refs/remotes/<remote>/<branch>` in the COMMON
  gitdir (shared by every worktree) plus objects and reflogs, and two concurrent
  `git fetch --quiet origin main` produced `cannot lock ref` in **30 of 30** trials. Worse,
  reading `HEAD..FETCH_HEAD` in a SECOND process is racy — another session's fetch in between
  made a pushability check return a confident `0` on a checkout that was genuinely behind.
  **Use `git ls-remote` when you only need to know what the remote has**: zero local writes,
  12/12 concurrent runs clean.
- 🔴 **A ledger that RESTATES cannot catch what it was written for.** A test asserting "every
  status the module emits is documented in the skill" iterated a hand-written literal, so the
  status added by the very PR that needed it walked straight past. Deriving the list from the
  module (`re.findall(r'status=([a-z-]+)', src)`) caught a SECOND undocumented status
  immediately.
- 🔴 **Two guards reaching one outcome cannot be told apart by any test.** A `cat-file -e`
  check and `merge-base --is-ancestor` both refused on an unknown sha, so deleting either
  stayed green — the dead-predicate shape. Keep one, or pin the DIAGNOSTIC that differs
  (which is what made the second one worth keeping elsewhere: the message, not the verdict).
- 🔴 **A test can be satisfied through the wrong branch.** An ahead-and-behind fixture never
  reached the ancestry comparison because its repo had never fetched the remote commit, so
  the unknown-tip path decided and flattening `merge-base` to `False` stayed green. Ask which
  branch your fixture actually takes.
- **Reading a diff/grep is an instrument too.** A case-wrong pattern (`raw` vs `RAW`) reported
  content missing from `origin/main` that was present; a typo'd test path made pytest say "no
  tests ran"; a `-k` selector that matched the wrong 4 tests made a mutant look survived.
  Give every zero a positive control.

- 🔴 **A `grep` you wrote the pattern for is not a leak check.** `grep -iE 'oauth2-proxy|CSRF|…'` over the diff returned nothing and was reported clean. It was not: what survived was a service name WITHOUT the prefix the pattern required, plus two fragments of ordinary English no hand-written pattern would contain. The zero was a fact about the pattern. Replaced by `scripts/tests/test_store_content_not_copied.py`, which DERIVES 8-word phrases from the store — and immediately found **two more copies** that neither the grep nor a full adversarial audit had caught.
- 🔴 **A denylist beats an allowlist for "which files can hold prose".** That check first scanned 632 of 839 tracked files (an extension allowlist missing every `.mjs`/`.js`/`.html` and 48 extensionless scripts) while documented as covering "any tracked file".
- 🔴 **`{7,40}` inside a `*` regex loop is a ReDoS.** Introduced mid-review; measured on a SENTENCE-CASED bullet quoting long shas with no trailing colon: 64 hex 0.028 s, three 40-char shas **no return in 30 s**, hanging `/handoff` and `--validate` with no output. Fix is a non-splittable atom, `(?![0-9a-fA-F])`. The all-caps form is unaffected — which is why its regression test must be sentence-cased.
- 🔴 **A timing guard must be shown to REACH the code it times.** That ReDoS test was vacuous twice: first the payload was all-caps and short-circuited the branch; then it called `parse_journal_bullets` without reading `near_miss_marker`, which is a LAZY property. Both passed with the fix deleted.
- 🔴 **An evaluation matrix in a scratchpad is an opinion.** Three rounds changed one regex, each justified by a private matrix; a differently-constructed one inverted the verdict. Now `scripts/tests/fixtures/near_miss_shapes.json`, parametrized, with a `matrix_problems()` guard that is itself tested against degenerate matrices.
- 🔴 **A fixture can be blind to the class its own change introduces.** The `prose` arm shipped with the all-caps branch contained ZERO all-caps shapes, so 39 green assertions said nothing about `OPENSSL_CONF` / `OPEN_MAX` firing a red advisory.
- 🔴 **A mutation result with no BASELINE is a fact about the harness.** One probe reported KILLED for both variants because it ran bare `python3` with no pytest installed.
- **Two claims of mine measured false and were corrected in place:** "`re.I` fires on ordinary prose" (0 FPs over 196 live bullets — the examples were constructed), and a phrase-distribution row that reproduced under no tree-and-filter combination.
- **Splice a comment with anchored `Edit`, not index arithmetic** — one such splice deleted a whole regex definition and turned 129 tests red.
- **A new test file must be `git add`ed or the flake silently omits it** — the gate passed green without ever running it; caught only because `skipped=` did not move.

### The structural finding (2026-08-17) — and the two technicalities that were NOT the cause
- 🔴 **The store was reachable through exactly ONE door.** Measured over the 40 most recent
  `datapacket-talos` sessions: `/handoff` ran in **12/40**; an index window ran **11** times when
  `/handoff` ran and **2** when it did not. **20 of the 32 sessions that landed a commit or PR (63%)
  never opened the store.** Cause: that repo's 92 KB always-on `CLAUDE.md` named two homes for durable
  knowledge and contained **zero** occurrences of `analyze-service-index`/`subsystem_touch`/
  `subsystem_recall`. Fixed by `#1081`.
- 🔴 **The subtler half: a window that runs can answer about the WRONG REPO.** Scope follows `--repo`,
  and that project dir is a dispatch hub — **7 of 13** window-running sessions left at least one PR's
  repo unscoped. Fixed by `#527`.
- ⚠ **Both technicalities investigated first were BENIGN, proven with controls** — do not re-derive them.
  The 17-vs-7 path gap was 7 writes + 10 **reads** (inputs). `--exclude claudedocs` was a **complete
  no-op** here, with a positive control showing the flag *is* live. The real cause was one level up and
  invisible to the tooling itself.
- **`--session` nominated an entry in 0 of 12 runs** across those 40 sessions. Not a bug to fix in the
  window: worktree-isolated subagents are the standing default and are two of its three blind spots.
  `#522` makes it say so with the run's own numbers.

### Verification traps hit THIS session (each produced a confident wrong answer)
- 🔴 **`nix build` returned a CACHED result — a 0-byte log with exit 0.** Hit twice. That is not a pass;
  read the real output with `nix log <drv>`, and a valid store output is itself evidence since the
  derivation fails on test failure.
- 🔴 **A trailing `echo` destroyed a push's exit status** — my `git push … | tail; echo "pushed"` printed
  `pushed` while the push had **failed**. Reading the content is what caught it.
- 🔴 **`yq -r` returns the literal string `null` on a missing path**, which `[ -z ]` does not catch; the
  resulting `sha256("null")` = `74234e98afe7498f…` read as a genuine token **MISMATCH**. It was an
  instrument error.
- 🔴 **A suite run without its dependency looked like a code failure** — `ModuleNotFoundError: yaml` gave
  `pass=4 fail=11`, and one assertion (`rc=1`) passed *vacuously* because a Python crash also exits 1.
  That accident is what exposed homelab `#327`.
- 🔴 **A pre-push gate refused with `gate 9 could not RUN … pyyaml missing`** — the documented exit-3
  environment case, **not** a violation. Supplying the dependency is the fix; `--no-verify` would have
  bypassed that repo's PRIMARY gate (branch protection is unavailable there).
- **`kubectl diff -k` shows a SOPS secret as differing even when it matches** (manifest holds ciphertext,
  cluster holds plaintext). Decrypt and compare hashes; `.secrets/age.key` is gitignored and does **not**
  come with a worktree — use the base clone's copy.
- 🔴 **A squash merge makes a stacked child's diff show its parent's files again.** After merging a
  stacked parent, `#329` listed **9** files whose content was byte-identical to `trunk`. Rebasing onto
  `trunk` reduced it to the **2** it actually changes. Merge a stacked parent **without**
  `--delete-branch`, or GitHub auto-closes the child and refuses to reopen it.

### On the audits
- **Two blind audits and two blind delta re-audits ran.** Every round found something the previous round's
  fix had introduced — including a mutation sweep returning **24/24 green with two criticals in the file**,
  and a `-k` selector that made a live mutant look SURVIVED (it dies to 8 tests when the whole file runs).
- 🔴 **An auditor's reasoning can be wrong while its finding is right.** One claimed `homelab-infra` had no
  subsystem-store app; `homelab-talos` *is* the local clone of `ZacxDev/homelab-infra`. The substantive
  half — that the referenced NetworkPolicy was unmerged — was correct. **Acting on it as written would
  have broken a correct path.**
- 🔴 **A finding can be true and its framing exculpatory.** A re-audit called a red test "not
  delta-introduced". It was green on `trunk` and red on the branch — i.e. **that PR's regression**,
  merely present since its first commit.

### The gateway bind change — scoped, then DECLINED
Production's gateway binds `listen 0.0.0.0:<port>`; homelab's binds `10.42.0.10:<port>` (its nebula
IP), and that is the reason homelab's relay ports were **never reachable from outside** to begin
with — a consequence of where the process binds, not of any filtering rule. Pointing production's
bind at the same place would leave future ports closed unless something opened them.
**Declined**, and the reasoning is worth keeping: it does **not** retire `RELAY-GUARD` (five
non-nginx ports keep it alive), `#340` already makes the recurrence *detected* on the PR that
introduces it, and the change costs 39 hand-edited listen lines, another gateway roll that blips
every public service, and a reboot bind-race. Revisit only if the gate proves insufficient.

### A range-based default-deny was REJECTED BY MEASUREMENT, not by taste
The tempting cheap fix — drop `8000-8199` / `9000-9099` on `eth0` with an allowlist, one small edit,
no nginx changes, new ports closed by default — would have **severed konnectivity** (`8132` is inside
that range and is configured to the public address) and also caught k0s-reserved `9099`.
🔴 **A range tells you nothing about the individual ports it spans.**

### Instrument traps hit this session, each caught by a control
- `docker manifest inspect` answers **"not found" for tags that plainly exist** on
  `harbor.homelab.lan` — it reported `0.1.0` missing while the running pod was pulling it. Use
  `docker pull` and watch the control: pull a tag you know exists, then pull the candidate.
- `build-push.sh` builds from the **working tree, not git**. Check the four files the Dockerfile
  copies are clean before claiming an image matches a commit.
- My own ad-hoc IP grep filtered the two well-known public DNS resolver addresses out as "obviously
  benign"; the repo's IP gate does not share that assumption and failed the commit. **The gate is the
  authority, not your grep.** (Writing this very line tripped it a second time — the gate flags *any*
  routable literal, including one quoted to explain the lesson. Describe them, don't type them.)
- A **local resolver caches the old NXDOMAIN** for ~10 min after `external-dns` creates a record.
  `curl` then returns `http=000`, which is your resolver, not the service.

### The equivalent-mutant lesson
While mutation-testing a fix that swapped the order of two report blocks, my opening mutant shifted
the `return` beyond both print blocks — output identical, suite green, **zero information**. The
faithful one, which put an early exit ahead of the opening block and so matched the real historical
shape, killed 4 assertions. Also:
asserting both findings' *strings* appear was too weak, because the buggy message contained the same
token; the assertion that bites pins the **order**. **Check a mutant changes OUTPUT, not just control
flow.**

### 2026-08-19/20 — what this week's defects had in common
🔴 **The tools computed the right thing and communicated it badly.** Six separate defects, one
shape: the information existed and was absent, mistimed, or worded so the reader inferred something
false. Four of them cost *multiple* sessions each independently reaching the same wrong conclusion.
That is the class to hunt first in this surface, not logic errors.

- **A guard can encode the bug it should catch.** THREE found this week: an assertion pinning
  `pair_strength('ratelimit','rate') == PREFIX_STRENGTH` (the defect, written down as intent); one
  pinning "`index_scopes` returns ONLY the owner"; and two guards asserting `== ()`, which a null
  scanner satisfies. All passed forever. **When a test documents a contract, ask whether the
  contract is right — passing tests are not evidence the behaviour is.**
- **A message becomes belief.** The `WRONG WINDOW` block was read as "the window is structurally
  dead" by four runs; the `behind` message's devrc-only `ship.sh` claim was repeated back as fact
  about a repo `ship.sh` does not converge. **Counterweights belong in the text the reader is
  looking at, not in a doc they will open later.**
- **Stale figures, four times in one day** (opencode pin, browser-bridge floor, browser-bridge size,
  `resume/SKILL.md`). Every one was prose asserting a measurement of an artifact someone later
  edited; every one was caught by a gate built for it. **If a fifth appears, derive the figure at
  test time rather than restating and pinning it** — `TARGET_FLOORS` already works that way.

### Instrument traps hit this session — each produced a confident WRONG answer
- 🔴 **Never read a gate verdict through a pipe.** `nix build … | tail` reports the *tail's* status;
  a background build printed `[exited with code 0]` for a run that FAILED. Bit **three times**. Get
  the drv (`nix eval --raw .#checks.x86_64-linux.pytests.drvPath`), then `nix log <drv>`, and read
  `TOTAL collected=… passed=… failed=…` from the CONTENT.
- 🔴 **A check you do not BRANCH on is not a gate.** The store-content check was run, returned 1,
  and an unconditional `git commit` in the same command block ran anyway — pushing past a red gate.
  Run the check in its own block and gate on `$?`.
- 🔴 **Two-dot `git diff main..HEAD` renders other people's landed work as YOUR deletions.** Read as
  an agent deleting 1,266 lines of unrelated tests. Use three dots.
- 🔴 **Uniformity is not agreement.** A verdict-comparison harness returned IDENTICAL on 8/8
  fixtures because every fixture failed identically for an unrelated reason (`git command failed`,
  the tool resolves a repo from cwd). A comparison needs a case that DIFFERS before its "same"
  means anything.
- 🔴 **The base clone serves stale files that look authoritative.** It is frequently on another
  session's branch. A merged fix appeared not to have landed because it was read from there; the
  same clone refused a `--ff-only` that would have merged `main` into a teammate's feature branch.
  **Check `git -C <repo> branch --show-current` before reading OR writing.**
- **A grep can forbid discussion.** A test asserting `"merge --ff-only" not in err` would have
  banned *naming* the operation being warned against. Assert the command form, not the substring.

## How to verify

```bash
# the store, from off-mesh over the real path (this repo is PUBLIC — no literal addresses here)
TOK=$(KUBECONFIG=$KC_HOMELAB kubectl -n subsystem-store get secret subsystem-store-token \
        -o jsonpath='{.data.token}' | base64 -d | sed -n 1p)
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $TOK" \
     https://store.zacx.dev/api/v1/recall/devrc          # 200
curl -s -o /dev/null -w '%{http_code}\n' https://store.zacx.dev/api/v1/recall/devrc   # 401
curl -s -o /dev/null -w '%{http_code}\n' https://store.zacx.dev/                      # 404
# ⚠ http=000 means YOUR RESOLVER, not the service — dig @<a-public-resolver>, or curl --resolve

# the client-IP chain survived every hop, and the trusted-proxy value is right
KUBECONFIG=$KC_HOMELAB kubectl -n subsystem-store logs deploy/subsystem-store-api | grep audit | tail -3
#   want: ip=<your public IP> peer=trusted token=<fp> auth=ok result=200
#   report the PAIR, never a bare zero:
KUBECONFIG=$KC_HOMELAB kubectl -n subsystem-store logs deploy/subsystem-store-api \
  | grep -c "peer=untrusted"   # 0 …and peer=trusted must be NON-zero, or the log is wired to nothing

# the host firewall parity gate (runs in CI on every homelab-infra PR; also runnable by hand)
python3 ~/workspace/homelab-talos/scripts/check-relay-guard.py        # rc 0
bash    ~/workspace/homelab-talos/scripts/tests/test-check-relay-guard.sh   # pass=47 fail=0

# 🔴 SET THESE FIRST. An angle-bracket placeholder inside this fence is shell
# REDIRECTION, not a blank to fill in: `nc -z -w 5 -v <node-public-ip> 8102` runs
# `nc -z -w 5 -v` with its stdin/stdout redirected, and IF a file named
# node-public-ip exists in the cwd it TRUNCATES ./8102 to 0 B (measured, not
# theoretical) while printing an nc usage banner that reads as "wrong flags".
# With no such file bash aborts on the failed input redirect and touches nothing
# — so testing this in an empty dir will UNDERSTATE it. Addresses are never
# written down here — this repo is PUBLIC — so resolve them into variables:
NODE_PUB=                             # ← the node's primary public IPv4, from `ip -o -4 addr` on the node
LB=$(KUBECONFIG=$HOMELAB/production-kubeconfig kubectl -n traefik get svc traefik \
       -o jsonpath='{.status.loadBalancer.ingress[0].ip}')

# the node itself — the repo checker CANNOT see it
ssh root@"$NODE_PUB" 'sha256sum /root/relay-firewall.sh; systemctl is-active relay-firewall'
#   compare that sha to: git -C ~/workspace/homelab-talos show origin/trunk:k0s/host-firewall/relay-firewall.sh | sha256sum
ssh root@"$NODE_PUB" 'iptables-save -t raw | grep -c "^-A RELAY-GUARD"'   # 44

# 🔴 THE OFF-MESH PORT PROBE — read the SIGNATURE, not "did it fail".
#    `-v` is REQUIRED or nc prints nothing and your parser silently sees an empty string.
nc -z -w 5 -v "$NODE_PUB" 8102   # want "timed out" (DROP).
nc -z -w 5 -v "$LB"       8102   # 🔴 "refused" would mean the host ACCEPTED it and merely
                                 #    lacked a listener — that is OPEN AT THE FIREWALL, not closed
nc -z -w 5 -v "$NODE_PUB" 9100   # want "timed out" — node_exporter, closed 2026-08-18
nc -z -w 5 -v "$NODE_PUB" 8200   # control: must stay "refused", else the guard went blanket
nc -z -w 5 -v "$NODE_PUB" 22     # control: must stay open
nc -z -w 5 -v "$NODE_PUB" 179    # control: must stay OPEN — Calico's v6 mesh needs it
curl -s -o /dev/null -w '%{http_code}\n' https://auditloop.zacx.dev/   # 302 — edge path via a GUARDED port

# THE KILL SWITCH — one line
#   delete `- gateway/subsystem-store-ingress.yaml` from
#   clusters/production/apps/nebula/kustomization.yaml, commit, then:
#   flux reconcile kustomization nebula
```
## State now — 🔴🔴 THE STORE IS PUBLIC. `#329` merged 2026-08-18T23:28:31Z.

🔴 **THE CUTOVER IS DONE. `store.zacx.dev` is live on the public internet** (homelab-infra
`#329`, merged 2026-08-18T23:28:31Z as `d27b0cc1`), fronted by Cloudflare and gated on a bearer token
plus a mandatory `CF-Connecting-IP`.

- **homelab-infra `trunk` at `d27b0cc1`; devrc `main` at `268b49f`.** Both base clones re-synced;
  both hosts converged and switched (441 / 402 managed artifacts, 0 dangling).
- **Live, re-read at handoff time:** pod on image `0.2.0`, ready, **restarts=0**; the public
  authed GET returns **200**; the production node's `RELAY-GUARD` carries **44** DROP rules.
- ✅ `0.2.0` running — `#520`'s trusted-peer fix is finally executing, and
  `SUBSYSTEM_STORE_TRUSTED_PROXIES` is load-bearing rather than inert. **Next tag MUST be `0.3.0`.**
- ✅ Token rotation exercised end to end (`#344` overlap, `#345` retirement) — the proposal's §4
  pre-cutover requirement, met *before* the cutover.
- ✅ A CI gate now catches the defect class that started this: `scripts/check-relay-guard.py` runs
  as a `relay-guard` leg on `gitops-validate` for every homelab-infra PR (`#338` + `#340`).

### Shipped this session — 12 PRs, 2 repos
| theme | PRs |
|---|---|
| the exposure closed | homelab `#337` `#339` `#341` `#342` |
| the recurrence gated | homelab `#338` `#340` |
| the service completed | homelab `#343` `#344` `#345` `#329` |
| the record | devrc `#529` `#532` `#533` `#543` |

Store entries updated in two scopes: `devrc/subsystem-store-api` and `homelab-talos/subsystem-store`.
## State now

🔴 **The store SERVICE is unchanged since the cutover. What moved on 2026-08-19/20 is the TOOLING
around it** — the read half, the write half, recon, and the handoff writer. **18 PRs merged to
devrc `main`**, all gate-green, plus homelab-infra `#354` applied and verified on the node.

- **devrc `main` at `fc1f581`**; full suite green (`13081 collected, 0 failed`), and the
  store-content gate green after **two separate live leaks were closed today**.
- **The service itself:** pod `0.2.0`, ready, restarts 0; public authed GET returns 200; auth
  verified live (no token → 401, bad token → byte-identical 401, valid → 200, positive control
  passed). `SUBSYSTEM_STORE_TRUSTED_PROXIES` is set, so `#520`'s trusted-peer fix is executing.
- **Layer 1 exists** (created 2026-08-19) but is a **10s throttle, not a gate** — the Free plan
  refused both design knobs. See the RESOLVED block above; do not read "three layers" as three
  equal layers.
- **Host firewall:** both production nodes now carry `RELAY-GUARD` (the second never had one).
  Applied by hand and verified on the node — chain + jump present on **v4 and v6**, five ports,
  `9100` went from serving 1,769 unauthenticated metric lines to timing out from off-node.

### Shipped 2026-08-19/20 — 18 PRs, devrc
| theme | PRs |
|---|---|
| the two leaks closed | `#556` (handoff doc) `#587` (prune-skill reference) |
| the record corrected | `#565` `#568` |
| analyze-service made cheap | `#552` (recon 22.5 tool calls → 1) `#550` (proposal) |
| the index tells the truth | `#560` `#574` `#589` `#598` |
| search stopped inventing matches | `#594` |
| the handoff writer stopped hiding things | `#588` `#599` |
| recall got cheaper | `#575` |
| gates unbroken | `#581` `#570` `#571` |
