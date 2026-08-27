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
- Branch: `main`, clean of this thread's work. The tree carries **another session's**
  uncommitted discord-embed-ext work (` D claudedocs/handoff-discord-embed-ext-rescue.md`,
  ` M scripts/discord-embed-ext/extension/embed_enlarge.js`, ` M .../embed_enlarge.test.mjs`)
  plus the long-standing untracked `nix/system/apply-nebula-443.sh.LOCAL-preserved-2026-08-02`.
  **None of that is this thread's — do not commit or discard it here.**
- **All this thread's work MERGED, SHIPPED and VERIFIED BY ARTIFACT on BOTH hosts.**
  No open PR, no claim held. `ship.sh` rc 0 with **cross-host agreement COMPARED**
  (2 hosts at `d3875d64`), 0 dangling / 0 stale on each.

Five PRs across this thread, each merged, shipped and verified by artifact on BOTH hosts:

| PR | commit | what |
|---|---|---|
| #878 | `5426fe54` | `claude/skills/activity/reference/queries.md` — i3 queries hardcoded `host='laptop'` |
| #892 | `1c9c040a` | `claudedocs/close-the-loop/STATE.md` + `the-algorithm-applied-2026-06-17.md` — repoint `AGENTIC_LEVERAGE.md`, restore a measured count |
| #897 | `6509702b` | `CLAUDE.md` — document the test-subset runner that already existed |
| #913 | `8e42c5e6` | `browser` is no longer laptop-only — 5 stale claims across `activity/SKILL.md`, `activity/reference/queries.md`, `scripts/collector/deadman.py` |
| #916 | — | the retracted "workbench is headless" claim survived in COMMENTS — 3 sites incl. `scripts/claude-hooks/claude-notify.py` |

Earlier in the session the laptop was 6 commits behind and was converged (`ship.sh --no-local`).

### What changed in the world, not just the docs
`workbench/browser` began emitting **2026-08-26 21:46** — the activity extension was loaded
into the workbench's Brave. The roster is now **18 pairs (9 laptop / 9 workbench)**, not 17.
`deadman.py` picked the new pair up with **no edit anywhere**; only the prose had to change.

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

### 🔴 UNRESOLVED — a live ClickHouse credential was disclosed to a third party
- **Symptom + exact repro:** dispatch an opencode run whose task needs the collector creds.
  The agent ran `head -90 ~/.config/activity-collector/env` and it was **ALLOWED**; seconds
  later `cat ~/.config/activity-collector/env | head -5` was **auto-rejected** with
  `permission requested: external_directory (/home/zach/.config/activity-collector/*)`.
  Same file, same dir, opposite outcomes — the guard is inconsistent **by command shape**.
- **Observed (with values):** the `activity_writer` password was printed in full into the run
  log. Copies found by `grep -al`: the dispatch log (deleted), `~/.local/share/opencode/log/opencode.log`
  (**16 occurrences**), `~/.local/share/opencode/opencode-stable.db` (**37 occurrences**).
  `~/.local/share/opencode/storage` — none.
- **Ruled out — it never reached git.** `.opencode-dispatch/` is gitignored (`.gitignore:59`,
  git reports it `!!`) and the string is absent from `origin/main`. Verified, not assumed.
- **🔴 The part that cannot be undone:** the agent read the file INTO ITS CONTEXT, so the
  credential was transmitted to the model provider — **OpenRouter → DeepSeek**
  (`openrouter/deepseek/deepseek-v4-flash`). Either party may retain or log it. **This, not the
  local files, is why rotation is required**; scrubbing disk copies alone fixes nothing.
- **Status: NOT ROTATED.** Re-verified at handoff time: the original value is still live in
  `~/.config/activity-collector/env`.
- **Next probe / action:** rotate `activity_writer` in ClickHouse, update
  `~/.config/activity-collector/env` on **both** hosts (chmod 600), restart
  `activity-collector` on each, then confirm writes resume with
  `python3 ~/workspace/devrc/scripts/collector/deadman.py` (expect rc 0, `dead=0`).
  Do the two hosts together — a half-done rotation stops the pipeline writing.

## Next steps (ranked)
🔴 **Ranks were REASSIGNED this session.** Verified `claim-work --list` held **0** claims
against this doc first, so no live claim was re-pointed. Old 1/2/3 are now 2/–/3.

1. **Rotate the leaked ClickHouse credential + scrub the two local copies** (see the
   investigation block above). Touches no repo file; `~/.config/activity-collector/env` on
   both hosts, ClickHouse, and the two `~/.local/share/opencode/` stores.
2. **Verify the CH user/grant roster** (devrc — `claude/skills/activity/SKILL.md` only if the
   claim is wrong). Was rank 1. Still the single unmeasured SKILL.md claim; the admin-password
   probe is in the "Gotchas" of the original doc. **Do it DURING step 1** — rotation
   authenticates as admin anyway, which is exactly what this probe needs.
3. **The `AGENTIC_LEVERAGE.md` cross-repo reference is UNGUARDED** (devrc —
   `claudedocs/close-the-loop/STATE.md`). Unchanged from the original rank 3: `test_doc_path_rot`
   skips it because its first segment is outside `ROOTS`. Accept, or extend to sibling repos.
4. **Record the CI-trigger trap in the `resume` skill** (devrc — `claude/skills/resume/SKILL.md`,
   step 6). One line: push the branch early for visibility **but open the PR in the same breath**.
   See the Gotcha below — this is a trap the skill's own instructions currently lead you into.
5. **DECLINED, and re-confirmed this session: do NOT build a machine-checked guard for
   `reference/queries.md`.** Was rank 2. Carried forward because it is measured, not opinion:
   nothing checks that file's host-scope claims, and the `host='laptop'` defect survived from
   whenever `SKILL.md` retracted it until **2026-08-26** — only a human reading the two files
   against each other found it. Weighed against the "build more harness" reflex
   `close-the-loop/STATE.md` warns about: the defect was found, in a doc, and cost one PR.
   This session found a *second* instance of the class (#916), which strengthens the case
   slightly — a THIRD would change the answer.

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

- 🔴 **A CI trigger needs the PR to EXIST — confirmed by controlled comparison, not theory.**
  #913: pushed the commit, opened the PR ~1 min later → **0 check-runs for 27 minutes** while
  `tekton/devrc-pytests`/`-nodetests` sat unreported and the PR read `BLOCKED`. #916: pushed and
  opened the PR immediately → both checks present within ~1 min. Same repo, same day, same
  protection. Sibling PRs #907/#908/#909 were green throughout, which is the control that
  ruled out an outage. **The `/resume` skill's own "push the branch the moment you create it"
  step causes this** if you then do the work before opening the PR. Fixed by an empty commit;
  do NOT reach for the branch-protection escape hatch for this.
- 🔴 **BLIND DOGFOODING FOUND A DEFECT THAT THREE PROSE SWEEPS AND A GREP MISSED — repeat it.**
  An opencode run was given tasks whose correct answers depended on the docs being right, told
  nothing about what had changed, and forbidden from reading git history. It reported unprompted
  that `SKILL.md`'s `## status` block said `i3-source+keylog laptop-only` while the source table
  100 lines above marked both ✅ on both hosts. **The claim had been retracted in PROSE three
  times and left standing in every COMMENT-borne copy**, because a prose sweep does not read
  inside a fenced command block or a code comment. I ran a `laptop-only` grep in the same session,
  saw the line in my own output, and walked past it. → PR #916.
- 🔴 **`grep -c` on a retraction matches the retraction — PREDICT the count before reading it.**
  After #916 the laptop still greps `1` for `i3-source+keylog laptop-only`; line 171 is the
  retraction note quoting the old text. Predicting the number first is what stops it reading as
  "the fix did not land".
- **The budget on a NEW telemetry pair is deliberately NOT hand-tuned.** `workbench/browser`
  arrived with a `2.0h` budget sized off ~1 day of history; the p99 gap grows and the budget
  widens on its own. Nothing in `deadman.py` is hand-listed and an exception table is what it
  exists to avoid. ⚠ Budget is burned in **ACTIVE buckets** (`keys`/`i3`/`tmux`/`zsh` on that
  host), so an operator asleep with Brave shut costs it nothing — **an overnight false-DEAD was
  predicted in this session and was WRONG**; read the mechanism before predicting an alarm.
- **`ship.sh` reports DIRTY-AND-IN-THE-ARTIFACT, and it is not cosmetic.** The workbench's
  current generation is `origin/main` **PLUS** an uncommitted
  `scripts/discord-embed-ext/extension/embed_enlarge.js` — nix reads that path at build time.
  It will silently revert on the next clean-tree switch. Read the per-host lines, never the
  final verdict.
- **`resume-state.sh` falls back to the NEWEST handoff when the named one is absent**, and says
  so as a `!` gap. This session's checkout was on an older branch, so the named doc was missing
  and the digest reconciled a *different* initiative. Fix: read the doc from `origin/main`
  (`git show origin/main:<path>`) or point the reconciler at a worktree that has it.

## How to verify
```bash
# both this thread's PRs live in the DEPLOYED artifact (a /nix/store path, not the repo source)
grep -c '9 on the workbench, 18 total'   ~/.claude/skills/activity/SKILL.md   # 1
grep -c 'All five are LOADED on BOTH'    ~/.claude/skills/activity/SKILL.md   # 1
ssh zach@192.168.50.155 'grep -c "All five are LOADED on BOTH" ~/.claude/skills/activity/SKILL.md'  # 1

# the roster really is 18 pairs and nothing has died
python3 ~/workspace/devrc/scripts/collector/deadman.py    # rc 0, evaluated=18, dead=0

# both hosts converged AND compared (not "NOT COMPARED")
~/workspace/devrc/scripts/drift-check.sh

# 🔴 has the credential been rotated yet?
# The pre-rotation value is NOT written here on purpose — this repo is PUBLIC, and a
# verify step that embeds the secret is how a contained leak becomes a committed one.
# (Drafting this doc reached for exactly that and it was caught before any write.)
# Compare the live value against the one quoted in the opencode log instead:
sed -n 's/^CLICKHOUSE_PASSWORD=\(....\).*/live prefix: \1…/p' ~/.config/activity-collector/env
grep -aom1 'CLICKHOUSE_PASSWORD=....' ~/.local/share/opencode/log/opencode.log
# same 4-char prefix  => NOT rotated, still exposed.
# differs, or the log has been scrubbed => rotated; then delete the two local copies:
#   ~/.local/share/opencode/log/opencode.log  and  ~/.local/share/opencode/opencode-stable.db
```
