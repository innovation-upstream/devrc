# What the platform builder actually runs

Source of truth, read 2026-09-07 from the deployed pipeline:

```
civitai/talos-infra
  clusters/production/apps/tekton-builds/app-blocks-pipeline.yaml
```

🔴 **Not** `civitai/gpu-fleet-infra`. That repo also contains an
`app-blocks-pipeline.yaml`, it has **no** `buildCommand`/`pnpm`/`corepack`
handling at all, and reading it produced a confident, wrong conclusion that the
platform could not build pnpm apps — while a pnpm app had been live for six
weeks. Check `talos-infra` first; if the two disagree, deploy history wins over
either file.

## The install recipe

🔴 **THIS FILE IS THE SINGLE OWNER OF THE RECIPE.** `preflight.py` used to carry
a second copy of it, and the two drifted: this one passed `--ignore-scripts` on
the pnpm branch, that one did not. Two statements of one rule means one of them
is wrong and nothing tells you which — so the script now states only the
lockfile mapping it actually asserts and points here for the rest. Do not
re-paste the recipe anywhere.

The builder selects its install command from `buildCommand`'s **first word**:

```sh
pnpm)  [ -f pnpm-lock.yaml ]    || fail  # "commit your lockfile"
       corepack enable; pnpm install --frozen-lockfile --ignore-scripts ;;
yarn)  corepack enable; yarn … ;;
*)     [ -f package-lock.json ] || fail
       npm ci --ignore-scripts ;;
```

⚠ **`--ignore-scripts` ON THE PNPM BRANCH IS THE ONE FLAG THIS FILE DOES NOT
OWN.** Its value is whatever
`clusters/production/apps/tekton-builds/app-blocks-pipeline.yaml` in
`civitai/talos-infra` says, and it is being re-read there rather than settled
from here. Read the yaml before relying on it; the npm branch's
`--ignore-scripts` is not in doubt.

Consequences worth knowing before you change a manifest:

- **No `buildCommand` at all ⇒ the legacy `npm run build` default**, so a
  pnpm-only tree fails on the missing `package-lock.json`. This shipped once.
- Reverting the word `pnpm` to `npm` without restoring `package-lock.json`
  hard-fails the build of the **live** app.
- `--ignore-scripts` means postinstall hooks never run in the build.

## The manifest allowlist

`BUILD_COMMAND_RE` in civitai's `block-manifest-validator.service.ts`:

```
/^(?:(?:npm|pnpm|yarn) run [a-zA-Z0-9:_-]+|(?:npx )?vite build)$/
```

Anchored, and shell metacharacters are rejected separately. So `pnpm  run build`
(two spaces) is **rejected outright**, even though first-token parsing reads it
as a valid pnpm build. `outputDir` defaults to `dist` when omitted.

## Three environments that can silently disagree

| | node | package manager | pinned by |
|---|---|---|---|
| dev shell | `.nvmrc` | `flake.nix` `pnpmMajor` | the app repo |
| CI | `.nvmrc` via `node-version-file` | `pnpm/action-setup` | the app repo |
| **platform builder** | **`node:22-alpine`** | **`corepack enable` — unpinned** | talos-infra |

A green local run and a green merge gate are evidence about the first two only.
`.github/` is **not** in the submitted bundle, so CI never executes
`buildCommand` even once. Treat a build failure that reproduces nowhere locally
as a node-major or package-manager difference first.

The app repos pin node 24; the builder is on 22 (LTS until 2027-04, not EOL).
`civitai/talos-infra#1456` proposes aligning it — note that builder builds
**every tenant's** app, not just ours.

## The parent-origin allowlist is not yours

The builder injects `VITE_BLOCK_ALLOWED_PARENT_ORIGINS` as a Docker `ENV`,
which outranks any `.env` file in the bundle. Setting it in an app repo affects
a **local** build only. Never "fix" a blank embedded iframe by editing the app
repo — the value must mirror the per-app CSP `frame-ancestors`, and both live
platform-side.

## What the bundle contains

Submitted by `civitai app submit` is the SOURCE tree; the platform rebuilds.
Dropped at any depth: `node_modules`, `dist`, `build`, `out`, `.git`, `.next`,
`.turbo`, `.venv`, coverage/cache dirs, `*.zip`, and `.env*` files — which
includes **`.envrc`**. Kept at the project root only: `.env.example`,
`.env.production`, `.env.sample`.

🔴 **RETRACTED (verified against CLI 0.1.105, 2026-09-25): "export with
`git archive`, not from a worktree".** That rule was true of an older CLI, whose
exclusion list matched `.git` only as a DIRECTORY, so a linked worktree's `.git`
FILE was packaged and the platform rejected the bundle. The CLI fixed it
(issue #409): `.git`/`.hg`/`.svn` are now matched on the **exact name**
regardless of file type — `vcsMetadataNames` in `internal/pkgzip/pkgzip.go`, and
`app submit --help` states it outright ("in a linked worktree or a submodule,
`.git` is a file").

Measured: `civitai app submit . --package-only` run inside a linked worktree
printed `Skipped 3 path(s): .env (.env*), .git, node_modules/` and produced a zip
with no `.git` entry, while a sibling `.gitattributes` **was** packaged (7 files
vs 6) — so the match is on the exact name, not a prefix.

**Following the retracted advice now costs two guards**, because both degrade
silently on a directory that is not inside a git work tree, which is exactly what
a `git archive` export is:

- the **dirty-tree refusal** (`app_submit_dirty_guard.go`): "no repo, or no
  `git` on PATH: proceed SILENTLY";
- the **build provenance stamp** (#411): "no repo / no git / bare repo / inside
  `.git` → `Provenance{}` (send nothing)", so `civitai app status` shows an empty
  `SOURCE` column for that release and nobody can later ask which commit is live.

So: submit from a clean checkout that IS a repo — a worktree created off
`origin/<default>` is clean by construction and stamps the right sha.
