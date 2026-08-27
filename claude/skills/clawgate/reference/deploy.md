# clawgate — build + deploy a new version

Read when: you are actually **building and shipping a clawgate version** (Go code, embedded kubeclaw
chart, CSS, manifest pin), or debugging a local e2e failure. Routine ops (status / send a test /
logs) don't need this file.

## 🔴 "Committing IS deploying" is true of the MANIFEST, not of container CODE — and the difference is silent
`deployment.yaml` pins an **immutable literal tag** (`clawgate:<ver>`) and there is **no Flux image
automation** in `clusters/workbench/apps/clawgate/` (MEASURED 2026-08-02: no `ImageRepository`, no
`ImagePolicy`). So a commit that changes `containers/clawgate/**` — Go code, the embedded kubeclaw
chart, the extension — lands on `trunk`, reconciles cleanly, and **changes nothing that is running**,
because the cluster still pulls the old pinned tag. Only step 3's pin bump deploys.

Ground case: the vendored kubeclaw chart re-sync (0.7.0 → 0.7.1, PR #274) merged to `trunk` and sat
there inert — the chart is `//go:embed`ded into the binary (`internal/agents/embed.go`), so it could
not reach the cluster until 0.7.82 was built and pushed. **`git log` on `trunk` is NOT evidence a
code change is live; the live pin and `clawgatectl health` are.**

## 🔴 ONE commit path: a dedicated git WORKTREE off `origin/trunk`, ending on `trunk` (= live deploy)
homelab-talos is GitOps from `trunk` — **committing deploys the manifest** (see above). Do **NOT**
commit from the main checkout: it is permanently dirty with unrelated uncommitted files
(`.sops.yaml`, secrets, WIP), so the old `git pull --rebase` stash/autostash dance autostashes that
tree and conflicts on files you never touched (this corrupted `.sops.yaml` once, 2026-06-24; on
2026-07-30 the same pattern left the base clone 262 commits behind with three stale-file conflicts).
A clean worktree rebases only your staged paths. **NEVER `git add -A`** — stage explicit clawgate
paths.

## The runbook

```bash
VER=0.7.83   # 🔴 FETCH trunk + check the LIVE deployment pin FIRST — Zach ships concurrently and a
             # mutable-tag clobber bit once (memory clawgate-version-before-build). This fired FOR
             # REAL 2026-07-30: 0.7.77 landed from Zach's session WHILE 0.7.78 was being built here.
             # Derive the number from the LIVE pin, NEVER from this file or HANDOFF. (Last known
             # shipped: 0.7.82, built + deployed 2026-08-02 — assume stale, verify with the status
             # snippet in SKILL.md.)

# 0. fresh worktree off the latest trunk (clean tree — only YOUR changes live here)
cd /home/zach/workspace/homelab-talos && git fetch origin trunk
git worktree add /home/zach/workspace/homelab-trunk -B clawgate-$VER origin/trunk
cd /home/zach/workspace/homelab-trunk/containers/clawgate
#  ... make your code changes here, in the worktree ...

# 1. test (Go + hook bats + e2e) — must be green. A worktree-local `go test` needs app.css built
#    FIRST (it's gitignored; a missing one 404s BOTH TestStaticAssetsServed and TestOpenRoutesNoAuth).
#    🔴 BUILD IT FROM INSIDE containers/clawgate/ — see the CSS-cwd trap below. Correct form:
nix-shell -p tailwindcss --run "cd /home/zach/workspace/homelab-trunk/containers/clawgate && tailwindcss -i ./web/css/input.css -o ./web/static/app.css --minify"
wc -c web/static/app.css   # sanity: ~36 KB. ~5 KB = the cwd trap fired. Or: grep -c '\.h-14' web/static/app.css
nix-shell -p go --run 'go build ./... && go vet ./... && go test -race -cover ./...'
nix-shell -p bats jq --run 'bats hook/tests/clawgate-hook.bats'
make e2e   # 83 pass / 2 skip; a fresh worktree npm-installs e2e/node_modules first
#          # ⚠ if `make e2e` is KILLED (timeout/Ctrl-C) the fixture's teardown doesn't run →
#          # a `clawgate-e2e-pg-*` postgres container LEAKS. They pile up and starve the box
#          # (FAB tests flake under load). Clean: docker rm -f $(docker ps -aq --filter name=clawgate-e2e-pg)

# 2. build (Dockerfile builds Tailwind CSS then the static Go binary, embeds assets) + smoke + push
docker build --build-arg VERSION=$VER -t harbor.homelab.lan/library/clawgate:$VER -t harbor.homelab.lan/library/clawgate:latest .
docker run -d --name cg-smoke -p 8219:8104 harbor.homelab.lan/library/clawgate:$VER && sleep 3   # CLAWGATE_INSECURE_COOKIES is dead — no Go code reads it
clawgatectl --api-url http://localhost:8219 health; docker rm -f cg-smoke   # rc 6 = never came up
docker push harbor.homelab.lan/library/clawgate:$VER && docker push harbor.homelab.lan/library/clawgate:latest

# 3. bump the pin, stage explicit paths, commit, rebase (clean tree → no autostash), push
cd /home/zach/workspace/homelab-trunk
# 🔴 BUMP BOTH. The version lives in TWO files and `version_pin_test.go` asserts
# they are equal, so moving the pin alone turns `go test ./...` RED — which is
# `tekton/clawgate-ci FAILED: go` on EVERY PR that touches containers/clawgate,
# not just yours. It has shipped twice: #408 ("the deploy pin moved without it")
# and again at 0.8.2, where the fix had to land as its own PR (#427, "trunk was
# red for every PR"). The tell you will see FIRST is clawgatectl printing
# `note: server <new>, clawgatectl built for <old>` on every command — if you
# see that after a deploy, this step is what you missed.
sed -i "s#clawgate:[0-9.]\+#clawgate:$VER#" clusters/workbench/apps/clawgate/deployment.yaml
sed -i "s#^var buildVersion = \".*\"#var buildVersion = \"$VER\"#" containers/clawgate/cmd/clawgatectl/client.go
# CONFIRM both moved before committing — a sed that matched nothing exits 0.
grep -m1 -oE "clawgate:[0-9.]+" clusters/workbench/apps/clawgate/deployment.yaml
grep -nE '^var buildVersion' containers/clawgate/cmd/clawgatectl/client.go
# The guard that fails the whole fleet if these drift. ⚠ COUNT the result line —
# a `-run` filter that matches nothing prints "ok ... [no tests to run]" and
# exits 0, so a typo'd test name reads exactly like a pass.
go test . -run TestDeployPinMatchesClientBuildVersion -v | grep -E '^(=== RUN|--- (PASS|FAIL)|ok|FAIL)'
git add <your changed clawgate paths> clusters/workbench/apps/clawgate/deployment.yaml \
        containers/clawgate/cmd/clawgatectl/client.go containers/clawgate/HANDOFF.md
git commit -m "..." -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
git fetch origin trunk && git rebase origin/trunk && git push origin HEAD:trunk

# 4. reconcile + verify
KC=/home/zach/workspace/homelab-infra/workbench-kubeconfig   # PER-HOST — see SKILL.md Key facts
flux --kubeconfig $KC reconcile kustomization clawgate --with-source
until kubectl --kubeconfig $KC -n clawgate get pod -l app=clawgate -o jsonpath='{.items[0].spec.containers[0].image}' | grep -q "$VER"; do sleep 4; done
clawgatectl health   # confirm the live version (+ a stderr skew note if it differs)

# 5. clean up the worktree (force: removes gitignored build artifacts too)
cd /home/zach/workspace/homelab-talos
git worktree remove /home/zach/workspace/homelab-trunk --force && git branch -D clawgate-$VER

# 6. re-sync the base clone — it is write-only and silently falls behind
git -C /home/zach/workspace/homelab-talos fetch origin && git -C /home/zach/workspace/homelab-talos merge --ff-only origin/trunk
```

## 🔴 THE CSS-cwd TRAP (cost hours on 2026-07-30 — read before debugging any e2e failure)
Build `app.css` **from inside `containers/clawgate/`**. Running
`tailwindcss -i ./web/css/input.css -o ./web/static/app.css --minify` from any other cwd makes the
Tailwind config's **relative content globs resolve against the wrong tree**, so it finds no templates
and silently emits a **~5 KB** stylesheet with **zero utility classes** instead of ~36 KB.
Downstream: the FAB renders zero-sized → Playwright reports it `hidden` → **~25 e2e tests fail in a
way that looks exactly like a code regression.** ⚠ **`TestStaticAssetsServed` does NOT catch this** —
it asserts app.css is *served*, not that it contains anything. **The Dockerfile builds its own CSS,
so the IMAGE is never affected** — this only ever breaks the local gate. Always
`wc -c web/static/app.css` (~36 KB) or `grep -c '\.h-14' web/static/app.css` after building.

## 🔴 When an e2e run fails, run the PRISTINE-TRUNK baseline BEFORE theorising
A throwaway worktree at `origin/trunk` running the same spec is the one control that separates "my
change broke it" from "the box/env is broken". On 2026-07-30 it settled the CSS trap in a single run,
after several rounds of a plausible-but-wrong host-load hypothesis.

## Deploy mechanics (as last run)
The **docker build runs ON the workbench** via `DOCKER_HOST=ssh://zach@192.168.50.250`, invoked from
a local worktree off `origin/trunk` — no local Docker daemon needed, and the image lands next to
harbor. In the deploy gate **`make e2e` is SKIPPED** (the killed-e2e `clawgate-e2e-pg-*` container
leak starves the box) in favour of the **PG-gated unit tests against a throwaway
`postgres:16-alpine`** — same DB coverage without the leak risk.

## The vendored kubeclaw chart is embedded at BUILD time
A kubeclaw release doesn't reach clawgate-provisioned agents until `make sync-chart` + rebuild +
redeploy clawgate.

🔴 **`make check-chart` is only meaningful if `~/workspace/kubeclaw` is CURRENT — sync it FIRST or it
silently tests the wrong tree.** `check-chart` depends on `sync-chart`, which **rsyncs from the local
`~/workspace/kubeclaw` clone** — a normal checkout, stale like any other. A stale clone makes
`check-chart` **clobber the deployed chart with an older one and then report a false failure** — the
"drift" it finds is drift it just created (measured: clone at **0.3.14** vs vendored **0.7.1**).
Always, before either target:

```bash
git -C ~/workspace/kubeclaw fetch origin && git -C ~/workspace/kubeclaw merge --ff-only origin/trunk
```

`--ff-only` is the point: it cannot autostash, and a refusal is the signal that the clone diverged.

## Verifying UI live is SIMPLE — the LAN UI is OPEN (no auth since 0.7.37)
Drive a standalone Playwright (or curl) directly at the LAN pod `http://192.168.50.250:30302` — no
cookie, no auth, no session injection. Use standalone Playwright, not the shared Playwright MCP
browser, which is usually locked (`Browser is already in use … use --isolated`). To borrow Playwright
deps in a fresh worktree, symlink `e2e/node_modules` to the main checkout's.
