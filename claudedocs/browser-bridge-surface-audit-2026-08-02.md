# browser-bridge surface audit — 2026-08-02

**Scope:** static audit of the agent-facing surface (MV3 extension, `browser` CLI,
`server.py`, `SKILL.md` + `reference/`, `opencode/` tool surface) for surface
consistency, error-message quality, token efficiency, and test-coverage gaps.

## Which tree I read

🔴 The base clone is on `feat/annotated-frame-support`, not `main`.

| file | source read | note |
|---|---|---|
| `server.py`, `browser`, `browser-agent`, `manifest.json` | **`origin/main`** | byte-identical to the worktree (`wc -c` matched) → line numbers valid in both |
| `extension/protocol.js`, `extension/service_worker.js` | **`origin/main`** (extracted to scratch) | worktree DIFFERS (WIP). All cited line numbers are `origin/main` line numbers. The op sets are identical between the two (`diff` of the `ALLOWED_OPS` block and the `OPS` keys — only line offsets moved). |
| `SKILL.md` | **`origin/main`** (12,007 B) | worktree is 11,996 B (another session's WIP) |
| `tests/*` | **worktree** | the suites I ran were the worktree's; counts below are worktree counts |

Live probes run (read-only, permitted): `browser whoami`, `browser --instance personal ping`.
Host resolved as **laptop** (`192.168.50.155`), 2 instances connected (`personal`, `work`),
extension `0.7.0`, `extension_stale: false` on both.

Suite counts (worktree, **counted from the reporter lines, not exit codes**):
- `node --test tests/*.test.mjs` → `tests 460 / pass 460 / fail 0 / skipped 0 / todo 0`, 6.9 s
- `pytest tests/` (under `nix-shell -p python3Packages.pytest python3Packages.requests`) → `369 passed`, 0 failed, 0 skipped, 100.6 s

---

# 1. The op matrix

Seven sources, not three — the extra ones are where the interesting gaps are.

| source | where | count |
|---|---|---|
| **EXT-IMPL** | `extension/service_worker.js:619` `OPS` object keys | 18 |
| **EXT-CONTRACT** | `extension/protocol.js:47` `ALLOWED_OPS` | 18 |
| **SRV** | `server.py:145` `ALLOWED_OPS` + `:152` `SERVER_OPS` | 18 + 1 |
| **CLI** | `browser:355` `SUBCOMMANDS` | 24 names |
| **SKILL** | `SKILL.md:56-78` ops table | 21 rows covering all 24 names |
| **AGENT** | `opencode/tools/browser_tool_impl.mjs:60` `OP_TO_SERVER` / `:91` `ALLOWED_OPS_DEFAULT` | 12 / 11 |
| **LIVE** | `browser --instance personal ping` → `.result.data.ops` (MEASURED today) | 18 |

## Matrix

`✔` = present · `—` = absent · `n/a` = not that layer's concept.

| wire op | EXT-IMPL | EXT-CONTRACT | SRV | LIVE | CLI name | SKILL | AGENT | verdict |
|---|---|---|---|---|---|---|---|---|
| `getHtml` | ✔ 620 | ✔ | ✔ | ✔ | `html` | ✔ :67 | ✔ (`html`) | **consistent** |
| `text` | ✔ 675 | ✔ | ✔ | ✔ | `text` | ✔ :66 | ✔ | **consistent** |
| `eval` | ✔ 806 | ✔ | ✔ | ✔ | `js`, `eval` | ✔ :68 | ✔ | **consistent** |
| `tabs` | ✔ 894 | ✔ | ✔ | ✔ | `tabs` | ✔ :64 | — | **deliberately-unreachable** (agent) — cited: `browser_tool_impl.mjs:687` "`tabs` would leak other tabs' URLs" |
| `nav` | ✔ 929 | ✔ | ✔ | ✔ | `nav` | ✔ :65 | ✔ | **consistent** |
| `screenshot` | ✔ 978 | ✔ | ✔ | ✔ | `screenshot` | ✔ :69 | ✔ | **consistent** |
| `open` | ✔ 1157 | ✔ | ✔ | ✔ | `open` | ✔ :62 | — | **deliberately-unreachable** — agent's tab is env-forced (`browser_tool_impl.mjs:175-184`) |
| `close` | ✔ 1182 | ✔ | ✔ | ✔ | `close` | ✔ :63 | — | **deliberately-unreachable** — same rationale |
| `frames` | ✔ 1015 | ✔ | ✔ | ✔ | `frames` | ✔ :70 | ✔ | **consistent** |
| `click` | ✔ 1050 | ✔ | ✔ | ✔ | `click` | ✔ :71 | ✔ | **consistent** |
| `type` | ✔ 1093 | ✔ | ✔ | ✔ | `type` | ✔ :72 | ✔ | **consistent** |
| `key` | ✔ 1125 | ✔ | ✔ | ✔ | `key` | ✔ :73 | ✔ | **consistent** |
| `wake` | ✔ 1306 | ✔ | ✔ | ✔ | `wake` | ✔ :75 | ✔ | **consistent** |
| `activate` | ✔ 1325 | ✔ | ✔ | ✔ | `activate` | ✔ :76 | — | **deliberately-unreachable** — the strongest exclusion in the codebase, `browser_tool_impl.mjs:44-53` ("the autonomous model can NEVER reach it, not even via `BROWSER_AGENT_ALLOWED_OPS`… Telemetry caught a driving session calling it 1–5×/minute") + `:765-767` |
| `upload` | ✔ 1029 | ✔ | ✔ | ✔ | `upload` | ✔ :74 | opt-in only | **deliberately-gated** — in `OP_TO_SERVER` but not `ALLOWED_OPS_DEFAULT`; rationale `browser_tool_impl.mjs:74-89` |
| `ping` | ✔ 1361 | ✔ | ✔ | ✔ | `ping` | ✔ :60 | — | **undocumented exclusion** (agent) — no rationale comment; see F9 |
| `emulate` | ✔ 1208 | ✔ | ✔ | ✔ | `emulate` | ✔ :77 | — | **undocumented exclusion** (agent) — no rationale comment; see F9 |
| `context` | ✔ 1369 | ✔ | ✔ | ✔ | `context` | ✔ :61 | — | **undocumented exclusion** (agent) — the notable one; see **F9** |
| `release` (SERVER_OPS) | n/a | n/a | ✔ `:152` | n/a | `release` | ✔ :63 | — | **consistent** — server-side only by design (`server.py:149-151`) |
| `health` | n/a | n/a | `GET /health` | n/a | `health` | ✔ :59 | — | **consistent** (HTTP endpoint, not an op) |
| `whoami` | n/a | n/a | `GET /whoami` (`server.py:1833`) | n/a | `whoami` | ✔ :58 | ✔ | **consistent** — agent short-circuits to the GET before any `/cmd` body (`browser_tool_impl.mjs:221-233`), with the `activeTabDomain` cross-profile leak stripped (`:680-695`) |
| `instances` | n/a | n/a | `GET /instances` | n/a | `instances` | ✔ :59 | — | **consistent** |
| `agent` | n/a | n/a | n/a | n/a | `agent` | ✔ :78 | n/a | **consistent** — `exec`s `browser-agent` (`browser:1292`) |

## Verdict

**Zero dead ops. Zero undocumented ops. The wire contract is fully consistent
across EXT-IMPL / EXT-CONTRACT / SRV / LIVE / CLI / SKILL.**

Two things the task flagged as open are **already closed on `main`** and should not
be re-fixed:

- **`context` dead on `main`** — CLOSED. `server.py:145-147` includes it;
  `tests/test_server.py:3751` `test_ping_op_set_mirrors_the_extension_protocol_js`
  now parses `protocol.js` and asserts set-equality with `S.ALLOWED_OPS`, so the
  regression class is structurally guarded; and the LIVE `ping` on this host
  returned all 18 ops including `context` (MEASURED today).
- **`unknown_op` reading as a contradiction with `health`** — CLOSED.
  `browser:655-656` maps a dispatched-op `unknown_op` to: *"your loaded
  browser-bridge extension is OLDER than this CLI. Reload it in
  brave://extensions. If the reload doesn't take, FULLY RESTART Brave (the
  extension's long-poll keeps the old service worker alive, so ↻ is unreliable)."*

The remaining surface risk is one layer **out** from where the guards are — see G1/G2.

---

# 2. Findings

Ranked by measured cost. Token conversion throughout uses **bytes ÷ 4** (the
standard English/JSON heuristic); I state bytes as the measured quantity and
tokens as the derived estimate.

### F1 — `health` in the quick start costs ~470-540 tokens on **every** browser task · MEASURED

`SKILL.md:10-11` instructs both `whoami` and `health` as the orientation step.
Measured live today on this host, 2 instances connected:

| call | bytes of output |
|---|---|
| `browser whoami` | 1,224 |
| `browser health` | **1,553** |

`whoami` already carries everything orientation needs: host label, connected
count, per-instance `key`/`label`/`extension_version`/`extension_id`/
**`extension_stale`**, and bridge diagnostics. `health`'s *unique* content is the
full `activeTab` URL+title and `known_instances` liveness ages — **triage**
material, and `SKILL.md:98` already routes triage to `health`.

Cost: **1,553 B ≈ 388 tokens** of output plus one Bash round-trip envelope
(command echo + result framing; INFERRED at 80-150 tokens) ≈ **~470-540 tokens per
browser task**, buying nothing the previous call did not already answer.

**Secondary — a privacy regression the design elsewhere works hard to avoid.**
`health` printed the *full* active-tab URL of *every* connected profile into my
transcript, including a Discord channel URL and a `claude.ai` settings URL.
`whoami` deliberately reports `activeTabDomain` only, and
`browser_tool_impl.mjs:680-691` documents at length why cross-profile URL exposure
is the leak to close (*"a `banking` profile sitting on chase.com while the agent
runs on `work`"*). Instructing a reflexive `health` on every task re-opens exactly
that, one layer up.

### F2 — the 626-byte frontmatter description is paid in **every session on the host**, browser task or not · MEASURED

`SKILL.md:3`. **Measured in my own context window this session:** the full
626-byte description appears verbatim in the available-skills system-reminder of
*this* session, which is not a browser task. It is therefore a per-**session**
cost across all repos, not a per-browser-task cost — and it is *also* counted
against the 12,288-byte gate (`test_skill_size.py:52` reads the whole file).

Roughly 340 B of it is connective prose and an ops list (*"read the active tab's
HTML, run JS in it, list/navigate tabs, and screenshot the visible tab"*,
*"(loopback rendezvous server + MV3 extension)"*) that adds no trigger signal the
"Use when…" clause does not already carry.

### F3 — `browser --help` emits 25,440 bytes ≈ 6,360 tokens · MEASURED

`browser:348` implements help as `grep -E '^#( |$)' "$0" | sed -E 's/^# ?//'` —
i.e. it dumps the *entire* 325-line header comment.

    grep -E '^#( |$)' browser | sed -E 's/^# ?//' | wc -c  →  25440

That is **2.1× the entire SKILL.md** (12,007 B). Running `--help` is the natural
reflex when an op errors, and doing so more than triples the skill's cost in a
single call. The header is excellent *reference* material — it is simply the
wrong default response to "what are the flags again?".

### F4 — `HIDDEN_TAB_NOTE` is emitted **twice** per hidden read · MEASURED

`HIDDEN_TAB_NOTE` (`protocol.js:1322-1327`) is 324 bytes. It lands:

1. **in-band** as `data.note` — `protocol.js:1339` (`annotateVisibility`) — 342 B
   as jq-pretty-printed output including key and indent;
2. **on stderr** as `browser: <note>\n` — `browser:481-484` (`_hidden_warn`) — 334 B.

Total **676 B ≈ 169 tokens per hidden read**. `open` creates tabs in the
background (`SKILL.md:62`), so hidden is the *default* state for every multi-step
task — this is not an edge case.

The note exists for a good reason (`protocol.js:1315-1320` — the wording is
LOAD-BEARING; the old wording taught `activate` and telemetry caught 1-5 calls a
minute). The redundancy, not the note, is the finding.

### F5 — the 504 timeout message points at the one remedy the subsystem is designed to eliminate · MEASURED

`browser:578`:

```
504) echo "$resp" >&2; die "timeout waiting for the extension to answer (is Brave focused / responsive?)" ;;
```

Three problems:

1. **"is Brave focused"** is misleading guidance in a subsystem whose entire design
   premise is that ops work on background/occluded tabs *without* focus
   (`SKILL.md:69` — screenshot "works on a BACKGROUND/occluded tab"; `SKILL.md:75` —
   wake un-throttles "with NO focus movement"). The reflex this sentence installs
   is `activate`, which `protocol.js:1315-1320` explicitly forbids installing.
2. It is **indistinguishable between two root causes that need opposite actions**:
   a wedged/asleep service worker (fix: `ping`, then a FULL Brave restart — ↻ is
   unreliable) versus a genuinely slow op (fix: retry, or `--wake` on a throttled
   tab).
3. It does not report the timeout value (default 20 s, `server.py:72`
   `BROWSER_BRIDGE_CMD_TIMEOUT`), so the operator cannot tell whether they hit a
   fast failure or waited out the full budget.

Suggested wording (needs the operator's sign-off): *"no answer from the extension
within 20s. First: `browser ping` — no answer or `unknown_op` → the service worker
is stale/wedged, FULLY RESTART Brave (↻ is unreliable). If ping answers, the op
itself was slow: retry, or add `--wake` if the tab is throttled."*

### F6 — two error messages point at a SKILL.md section that does not exist · MEASURED

`browser:588`: *"…back off and retry (a bare high-rate eval loop is throttled —
see **SKILL.md Concurrency**)"*, and the comment at `browser:582` ("see SKILL.md →
Concurrency").

`SKILL.md` on `origin/main` has **7** `##` headings and none is named Concurrency
(`grep -c "Concurrency" SKILL.md` → **0**). The content the message wants is the
unheaded **"Concurrent drivers"** paragraph at `SKILL.md:119-121`, inside
`## This is the user's LIVE session`. A dangling pointer in a user-facing error
message.

### F7 — the eviction playbook lists 8 of the 11 reference topics · MEASURED

`tests/test_skill_size.py:41-43` tells a maintainer the *existing topics* are
`agent, css-hit-test, errors, frames-cdp, security-ops, spa-wake, tabs-instances,
x-fallback`. `git ls-tree origin/main reference/` shows **11**: those plus
**`emulation`**, **`read-envelopes`**, **`auth-pages`**. A maintainer following
the playbook would create a duplicate topic for content that already has a home.

(Positive: all 11 files referenced in `SKILL.md` exist, and all 11 files that exist
are referenced — verified by set comparison. No orphans, no dangling pointers.)

### F8 — a comment contradicted by the constant beside it · MEASURED

`tests/test_skill_size.py:76-79` states the headroom test's purpose: *"'You are one
word from breaking it' becomes a signal instead of a surprise."* But
`MIN_HEADROOM_BYTES = 100` (`:30`) is **smaller than the smallest realistic unit of
edit**. Measured on `origin/main`'s SKILL.md:

| table | rows | total bytes | mean | min | max |
|---|---|---|---|---|---|
| Ops (`:57-78`) | 21 | 3,995 | **190** | 48 | 473 |
| Reference (`:129-140`) | 11 | 1,823 | **166** | — | — |

A 100-byte floor cannot warn you that you are one row from the ceiling, because one
row is ~1.7-1.9× the floor. In practice the headroom test and the ceiling test fire
at the same moment — which is precisely the "re-breached three times in one day"
pattern the docstring at `:6-8` describes. See the ceiling recommendation in §3.

### F9 — `context`, `ping` and `emulate` are unreachable by the autonomous agent with **no rationale**, and the parity guard makes the gap invisible · MEASURED

`opencode/tools/browser_tool_impl.mjs:60-73` (`OP_TO_SERVER`) maps 12 ops. The
allowlist gate is `if (!allowed.includes(op) || !Object.hasOwn(OP_TO_SERVER, op))`
(`:215-217`), so an op absent from `OP_TO_SERVER` is unreachable **even via an
explicit `BROWSER_AGENT_ALLOWED_OPS` opt-in**.

Every other exclusion in this file carries an explicit, careful rationale
paragraph — `activate` (`:44-53`), `upload` (`:74-89`), `tabs` (`:687`), `open`/
`close` (`:175-184`). **`context`, `ping` and `emulate` carry none.**

`context` is the one that matters. `SKILL.md:61` calls it *"page metadata, no DOM
read… **Cheapest read**"* — precisely what a cheap, token-constrained model wants
before deciding whether to spend a `text`. It is also the newest op, and the
omission pattern is exactly what propagation lag looks like.

**Why it is invisible:** the four-source parity test
(`tests/browser_tool.test.mjs:846-895`) pins `browser.js`'s typed enum, the
agent-md capability table, the README's published contract, and
`ALLOWED_OPS_DEFAULT` **to each other** — and to nothing upstream. All four are
consistently missing `context`, so the guard reports green and the gap is
self-consistent. See V3.

**Verdict: undocumented, cannot be settled statically.** Whether `context` *should*
be reachable is the operator's call. Whether the file's own convention (every
exclusion gets a written rationale) is being met is not — it is not.

### F10 — the 503 message is a two-hop pointer · MEASURED, minor

`browser:576`: *"no extension connected — load the browser-bridge extension in
Brave (see SKILL.md)"*. SKILL.md contains no extension-loading instructions; they
live in `reference/security-ops.md`, which `SKILL.md:139` routes to for "first-time
setup or a second profile". Naming that file directly saves the reader a hop.

### F11 — one constant's wording contract is split across two test files, pointing opposite ways · MEASURED, test hygiene

`tests/hidden_tab.test.mjs:81` asserts `assert.match(out.note, /browser activate/)`
— i.e. it **pins the presence** of the string the design wants de-emphasized, and
would block the cleanest form of the F4 fix. `tests/wake.test.mjs:137` asserts the
complementary `!/run 'browser activate'/`. Neither file mentions the other. A
maintainer touching `HIDDEN_TAB_NOTE` will find one and not the other.

---

# 3. Token-efficiency proposals

Estimation method for all rows: **bytes ÷ 4 → tokens**. Bytes are MEASURED
(`wc -c` on real output or on the file); token figures are derived estimates and
labelled as such. Round-trip overhead (tool-call echo + result framing) is
INFERRED at 80-150 tokens and never counted as measured.

| # | proposal | saving | when paid | risk |
|---|---|---|---|---|
| **P1** | Drop `$BB health` from the quick start (`SKILL.md:11`) | **1,553 B ≈ 388 tok** + 1 round trip (~80-150 tok) ≈ **470-540 tok**; frees ~85 B of gate | **per browser task** | LOW |
| **P2** | Trim the frontmatter description (`SKILL.md:3`) 626 B → ~280 B | **~346 B ≈ 87 tok**; frees **346 B** of gate (1.9× today's entire 181 B margin) | **per session, all repos** | MED-LOW |
| **P3** | Emit `HIDDEN_TAB_NOTE` once: keep `data.note` in-band, shorten the stderr line (`browser:481-484`) to ~95 B | **~239 B ≈ 60 tok per hidden read**; ~5 reads/task ≈ **300 tok/task** | per hidden read | MED |
| **P4** | Default `--help` to a ~40-line synopsis; full header behind `--help-full` (`browser:348`) | **~24,000 B ≈ 6,000 tok** per `--help` | per `--help` call | LOW |
| **P5** | Composite `browser read <url>` (open+nav+wake+text) | ~1 round trip + the `open` envelope ≈ **150-250 tok** | per one-shot read task | MED |
| P6 | *(no change recommended)* drop derived `domain`/`path`/`searchParams` from read envelopes (`protocol.js:115-122`) | ~150 B ≈ 38 tok/read | per read | — |
| P7 | *(opt-in only)* a `--compact` global flag skipping `jq .` | whoami: 1,224 → 928 B = 296 B, but almost all newline/indent → INFERRED 40-70 tok | per call | LOW |

**On P3 — it must not lose its effect.** The note exists because a silent wrong
answer is worse than a verbose one (`protocol.js:1315-1320`). The proposal keeps
the full 324-byte note **in-band and byte-identical** (that is the copy the load-
bearing comment and `wake.test.mjs:134-137` protect) and shortens only the stderr
*duplicate* to a pointer that still names the remedy first:
`browser: tab hidden (throttled) — fix: browser wake, or re-run with --wake; full note in .data.note`.
`activate` is simply absent from the short form, which is a *stronger* de-emphasis
than the current mention. ⚠ `hidden_tab.test.mjs:81` pins `/browser activate/` on
the **in-band** note, which this leaves untouched.

**On P6 — flagged and rejected.** `url` + `domain` + `path` + `searchParams` are
all functions of `url`, so the derived three are ~90 B of redundancy plus ~60 B of
jq key/indent per read. But the derivation genuinely saves the agent a parse step,
and 38 tokens does not justify a wire-contract change plus test churn. No action.

## Recommendation on the byte ceiling itself

**Keep `MAX_BYTES = 12_288`. Raise `MIN_HEADROOM_BYTES` from 100 to 250.**

*Keep the ceiling* — not because 12 KiB is principled (it is not; it is a round
number), but because the forcing function has demonstrably worked. Eleven
well-scoped reference files exist *because* the ceiling forced eviction rather
than accretion, and every one of SKILL.md's 7 remaining sections is decision-time
content by the project's own criterion. Raising the ceiling removes the pressure
that produced that structure, and the ratchet only turns one way. The correct
response to "we are 181 bytes from the ceiling" is P1+P2, which free 431 B at low
risk — not a bigger number.

*Raise the headroom floor* — this is the actual defect (F8). The docstring at
`test_skill_size.py:76-79` promises an early warning; a 100-byte floor is below the
mean table row (190 B ops / 166 B reference, MEASURED), so it fires simultaneously
with the ceiling and delivers a surprise, not a signal. **250 B** is ≥ the mean
reference row and ≥ 1.3 mean ops rows, which makes the warning actually precede the
breach. Sequence matters: land P1+P2 first (181 B margin → 612 B), *then* raise the
floor, so the tree is never red.

## Recommendation on symlinking `reference/`

**No — do not symlink it.**

*The gain is bounded and small.* The only per-token change is deleting
`SKILL.md:125-126`, the two-line explanation of why paths are repo-absolute —
**204 bytes, MEASURED**. Nothing else moves: the table rows already read
`reference/x.md`, and a `Read` call needs an absolute path either way. On path
length the symlink is barely shorter —
`~/.claude/skills/browser/reference/errors.md` (45 ch) vs
`~/workspace/devrc/scripts/browser-bridge/reference/errors.md` (58 ch) — 13
characters, on the 0-1 reference files a typical task opens.

*The cost is a second address for the same content.* Deployed as a `home.file`
copy, the deployed tree drifts from the repo and recreates exactly the "which one
is live?" hazard that `CLAUDE.md`'s `readlink -f` rule exists to resolve — and this
subsystem has already been bitten by precisely that (an agent misjudged the browser
skill as an in-store copy). Deployed as `mkOutOfStoreSymlink` it stays live, but
adds 11 store paths and makes every *new* reference file subject to the flake
`git add` gotcha, which is a silent-omission failure mode.

204 bytes is 59% of what P2 alone yields at lower risk. Spend the effort on P1/P2/P4.

## ⚠ Collision with PR #266

PR #266 is in flight on `SKILL.md`, `reference/spa-wake.md`, `reference/errors.md`.

- **Will collide:** P1 (`SKILL.md:11`), P2 (`SKILL.md:3`) — both edit SKILL.md.
- **Will not collide:** F5, F6, F10, P4 (all in `browser`); F7, F8, S4 (all in
  `tests/test_skill_size.py`); G1/G2/G3 (new test files).

Recommended order: land the CLI and test-file items first; rebase the SKILL.md
items after #266 merges. Per `RULES.md`, `gh pr view 266 --json mergeable,mergeStateStatus`
is the only authority on whether it conflicts — do not infer it from a local merge trial.

---

# 4. Test-coverage gaps and vacuous tests

## Gaps (behaviour with no coverage)

### G1 — nothing binds the CLI's `SUBCOMMANDS` to the op inventory · the highest-value missing test

`browser:355` declares 24 subcommand names. They map to 18 wire ops + 1 server op +
4 HTTP endpoints, **entirely by hand**, across ~20 `case` arms. There is no test
asserting that mapping (verified: `grep -n "SUBCOMMANDS" tests/*.py` → no hits).

Two defects it cannot catch:
- an op added to `server.py` + the extension but never given a CLI name → **invisible
  to every agent**, which is the `context` bug one layer up, at the layer the agent
  actually touches;
- a CLI arm dispatching an op the server no longer allows → a **dead subcommand**
  that fails at runtime with a server 400.

Fix: a pytest that parses `SUBCOMMANDS` from `browser`, subtracts the 4 non-op names
and the `js`/`eval` alias, and asserts the remainder maps onto
`S.ALLOWED_OPS | S.SERVER_OPS` — and, in the other direction, that every member of
that union has a CLI name. The pattern to copy is
`tests/browser_tool.test.mjs:846-895`.

### G2 — nothing binds `SKILL.md`'s op table to `SUBCOMMANDS`

`SKILL.md` is the *only* surface a Claude agent reads. An op that is not in that
table is functionally dead to the agent even when every wire layer is perfect —
the same failure mode as `context`, relocated to the docs. Today the table happens
to cover all 24 names (verified by hand for this audit), but nothing holds it there.

Fix: parse the `` | `op` | `` first column of the `## Ops` table and assert coverage
of `SUBCOMMANDS`.

### G3 — `context` has thin coverage for the op that shipped dead

Total assertions naming `context` across both suites:
`protocol.test.mjs:44` (in the sorted `ALLOWED_OPS` literal), `:589-590`
(`ALLOWED_OPS.includes` + `validateCommand`), and `test_server.py:994` (in the
18-op literal). That is it.

No test asserts `context ∈ TAB_SCOPED_OPS` (`server.py:155-158`), no round trip
through `FakeExtension`, no envelope-field pin on
`{url,domain,path,query,title,tabId}`. The set-equality drift test
(`test_server.py:3751`) does hold the line against re-deletion, which is the
important half — but the op's own behaviour is unpinned.

### Not a gap — `_hidden_warn`, and it is the model to copy

`tests/test_server.py:3515` `test_browser_cli_hidden_tab_note_to_stderr_exit0`
exercises the real CLI against a stub server, **and** `:3531` is an explicit
negative control asserting a *visible* tab prints no warning
(`assert "hidden" not in r.stderr.lower()`). Positive case plus negative control in
adjacent tests. This is exactly right and should be the template for G1-G3.

## Vacuous / expectation-derived tests

### V1 — `hidden_tab.test.mjs:79, 93, 104` · expectation derived from the implementation

```js
assert.equal(out.note, HIDDEN_TAB_NOTE);
```

`HIDDEN_TAB_NOTE` is imported from the module under test (`:14`), so this asserts
"the implementation equals itself".

**Mutation that leaves all three green:** replace the body of `HIDDEN_TAB_NOTE` at
`protocol.js:1322-1327` with `"x"`. Lines 79/93/104 still pass.

**Honest verdict: not vacuous today, but load-bearing elsewhere.** That specific
mutation *is* caught — by `hidden_tab.test.mjs:80-81` and `wake.test.mjs:134-137`,
which pin literal substrings. The hazard is that lines 79/93/104 *read* as the
wording contract while carrying none of it, and lines 80-81 read as redundant
next to 79. Deleting 80-81 as duplication would silently move the entire wording
contract into a different file with no comment saying so. Recommend a one-line
comment on `:79` pointing at `wake.test.mjs:134-137` as the real pin.

### V2 — `test_server.py:989-994` · self-labelled weak signal, no action needed

```python
assert set(S.ALLOWED_OPS) == {"getHtml", …, "context"}
```

**Mutation that leaves it green:** remove `context` from **both** `server.py:145-147`
and this literal — which is exactly the edit a "just make the test pass" fix
produces. The structural guard at `:3751` is what actually holds the contract.

**No action.** The test's own docstring (`:983-988`) already concedes it is *"a
weaker signal than the drift test — consider dropping it if it keeps costing a red
`main`"*. That honesty is the mitigation, and the deliberate speed bump has value.
Cited here only so it is not mistaken for real coverage.

### V3 — `browser_tool.test.mjs:846-895` · a real test with the exact blind spot this audit found

The four-source parity test parses four real files and asserts they agree — it is
genuinely not vacuous. But it pins the four agent-facing sources **to each other
and to nothing upstream**.

**Mutation that leaves it green:** omit any wire op from all four sources
simultaneously. **This is not hypothetical — it is the current state:** `context`,
`ping` and `emulate` are missing from all four, the test is green, and F9 is
therefore invisible to CI.

Fix: add a directional assertion —
`ALLOWED_OPS_DEFAULT ⊆ server ALLOWED_OPS` (already true), plus a
`REVIEWED_EXCLUSIONS` literal listing every wire op deliberately withheld from the
agent, asserted to partition the wire op set exactly. Adding a wire op then forces
a conscious "reachable, or excluded and why?" decision instead of silence.

---

# 5. SAFE vs RISKY

## SAFE

Each row names the test that proves the change AND the negative control proving
that test is not vacuous.

| # | change | proving test | negative control |
|---|---|---|---|
| **S1** | Remove `$BB health` from `SKILL.md:11` (P1) | `test_skill_size.py::test_skill_md_keeps_working_headroom` proves the byte reduction landed | **RUN TODAY, VALIDATED:** a 12,250-byte SKILL.md makes it fail with `assert 38 >= 100` while `test_skill_md_under_hard_ceiling` correctly stays green (12,250 < 12,288). The harness discriminates. Behavioural safety: no test asserts the quick-start block's *content* (`grep SKILL_MD tests/*.py` → only `test_skill_size.py`), `health` remains a live subcommand (`browser:709`) and remains in `SKILL.md:98`'s triage step. |
| **S2** | Fix the `SKILL.md → Concurrency` dangling pointer (`browser:582,:588`) (F6) | **none exists — must be added:** assert every `SKILL.md → <heading>` string in `browser` matches a `^## ` heading in `SKILL.md` | point the new test at `SKILL.md → Nonexistent` and watch it go red **before** certifying |
| **S3** | Update the eviction playbook's topic list (`test_skill_size.py:41-43`) to the real 11 (F7) | **none exists — must be added:** `assert set(topics_in_playbook) == {p.stem for p in reference_dir.glob("*.md")}` | delete one topic from the playbook string and watch the new test go red |
| **S4** | `MIN_HEADROOM_BYTES` 100 → 250 (F8) — **after S1/P2 land** | the gate itself | validated today at 12,250 B (see S1). Additionally: with the current 12,007-byte file a 250 floor would already be red (281 B free → passes; but 12,050 B would fail), so sequence S1/P2 first. |
| **S5** | Add the G1 and G2 parity tests (pure additions, no production change) | the new tests | **required before certifying:** delete one name from `SUBCOMMANDS` (G1) and one row from the SKILL.md ops table (G2) and confirm each new test goes red **with its own assertion message**, not another guard's |
| **S6** | Add the comment on `hidden_tab.test.mjs:79` pointing at `wake.test.mjs:134-137` (V1) | n/a — comment only | n/a |

## RISKY — needs operator sign-off, and live verification on real Brave per `reference/security-ops.md`

| # | change | why risky |
|---|---|---|
| **R1** | Trim the frontmatter description (P2 / F2) | the description **is** the skill's routing signal; over-trimming silently reduces trigger recall and **no suite here can test that**. Verify by confirming the skill still auto-triggers on a browser-shaped request. Keep every distinctive noun; cut only connective prose. |
| **R2** | Shorten the stderr hidden-tab warning (P3 / F4) | touches wording the code calls LOAD-BEARING (`protocol.js:1315-1320`); the failure mode is **silent** — an agent stops reaching for `wake`. Requires updating the wording pins in `wake.test.mjs:134-137` and a live hidden-tab read to confirm the remedy still reads first. |
| **R3** | Split `browser --help` / `--help-full` (P4 / F3) | changes a documented interface, and `test_browser_cli_args.py:387` (`test_help_documents_the_end_of_flags_separator_generally`) would need **retargeting to `--help-full`**. Retargeting a test so a change passes is the move that most needs a second reviewer. |
| **R4** | Rewrite the 504 timeout message (F5) | correct as analysis, but the replacement wording installs a new operator reflex (`ping` → full Brave restart). Wording that installs a reflex is exactly what `protocol.js:1315-1320` says to change deliberately, not incidentally. |
| **R5** | New composite `browser read <url>` (P5) | new wire surface across server + extension + CLI + SKILL, plus a policy decision (always wake?) the current `--wake` deliberately leaves to the caller. Full live-verify gate. |
| **R6** | Add `context` to the agent's `OP_TO_SERVER` (F9) | expands the autonomous model's reach. Cheap and read-only, but this file's whole stance is that reach is granted deliberately with a written rationale — **that is an operator decision, not an audit one.** At minimum, add the missing rationale comment for `context`/`ping`/`emulate` either way (that part is SAFE). |

---

## Claims I could not settle statically

- Whether `context`'s absence from the agent surface is intentional. Settleable only
  by the operator. The code convention (every exclusion carries a rationale) is not
  met either way.
- The per-round-trip transcript overhead (used in P1/P5) is INFERRED at 80-150
  tokens, not measured. Measuring it requires reading a real transcript — which is
  the other agent's scope, deliberately not duplicated here.
- Whether P3's shortened stderr line preserves the behavioural effect. Only a live
  hidden-tab read with an agent in the loop answers that; no static or unit test can.
