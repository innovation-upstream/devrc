---
clawgate-task: 360
---
# Handoff: cairn-phase2 — 2026-08-27

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
Make the hosted subsystem store the single datastore every host reads and writes, instead of retiring it. Phase 2 (the read-through client) is DONE and LIVE. Phase 3 (the write path) is filed as clawgate **#371** and not started.

## State now

- **Branch / PRs:** all merged. devrc **#863** (`7e153811`) phase-2 CLI + snapshot route · **#901** (`117116ea`) Dockerfile file-list rot · **#902** (`277560e2`) header-case fix. homelab-infra `6928e9b9` image pin.
- ⚠ **The devrc base clone is on ANOTHER session's branch** (`docs/handoff-783-decision`), so `git -C ~/workspace/devrc status` will not say `main` and `merge --ff-only` refuses. Left alone deliberately — switching it back is the wrong-branch hazard pointed at whoever is working there. Use worktrees; read with `git show origin/main:<path>`.

**DONE this session:**
- `scripts/cairn` — read-through client, four states (`live` / `cached (stale)` / `scope-empty` / `store-unreachable, no cache`). Renamed from `scripts/store` per the operator's Cairn naming decision (`claudedocs/handoff-cairn.md`, PR #889).
- `GET /api/v1/snapshot` on the store API — gzipped tar, PAX (sub-second mtimes are load-bearing: the reader orders the index by mtime, so a whole-second tar silently reorders every digest).
- A **total path classifier** in `server.py` (`classify_path` + `_ROOT_ACTIONS`/`_ENTRY_ACTIONS` + a raising `action_for`) replacing four rounds of ad-hoc `if` arms.
- Deployed image **`0.4.0`** to homelab; `subsystem-store` kustomization reconciled.
- `~/.config/subsystem-store/env` (mode 600) on **both** hosts.
- clawgate **#371** filed for phase 3; **#360** set `ready_for_review` with per-criterion evidence.

**Deploy/verify status — deployed AND verified, as separate claims:**
```
pod subsystem-store-api-5bcf6cf69-w6l84  image=0.4.0  ready=1
GET /api/v1/snapshot   200, x-store-entries: 75, gzip tar, 76 members   (404 at 0.3.0)
no token               401
cairn recall  live 0 · scope-empty 0 · cached(stale) 0 · unreachable/no-cache 3
```

## Open investigations — live diagnosis state

### §5's off-mesh control has NEVER run
- **Symptom + exact repro:** not a bug — an untested claim. Every auth probe in this work ran from the workbench, which is ON the mesh.
- **Observed (with values):** `curl -s -o /dev/null -w '%{http_code}' https://store.zacx.dev/api/v1/snapshot` → `401` without a token, `200` with. Both from `192.168.50.250`.
- **Ruled out:** nothing. The app-level rejection is proven; the ROUTE is not.
- **Leading hypothesis:** the public route is correct — but route 1 carries no middleware, so a routing mistake is silent and the request that would reveal it is the one that does not come from your own network.
- **Next probe:** from a phone on cellular (or any off-mesh host): `curl -si https://store.zacx.dev/api/v1/recall/devrc` → expect `401`; and `curl -si https://store.zacx.dev/` → expect `404` (no route matches `/`).

### `/api/v1/recall/<scope>` 503s on an Emacs lock file
- **Symptom + exact repro:** open any store entry in Emacs (creates `.#entry.md`, a **dangling symlink** whose name ends `.md`), then `GET /api/v1/recall/<that-scope>`.
- **Observed (with values):** `503 store-unreachable`, body `index entry unreadable: … FileNotFoundError … /widget-cfg/.#thing-alpha.md`. `/api/v1/snapshot` on the same store returns **200** — the snapshot path was fixed, recall was not.
- **Ruled out:** it is not the snapshot classifier (that skips dotfiles inside scopes since `ceaed7c4`).
- **Leading hypothesis:** `load_index` at `scripts/lib/subsystem_resolver.py:1743` uses `scope_dir.glob("*.md")`, and pathlib glob **does** match a leading dot.
- **Next probe:** none needed to diagnose — the fix is a dotfile filter at that line. It was NOT applied because #360's non-goals forbid touching `subsystem_resolver.py`. Decide whether #371 may.

### `scope_label`'s separator is unpinned by 2258 tests
- **Observed:** mutating `subsystem_recall.py:698` `"/, "` → `"/ ,"` **SURVIVED** 357 + 1903 tests across every file importing `subsystem_recall`.
- **Leading hypothesis:** every expectation is derived by calling `rc.search(...)`, so a mutation inside `scope_label` moves both sides of the assertion together.
- **Next probe:** pin one literal expected label string in `test_subsystem_recall.py` and re-run the mutant.

## Next steps (ranked)

1. **Start clawgate #371 phase 3 — the two-token authorization work FIRST** (`devrc`: `scripts/subsystem-store-api/server.py`, `scripts/tests/test_subsystem_store_api.py`). Criterion 3's enumeration property (a refused scope must be byte-identical to a nonexistent one) is far cheaper to build in than to retrofit.
2. **Decide whether #371 may touch `scripts/lib/subsystem_resolver.py`** to fix the recall lock-file 503. #360 forbade it; #371 does not inherit that non-goal automatically.
3. **Run §5's off-mesh control** from a phone on cellular (two curls, above). Cheap, and it is the last unmeasured claim about the public route.
4. **Re-seed the pod** — it still serves the 2026-08-20 snapshot (`entry-files=75`) while the union across both hosts is 19 scopes / 139 entries. Blocked on #371's write path or a manual `seed.sh` run.
5. **Pin `scope_label`** (above) — small, and it closes a hole a mutation sweep already found.

## Gotchas / decisions / dead-ends

- 🔴 **Flux reads `ZacxDev/homelab-infra` @ `trunk`, NOT `homelab-talos`.** Both repos hold byte-identical `clusters/homelab/apps/subsystem-store/` manifests. Bumping the wrong one reconciles cleanly and changes nothing running.
- 🔴 **The Dockerfile enumerates its files TWICE** — `COPY` lines *and* `Dockerfile.dockerignore`, which is an **allowlist** (`**` then `!` unignores). Adding only the COPY still fails with `"…": not found`. `test_the_image_copies_every_module_it_needs` now asserts the transitive import closure against **both**.
- 🔴 **Response headers arrive LOWERCASE in production** (HTTP/2 + Cloudflare). `dict(resp.headers)` discards `email.Message`'s case-insensitivity — that made the truncated-transfer guard inert in prod while passing locally. Return `resp.headers` itself; never build a dict from it (a `{k.lower(): v}` comprehension also flips duplicate-header resolution FIRST→LAST, against `sole_header`'s doctrine).
- **Cloudflare 403s the default `Python-urllib` User-Agent.** Same token, same path: curl default → 200, `Python-urllib/3.12` → 403. Any Python client must set its own UA.
- **`build-push.sh` needs a version argument and has no default** — a `:latest` default is how a mutable tag gets clobbered. Derive the next version from the LIVE pin, not from docs.
- **Decision:** the CLI is `cairn`, not `store` — operator, 2026-08-26. #360's criterion 2 was amended to drop `touch` (a WRITE verb wrongly listed in a read-through phase); it moves to #371.
- **Decision (2026-08-27, three rulings on #371):** design for a team instance now; per-person scope allowlist enforced server-side; every appended bullet records actor + session. 🔴 **The actor must be DERIVED from the authenticated token, never supplied by the client** — a self-asserted actor lets any token-holder attribute a bullet to someone else.
- **Dead end:** rsync-based host→host sync (the original #359). Delivers *presence*, not consistency — one-way, `--ignore-existing`, shared entries still diverge. Superseded.

## How to verify

```bash
# the deployed route (404 at 0.3.0)
set -a; . ~/.config/subsystem-store/env; set +a
curl -s -o /dev/null -w '%{http_code}\n' "$SUBSYSTEM_STORE_URL/api/v1/snapshot"                 # 401
curl -sI -H "Authorization: Bearer $SUBSYSTEM_STORE_TOKEN" \
     -H 'User-Agent: subsystem-store-client/1' "$SUBSYSTEM_STORE_URL/api/v1/snapshot" | head -1  # 200

# the four states — 🔴 capture rc WITHOUT a pipe; `| head` returns head's status, not cairn's
out=$(python3 ~/workspace/devrc/scripts/cairn --cache /tmp/c1 recall --scope devrc 2>&1); echo "rc=$?"
# live 0 · scope-empty 0 · cached(stale) 0 · store-unreachable/no-cache 3

# the suites
cd <a devrc worktree>; PYTHONDONTWRITEBYTECODE=1 python -m pytest \
  scripts/tests/test_cairn_cli.py scripts/tests/test_subsystem_store_api.py -q -p no:randomly
# 359 passed
```
