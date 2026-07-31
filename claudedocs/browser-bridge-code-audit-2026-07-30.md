# browser-bridge code audit — 2026-07-30

Read-only audit of `~/workspace/devrc/scripts/browser-bridge/` for tool gaps,
error-handling gaps, test-coverage gaps, and docs-vs-code drift.

> **Live-verification disclaimer (read first).** Nothing in this report is
> live-verified against real Brave. CI cannot drive a logged-in browser, so every
> finding below is either (a) VERIFIED by running the unit/integration suites and
> reading code, or (b) INFERRED by reading. Each finding is labelled. The
> live-behaviour gate — does the op actually do the right thing in Brave — belongs
> to the operator and is explicitly out of scope here.

---

## 1. Method + actual test-run output

### What I ran

The canonical invocations (per `README.md` / the test layout):

```
node --test scripts/browser-bridge/tests/*.test.mjs
python -m pytest scripts/browser-bridge/tests -q
```

The system `python` on this host has no `pytest`, so pytest was run under
`nix-shell -p python312Packages.pytest python312Packages.requests`.

### Actual output (VERIFIED)

**node (`node --test tests/*.test.mjs`)**

```
ℹ tests 186
ℹ suites 0
ℹ pass 186
ℹ fail 0
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 587.591785
```

**pytest (`python -m pytest tests -q`)**

```
........................................................................ [ 34%]
........................................................................ [ 69%]
................................................................         [100%]
208 passed in 64.52s (0:01:04)
```

**Totals: 394 tests, 394 pass, 0 fail, 0 skipped, 0 todo.** No test was modified.

Note: several pytest tests are `@pytest.mark.skipif(shutil.which("curl") is None)`
(e.g. `tests/test_server.py:2638`). `curl` IS present on this host, so those ran —
on a host without curl the real-CLI end-to-end tests would silently vanish from the
count without the run being marked skipped in aggregate. Worth knowing before
trusting a green run elsewhere.

### What the suites do and do not reach

| layer | file(s) | reached? |
|---|---|---|
| extension pure helpers (`protocol.js`) | `protocol.test.mjs`, `cdp_protocol.test.mjs`, `hidden_tab.test.mjs` | yes, densely |
| extension glue (`service_worker.js`, faked `chrome.*`) | `service_worker.test.mjs`, `frame_oopif.test.mjs`, `frame_eval_cdp.test.mjs`, `upload.test.mjs` | yes |
| server (`server.py`, in-process) | `test_server.py` | yes, densely |
| **bash CLI (`browser`), really executed** | `test_server.py:2635`+ (`BROWSER_BIN`, `subprocess.run`) | **yes — better than expected** |
| agent wrapper (`browser-agent`) | `test_browser_agent.py`, `test_browser_agent_parse.py` | yes (fake opencode + fake browser) |
| agent tool **impl** (`browser_tool_impl.mjs`) | `browser_tool.test.mjs` | yes |
| agent tool **schema** (`opencode/tools/browser.js`) | — | **NO — never imported or asserted** (see F-1) |
| real Brave / real opencode | — | no (by nature) |

---

## 2. Per-op coverage MATRIX

Ops are the `browser` CLI subcommands (`browser:349-568`). "Happy" = a
success-path assertion exists; "Fail" = at least one failure-path assertion
exists. Layers: **C**=bash CLI executed, **S**=server, **X**=extension
(pure/glue), **A**=agent tool impl.

| op | happy | fail | layers | notes / evidence |
|---|---|---|---|---|
| `health` | ✅ | ⚠ partial | S | `test_server.py` `test_health_includes_extension_version_and_current`, `…_null_when_unreported`, `…_no_extension_still_reports_current_version`. **No CLI-layer test**, no non-200 path. |
| `whoami` | ✅ | ✅ | C,S,A | `test_whoami_shape_zero_instances`, `_missing_token_401`, `_wrong_token_401`, `_bad_host_403`, `_git_head_null_still_200`; CLI `test_browser_cli_whoami`; agent `browser_tool.test.mjs:484,498,525,538`. **Best-covered op.** |
| `instances` | ⚠ indirect | ❌ | S | Covered only via registry/`snapshot` assertions; **no CLI test, no non-200 branch, `render_instances` never exercised.** |
| `open` | ✅ | ⚠ | S,X | Ownership recording tested server-side; `browser-agent`'s open→probe→retry tested in `test_browser_agent.py`. No CLI-level test. |
| `close` | ✅ | ✅ | S | `no_owned_tab` asserted in `test_server.py`. No CLI-level assertion of the CLI's own `no_owned_tab` message (`browser:304-306`). |
| `release` | ✅ | ❌ | S | Server-side only (`release_session`). **No failure path, no CLI test.** |
| `activate` | ✅ | ✅ | S,X,A | Strong: `hidden_tab.test.mjs` (clamp, discarded tab, never-completing tab / #189 no-wedge, `windows.update` failure swallowed), `test_server.py` i3 paths incl. timeout. |
| `html` (`getHtml`) | ✅ | ✅ | S,X | `frame_oopif.test.mjs` `--frame html`; `frame_not_found` covered. No CLI-level test. |
| `text` | ✅ | ✅ | C,S,X,A | `test_browser_cli_text_default_maxbytes_no_selector`, `…_selector_and_maxbytes`, `…_rejects_bad_maxbytes` (CLI failure path ✅); `normalizeText` unit-tested. |
| `eval` | ✅ | ✅ | C,S,X,A | `frame_eval_cdp.test.mjs` covers `frame_not_found`, `frame_eval_failed`, `cdp_timeout`. CLI reached via the 429 test (`test_browser_cli_backs_off_on_429` uses `eval`). |
| `tabs` | ✅ | ❌ | S | Server contract only. **No failure path, no CLI test.** |
| `nav` | ✅ | ⚠ | S,A | `missing_field:url` server-side; `nav_scheme_denied` only in the **agent** layer (`browser_tool.test.mjs`). **No CLI test.** See F-6. |
| `screenshot` | ✅ | ⚠ | C,S,X | `test_browser_cli_screenshot_fullpage_flag`; `captureWithRetry` quota/transient/non-transient all unit-tested; CDP-path non-regression asserted. **The CLI's "response missing image data" branch (`browser:471-475`) is untested.** |
| `frames` | ✅ | ✅ | C,S,X | `test_browser_cli_frames`; `ambiguous_frame` asserted in `frame_oopif.test.mjs`. |
| `click` | ✅ | ✅ | C,S,X,A | `test_browser_cli_click_and_frame_flag`, `test_browser_cli_click_requires_selector` (CLI failure ✅), `element_not_found` in `frame_oopif.test.mjs`. |
| `type` | ✅ | ✅ | C,S,X,A | `test_browser_cli_type_with_selector`; `test_type_requires_text_400`; telemetry-never-echoes-text asserted. |
| `key` | ✅ | ✅ | C,S,X,A | `test_browser_cli_key`; `test_key_requires_key_400`; `keyEventParams` unit-tested. |
| `upload` | ✅ | ✅ | C,S,X,A | Strongest failure coverage: `not_a_file_input`, `element_not_found`, `frame_not_found`, own-tab-only, fixed CDP method set (`upload.test.mjs`); CLI `test_browser_cli_upload_rejects_missing_and_nonfile_path`. **But see F-1 — the agent-facing schema is untested.** |
| `agent` | ✅ | ✅ | (wrapper) | `test_browser_agent.py` covers the tool-set gate, timeout process-group kill, tab close on every exit path, schema-parse + one retry. **Never run for real on either host (telemetry).** |

### Ops with ZERO failure-path coverage: **3**

`instances`, `release`, `tabs`.

All three are low-blast-radius read/bookkeeping ops, which is why this is ranked
low — but `instances` is the one a human reads during an incident, and its CLI
rendering path (`render_instances`, `browser:185-201`) has never been executed by
a test.

### Ops with no CLI-layer coverage at all: **8**

`health`, `instances`, `open`, `close`, `release`, `html`, `tabs`, `nav`.

### Documented error shapes — coverage (VERIFIED by grep across `tests/`)

| error | asserted? | where |
|---|---|---|
| `unknown_op` | ✅ | `protocol.test.mjs`, `test_server.py`; **CLI stale-extension mapping** `test_browser_cli_unknown_op_maps_to_stale_extension_message` |
| `frame_not_found` | ✅ | `frame_eval_cdp`, `service_worker`, `frame_oopif`, `upload` |
| `ambiguous_frame` | ✅ | `frame_oopif.test.mjs` |
| `owned_tab_gone` | ✅ | `browser_tool`, `test_browser_agent`, `cdp_protocol`, `protocol`, `test_server` |
| `cdp_attach_refused:<scheme>` | ✅ | `cdp_protocol.test.mjs` |
| `rate_limited` | ✅ | `test_server.py` + **CLI** `test_browser_cli_backs_off_on_429` |
| `queue_full` | ✅ server | `test_server.py` — **CLI branch `browser:292-294` NOT covered** (the 429 CLI test returns `rate_limited` only) |
| `superseded` | ✅ server | `protocol.test.mjs`, `test_server.py` — **CLI branch `browser:307-308` NOT covered** |
| `unknown_instance` | ✅ server | `test_server.py` — **CLI branch `browser:310-315` NOT covered** |
| `no_owned_tab` | ✅ server | `test_server.py` — **CLI branch `browser:304-306` NOT covered** |
| `bad_tab` | ✅ | `test_server.py` |
| `nav_scheme_denied:<scheme>` | ✅ agent only | `browser_tool.test.mjs` — **not a server/CLI concept at all**, see F-6 |
| `unauthorized` | ✅ | `browser_tool.test.mjs`, `test_server.py` — **CLI branch `browser:286` NOT covered** |
| `bad_host` | ✅ | `test_server.py` |
| `ambiguous_instance` | ✅ server | `test_server.py` — **CLI branch `browser:299-303` NOT covered** |
| `extension_not_connected` (503) | ✅ server | `test_server.py` — **CLI branch `browser:284` NOT covered** |
| `timeout` (504) | ✅ server | `test_server.py` — **CLI branch `browser:285` NOT covered** |
| `missing_field:<f>` | ✅ | `protocol.test.mjs`, `test_server.py` |
| `frame_eval_failed` | ✅ | `frame_eval_cdp`, `cdp_protocol` |
| `element_not_found` | ✅ | `frame_oopif`, `service_worker`, `upload` |
| `not_a_file_input` | ✅ | `upload.test.mjs` |
| `cdp_timeout` | ✅ | `frame_eval_cdp`, `cdp_protocol`, `upload` |

**Headline:** every documented error shape is asserted *somewhere*. The
consistent gap is **the CLI's own translation layer**: of the 9 distinct HTTP
error branches in `cmd_op` (`browser:282-343`), only **2** are exercised
(429/`rate_limited` and the `unknown_op` op-level mapping). The other 7 —
including all the human-facing "here's what to DO next" messages — are dead code
from the suite's point of view. That is the single biggest structural test gap,
and it is exactly the layer whose whole purpose is a good error message.

---

## 3. Error-handling gaps in the code

The FAIL-LOUD-AND-SAFE discipline is, on the whole, **well upheld** — noticeably
better than the #190 history would suggest. The `finally`/always-detach and
bounded-wait properties I specifically went looking for are present:

**Verified-good (no action needed, recorded so a future reader doesn't re-litigate):**
- CDP attach/detach is `finally`-wrapped (`extension/service_worker.js:157-186`
  delegating to `withCdpSession`, `extension/protocol.js:498`), plus
  `chrome.debugger.onDetach` clears out-of-band detaches
  (`service_worker.js:143` `cdpAttached`).
- Every CDP call is timeout-bounded (`protocol.js:451-453`,
  `promiseWithTimeout` at `protocol.js:462`) — the #189 no-wedge property has a
  direct regression test (`frame_eval_cdp.test.mjs`, "a HUNG `Runtime.evaluate`
  settles with `cdp_timeout` and STILL detaches").
- `eval --frame` explicitly refuses to return a silent null — a failure to
  execute is `frame_not_found`/`frame_eval_failed`, a genuine `null` is a value
  (`README.md:219-233`, tested).
- Frame listener is removed in a `finally` (`service_worker.js:276-290`).
- `waitForTabLoad` returns at a bound rather than wedging
  (`protocol.js:290`, tested for the never-completing and discarded cases).

**Gaps found:**

**E-1 — `tabVisibilityState` swallows every failure to `null`, and the caller
cannot tell "visible" from "couldn't check".** `service_worker.js:122-132`
returns `null` on any throw. The hidden-tab warning (`browser:243-257`) only
fires on `hidden === true`, so a *failed* visibility probe silently degrades to
"no warning" — i.e. a read of a throttled, unrendered SPA returns a shell DOM
with **no** warning. This is the exact shape of the #190 bug class (a failure
that returns a value), just with a low blast radius. It is *documented* as
best-effort in the comment, which is honest, but the degradation is invisible to
the caller. Suggest a third state (`visibilityState: "unknown"`) so the CLI can
say "could not confirm the tab is visible". **(inferred by reading; low severity)**

**E-2 — the CLI's `op_error` conflates "no error" with "unparseable-but-exit-0".**
`browser:225-236`: the python helper exits 2 on unparseable JSON and prints
nothing when `ok` is not `False`. `cmd_op` handles `rc != 0`
(`browser:324-327`) correctly. But if the server ever returned a 200 whose
`result` is not a dict (e.g. `null`), `op_error` prints nothing and exits 0 → the
CLI reports success and prints the malformed body. Narrow, but it is a
silent-wrong-answer path on a surface whose contract is "never a silent wrong
answer". **(inferred; low)**

**E-3 — `screenshot`'s file-write branch can exit non-zero *after* the op
already succeeded, with no cleanup or reuse path.** `browser:466-479`: if
`dataUrl` is missing, the CLI prints "screenshot response missing image data" and
exits 1 — but the screenshot was already taken and the data URL is discarded, and
the message does not tell the operator what to do (re-run without a path to get
the JSON). Untested branch. **(inferred; low — message quality)**

**E-4 — `render_instances` fails open to silence.** `browser:186-200` exits 0 on
unparseable stdin, so the 409-ambiguous / 404-unknown-instance branches can print
the header line ("multiple instances connected — specify one with `--instance`")
followed by **no instances at all**, which reads as a bug in the bridge rather
than a parse failure. Never exercised by a test. **(inferred; low)**

**E-5 — no `finally` guarantee that `browser-agent`'s owned tab is closed if the
wrapper is `SIGKILL`ed.** `browser-agent` closes the tab on every *handled* exit
path (verified by `test_browser_agent.py`), and uses `setsid` + process-group
kill for its own timeout — good. But an external `kill -9` of the wrapper leaks
an owned tab. The server's `OWNER_TTL_S` (900s, `server.py:187`) *releases*
ownership but explicitly does **not** close the Brave tab (`browser:27-28`), so
the leak is a real, if minor, tab-leak. Documented behaviour, not a bug —
recorded so it is not mistaken for one. **(inferred; informational)**

**E-6 — `server.py` has ~20 `except Exception: pass/return None` sites.** I read
the ones on security-relevant paths (`resolve_host`, `git_short_head`,
`manifest_version`, telemetry emit, i3 foregrounding) and they are all correctly
best-effort diagnostics whose failure must not break an op — each is commented as
such (`server.py:318,329,359,385,402,470,493,537,1317`). **No swallowed exception
found on an op-execution path.** Recording this as a *cleared* concern.

---

## 4. Tool gaps

Judged against what the telemetry says is actually used (eval, nav, text, open,
getHtml, screenshot, tabs, close, frames, activate, click, key, release, upload).

| candidate | verdict | reasoning |
|---|---|---|
| **wait-for-selector / wait-for-settle** | **genuine gap — worth an op** | This is the highest-value missing primitive. Today the only settle mechanism is `activate`'s bounded wait (`protocol.js:251-259`), which requires **stealing the user's focus** — the one intrusive op — to get a load guarantee. Everything else is the agent hand-rolling a poll through repeated `text`/`eval` calls, which is precisely the pattern that generated the 43,740-eval hour. A `browser wait --selector <sel> [--timeout MS]` op, bounded server-side like `activate`, would remove the busiest toil loop *and* remove the main incentive to poll. Strongest recommendation in this section. |
| **scroll** | **worth an op (small)** | Currently an `eval` recipe, and SKILL.md:63-64 has to document a footgun about it: `window.scrollBy(0,1400)` returns `null` with no error and "looks like a broken bridge and isn't", so the operator must wrap it in an IIFE returning `"ok"`. A documented footgun around a one-line recipe is a decent signal the recipe should be an op. Cheap: `scroll` maps to an existing `eval`-shaped path. |
| **computed styles / hit-testing** | **keep as a recipe** | SKILL.md:308-340 documents a real `elementFromPoint` + `getComputedStyle` recipe. It is genuinely open-ended (which points, which properties, what to report) — an op would either be under-powered or grow a mini-DSL. The recipe is adequate. Only fix: the recipe is *long*, so consider promoting it to a named snippet the skill can paste verbatim rather than have the model re-derive. |
| **back / forward** | **minor gap, low priority** | Not in `ALLOWED_OPS` (`server.py:125-127`). `eval 'history.back()'` works and is used rarely per telemetry. Add only if it falls out of a `nav` refactor. |
| **structured extraction (tables/links)** | **keep as a recipe** | `text` + `eval` cover it, and the shapes callers want vary too much to freeze into an op. No evidence of toil in telemetry. |
| **composite `open→activate→read`** | **worth it, but as a CLI convenience, not a server op** | The SKILL's own "driving a throttled SPA" flow (SKILL.md:136, 184, 243) is a fixed 3-4 step dance every agent repeats. Collapsing it into `browser open --activate --read <url>` is pure CLI-side sequencing — no new server op, no new extension code, no new blast radius. Good effort:value. |
| **cookie / storage inspection** | **do NOT add** | This is the highest-sensitivity data on a live-cookie surface, and the agent tool is explicitly designed to be driven by a cheap model on hostile pages. Adding a first-class read of cookies/localStorage would hand a prompt-injected model a clean session-token exfil primitive. The `eval` path is already same-origin-bounded and audited; leave it there and do not make it ergonomic. |
| **downloads** | **do NOT add (for now)** | Symmetric to `upload` but writing to the local filesystem from attacker-influenced content. Given F-1/F-2 below are still open on `upload`, adding the write direction is the wrong order of operations. |

---

## 5. Docs-vs-code drift

Every numeric default in the docs was checked against code. **All of the
frequently-cited defaults are CORRECT** — recording this explicitly so the next
reader does not re-verify:

| claim | doc | code | verdict |
|---|---|---|---|
| rate limit 5/sec | `README.md:300` | `server.py:199` `RATE_PER_SEC=5` | ✅ correct |
| burst 20 | `README.md:301` | `server.py:200` `BURST=20` | ✅ correct |
| max queue 32 | `README.md:306` | `server.py:201` `MAX_QUEUE=32` | ✅ correct |
| `--steps` 12 | `SKILL.md`/`README.md` | `browser-agent:80` `STEPS=12` | ✅ correct |
| `--timeout` 120s | `SKILL.md`/`README.md` | `browser-agent:80` `TIMEOUT=120` | ✅ correct |
| text cap 32768 | `browser:61`, `README.md:193` | `browser:421` `maxb=32768`; `protocol.js:69` `32*1024`; `browser_tool_impl.mjs:79` `32768` | ✅ correct, and consistent across all three layers |
| activate wait ~3s / cap ~8s | `SKILL.md:136`, `README.md:158` | `protocol.js:251` `3000`, `:254` `8000` | ✅ correct |
| CDP timeouts | `README.md:230` ("bounded") | `protocol.js:451-453` 8000/8000/15000 | ✅ correct (docs are qualitative, no number to drift) |
| owner TTL | `browser:27` ("idle TTL") | `server.py:187` `900` | ✅ correct (no number claimed) |
| README op table vs `ALLOWED_OPS` | `README.md:144-160` | `server.py:125-127` | ✅ every documented op exists; `release` correctly documented as server-only (`server.py:132`) |

### Confirmed drift

**D-1 (CONFIRMED STALE, as briefed) — the opencode 1.17.20 version-skew claim.**
`opencode --version` on this host returns **1.18.4** (VERIFIED by running it).
The docs still assert a workbench/laptop split:
- `SKILL.md:409` — "versions resolve the deny differently (workbench 1.17.20, laptop 1.18.4)"
- `README.md:519-520` — "different opencode versions resolve it differently (workbench is 1.17.20, laptop 1.18.4)"
- `README.md:533` — "verified on 1.18.4"
- `README.md:572` — "defensive across the laptop 1.18.4 / workbench 1.17.20 version skew"
- `README.md:605` — "⚠ opencode version skew (workbench 1.17.20 vs laptop 1.18.4)"
- `README.md:609` — "It is **NOT verified on 1.17.20** (workbench)"

Both hosts are now 1.18.4 (a second check confirmed both run the *identical*
nix-store binary,
`/nix/store/64n428w29sra24db9d6h6clzdh0vy9hk-opencode-1.18.4/bin/opencode`, same
sha256). **Important nuance the docs get right and should keep:** the gate
(`browser-agent:168-190`) does not check a version *number* at all — it runs
`opencode debug agent browser-agent` and asserts the resolved **`tools`** map is
browser-only. There is no version constant to bump; only the prose is stale.
Additional stale line found on the second pass: `README.md:613` still says
"**Recommend upgrading workbench to ≥1.18.4**" — already done.

**⚠ But do NOT conclude from this that the gate now passes.** It does not,
for a completely different reason — see **D-6**, which supersedes the "just a
doc fix" reading of D-1.

**D-6 (NEW, VERIFIED BY ME — highest-value finding of the second pass) —
`browser agent` is broken by an opencode stdout-truncation race, and the docs
misattribute the failure to version skew.**

`browser-agent:168` captures the gate input with command substitution:

```bash
gate_out="$(cd "$SCRATCH" && "$OPENCODE_BIN" debug agent browser-agent 2>/dev/null)"
```

`$(...)` is a **pipe**. opencode truncates its stdout when stdout is a pipe
rather than a file — it exits without flushing the full buffer. The gate then
parses truncated JSON, fails to parse, and **fails closed** (exit 3 →
`unparseable debug-agent tool set`).

Measured on **this host (laptop, 192.168.50.155)**, VERIFIED by running:

```
opencode debug skill   pipe=65536   file=293329   (deterministic, 3/3)
opencode debug v2      pipe=55276   file=55276, 55276, 6103  (varies run to run)
```

`debug skill` truncates at **exactly 65536 bytes (64 KiB)** on a pipe while the
same command redirected to a file yields 293329. `debug v2` shows the race
directly: identical invocations produced 55276 twice and 6103 once.

A parallel investigation independently reproduced the same class of failure on
the **workbench (192.168.50.250)** against the real gate — truncation at **8192
bytes**, 3/3 runs, producing exactly the gate's failure text
(`unparseable debug-agent tool set: Unterminated string starting at: line 162
column 13 (char 3761)`), while the same command redirected to a file yielded 9530
bytes and parsed cleanly to a tool set of `['browser']` — i.e. **the confinement
config is correct; only the capture is broken.**

Two thresholds (8192 vs 65536) and a run-to-run variance mean this is **not a
fixed buffer cap but a flush race on exit**, so which hosts are affected depends
on output size, timing, and config — it is not a stable property of a host.

Assessment:
- **The gate is behaving correctly** — it fails closed on unparseable input,
  exactly as designed (`browser-agent:170-190`). This is the fail-closed
  discipline working.
- **But `browser agent` is non-functional wherever the race bites**, and the
  docs tell the reader to blame opencode 1.17.20 — a diagnosis that is now
  false and would send someone chasing a version upgrade that is already done.
- It also plausibly explains why **`browser agent` has never been invoked on
  either host** (telemetry). I cannot prove that causally — telemetry records
  invocations, not refusals — so I state it as a hypothesis, not a finding.
- **Fix:** capture via a temp file (or `stdbuf`) instead of `$()` at
  `browser-agent:168`. Small, contained, and testable.

Honesty note on provenance: the workbench measurements were taken over ssh by a
parallel investigation (pipe-vs-file behaviour was identical under `ssh -tt`, so
it is not an ssh artifact, but it was not tested from a workbench-local
interactive terminal). **The laptop measurements above are mine and were run
directly.** The truncation itself I consider VERIFIED; the specific claim "the
real gate fails on workbench" is verified-by-proxy, and re-running
`browser agent --dry-run` on the workbench locally is the cheap confirmation.

**D-2 — the `tools`-vs-`permission` gate question (SETTLED).** A prior
investigation flagged that the resolved permission map shows `*: allow`, which
would look alarming. I checked the gate directly: `browser-agent:170-190` reads
`json.load(sys.stdin)["tools"]` — the **tools** map, not the permission map — and
fails closed on *any* uncertainty (unparseable output → exit 3; `browser` not
`True` → exit 4; any other tool `True` → exit 5; any of
`bash/read/edit/write/webfetch` **absent** → exit 6, so absence never reads as
"disabled"). **The gate's actual check is sound and is checking the right map.**
The `permission` frontmatter in `opencode/browser-agent.md` (`"*": deny`,
`browser: allow`) is a second, independent layer; whatever the resolved
permission map shows does not weaken the tools gate, because tool *enablement* is
what bounds the action surface. I consider this lead closed — no finding.

**D-3 — `nav_scheme_denied` is documented as a bridge error shape but exists ONLY
in the agent tool.** `SKILL.md:419` and `README.md:543-550` describe
`nav_scheme_denied:<scheme>`. Grep across `server.py`,
`extension/service_worker.js`, `extension/protocol.js` returns **zero** hits; the
only definition is `browser_tool_impl.mjs:117,122,222,420`
(`NAV_ALLOWED_SCHEMES`). So `browser nav file:///etc/passwd` from the **CLI** is
not scheme-gated at all. That is a defensible design (the operator's own CLI is
trusted; the *model* is not), and README.md:543 is in the agent section — but
`SKILL.md:419`'s placement invites the reading that the bridge denies it
globally. **doc-fix**: state plainly that the scheme gate is an
autonomous-agent-only control, and that the CLI deliberately has no such gate.

**D-4 — `README.md:293` cites "43,991 evals in one hour"; the operator's current
telemetry read is 43,740.** Same event, drifted number. Trivial, but it is a
figure that gets quoted into decisions. **doc-fix.**

**D-5 — extension manifest is `0.2.0` (`extension/manifest.json:4`)**, and
`README.md:286` uses "a pre-0.2.0 build" as its stale-extension example. Correct
today, but the manifest is explicitly *not* bumped per change
(`README.md:82-84`), so this example silently rots. Recording as a known
maintenance hazard, not a current error.

**D-7 — `README.md:496-498` lists the agent's op set as 10 ops
(`text,html,eval,nav,screenshot,frames,click,type,key,activate`); the code's
`ALLOWED_OPS_DEFAULT` has 12 (adds `upload` and `whoami`).** Note the direction:
**README matches the safe `browser.js` enum**, while `browser_tool_impl.mjs:74-77`
and `opencode/browser-agent.md:22-33` (which lists all 12 accurately) match the
permissive list. So the drift is now a **three-way** disagreement across README /
tool schema / impl+prompt. This materially strengthens **F-1**: the safe behaviour
is what README documents, so option (a) — dropping `upload`/`whoami` from
`ALLOWED_OPS_DEFAULT` and the agent-md table — makes code match the *already
published* contract rather than changing it.

**D-8 — 13 env vars read by code are documented nowhere in README/SKILL:**
`BROWSER_BRIDGE_PORT`, `_HOST`, `_TOKEN_FILE`, `_CMD_TIMEOUT` (default 20,
`server.py:1712`), `_POLL_TIMEOUT` (default 25), `_I3_TIMEOUT` (1.5,
`server.py:213`), `_NO_AUTOSTART`, `_SPOOL_EMIT`, `_ACTIVATE_TIMING`,
`_CDP_TIMEOUTS` (test-only, `service_worker.js:154`), `BROWSER_AGENT_AUDIT`,
`BROWSER_AGENT_ALLOWED_OPS`, `BROWSER_AGENT_SESSION_ID`. Several are documented in
`server.py:72-79`'s own `--help`, so the gap is README/SKILL specifically. Every
*documented* env var was confirmed to be genuinely read — this is an omission, not
an error. `BROWSER_AGENT_ALLOWED_OPS` is the notable one given F-1.

**D-9 — the CDP timeout constants are never documented numerically.**
`README.md:673-674` says only "a bounded budget"; actuals are `protocol.js:451-453`
(8000/8000/15000). Not wrong, just absent.

**Second-pass coverage note:** the systematic line-by-line prose sweep of
SKILL.md + README.md was completed on the second pass. It additionally confirmed
as CORRECT: the SKILL.md subcommand table (`SKILL.md:119-140`) matches the
`case "$sub"` dispatch exactly (all 19 subcommands + `--print-session-id`, no
drift); the README op table matches `ALLOWED_OPS` and `service_worker.js`'s `OPS`
exactly (14/14, zero documented-but-unimplemented, zero
implemented-but-undocumented); `release` correctly documented as server-only; the
manifest `0.2.0` / `SKILL.md:46` "pre-0.2.0" example is consistent; and the
`setsid` process-group kill is confirmed at `browser-agent:298-311`.

---

## 6. The `upload` agent-reachability assessment

### The claim as briefed

`ALLOWED_OPS_DEFAULT` (`browser_tool_impl.mjs:74-77`) includes `upload`, and
`buildRequest`'s upload branch (`:274-284`) does presence checks only —
explicitly documented as "ANY path is allowed for the agent … no path allowlist"
(`:65-73`, `:277`), audit-logged (`server.py:1608-1612`, `1666-1675`) but not
prevented. A test even pins this as intended
(`browser_tool.test.mjs:570` — "upload allows ANY path (explicit exfil tradeoff — no path allowlist)").
So on the impl layer the briefing is **accurate**.

### What I found that changes the severity

**F-1 (KEY FINDING) — the typed tool schema the model actually sees does NOT
expose `upload`.** `opencode/tools/browser.js:38-40` declares:

```js
op: tool.schema
  .enum(["text", "html", "eval", "nav", "screenshot",
         "frames", "click", "type", "key", "activate"])
```

`upload` is **not in the enum**, `whoami` is **not in the enum**, and there is
**no `path` argument declared at all** in the `args` block
(`browser.js:41-60` declares only `op, selector, url, js, text, key, frame,
maxBytes, waitMs`). `tool.schema` is zod
(`@opencode-ai/plugin/dist/tool.d.ts:1` — `import { z } from "zod"`), and
opencode validates tool args against it before `execute` runs.

So end-to-end, today, the model's call would fail **twice** before touching a
file: the `op` value fails enum validation, and even if it did not, `path` is an
undeclared arg that would not survive schema parsing → `buildRequest` would throw
`upload_missing_path` (`browser_tool_impl.mjs:280`).

**Exploitability verdict: NOT currently exploitable end-to-end.** Combined with
the telemetry fact that **`browser agent` has never been invoked on either host**,
real-world exposure to date is **zero**.

### Severity call

**Low as an active vulnerability. Medium as a latent one.** I am deliberately not
inflating this. The reasons it is not "none":

1. **The two layers disagree, and the safe one is accidental.** Nothing in the
   code or comments says "the enum is the upload gate". The impl's comment block
   (`browser_tool_impl.mjs:65-73`) reads as though upload *is* reachable and the
   audit log is the compensating control. Whoever wrote the enum and whoever
   wrote `ALLOWED_OPS_DEFAULT` did not agree, and no test asserts the enum
   (`browser.js` is never imported by any test — VERIFIED). A future edit that
   "fixes" the mismatch in the obvious direction — adding `upload` and `path` to
   the schema so the documented feature works — opens full local-file exfil with
   **no remaining gate**.
2. **The agent definition actively instructs the model to use it.**
   `opencode/browser-agent.md` lists `browser(op="upload", selector="…", path="…")`
   in its capability table, and `op="whoami"` too. So the prompt promises two ops
   the schema rejects. That is a live functional bug (`whoami` in particular is
   the "confirm which host you're on before acting" safety habit the docs push),
   and it is the exact pressure that would cause someone to widen the enum.
3. **`BROWSER_AGENT_ALLOWED_OPS` does not help.** `allowedOpsFromEnv`
   (`:148-151`) can only *narrow* against the impl list; it cannot widen past the
   schema, and it is not what is blocking upload today.

The realistic attack chain, were the schema widened: a prompt-injecting page the
agent is told to read instructs it to call
`upload(selector="input[type=file]", path="~/.ssh/id_ed25519")` against a file
input the attacker placed on their own page, then `click` the submit button. Every
step is an op the agent already has (`click` is in the enum, the tab is the
agent's own and is on the attacker's domain by construction if the goal sent it
there). Chrome reads the file by path and posts it. The domain allowlist does not
help — the attacker page *is* the allowed domain. The audit log records it after
the fact. So the chain is short and fully mechanised; only the enum is in the way.

### Minimal fix options (assessed, not applied)

| option | effect | cost | verdict |
|---|---|---|---|
| **(a) Drop `upload` from `ALLOWED_OPS_DEFAULT`** (`browser_tool_impl.mjs:74-77`) and from the `browser-agent.md` capability table | Makes the two layers agree in the SAFE direction. Removes the pressure to widen the enum. Operator keeps `upload` on the CLI (where it is validated at `browser:544-548`). | ~15 min + test update (`browser_tool.test.mjs:285,293,551-577` assert the current list) | **Recommended.** Highest safety per unit effort, and costs nothing real: the agent has never used upload, and an upload is an operator-intent action, not a browsing action. |
| **(b) Server-side sensitive-path deny-list** in `server.py` (reject `~/.ssh/**`, `~/.config/browser-bridge/token`, kubeconfigs, `**/.env`, `id_*`) | Defends the CLI path too, and defends any future caller. But a deny-list on a filesystem is a losing game (symlinks, `/proc/self/…`, relative traversal, unanticipated secrets). | ~1-2 h to do properly | **Do as defence-in-depth, not as the primary control.** Note it must resolve symlinks first — the CLI already does `os.path.realpath` (`browser:547`), the server does not. |
| **(c) Both** | (a) closes the agent surface, (b) bounds operator/CLI mistakes | | **Best if the budget exists.** Do (a) first; it is the one that matters. |
| **(d) Make the enum the documented gate** (leave the impl as-is, add a comment + a test asserting `upload`/`whoami` are absent from `browser.js`'s enum) | Cheapest possible | ~20 min | Acceptable *only* alongside (a). On its own it enshrines a confusing two-list design. |

Whichever is chosen, **add a test that asserts `browser.js`'s `op` enum equals
the agent's intended op set** — that is the missing check that let the two lists
drift apart silently, and it is ~10 lines.

---

## 7. Ranked remediation list

| # | finding | label | severity | effort |
|---|---|---|---|---|
| 1 | **`browser-agent:168` — replace `$(...)` capture with a temp file (or `stdbuf`)** so the tool-set gate reads opencode's full `debug agent` output instead of a pipe-truncated prefix. This is what makes `browser agent` refuse to run (D-6). Verified truncation on the laptop (64 KiB) and, by proxy, on the workbench (8 KiB) | `cli-change` | **High (feature is dead)** | ~1 h incl. a regression test |
| 2 | Reconcile the agent op surface: drop `upload` (and decide `whoami`) from `ALLOWED_OPS_DEFAULT` + the agent-md table, and add a test pinning `browser.js`'s `op` enum against the intended set (F-1, D-7). Note this makes code match the contract **README already publishes** | `security` + `test-gap` | **Medium (latent)** | ~30 min |
| 3 | `whoami` (and `upload`) are promised to the model in `browser-agent.md` but rejected by `browser.js`'s enum — the agent literally cannot run the "confirm your host first" habit the docs mandate. Make schema + impl + prompt + README agree (currently a three-way split) | `cli-change` (tool schema) | **Medium (functional)** | ~30 min |
| 4 | Cover the CLI's 7 untested error branches (`503/504/401/409×3/404`) — the whole human-facing "what to do next" layer is unexercised. The existing `_CannedCmdServer` harness (`test_server.py:3400`+) makes each one ~10 lines | `test-gap` | Medium | ~2 h |
| 5 | Rewrite the stale opencode 1.17.20 claims (D-1) at `SKILL.md:409`, `README.md:519-520,533,572,605,609,613` — **and replace the framing**: the failure mode is the stdout-truncation race (#1), not version skew. Delete the "⚠ opencode version skew" block (`README.md:605-613`) and the "recommend upgrading workbench" line entirely | `doc-fix` | Medium (actively misdiagnoses #1) | ~30 min |
| 6 | Add a `wait` op (wait-for-selector / wait-for-settle), bounded server-side like `activate` — removes the busiest toil loop and the main incentive to poll with `eval` | `new-op` | Medium (value) | ~1 day |
| 7 | Clarify that `nav_scheme_denied` is an autonomous-agent control only, and that the CLI has no scheme gate (D-3) — `SKILL.md:419` | `doc-fix` | Medium (security-model clarity) | ~10 min |
| 8 | `tabVisibilityState` returning `null` on failure means "unknown" is indistinguishable from "visible", so a throttled-SPA read can return a shell DOM with no warning (E-1) | `extension-change` | Low | ~1 h |
| 9 | Server-side sensitive-path deny-list for `upload`, with symlink resolution (option (b)) | `security` | Low (as defence-in-depth) | ~1-2 h |
| 10 | Add a `scroll` op — it is the one `eval` recipe whose footgun had to be documented (`SKILL.md:63-64`) | `new-op` | Low | ~2 h |
| 11 | Composite `browser open --activate --read <url>` to collapse the repeated 3-4 step SPA dance; CLI-side sequencing only, no new blast radius | `cli-change` | Low | ~3 h |
| 12 | Failure-path tests for `instances`, `release`, `tabs` (the 3 ops with zero failure coverage); include `render_instances` | `test-gap` | Low | ~1 h |
| 13 | `op_error` can exit 0 on a 200 whose `result` is not a dict (E-2); `screenshot`'s missing-image-data branch gives no next step (E-3); `render_instances` fails open to silence (E-4) | `cli-change` | Low | ~1 h total |
| 14 | Document the CDP timeout constants (D-9) and the 13 undocumented env vars (D-8), notably `BROWSER_AGENT_ALLOWED_OPS` | `doc-fix` | Low | ~30 min |
| 15 | Update the "43,991 evals" figure to 43,740 (`README.md:293`) (D-4) | `doc-fix` | Trivial | ~2 min |

### Explicitly NOT recommended
- Cookie/localStorage inspection ops, and download ops — both would hand a
  prompt-injected cheap model a clean exfil/write primitive on a live-cookie
  surface. Keep them behind `eval`.
- Any work on `eval --frame` grandchild-OOPIF `frame_not_found` — **known and
  in-flight**, another agent is fixing it. It currently fails safe.

---

## 8. What could not be verified without a live browser (or at all)

- **Every op's actual behaviour in Brave.** All extension coverage is against
  faked `chrome.*` APIs. A test passing proves the decision logic, not that
  Chrome does what we think.
- **Whether opencode 1.18.4's zod validation rejects an out-of-enum `op` before
  `execute`, or passes it through.** I confirmed `tool.schema` is zod
  (`@opencode-ai/plugin/dist/tool.d.ts:1`) and that `upload`/`path` are absent
  from the schema, but I did **not** run opencode to observe the rejection. If
  opencode were lenient here, `op="upload"` would reach `buildRequest` and be
  refused as `upload_missing_path` — still blocked, but by a thinner margin.
  This is the one inference the `upload` severity call rests on; **it deserves a
  live check by the operator**, and it is cheap: run `browser agent --dry-run`
  with a goal that induces an upload call and read the audit log.
- **The real gate failing on the workbench specifically.** I VERIFIED the
  underlying opencode stdout-truncation on this host (laptop) directly, and a
  parallel investigation reproduced the actual gate failure on the workbench over
  ssh. I did not run the real gate on a workbench-local terminal. Cheap
  confirmation for the operator: run `browser agent --dry-run "test"` on the
  workbench and check whether it dies at `tool-set gate FAILED` /
  `unparseable debug-agent tool set`.
- **Whether the truncation can ever produce PARSEABLE-but-wrong JSON.** A
  truncated JSON object is almost certainly unterminated → parse error → exit 3 →
  fail closed, which is safe. I did not attempt to construct a truncation point
  that parses. If one existed the gate could in principle read a short tool map;
  note that even then `required_false` absence (exit 6) is a second backstop, so
  I assess this as very unlikely to be exploitable.
- **The extension's supersede back-off** (`README.md:100` already flags this as
  browser-only-verifiable).
- ~~A full prose sweep of SKILL.md/README.md~~ — **completed on the second
  pass** (see the second-pass coverage note in §5). Both the numeric/default
  sweep and the behavioural-prose sweep are now done.
