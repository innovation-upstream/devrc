# Handoff: browser-bridge audit round, CI gates, and two real deploys — 2026-08-02

## Goal

Pick up the 2026-08-01 handoff's three open items (prove the auto-wake cure, review PR #266,
apply the approved clawgate follow-ups), then take everything that surfaced all the way to
merged-deployed-verified rather than merged.

## State now

- `main` @ **`9254361`** (moved past my last merge — other sessions landed #71, #55, #276, #283, #285).
  My last was `81e2d76`.
- Both hosts converged + switched at `81e2d76`; **re-run `scripts/ship.sh` to pick up the newer tip.**
- All 4 Brave profiles on extension **0.7.1**, `extension_stale:false`.
- `scripts/browser-bridge/SKILL.md` **11,845 B** — ceiling 12,288, floor **250** (raised from 100),
  so ~193 B usable. `tests/test_skill_size.py` owns the constants.
- Gates: `nix flake check` green — pytest **2920 collected / 2918 passed / 2 skipped**,
  node **468/468**.
- clawgate **0.7.82** live (embedded kubeclaw chart **0.7.1**).

### Merged (devrc)

| PR | What |
|---|---|
| #272 | The ≥30s wake rig already existed; auto-wake cure made falsifiable |
| #266 | wake fires `visibilitychange` **twice**; discard is loud, the reload after it is silent |
| #274 | The two audit reports (usage + surface) |
| #275 | Dropped `health` from the quick start; eviction playbook globbed; floor 100→250 |
| #277 | Agent surface anchored to the real op inventory; `context` made reachable, `ping`/`emulate` declared excluded |
| #278 | `nav`/`open --wake`, in-flight-derived ping deadline, split routing errors, orientation telemetry, screenshot hint |
| #280 | **Node suite now gates** (`checks.nodetests`) |
| #281 | clawgate: kubeconfig is per-host; trunk-commit deploys the manifest, not container code |
| #282 | Rescued 2 commits stranded on the workbench |
| #284 | **Pytest gate fails loudly on silent coverage collapse** — skips 125 → 2 |

Elsewhere: `homelab-infra#274` (kubeclaw chart 0.7.0→0.7.1) + clawgate **0.7.82** built, pushed,
deployed, verified. GitHub issue **#273** opened, probed, closed on measurement.

## The four things worth remembering

### 1. "Merged ≠ deployed" bit twice, in OPPOSITE directions

- **Inert on trunk:** the kubeclaw chart is `//go:embed`ded into the clawgate binary, and
  `deployment.yaml` pins a **literal** tag with **no Flux image automation**. So the re-sync merged,
  reconciled cleanly, and changed nothing running. `git log` read as shipped. Only a build + pin
  bump deployed it.
- **Stale behind a green deploy:** `ship.sh` reported `✅ VERIFIED … + switched` on the workbench
  while the `browser-bridge` unit was crash-looping on `Errno 98`. An **orphaned non-systemd process
  from Aug 1 16:18** held port 8788 and served the OLD `server.py`. Every "deployed" claim about that
  host would have been measured against the orphan.

**Check the consumer is running your artifact** — unit `active` (not `activating`), and `ss -lptn` +
`/proc/<pid>/cgroup` to confirm the listener is under the unit.

### 2. Twelve false-signal harnesses — and a known rule did not inoculate me

RULES.md already named "`diff` defaulting to unified output". I read it and hit the trap anyway in a
different shape: unified output meant `^>`/`^<` greps matched **nothing**, reporting "0 lines differ"
for files differing by **1,445 bytes** — a false CLEAN where the recorded case was a false PASS.
`cmp` settled it. A rule that names one *manifestation* does not inoculate against the *class*.

New ones this session: `$var:path` zsh bad-substitution (loop compared nothing, printed "NO MATCH" —
nearly concluded a deployed file held uncommitted work); node 24's reporter format breaking a
`^# tests` grep; `rc=$?` reading `echo`'s status not the pipeline's; a self-signed cert making
**every** harbor tag report absent; `nix-shell -p python312Packages.pytest` giving a FALSE FAIL for
`mail-actions`/`initiatives` (missing `psycopg2`/`minio`); a bare `python3 -m pytest` exiting **0**
printing `No module named pytest`; `node --test <dir>` silently `# tests 1 / # fail 1`;
zsh not word-splitting unquoted vars (reported all 10 required tools "MISSING").

### 3. I counted declarations and reported them as instances — 60× off

I grepped `skipif` decorators, found "2 node-related skips", and sized the work on it. Those two
decorators gated **123 tests** (`initiatives`: 660 passed/123 skipped in the sandbox vs 783/0 with
node). That error was the difference between a nit and the session's most valuable change.

### 4. The merged-tree gate earned itself

#278 added a docstring containing the phrase `` `cmd_op stderr` ``; #277's parity parser harvested
it as a **phantom wire op named `stderr`**. Red on the merged tree, **invisible on either branch
alone** — the test only existed on the other side of the merge. Fixed on both sides (reworded, and
the parser hardened with a shell lexer + command-position requirement).

## Open investigations / next steps

1. **Re-run `scripts/ship.sh`** — both hosts are at `81e2d76`, `main` is `9254361`.
2. **Docs/skills update PR is IN FLIGHT** (dispatched at end of session): RULES.md additions
   (consumer-verification, declarations-vs-instances, output-format-drift as a class), `CLAUDE.md`
   stale byte figure (`281 B free` → 443 headroom) + the two new gates, the #273 measurement into
   `reference/tabs-instances.md`, clawgate 0.7.82 + the `check-chart` hazard. **Check it landed.**
3. **Pre-push hook parity** — the flake gate covers the node suite; `githooks/tests-on-push.sh` does
   not. One line, ~8 s/push. Deliberately left as the operator's workflow call.
4. **Stale PRs** — #223, #231, #57 predate this work and were never triaged.
5. **auditloop credentials — DROPPED by the operator.** Do not re-raise.

## Gotchas / decisions / dead-ends

- 🔴 **`SKILL.md` is byte-gated with ~193 B usable.** Any core addition needs an eviction in the
  SAME commit. The floor was raised 100→250 because a 100 B floor fires *simultaneously* with the
  ceiling (mean ops row 190 B, reference row 166 B) instead of warning ahead of it.
- 🔴 **`~/workspace/kubeclaw` must be current before `make check-chart`** — it rsyncs *from* that
  clone. It was stale at chart 0.3.14 while the vendored copy was 0.7.1; running check-chart would
  have clobbered the deployed chart and reported a false failure. Fast-forwarded to 0.7.1 today.
- **The clawgate kubeconfig path is PER-HOST**, not wrong: laptop `~/workspace/homelab-infra/…`,
  workbench `~/workspace/homelab-talos/…`, each absent on the other. I reported it as "the skill is
  wrong" after checking one host — the one-measurement-not-a-general-claim error, made while citing
  that rule.
- **`--frame` is a GLOBAL flag** (parsed pre-subcommand, `browser:341-342`) —
  `browser --tab T --frame 4515 text --annotated`, not `text --frame 4515`. I got this wrong live.
- **An extension change needs `manifest.json` bumped** or `ping`/`extension_stale` cannot tell a
  loaded build from a stale one. #271 shipped without it; the bump is what made
  `expected 0.7.1 / loaded 0.7.0 / stale True` observable, and then `stale False` after ↻.
- **A guard that reimplements what it guards will drift** — #278 carries a copy of #277's parser as a
  defence-in-depth check; #277 has since hardened the real one. Currently the copy is *stricter*
  (visible false alarm, not a silent hole). Watch the direction.
- **Worktree isolation protects files, not branch refs** — two of my own agents collided on a branch
  ref (one held a pre-rebase tip).
- **`rerere` silently replayed a merge resolution** recorded by an *agent's throwaway worktree*:
  `git status` showed `UU` with **no conflict markers**. A replayed resolution is not a reviewed one
  — verify the content.
- **DEAD END (measured):** a forced `brave://discards` discard **changes the tabId** and releases
  ownership, so the `documentEmulation` staleness hazard (#273) is not reachable. The safety is a
  property of the browser, not the bridge — if a future Chromium preserves the id it returns silently.
- ⚠ **Never kill Brave** (`restore_on_startup` unset). ↻ is falsifiable via `browser ping`.

## How to verify

```bash
BB=~/workspace/devrc/scripts/browser-bridge/browser
$BB whoami                          # host + per-instance extension_stale
$BB --instance personal ping        # ~70ms healthy

# gates — COUNT, never read an exit code
nix flake check                     # pytests 2920/2918/2 skipped; nodetests 468/468
node --test --test-reporter=tap scripts/browser-bridge/tests/*.test.mjs   # GLOB form only
nix-shell -p 'python312.withPackages(p:[p.pytest p.psycopg2 p.requests p.minio])' \
  --run "python -m pytest scripts/browser-bridge/tests -q"                # 436 passed

# deploy state — merged is NOT deployed
scripts/ship.sh                     # converge both hosts
systemctl --user is-active browser-bridge          # must be `active`, not `activating`
curl -sf http://192.168.50.250:30302/health        # clawgate 0.7.82
```

---

## ADDENDUM — written after the above; the "State now" section is superseded

Everything above was written mid-session. What followed changed the state and added the
session's sharpest finding, so read this section as authoritative where the two disagree.

**State now (final):** `main` @ **`c9cb47a`**. **Both hosts converged + switched at `c9cb47a`.**
`nix flake check` **green**: pytest **4373 collected / 4372 passed / 1 skipped / 0 failed**,
node **468/468**. **Zero open PRs, zero open issues.** 17 devrc PRs merged this session.

### PR #276 shipped FOUR regressions that concealed each other

The gate hardening (#284) made the pytest check loud, and it immediately went red on `main` —
which read like "the new gate is noisy" and was actually four real defects, all from one commit:

| # | defect | fails where | invisible where | fixed by |
|---|---|---|---|---|
| 1 | `HERMETIC_DIRS` gained a FILE; `run_pytest()` guarded on `[ ! -d ]` → **913 tests unrun** | everywhere | *masked 2 and 3* | #289 |
| 2 | `SECRET_PATTERNS` moved to `guard_core.py` → drift test parses `[]` | dev hosts (hook deployed) | **skips** in sandbox | #291 |
| 3 | `nix-instantiate` absent from the check → 10 handle tests fail | sandbox | **passes** on dev hosts | #290 |
| 4 | `guard.js` couldn't find its core once deployed → every bash call refused | deployed hosts | — | #292 (other session) |

**(2) and (3) are exact complements** — each structurally invisible in the tier where the other
bites. That is the durable lesson, and it is now a rule in `claude/RULES.md`:
*a suite that runs in TWO TIERS must be green in BOTH.*

### Three declarations-vs-instances cases in ONE session
2 `skipif` decorators → **123** tests · 1 `HERMETIC_DIRS` entry → **913** tests ·
1 `nix_eval()` helper → **10** parametrized tests. Three is a pattern, not a coincidence.
Also now a rule.

### Verification notes worth keeping
- #291 deleted a pinned skip. The named risk was "a gate that silently stops counting", so that
  failure mode was tested **directly**: injecting an unpinned skip →
  `ERROR: 2 test(s) skipped, but 1 are pinned … MORE than pinned: tests stopped running`, exit 1;
  clean on revert. A targeted test of the named risk beat a general audit.
- #290's proof is an **identical collected count with failures → 0** (`959/949/10` → `959/959/0`).
  "Stopped failing" and "passes" are different claims; only the count distinguishes them.
- An agent was killed mid-mutation by a session limit. Before resuming it, the worktree was checked
  for a leftover mutation (**0 unstaged changes** — clean) and for a runaway process. Resuming blind
  would have measured the AFTER run against a poisoned baseline.

### 🔴 One thing left in the operator's tree
`ship.sh` was blocked on the **workbench** by untracked `nix/system/apply-nebula-443.sh`, which had
since landed upstream **with different content** (local copy has different variable names, a
`BACKUP` path, a different guard) — the operator's own variant, not a stale duplicate. It was
**renamed, not deleted**, sha verified identical:

```
nix/system/apply-nebula-443.sh.LOCAL-preserved-2026-08-02
```

Upstream's version now occupies the original path. `mv` it back to restore, delete to discard.
**Operator's call — do not resolve this unasked.**

### Standing operator decisions
- **auditloop credential rotation: DROPPED by the operator. Do not re-raise.**
- **Pre-push hook parity** (`githooks/tests-on-push.sh` does not run the node suite): deliberately
  left as the operator's workflow call, ~8s/push.
