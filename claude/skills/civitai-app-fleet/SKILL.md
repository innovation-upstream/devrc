---
name: civitai-app-fleet
description: "Bulk iteration across the Civitai App Block fleet (gen-matrix, sensei, model-benchmarking, playable-collections, custom-generators, app-requests, generate-from-model) — rolling one change through every app repo — plus release: civitai app submit, version bumps, a refused submit, deploy state. Platform-side ops is the `app-blocks` skill in talos-infra."
argument-hint: "<action> — inventory | state <app> [version] | floor <app> | preflight <dir> [floor] | fan-out"
allowed-tools: Bash, Read, Edit, Write, Grep, Glob
---

# Civitai App Block fleet

Seven repos that ship one app each. Most work here is **the same change rolled
through all of them**; releasing is the last mile of that, not the whole job.
Platform operation — Tekton, Flipt, deploy diagnosis — is `app-blocks` in
`civitai/talos-infra` (checked out here as `datapacket-talos`), and it only
loads with that repo as cwd.

## Start every bulk pass with the inventory

```bash
SKILL=~/.claude/skills/civitai-app-fleet
python3 $SKILL/fleet.py            # --json drives a fan-out; --no-platform skips the CLI
                                   # 🔴 it git-fetches SEVEN SHARED clones by
                                   # default — --no-fetch to touch nothing
```

It reports, per repo: **default branch** (`sensei` is `trunk`, six are `main`),
what the base clone currently has **checked out and how dirty it is**, both
version fields, `buildCommand`, lockfile, **whether it declares multiple vitest
projects** (`2+` vs `1` — a class, not a count: the check cannot tell two from
three) and **which file holds the version-lockstep guard** (split between
`manifest.test.ts` and `version-lockstep.test.ts`).

Every one of those columns was a wrong assumption before it was a column. Do not
write them into a brief from memory — run it, because the answers move.

## Fanning out

Read `claude/skills/civitai-app-fleet/reference/fan-out.md` before dispatching
agents. The short version, each of which cost real time:

- **Do NOT pass `isolation: "worktree"` for cross-repo work** — it builds a
  worktree of the CWD's repo, not the target's. Have each agent run
  `git -C <target> worktree add` itself.
- **Branch off a freshly fetched `origin/<default>`, never local `HEAD`.** Base
  clones sit on other people's branches with uncommitted work more often than
  not, and a live session moved one underneath this work four times in an
  afternoon.
- **One brief file, not N prompts.** Agents given the same brief produce
  comparable output; agents given N paraphrases diverge, and then you cannot
  tell a finding from a wording difference.
- **Mandate evidence, not "done"** — before/after test counts, a mutation
  matrix, and merges verified BY CONTENT (`git cat-file -e origin/<base>:<path>`),
  never by ancestry, since a squash never makes the branch an ancestor.
- **A guard that is right in one repo and wrong in six is the normal case.**
  When repos disagree, the majority is not the evidence; find which is right.

## Releasing

Both version fields move together; every repo has a guard for it. pnpm repos
need **no** lockfile edit — `package-lock.json` recorded the root version,
`pnpm-lock.yaml` does not.

```bash
python3 $SKILL/app_state.py <app> <version>   # authoritative state; exit 0 iff live
python3 $SKILL/app_state.py <app>             # the submit floor

# Submit from a clean export, NEVER a worktree: a worktree's .git is a FILE,
# and the CLI's exclusion list only drops .git DIRECTORIES.
git -C <repo> archive origin/<default> | tar -x -C <dir>
python3 $SKILL/preflight.py <dir> "$(python3 $SKILL/app_state.py <app> | sed 's/.*floor=//')"
civitai app validate <dir>
civitai app submit <dir> --yes            # --yes required non-interactively
```

Then a **moderator must approve** before anything builds. A failed build leaves
the previous version serving — it is not an outage. The `.env*` rule drops
`.envrc` from every bundle, but KEEPS `.env.example`, `.env.production` and
`.env.sample` at the project root — `.env.production` is load-bearing for some
apps, so the second half of that rule matters as much as the first.

`CIVITAI_STATUS_FILE=<dump>` makes `app_state.py` read a captured status instead
of calling the CLI — for reasoning about a past state, and how the tests run
offline.

## The three reads that cost the most time

1. **`civitai app status <slug>` shows only the NEWEST submission.** A withdrawn
   duplicate of the same version masks a healthy `building` row underneath.
   `app_state.py` reads the list instead.
2. **The submit floor is the highest version ON RECORD, not deployed** — a
   withdrawn submission still occupies it.
3. **The builder picks its install command from `buildCommand`'s first word.** A
   manifest and lockfile that disagree is a guaranteed build failure **CI cannot
   see**, because `.github/` is not in the bundle. `preflight.py` and
   `civitai app validate` both catch it.

## Where a change belongs

| The change is about | Repo |
|---|---|
| One app's own UI / logic | that app's repo |
| Anything imported from `@civitai/*` | `civitai/civitai-app-starters` — **all five packages ship from there** |
| Host, scopes, money path, app storage, submit/approval | `civitai/civitai` |
| The `civitai` CLI | `civitai/cli` (Go) |
| The build pipeline | `civitai/talos-infra` → `clusters/production/apps/tekton-builds/app-blocks-pipeline.yaml` |
| Public docs | `civitai/civitai-developer-docs` |

Canonical checkouts are `~/workspace/civit/<repo-name>`; suffixed siblings are
worktrees, often someone else's.

## Verifying

Never report a submit from `civitai app status <slug>` alone — use
`app_state.py`, then confirm the URL serves:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://<slug>.civit.ai/
```

The real Buzz spend loop is Turnstile + auth gated and is **not** verifiable
from here by any local run or test. Say so rather than implying coverage.

## Reference

- `claude/skills/civitai-app-fleet/reference/fan-out.md` — dispatching a change
  across the fleet, and the hazards that actually bit.
- `claude/skills/civitai-app-fleet/reference/platform-build-contract.md` — what
  the builder runs, and the three environments that can silently disagree.
- `scripts/tests/test_civitai_app_fleet.py` — guards for the scripts. Every case
  is a defect that occurred.
