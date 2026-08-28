# Handoff: browser-agent-site-notes — 2026-08-27

## Run this first — the index, one read-only command
```bash
python3 ~/workspace/devrc/scripts/lib/subsystem_recall.py --repo devrc
```
Terse pointers this doc does not carry, curated by past sessions and outliving it.
🔴 RECALL, NOT LIVE OBSERVATION — every line is a pointer to VERIFY, never a current
reading, and it may describe a gotcha already fixed. `scope-absent`/`scope-empty` means
nothing is recorded yet: ordinary, not an error, and not a clean bill of health.
Non-blocking: if it exits non-zero, print the stderr line and carry on.

## Goal
Trace and analyse the browser-bridge skill and its flows; document the one structural
gap that trace found — the autonomous `browser agent` is BLIND to `site_notes`, so on a
registered host it runs without that site's flow notes.

## State now
- **devrc PR #940 MERGED** — squash `880786cf`, `mergedAt 2026-08-28T00:29:26Z`.
  Verified **by content** on `origin/main` (a squash never makes the head an ancestor,
  so ancestry lies): SKILL.md sentence present, `reference/agent.md` section present,
  3 new tests present.
- Base clone `~/workspace/devrc` re-synced `--ff-only` to `880786cf`. Worktree
  `devrc-site-notes-agent` and the `/tmp/devrc-main-control-*` control worktree removed.
- **Merged with `tekton/devrc-pytests` RED.** Operator instruction ("skip tests, get it
  merged"). Mechanism: `enforce_admins` lifted → `gh pr merge --squash --admin` →
  `enforce_admins` restored **and verified** (`enforce_admins: true`, contexts
  `["tekton/devrc-pytests","tekton/devrc-nodetests"]`). Required checks stayed in force
  for non-admins throughout. Pre-change snapshot:
  `<scratchpad>/main-protection-backup.json`.
- Shipped in #940: `SKILL.md` +1 sentence (12,028 B), `reference/agent.md` new section,
  `browser_tool_impl.mjs` rationale comment, `tests/browser_tool.test.mjs` +3 tests.
- **4 audit rounds ran, each found real findings; round 5 was never run** (operator
  chose to merge). Rounds 1–4: 6🟡, 3, 3, 1 — three of the four rounds' findings were
  introduced *by the previous round's fix*.

## Open investigations — live diagnosis state

### `tekton/devrc-pytests` red on devrc — genuine failure or preemption kill? UNRESOLVED
- **Symptom + exact repro:** on PR #940 head `9e0ac29e`,
  `tekton/devrc-pytests` = `failure`, `tekton/devrc-nodetests` = `success`.
  `gh api repos/innovation-upstream/devrc/commits/9e0ac29eae6dabacb14f38beb0242b792242de5a/status`
- **Observed (with values):**
  - The same pipeline reported **success** on this PR's four earlier commits
    (`d9933e9e`, `c2576b8e`, `24a90318`, `ea1801de`), so the pipeline runs.
  - Prior run duration: pending `22:31:43Z` → success `22:52:44Z` = **21 min**.
  - GitHub status carries **`target_url: null`** — no link to the run.
  - **Locally the whole repo-wide suite is GREEN.** `nix develop <wt> --command bash
    scripts/run-tests.sh <wt>`, per-target: `scripts/tests` 1005 passed, dl-router 1005,
    **browser-bridge 792**, validation 102, session-analysis 490, session_insight 57,
    mail-actions 135, signal 716+1skip, initiatives 784, repo-cos 330+1skip. The run
    ended `RESULT: FAIL (exit=143)` at `scripts/task-spec-drafter/tests` **because I
    killed it during cleanup** — 143 is `trap 'exit 143' TERM` in `run-tests.sh:174`.
  - **No test has ever been observed red locally**, on any target, in any run.
- **Ruled out:**
  - *"Local repro of the CI failure"* — killed by my own `timeout 1500`, not a test
    (`RESULT: FAIL (exit=143)`; 143 = 128+15 = SIGTERM).
  - *"main is red"* — control on pristine `main` never ran: `run-tests.sh` printed
    "MISSING ENVIRONMENT, not a code failure — no test has run yet" and named the fix
    (`nix develop`). Its own guard, working.
  - *"Timing shows a hang"* — measured from inside a burst I caused: **load average
    22.7**, 4 concurrent pytest + 4 `run-tests.sh`. Any flake/timing read from that
    window is contaminated.
- **Leading hypothesis:** none, deliberately. The tekton skill records that this
  pipeline's failures split ~evenly between **genuine single-test failures**
  (~1 test in ~15,500, surfacing as `verdict exit=1`) and **kills** (exit 255 =
  scheduler preemption). They need opposite responses and an empty result cannot
  distinguish them.
- **Next probe:** read the TaskRun and apply the skill's discriminator — a step that
  printed `RESULT:` / `<leg> verdict=` **failed a test**; one that printed neither was
  **killed**. 🔴 **Requires the workbench** — `$KC_HOMELAB` is unset on the laptop and
  `~/workspace/homelab-talos/homelab-kubeconfig` does not exist there, so kubectl falls
  back to `localhost:8080`/`0.0.0.0:44123` and every read dies `connection refused`.
  ```bash
  KUBECONFIG=$KC_HOMELAB kubectl -n tekton-ci get pipelineruns -l ci.zacx.dev/repo=devrc \
    --sort-by=.metadata.creationTimestamp \
    -o custom-columns='NAME:.metadata.name,SHA:.metadata.labels.ci\.zacx\.dev/sha,REASON:.status.conditions[0].reason'
  # then, for the run whose SHA is 9e0ac29e:
  KUBECONFIG=$KC_HOMELAB kubectl -n tekton-ci logs <taskrun-pod> -c step-pytests | tail -40
  ```
  Cheaper alternative from any host: the **next** devrc PR. Red on an unrelated diff ⇒
  kill signature (noise). Green ⇒ #940's failure was real and is now on `main`.

## Next steps (ranked)

1. **Settle the `devrc-pytests` red** (repo: devrc; no files — a read). Run the Next
   probe above from the workbench, or read the next devrc PR's check. Closes when the
   discriminator says `killed` (noise, nothing to do) or names a failing test (fix it —
   it is on `main` now).
2. **Reload the browser-bridge extension in BOTH Brave profiles** (repo: none — operator
   action, cannot be done by an agent). Both `work` and `personal` report
   `extension_stale: true`: loaded build `04bbd6f9c695141d`, deployed
   `e1ee86a50a811d40`, versions identical at `0.8.1` — the case a version compare cannot
   see. Loaded code predates **2026-08-24**, missing `b20b7835` (#797, bound the
   screenshot fast path) and `b242fc2d` (#814, bound `open`'s reuse probe) — both
   bounded-hang fixes, so the symptom is a wedged op, not an error. Fix: `brave://extensions`
   → **Remove** → **Load unpacked** `~/.local/share/browser-bridge-ext/` (a ↻ reload is
   unreliable — the long-poll keeps the old worker alive). Verify: `browser whoami` →
   require `extension_stale: false`; `null` = undecidable, NOT ok.
3. **Decide the SKILL.md byte budget** (repo: devrc; file
   `scripts/browser-bridge/SKILL.md`). Now **12,028 B** against the **12,038 B** enforced
   ceiling = **10 bytes of slack** (was 77 before #940). The next edit larger than that
   fails `test_skill_md_keeps_working_headroom`. `skill-audit.py` says "no prune needed"
   (a BYTES verdict) and the staleness pass came back clean, so nothing was churned.
   Demotion candidates, each with an existing sidecar home: `text` row 383 B →
   `read-envelopes.md`; `wake` row 361 B → `spa-wake.md`; `screenshot` row 343 B → a new
   topic. Any one buys 200–300 B. Closes when either a demotion PR merges or the operator
   says explicitly that 10 bytes is acceptable.
4. **Optional: round 5 delta audit of the merged #940** (repo: devrc; files
   `scripts/browser-bridge/reference/agent.md`). Rounds 1–4 each found something real and
   round 5 was skipped by operator choice. Scope is prose in one file; no code path ships
   behaviour change. Closes when a round returns no findings, or the operator declines.

## Gotchas / decisions / dead-ends

- 🔴 **The `site_notes` gap itself.** `server.py` `_annotate_site_notes` sets the field on
  the **envelope ROOT** (`result["site_notes"] = path`); the agent tool's
  `summarizeResult` reads `envelope.data`. Measured across all ops the agent can reach:
  **none forwards it.** Kept deliberately — the value is a repo-relative *path* and the
  agent def denies `read` (`opencode/browser-agent.md`: `"*": deny` / `browser: allow`),
  so forwarding it hands the model a filename it cannot open.
- 🔴 **`whoami` is the exception to the `.data` mechanism** — it reads the root
  (`const w = envelope || {}`). Safe for a different reason: it answers `GET /whoami`,
  which `_annotate_site_notes` never touches. Do not generalise `.data` to it.
- 🔴 **`OP_TO_SERVER` KEYS, not VALUES.** It maps tool-facing → wire name
  (`html` → `getHtml`) and `summarizeResult` takes the tool-facing one. Iterating values
  feeds it `getHtml`, which matches no branch and falls through to the terminal
  `JSON.stringify(data)` — exercising a path the agent never takes while skipping the
  `html` branch. Round 1 also proved `ALLOWED_OPS_DEFAULT` (13) is the wrong inventory:
  `upload` is reachable via `BROWSER_AGENT_ALLOWED_OPS`, and a `site_notes` forward added
  to that branch passed the FULL 91-test suite.
- 🔴 **The detection recipe took two rounds to get right, and the middle version was
  WORSE than the bug it fixed.** `open <url> && context` reported the OLD host (a
  re-`open` discards the url). Fixing it with a *conditional* `open` was worse: `nav` is
  in `TAB_SCOPED_OPS` but NOT `OWNED_TAB_ONLY_OPS`, so with no owned tab it drives the
  **operator's ACTIVE tab** (`server.py:215-217` says exactly that). Final form:
  `open && nav <url>`, read `site_notes` off the **nav** envelope (`nav` returns
  `url: cmd.url` at `service_worker.js:960,968` — deterministic; `context` reads the
  *committed* `tab.url` and races). And `open`'s own envelope carries a **decoy**
  `site_notes` for the tab's current host — discard it.
- ⚠ **Instruments lied four separate times this session; budget for it.**
  (1) a background task reported **exit 0** while its own output said
  `RESULT: FAIL (exit=143)`; (2) a "completed exit 0" notification was the *wrapper that
  launched `nohup`*, not the run; (3) two mutation mutants **never applied** (anchor
  matched 3 and 2 sites, python aborted) and their unmutated runs read as *survived* —
  a mutant that never ran reports SURVIVED; (4) a `/whoami`-doesn't-annotate "finding"
  was my grep window, not the code (`annotate_staleness` is called inside `_whoami()`,
  `server.py:3138`). Read CONTENT, never an exit code; verify a mutation applied.
- **Merged through a red required check by explicit operator instruction.** Minimal lever
  used (`enforce_admins` only, not deleting required checks), restored and verified in
  the same command. This is recorded, not endorsed: nobody knows yet whether a real
  failure landed on `main` — that is next step 1.
- **No `clawgate-task:` field on this doc.** `clawgate_handoff.sh resolve` exited **5**
  (0 tasks for this session). Its positive control confirmed the board is reachable and
  the token accepted (3 links for a different session), but a wrong session id also
  answers `200` with an empty array — so this is a narrow reading, NOT a clean bill of
  health. Per the tool's instruction: no field written, no task created.

## How to verify
```bash
# 1. #940 landed, by CONTENT (a squash is never an ancestor — ancestry would lie)
git -C ~/workspace/devrc fetch origin
git -C ~/workspace/devrc show origin/main:scripts/browser-bridge/SKILL.md | grep -c 'never sees'          # 1
git -C ~/workspace/devrc show origin/main:scripts/browser-bridge/reference/agent.md | grep -c 'never sees .site_notes'  # 1
git -C ~/workspace/devrc show origin/main:scripts/browser-bridge/tests/browser_tool.test.mjs | grep -c 'SITE NOTES'     # 3
gh pr view 940 --repo innovation-upstream/devrc --json state,mergeCommit   # MERGED / 880786cf

# 2. branch protection really was restored
gh api repos/innovation-upstream/devrc/branches/main/protection \
  --jq '{enforce_admins:.enforce_admins.enabled, contexts:.required_status_checks.contexts}'
# expect: enforce_admins true, both tekton contexts

# 3. the guards actually guard (all three must go RED)
cd ~/workspace/devrc && nix-shell -p nodejs --run \
  "node --test scripts/browser-bridge/tests/browser_tool.test.mjs"        # 91 pass
#   mutants that MUST kill: forward site_notes in the `upload` branch; make
#   summarizeResult return "" for every op but context; reword or MOVE the SKILL.md
#   sentence out of ## FIRST DECISION.

# 4. the byte ceiling
python3 ~/workspace/devrc/scripts/skill-audit.py scripts/browser-bridge/SKILL.md
```
