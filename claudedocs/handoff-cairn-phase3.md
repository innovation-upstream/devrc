---
clawgate-task: 371
---
# Handoff: cairn-phase3 — 2026-08-28

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
Make the hosted subsystem store the single datastore every host reads **and writes**. Phase 2 (read-through client) shipped earlier. This session shipped clawgate #371's criteria **1–7**: per-token identity, server-side scope authorization, the enumeration property, and the write path. **Criteria 8, 9, 10 remain open** — they are re-seed / cutover / credential-retirement operations, not code.

## State now

- **Branch / PRs: both merged, nothing in flight.**
  - **#915** squash `d60c968c` — criteria 1–3 (per-token identity + scope allowlist + enumeration property).
  - **#948** squash `caec932e` — criteria 4–7 (attributed append, commutative+idempotent, If-Match PUT, guards converted).
  - Both verified **by content, not ancestry** (a squash is never an ancestor of its base): production files byte-identical to their branch tips on `origin/main`.
- ⚠ **PR #907 — the phase-2 handoff doc — is still OPEN and unmerged.** `claudedocs/handoff-cairn-phase2.md` exists only on branch `docs/handoff-cairn-phase2`.
- Worktrees removed, branches deleted, claims `cairn-phase2-1` and `cairn-write-path` released.

**Gates at merge (#948):** both Tekton checks SUCCESS on the exact tip (`devrc-ci-bjjtb`), `mergeState CLEAN`, and an independent merged-tree run at current main: **2305 passed**, clean merge across 35 commits of drift.

**Verified by hand, not relayed from an agent:**
```
ship gate real            LOCK_EX -> LOCK_SH, whole file, NO -k  ->  exactly 4 red
forged actor discarded    write DID land; entry records cairn: zach/<session>; "someone-else" x0
legacy token write        403, identical for an existing and an absent scope
write enumeration         denied-scope write byte-identical to absent-ref write
desync closed             raw socket: 1 response, server sends EOF
deferred-ceiling fix      killed nothing at 92f6650e; 4 red at HEAD
deploy is a no-op         origin/main vs branch, bare legacy token: byte-identical, 12051 bytes
```

🔴 **Nothing has ever been run against the deployed pod.** It still serves image **`0.4.0`**; neither merge changes anything running until an image is built and pinned. **On the live pod every write answers 403** — a bare legacy token has no identity to derive an actor from. That is by design and it makes criterion 10 a hard prerequisite, not an afterthought.

## Open investigations — live diagnosis state

### `ConnectionResetError` in the 8-writer concurrency test
- **Symptom + exact repro:** no reliable repro. `test_EIGHT_concurrent_appends_all_survive` failed once in a local wider-gate run; two of eight writers got `ConnectionResetError` at `elapsed=0.0s`, the other six returned 200.
- **Observed (with values):** the test is now self-diagnosing and reported `MECHANISM = TRANSPORT`. The exception is a bare `ConnectionResetError`, **not** `URLError` — `urllib` wraps only `h.request()`, never `h.getresponse()`, so **the request was sent and the server reset an established connection.** Not connect-time.
- **Ruled out:**
  - **Accept-queue/backlog overflow.** `net.ipv4.tcp_abort_on_overflow = 0` on this host (measured), so overflow **drops SYNs and yields a timeout, never an RST**. The backlog is 5 (socketserver default, not overridden) against 8 writers and it is still not the mechanism.
  - **CPU saturation** — 25/25 green under 20-way load.
  - **Reproduction under load**: 12 runs by hand on a quieter host, 10 body-path runs, 20+ total — 0 resets. **0 across 11 saved run outputs and 0 in CI**, including the green run on the merged tip.
- **Leading hypothesis (LOCATED but UNCONFIRMED):** `_consume_body` returns `False, b""` **without draining the declared body** in five arms — `length > MAX_DRAIN_BYTES`, chunked `Transfer-Encoding`, negative and unparseable `Content-Length`, and the `DRAIN_DEADLINE_S` arm — **each setting `close_connection = True`**. Closing a socket with unread data still queued makes Linux emit **RST rather than FIN**, and the RST also discards the server's send buffer, so a client that received a valid response can still surface `ConnectionResetError`. That produces an RST where a backlog overflow produces a timeout, which is the discriminator.
- **Next probe:** loop the body-path tests and see whether the reset localises there:
  ```bash
  nix develop <repo> --command env PYTHONDONTWRITEBYTECODE=1 python -m pytest \
    scripts/tests/test_subsystem_store_api.py -q -p no:randomly \
    -k "CHUNKED or NoRequestSmuggling or Content_Length"
  ```
  A direct socket probe (2 MiB over-cap body, chunked TE, negative `Content-Length`, plus a control) got a clean `405` on all four at a 250 ms read, so if the window exists it is narrower than that.
- 🔴 **Do NOT add a bounded drain until the fault reproduces.** A fix that cannot be watched to fail is not verified, and it would suppress the only signal.

## Next steps (ranked)

1. **Merge PR #907** (`docs/handoff-cairn-phase2`) — the phase-2 handoff doc is unmerged and lives only on its branch. Cheapest item here; `claude/RULES.md` names an uncommitted doc as unsaved work.
2. **Criterion 10 — retire the shared token** (`devrc`: `scripts/subsystem-store-api/server.py` token file, `~/.config/subsystem-store/env` on both hosts). 🔴 **This now BLOCKS the write path**: a bare legacy token cannot write, so nothing can append until a mapped row exists. The card requires the rollback be exercised *before* the old line is deleted, and the audit log to show no request on the old fingerprint for 24h.
3. **Build and deploy an image carrying #915 + #948.** The pod runs `0.4.0`; both merges are inert until it is pinned. `build-push.sh` needs a version argument and has **no default** — derive the next version from the LIVE pin, not from docs.
4. **Criterion 8 — re-seed.** Store serves **75 entries / 9 scopes** against a union of **19 scopes / 139 entries** (workbench 114, laptop 26, overlap 1). Verify with `comm -23 <host entry list> <cairn ls-entries>` per host — must print zero lines.
5. **Criterion 9 — the cutover.** `subsystem-index` skill writes through `cairn`; local store becomes a read-only cache (`stat -c %a` = 444, an attempted write returns EACCES, *watched* not assumed). 🔴 **A backup must land BEFORE this** — see the read/write allowlist residual below.
6. **Add the `internal-error` alert** in the monitoring config. Without it the dispatch backstop converts a dropped connection into a quiet 500 that only the audit log sees.
7. **`scripts/cairn` has no write verb** — the CLI still only reads. Nothing can drive the write path from the command line.
8. **§5's off-mesh control still unrun** — from a phone on cellular: `curl -si https://store.zacx.dev/api/v1/recall/devrc` (expect 401) and `curl -si https://store.zacx.dev/` (expect 404). The last unmeasured claim about the public route.

## Gotchas / decisions / dead-ends

**Operator rulings this session (all acted on):**
- **PUT does not enforce attribution.** The claim is scoped to POST everywhere it is asserted, with a guard test pinning the limit. PUT is a whole-file replace used for `## Pointers` and the `OPEN:` → `RESOLVED <sha>:` rewrite; per-bullet enforcement would have to diff old against new bullet sets and risks refusing legitimate rewrites.
- **#371 MAY touch `scripts/lib/subsystem_resolver.py`.** #360's non-goal was #360's. This is what let the `visible_scopes` pushdown into `load_index` happen.
- **Entry-kind guard is NARROW.** Refuse `KIND_BROKEN_LINK`, `KIND_OTHER`, `KIND_LINK_TO_OTHER`, `KIND_DIRECTORY`, `KIND_LINK_TO_DIR`. **`KIND_LINK_TO_FILE` and regular files stay accepted** — no behaviour change for any legitimate caller. The broad form (mirroring `_ENTRY_ACTIONS` wholesale) was considered and rejected.
- **A `legacy` bare token may not write** — no identity ⇒ no actor to derive. Fails closed, and is why criterion 10 blocks the write path.

**Design decisions worth not re-litigating:**
- `If-Match` uses the **entry content hash**, not `scope_revision`. No scope in the served copy is a git repo, so `scope_revision` answers `"unknown"` for all of them and an `If-Match` on it would be satisfied forever by that literal string.
- Concurrency is an exclusive `flock` on a **side file** (`.<entry>.md.lock`), because the write is temp-file + `os.replace` — a lock on the entry's own inode is useless across the rename.
- The entry codec is consolidated behind `decode_entry_text`/`encode_entry_text`. **Four sites were deciding it and one was wrong**, which is what made a latin-1 byte in a nuance bullet permanently unappendable.

**Traps that cost real time — do not re-pay them:**
- 🔴 **`session` is a REQUIRED body field on the append route.** Omitting it gives 400, and reading "0 occurrences of the forged actor" off that response is a **false green** — nothing was written. Every write probe needs a positive control proving the bullet landed.
- 🔴 The server **fail-closes without `SUBSYSTEM_STORE_TRUSTED_PROXIES`** (min /24 for v4) and then **requires `CF-Connecting-IP`**. Without both, every response is empty — and two empty responses compare "byte-identical".
- 🔴 Store entries need valid front-matter or they read as **malformed**, which looks like a defect and is not.
- 🔴 A blocking `open()` on a FIFO **wedges a harness silently**. It wedged three agents. Subprocess + wall-clock deadline only.
- 🔴 `grep` here wraps ugrep and honours `.gitignore`; zsh does **not** word-split unquoted parameters (`for x in $LIST` loops once); and `$c:path` hits the **history-modifier trap** — brace it as `${c}:path` or you silently get the wrong ref. All three bit this session.
- An ad-hoc `importlib` loader for `server.py` needs `sys.modules[name] = mod` **before** `exec_module`, else the first `@dataclass` raises `AttributeError: 'NoneType' has no attribute '__dict__'`.
- `RESULT: all good` is a **test fixture's own output** in `gate.sh`; the real verdict is `RESULT: PASS (exit=0)`. A `RESULT:`-matching wait-loop fires ~25 minutes early.

**Residuals shipped deliberately, all documented in-tree:**
`X-Store-Snapshot newest=` cross-scope timing channel · orphan `.cairn-*.tmp` on SIGKILL · **read allowlist == write allowlist** (🔴 a backup must land before criterion 9, or every read token becomes a whole-file-destructive PUT credential) · ceiling window bounded at 0.75–1.25 s · `fcntl.flock` is single-host advisory, holds at `replicas: 1` only · idempotence-by-content-hash silently drops a genuinely new bullet byte-identical to an existing one · `_WRITE_INTERLEAVE` is an inert test seam in production code, unreachable from outside the process.

**On the audit ladder (9 rounds, 11 findings):** six of the eleven were **introduced by a previous round's fix** — including a surrogate crash caused by the fix for the lossy rewrite, and a response desync caused by the backstop added to stop requests vanishing. That is the entire argument for not stopping at the first green. 🔴 A correction on the record: the audit ceiling was twice described as "destroyed"; it was not — the **synchronous** double-emit was always caught, only the **deferred** case was lost.

## How to verify

```bash
# the write path is on main
git -C ~/workspace/devrc show origin/main:scripts/subsystem-store-api/server.py \
  | grep -c 'WRITE_ROUTES\|_append_bullet\|legacy-cannot-write'      # 16

# the ship gate still bites (whole file, NO -k — a narrower filter returns 3 and that is wrong)
#   mutate fcntl.LOCK_EX -> LOCK_SH, then:
nix develop <repo> --command env PYTHONDONTWRITEBYTECODE=1 python -m pytest \
  scripts/tests/test_subsystem_store_api.py -q -p no:randomly          # exactly 4 red

# the suites
nix develop <repo> --command env PYTHONDONTWRITEBYTECODE=1 python -m pytest \
  scripts/tests/test_subsystem_store_api.py -q -p no:randomly          # 602 passed

# the live store (unchanged by these merges — still 0.4.0, still 75 entries)
set -a; . ~/.config/subsystem-store/env; set +a
curl -s -o /dev/null -w '%{http_code}\n' "$SUBSYSTEM_STORE_URL/api/v1/snapshot"   # 401
curl -sI -H "Authorization: Bearer $SUBSYSTEM_STORE_TOKEN" \
     -H 'User-Agent: subsystem-store-client/1' "$SUBSYSTEM_STORE_URL/api/v1/snapshot" | head -1  # 200
```
