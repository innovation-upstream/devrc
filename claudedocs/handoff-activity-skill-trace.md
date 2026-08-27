# Handoff: activity-skill-trace — 2026-08-26

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
Trace the `activity` telemetry skill end to end, fix what the trace found, and answer
whether a token-efficient "find my recent activity on X" search tool is worth building.

## State now
- Branch: `main`, clean (one pre-existing untracked file, `nix/system/apply-nebula-443.sh.LOCAL-preserved-2026-08-02`, not this session's).
- **All work MERGED AND SHIPPED. Nothing in flight. No open PR from this thread.**

Three PRs, each merged, shipped via `ship.sh`, and verified by artifact on BOTH hosts:

| PR | commit | what |
|---|---|---|
| #878 | `5426fe54` | `claude/skills/activity/reference/queries.md` — i3 queries hardcoded `host='laptop'` |
| #892 | `1c9c040a` | `claudedocs/close-the-loop/STATE.md` + `the-algorithm-applied-2026-06-17.md` — repoint `AGENTIC_LEVERAGE.md`, restore a measured count |
| #897 | `6509702b` | `CLAUDE.md` — document the test-subset runner that already existed |

Deploy state: `ship.sh` rc 0 after each; both hosts converged, 0 dangling / 0 stale managed
artifacts on each; `drift-check.sh` reports **no drift** on either host.

### The trace itself (what was verified live, not read)
- Source `~/workspace/devrc/claude/skills/activity/` → `claudeSkills` derivation (`nix/home.nix:108`) → `home.file.".claude/skills"` (`:1095`). Deployed copy is a **/nix/store symlink** — needs a `switch`, never just a pull.
- Five systemd user units confirmed on **both** hosts (`activity-collector`, `keylog`, `browser-activity-receiver`, `i3-source` active; `claude-activity-source` is a timer-driven oneshot, so `inactive`/`activating` is correct, not a fault).
- `deadman.py` → `state: ok  rows=13897  evaluated=17  dead=0`, rc 0. All 17 (host, source) pairs fresh — matches SKILL.md's "9 laptop, 8 workbench" exactly.

### `activity/SKILL.md` audit — 14 claims verified, 1 unmeasurable, 0 stale
Verified against live state: 13 schema columns (exact names/order), 180d TTL + `toYYYYMM(ts)`
partitions, budget `clamp(2 × p99, 2h, 48h)` (`BUDGET_K=2.0`, `FLOOR_BUCKETS=24`,
`CAP_BUCKETS=576`), `PRESENCE_SOURCES={keys,i3,tmux,zsh}`, `PRESENCE_STALL_HOURS=72`, the two
retired invariants genuinely retired, extension manifest `1.4.0`, extension absent on
workbench, `ch-regrowth-check` `OnCalendar=*-*-11 09:00:00` + `Persistent=true` and absent on
the laptop, browser-bridge `HEARTBEAT_INTERVAL_S=900`, both per-host endpoints + `ACTIVITY_HOST`,
NodePort 30123, SOPS secret and dashboard manifests present on `homelab-talos` `origin/trunk`,
all 8 tool paths.

## Open investigations — live diagnosis state

### The ClickHouse user/grant roster is UNVERIFIED (the only unmeasured SKILL.md claim)
- **Symptom + exact repro:** `SELECT name FROM system.users ORDER BY name FORMAT TSV` against
  `$CLICKHOUSE_URL` using the collector's own creds.
- **Observed (with values):** `Code: 497. DB::Exception: activity_writer: Not enough privileges.
  To execute this query, it's necessary to have the grant SELECT(name) ON system.users.
  (ACCESS_DENIED) (version 25.7.x (official build))`
  ⚠ The 4-part ClickHouse version is elided ON PURPOSE: pasted verbatim it parses as a
  routable public IP literal and fails
  `test_no_public_ips.py::test_no_unallowlisted_public_ip_literal_is_committed` on this
  PUBLIC repo. A version string shaped like a dotted quad is a live trap for any "paste
  the actual error" rule — this doc tripped it, and then the FIRST fix tripped it again by
  naming the offending range numerically in its own explanation. Describe such a range in
  words; never write the quad.
- **Ruled out:** "the writer is an admin" — the ACCESS_DENIED is positive evidence it is NOT,
  which is half of what SKILL.md claims. Reachability and creds are fine (the same connection
  answered every other query in this session).
- **Leading hypothesis:** the claim (`default`=admin, `activity_writer`=INSERT+SELECT,
  `activity_reader`=SELECT) is correct; it simply cannot be read from the writer role.
- **Next probe:** decrypt the admin password and enumerate, verbatim:
  ```bash
  APW=$(SOPS_AGE_KEY_FILE=~/workspace/homelab-talos/.secrets/age.key sops -d --extract '["stringData"]["admin-password"]' <(git -C ~/workspace/homelab-talos show origin/trunk:clusters/homelab/apps/activity/secrets.enc.yaml) --input-type yaml)
  curl -s --user "default:$APW" --data-binary "SELECT name FROM system.users ORDER BY name FORMAT TSV" http://192.168.50.94:30123/
  curl -s --user "default:$APW" --data-binary "SHOW GRANTS FOR activity_writer, activity_reader FORMAT TSV" http://192.168.50.94:30123/
  ```

## Next steps (ranked)
1. **Verify the CH user/grant roster** (devrc — no file change expected; touches only
   `claude/skills/activity/SKILL.md` if the claim turns out wrong). Runs the probe above.
   Low value on its own; do it only if touching that table anyway.
2. **Decide whether `reference/queries.md` deserves a machine-checked guard** (devrc —
   `claude/skills/activity/reference/queries.md`, a new `scripts/tests/test_*.py`). Today
   NOTHING checks its host-scope claims: the `host='laptop'` defect survived from whenever
   SKILL.md retracted it until 2026-08-26, and only a human reading the two files against each
   other found it. A guard could assert each host claim against a live count, or at minimum that
   SKILL.md and its reference do not contradict each other. **Weigh against**: this is the same
   "build more harness" reflex `close-the-loop/STATE.md` warns about — the defect was found
   once, in a doc, and cost one PR.
3. **The `AGENTIC_LEVERAGE.md` cross-repo reference is UNGUARDED** (devrc —
   `claudedocs/close-the-loop/STATE.md`). `test_doc_path_rot` skips it: its first segment is
   `datapacket-talos`, outside `ROOTS`, so the tool classifies it "not a claim this repo can
   settle". If that file moves, nothing tells us. Accept, or extend the test to a known set of
   sibling repos.

## Gotchas / decisions / dead-ends

- 🔴 **`test_doc_path_rot` does NOT cover `claudedocs/`.** `CORPUS_DIRS = ("claude", "CLAUDE.md")`,
  and the corpus is built from `git ls-files`, so an uncommitted edit is invisible twice over.
  A green from it on a `claudedocs/` change means **nothing**. MEASURED by planting a dead path
  twice — once with a `claudedocs/` target, once with an in-scope `scripts/` target — both stayed
  GREEN. The same test on `CLAUDE.md` went **RED** naming `('CLAUDE.md', 263, ...)`. It is a real
  guard, on a corpus narrower than its name suggests.
- 🔴 **A subset test run does NOT need an ad-hoc `nix-shell`** — `nix develop <repo> -c python3 -m pytest <paths> -q`. `.envrc` is `use opencode`, so a loaded direnv has no pytest, and `claude/RULES.md`'s worktree recipe copies that `.envrc` into every worktree. Now documented in `CLAUDE.md`; recorded here because it was diagnosed WRONG first (three true observations → "no subset mode exists" → a recommendation to build one that already shipped in `flake.nix`).
- 🔴 **`grep -c` on a retraction matches the retraction.** Twice this session a count nearly produced a false finding: the "old claim still present?" check hit my own quoted retraction, and the "retired invariants" check hit comments recording their removal. Verify structure (`^- ` anchors, or read the lines), never a substring count.
- **DECIDED — do NOT build an activity-search tool.** Measured demand over 30d: `find-a-session` **1**, `recent-work-recall` **1**, `what-was-I-doing` **0**, against 11,501 short prompts; `/find-session` exists and is invoked 3×/30d (43 all-time), so low use is not unavailability. `context-reload` (116) is the real recurring shape and is already owned by `/handoff` (375) + `/resume`. Both of this session's real inefficiencies were **wrong beliefs about existing tools**, not missing tools.
- **`ship.sh` `merge --ff-only` will REFUSE and skip a host** when a tracked file it must update is dirty. Hit this on `claudedocs/close-the-loop/STATE.md` — a live `mkOutOfStoreSymlink` deploy target. Sequence that works: commit the content → merge → re-verify the local blob hash → `git checkout -- <path>` → `merge --ff-only`.
- **Dated measurement docs are not scratch.** `the-algorithm-applied-2026-06-17.md` had `/next-lever (8)` deleted from a top-openers list because the skill was later retired. That falsifies a measurement of 147 sessions. Annotate as since-retired; never delete the count.

## How to verify
```bash
# the fix that started this — both hosts, deployed artifact not repo source
grep -c '^- 🔴 \*\*i3 is on BOTH hosts' ~/.claude/skills/activity/reference/queries.md   # 1
ssh zach@192.168.50.155 'grep -c "i3 is on BOTH hosts" ~/.claude/skills/activity/reference/queries.md'  # 1

# the pipeline is alive and no source has silently died
python3 ~/workspace/devrc/scripts/collector/deadman.py    # exit 0, evaluated=17, dead=0

# both hosts converged, nothing stale or dangling
~/workspace/devrc/scripts/drift-check.sh                  # "no drift on the host(s) CHECKED"

# the documented subset runner actually runs
nix develop ~/workspace/devrc -c python3 -m pytest ~/workspace/devrc/scripts/tests/test_doc_path_rot.py -q
```
