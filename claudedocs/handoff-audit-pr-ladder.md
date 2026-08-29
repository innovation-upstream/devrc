# Handoff: audit-pr-ladder — 2026-08-28

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
Evaluate the `audit-pr` skill against how its round-ladder actually behaves in real
sessions, then fix what the measurement exposed. It exposed that the ladder's
findings-keyed stop rule does not terminate in the guard-hardening regime.

## State now
- Branch: `main`. Nothing of this work is uncommitted.
- **Eight PRs, all MERGED and verified by content in `origin/main`** (never by ancestry —
  a squash merge makes the branch head permanently a non-ancestor):
  - `#900` attribution gate · `#922` two worktree hazards · `#933` the two instruments
  - `#958` → squash `1b2117b6` — `scripts/audit-dispatch.py`, the brief assembler
  - `#979` → `8bab93b1` — espanso `:acq` split, rescued off the workbench's `main`
  - `#993` → `c7d70a40` — the ladder writeup in `round-ladder-evidence.md`
  - `#999` → `22e2830a` — espanso exact-trigger preference
  - `#1001` → `289865af` — round-13 fixes (`repo_unknown_reason`, runnable cross-repo recipe)
- **Deployed AND verified at the consumer**, not just the deploy: `#999` needed
  `systemctl --user restart keylog.service` on both hosts (workbench PID 2851719→2937124,
  laptop 1718672→1721180), then the original symptom was re-run against the deployed
  module `/nix/store/jawj46mw…-hm_keylog`: `acq` and `alo` both moved `None` → their own
  snippet, every other term unchanged.
- **Hosts are 2 commits behind** `origin/main` (`3d29aba1`) as of this writing — `#992`
  and `#996`, both other sessions' work. Not drift from this thread.

## Open investigations — live diagnosis state

### `discord-embed-ext` uncommitted work is baked into the workbench's deployed generation
- **Symptom + exact repro:** `bash ~/workspace/devrc/scripts/ship.sh` → the workbench leg
  prints `🔴 DIRTY AND IN THE ARTIFACT — nix reads 2 path(s) at eval/build time`.
- **Observed (with values):** three tracked files modified —
  `scripts/discord-embed-ext/extension/{embed_enlarge.js,manifest.json}` and
  `tests/embed_enlarge.test.mjs`; `git diff --stat` = 3 files, +68 −2. Two untracked
  `claudedocs/{handoff,proposal}-mention-detection.md`. Both extension paths are read by
  nix at eval/build time, so the built generation is `origin/main` PLUS them. The laptop
  built clean. **Both hosts report the same sha and run different code.**
- **Ruled out:** not this thread's work — `git log --diff-filter=A` shows no commit of mine
  touches `scripts/discord-embed-ext/`. Not a ship defect: ship reported it correctly and
  still converged rc 0.
- **Leading hypothesis:** an active session mid-feature (file mtimes 19:11–19:14, a test
  file appeared later), unaware the tree is also a deploy source.
- **Next probe:** `git -C ~/workspace/devrc diff scripts/discord-embed-ext/` and ask the
  owning session to commit or PR it. 🔴 Do NOT `checkout --` it — `claude/RULES.md` names
  in-tree work as unsaved work one routine checkout from silent deletion.

### A `localverify` remote keeps being written into the SHARED clone
- **Symptom + exact repro:** `git -C ~/workspace/devrc remote -v` → `localverify
  /tmp/verify-remote.git (fetch|push)`. The target directory **does not exist**.
- **Observed (with values):** `.git/config` mtime moved 17:41:21 → 20:10:26 → 21:49:12
  across one session, so it is written repeatedly, not a one-off. No
  `remote.localverify.skipDefaultUpdate`, so it IS included in `fetch --all` / `remote
  update`. `git fetch origin` is unaffected.
- **Ruled out:** not devrc's tracked code — searched with `find … | xargs grep`, **not**
  `grep -r`, because grep here is ugrep and honours `.gitignore`, which would return a
  confident zero for exactly the generated paths this could hide in. Zero hits either way.
  Not 23 separate pollutions: it appears in 23 devrc directories because **worktrees share
  the common git dir's config** — one entry seen 23 ways (a count of sites is not a count
  of instances).
- **Leading hypothesis:** another session's push/fetch verification pointing at the real
  repo instead of a scratch clone.
- **Next probe:** catch the writer — `inotifywait -m ~/workspace/devrc/.git/config` (via
  `nix-shell -p inotify-tools`) and correlate the next write with running sessions.
  Removing the remote without fixing the writer only races a live test.

## Next steps (ranked)
1. **Ship the 2-commit gap** — `bash ~/workspace/devrc/scripts/ship.sh`, then read EVERY
   per-host line, not the final verdict. Hosts at `289865af`, origin at `3d29aba1`.
2. **Hand `discord-embed-ext` back to its owner** (repo: `devrc`, paths above). Nothing to
   build; the ask is that the session commits or PRs it.
3. **Find the `localverify` writer** (repo: `devrc`, `.git/config`). See probe above.
4. **Consider `date` in the espanso picker** (repo: `devrc`, `nix/home.nix`) — it still
   resolves `None`, matching `:eos` and `:roo`, neither of which is *named* `date`, so the
   exact-trigger tie-break correctly does not apply. Only worth acting on if the keylog
   dataset shows it is a term actually typed — which it can now answer, because attribution
   works.

## Gotchas / decisions / dead-ends
- 🔴 **The ladder never returned a clean round in twelve.** The stop rule assumes
  convergence; in the guard-hardening regime each fix writes guards that become the next
  round's audit surface. Stopped on a stated criterion instead: no 🔴, no blast radius
  beyond "the brief contains a false sentence", and the recurring shape swept at EVERY
  consumer of the touched predicate. Full evidence in
  `claude/skills/audit-pr/reference/round-ladder-evidence.md` (landed by `#993`).
- 🔴 **The `#900` attribution gate cannot rescue a ladder whose payload IS PROSE.** It
  stops after two zero-payload rounds; here "fixed a defect" and "reworded a warning" are
  frequently the same edit. Structural blind spot on any generator/docs/prompt PR.
- **Nine instances of one shape**, each a predicate read as a STRONGER fact than it
  carries. Twice it was *a default stated as a fact* (`baseRefName or "main"`, then
  `REPO_UNKNOWN`'s own sentinel text). Sweep every consumer of a predicate you touch.
- **`--deselect` with an ABSOLUTE path matches nothing and pytest is silent.** Positive
  control: absolute → 9789 collected (inert), relative → 9187/9789 (602 deselected).
- **`-q -q` suppresses pytest's `N passed` line entirely**, leaving only a piped exit
  status — which is `tail`'s, not pytest's.
- **`$(...)` strips trailing newlines**, so a newline-rejection probe passes vacuously
  through command substitution; pass the value through argv instead.
- **`gh` has no `baseRefteName`-style `baseRepository` field** — use `url` +
  `isCrossRepository`.
- **Editing a file in the shared clone while it sits on `main`** violates
  feature-branches-only. Recovery used here: save the edit aside, `git checkout --` to
  restore byte-identical to HEAD, redo in a branch worktree.
- **A rescued commit gets a PR for a reason** — `#979` was already live on the workbench
  and its required check caught a real defect (`ask` gone ambiguous).
- **Five flakes in `test_subsystem_store_api.py` in one day**, all on unrelated PRs, and
  its assertion message described the OPPOSITE of the failure (`len(answers) == 0`,
  `raw == b''`, under the text "a second response followed the 200"). Four cycles were lost
  to that message before a pod was read in time. **CLOSED by another session's `#996`**
  (audit BEFORE responding + serialised sink); `saw_eof` is now asserted 11 times.

## How to verify
```bash
# every PR landed by CONTENT, never ancestry (squash merges break ancestry forever)
git -C ~/workspace/devrc fetch origin main
git -C ~/workspace/devrc show origin/main:scripts/audit-dispatch.py | grep -c 'refs/audit/'   # 7
git -C ~/workspace/devrc show origin/main:scripts/collector/keylog/espanso_detect.py | grep -c '_names_trigger'  # 2

# the espanso fix is live in the RUNNING collector, not merely deployed
systemctl --user show keylog.service -p MainPID -p SubState
python3 - <<'PY'
import sys, yaml
D = "/nix/store/jawj46mwcz9xd5ly4ixja5kdr9wvsmf6-hm_keylog"   # re-resolve after any switch
sys.path.insert(0, D)
import espanso_triggers as ET
from espanso_detect import EspansoDetector
d = EspansoDetector(ET.load_triggers(yaml.safe_load(open("/home/zach/.config/espanso/match/base.yml")), {"search_shortcut": "CTRL+SPACE"}))
for t in ("acq", "alo"):
    print(t, "->", d._attribute(t))    # expect :acq and :alo, NOT None
PY
```
