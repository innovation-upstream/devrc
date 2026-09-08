---
name: civitai-app-release
description: "Release and submit Civitai App Blocks (gen-matrix, sensei, model-benchmarking, playable-collections, custom-generators, app-requests, generate-from-model). Use for: civitai app submit, bump an app version, a refused submit, submission/deploy state, a pass across the app repos. Platform-side ops — Tekton, Flipt, deploy diagnosis — is the `app-blocks` skill in talos-infra."
argument-hint: "<action> — state <app> [version] | floor <app> | preflight <dir> [floor] | submit <repo> | fleet"
allowed-tools: Bash, Read, Edit, Write, Grep, Glob
---

# Civitai App Blocks — author-side release

You are the app AUTHOR here. Platform operation — Tekton, Flipt flags, the
publish-request state machine, deploy diagnosis — is `app-blocks` in
`datapacket-talos`, and it only loads with that repo as cwd.

## Use the scripts. They exist because prose failed.

Two reads of platform state were wrong in one session, each looking like an
answer rather than an error. Do not re-derive these by eye.

```bash
SKILL=~/.claude/skills/civitai-app-release

python3 $SKILL/app_state.py <app> <version>   # authoritative state; exit 0 iff live
python3 $SKILL/app_state.py <app>             # the submit floor
python3 $SKILL/preflight.py <export-dir> [floor]   # refuse a doomed submit
```

`CIVITAI_STATUS_FILE=<dump>` makes `app_state.py` read a captured
`civitai app status` instead of calling the CLI — use it to reason about a
past state, and it is how the tests run offline.

## The three facts that cost the most time

1. **`civitai app status <slug>` shows only the NEWEST submission.** A
   withdrawn duplicate of the same version masks a healthy `building` row
   underneath. Read the list view — which is what `app_state.py` does.
2. **The submit floor is the highest version ON RECORD, not deployed.** A
   withdrawn submission still occupies it, so an app whose latest row is
   `withdrawn` cannot re-submit at that version.
3. **The builder picks its install command from `buildCommand`'s first word**
   (`pnpm run build` → requires `pnpm-lock.yaml`; anything else → requires
   `package-lock.json`). A manifest and lockfile that disagree is a guaranteed
   build failure that **CI cannot see** — `.github/` is not in the bundle.
   `preflight.py` asserts the pairing; `civitai app validate` also catches it.

## Releasing

Both version fields move together (`block.manifest.json` + `package.json`);
every repo has a guard asserting it. pnpm repos need **no** lockfile edit —
`package-lock.json` recorded the root version, `pnpm-lock.yaml` does not.

```bash
# Submit from a clean export, NEVER a worktree: a worktree's .git is a FILE,
# and the CLI's exclusion list only drops .git DIRECTORIES.
git -C <repo> archive origin/<main-or-trunk> | tar -x -C <export-dir>
python3 $SKILL/preflight.py <export-dir> "$(python3 $SKILL/app_state.py <app> | sed 's/.*floor=//')"
civitai app validate <export-dir>
civitai app submit <export-dir> --yes     # --yes is required non-interactively
```

Then a **moderator must approve** before anything builds. `.envrc` is dropped
from every bundle by the `.env*` rule; `.env.production`, `.env.example` and
`.env.sample` are kept at the project root only.

A failed build leaves the previous version serving — it is not an outage.

## Where a change belongs

| The change is about | Repo |
|---|---|
| One app's own UI / logic | that app's repo |
| Anything imported from `@civitai/*` | `civitai/civitai-app-starters` — **all five packages ship from there** |
| Host, scopes, money path, app storage, submit/approval | `civitai/civitai` |
| The `civitai` CLI | `civitai/cli` (Go) |
| The build pipeline itself | `civitai/talos-infra` → `clusters/production/apps/tekton-builds/app-blocks-pipeline.yaml` |
| Public docs | `civitai/civitai-developer-docs` |

`sensei`'s default branch is **`trunk`**; the others use `main`. Canonical
checkouts are `~/workspace/civit/<repo-name>`; suffixed siblings are worktrees.

## Verifying

Never report a submit as done from `civitai app status <slug>` alone — use
`app_state.py`, then confirm the URL serves:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://<slug>.civit.ai/
```

The real Buzz spend loop is Turnstile + auth gated and is **not** verifiable
from here, by any local run or test. Say so rather than implying coverage.

## Reference

- `claude/skills/civitai-app-release/reference/platform-build-contract.md` —
  what the builder actually runs, and the three environments (dev shell, CI,
  builder) that can silently disagree.
- `scripts/tests/test_civitai_app_release.py` — guards for both scripts. Every
  case is a defect that occurred; the mutation battery is in the PR that added
  them.
