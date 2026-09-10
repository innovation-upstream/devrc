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

The builder selects its install command from `buildCommand`'s **first word**:

```sh
pnpm)  [ -f pnpm-lock.yaml ]    || fail  # "commit your lockfile"
       corepack enable; pnpm install --frozen-lockfile --ignore-scripts ;;
yarn)  corepack enable; yarn … ;;
*)     [ -f package-lock.json ] || fail
       npm ci --ignore-scripts ;;
```

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

🔴 Export with `git archive`, not from a worktree: a worktree's `.git` is a
**file**, and only `.git` *directories* are dropped.
