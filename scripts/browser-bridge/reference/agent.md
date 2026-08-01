# `browser agent "<goal>"` — autonomous read/navigate in an isolated tab

**Load this when:** you are about to run `browser agent` and need its flags,
guardrails or prereqs · the agent returned `status:"blocked"` or a non-zero exit ·
you got `op_not_allowed:<op>` or `nav_scheme_denied:<scheme>` · the wrapper died with
`unparseable debug-agent tool set: …` · you need the RCE-fix / typed-tool rationale ·
you need to know what the agent can and cannot reach.

Core: `~/workspace/devrc/scripts/browser-bridge/SKILL.md`.

## Status (2026-07-31) — read before re-diagnosing

✅ **Verified working end-to-end 2026-07-31.** A real run against a live tab returned
`{"answer":"Example Domain","evidence":[…],"steps_used":2,"status":"ok"}`.
⚠ A `--dry-run` does **NOT** prove this: it still invokes the model but *intercepts*
`nav`, so it exercises neither navigation nor reading. Only a real run does.

It had **TWO independent blockers**, both now fixed — either one alone made the
command unrunnable, which is why fixing the first didn't appear to help:

1. **#216** — an `$(...)` stdout flush race truncated the fail-closed tool-set gate's
   input, so the gate (correctly) refused with `unparseable debug-agent tool set: …`.
   Fixed by capturing to a file. See the ⚠ note under the gate bullet below.
2. **#234** — the tab-readiness probe ran `eval '1'` against `about:blank`, and
   `chrome.scripting` **cannot inject into `about:blank`**, so readiness could never
   be reached. Fixed by probing without injecting.

🔴 **Every doc misattributed both symptoms to an opencode *version* problem for the
entire arc.** They are not version-related — both hosts run opencode 1.18.4 and both
resolve browser-only. Do not re-open that hypothesis.

✅ **The `browser agent`-first default IS shipped** (SKILL.md → `## FIRST DECISION`).
It was held back until capability was measured rather than argued from token
arithmetic; the measurement is
`~/workspace/devrc/claudedocs/browser-bridge-deepseek-measurement-2026-07-31.md`.

## What was measured — and the scope it was measured at

**Scope, so the numbers carry it:** host **laptop `192.168.50.155`**, instance
**`personal`**, extension 0.2.0, `opencode` **1.18.4**, model
**`openrouter/deepseek/deepseek-v4-flash`** as OpenRouter routed it on
**2026-07-31**, defaults `--steps 12` / `--timeout 120`, **14 goals each run exactly
ONCE**. Nothing here is evidence about the workbench (`.250`), the `work` profile, a
different model, or run-to-run variance.

- **13/14 answers correct.** Excluding the deliberate out-of-allowlist guardrail
  case: 12/13. One run per goal, so the 95% binomial CI is ≈**0.66–0.998** — read it
  as "≈0.9", not as a rate.
- **~$0.0025 per run** (sum of attributed per-run deltas, $0.0345 over 14 runs;
  the campaign credit delta $0.0702 is an upper bound — that OpenRouter key is
  shared with other tooling).
- **Median wall-clock 22.65s**, range 11.9s–59.0s. Simple single-page reads: median
  18.1s, always 2 tool calls. Blind-click interactive goals were the expensive shape
  at 48.7s and 59.0s / 8–12 tool calls.
- The **one wrong answer** (`status:"ok"`, confidently wrong) came from reading an
  unrendered shell in a throttled hidden tab — 1 of 13 `ok` runs (7.7%). That is the
  failure the agent tool's deterministic auto-wake below closes **by construction**
  — it is NOT yet live-verified against that run, because F2 has not been
  reproducible since (2026-08-01: with auto-wake disabled, and again with `wake`
  denied outright, the rig rendered before the agent's slower read landed and both
  controls returned the CORRECT answer). The auto-wake mechanism itself IS
  live-verified: a real run's audit shows `auto_wake` → `auto_wake_exec` (wake) →
  `auto_wake_exec` (re-read) → `auto_wake_ok woke:true settleMs:1500`. Closing the
  gap needs a fixture that stays an unrendered shell for ≥30s. It was not a
  reasoning failure, and no other run produced a wrong `ok`.
- The out-of-allowlist guardrail behaved exactly as documented: clean
  `status:"blocked"`, exit 1, empty evidence, no fabrication, no hang.

### 🔴 Do NOT sell `evidence` as protection against a wrong answer

The design doc proposed "`status:"ok"` but empty/unquoted `evidence` → treat as
`partial`" as a **prerequisite** for the flip. The measurement **falsified that as a
safety net** and it was demoted:

- It would have flagged goal **B**, which was **correct** (its evidence was a
  paraphrase, not a substring of any tool output) — a false positive.
- It would **not** have flagged **F2**, the only wrong answer, because F2 quoted its
  unrendered page **verbatim**. The evidence was perfectly faithful; the *page* was
  in the wrong state.

Evidence grounding is anti-**confabulation** hygiene, and worth keeping as such. It
is not a correctness check. The real protections are (a) the deterministic
auto-`wake` below, and (b) escalation being cheap — taking over from the agent costs
~200 tokens, which is why agent-first wins even at a low success rate.

### Guardrails the measurement named

1. **Don't default to the agent for virtualised / lazy-loaded list content.** The
   civitai card-list goal only worked because the model woke the tab twice and then
   read `html`; the list could not be reproduced through `text` on a hidden tab even
   after waking.
2. **Keep `--allow-domains` tight, and include the frame hosts you expect.** Measured
   at two points on the same nested-OOPIF goal: with the frame hosts allowlisted the
   model **skipped `frames`** and top-level-`nav`'d to each iframe URL in turn (right
   answer, 7 calls); with the allowlist tightened to `127.0.0.1` only, it called
   `frames`, picked the deepest `frameId`, and used `eval --frame` (right answer).
   A tight allowlist is what pushes it onto the correct path.
3. **Ignore `steps_used`.** It disagreed with the tool audit in **5 of 14** runs and
   **always undercounted** (5 vs 7, 8 vs 12, 5 vs 7, 4 vs 6, 7 vs 8). Don't reason
   from it; `BROWSER_AGENT_KEEP_SCRATCH=1` + `tool-audit.jsonl` is the ground truth.
4. **Privacy is a hard boundary, not a preference** — see the ⚠ Privacy bullet below.

### Measured but NOT measured — don't over-claim

`status:"partial"` was **never produced** by any run, so its behaviour is unmeasured.
Nor were: needle-in-a-haystack reads over long innerText, the step-budget/timeout
boundaries (nothing exhausted either), failure paths other than the domain guardrail,
authenticated/high-secret pages (deliberately excluded), prompt injection or hostile
pages, and `activate` (never invoked).

Offload an open-ended "go read X and tell me Y" browsing task to a **cheap
autonomous agent** (opencode + DeepSeek `deepseek-v4-flash` via OpenRouter) so it
never burns YOUR context on transient page HTML — only a compact structured
result comes back.

```bash
browser agent "go to news.ycombinator.com and report the top 3 story titles" \
  [--instance K] [--allow-domains a.com,b.com] [--deny-domains x.com] \
  [--steps N] [--timeout S] [--dry-run]
```

- **Output (stdout):** one compact JSON object — never raw HTML:
  `{"answer":"…","evidence":["…"],"steps_used":N,"status":"ok|partial|blocked"}`.
  Exit 0 for `ok`/`partial`, non-zero for `blocked`/errors.
- **Own isolated tab + NO shell (structural safety).** The wrapper `open`s a NEW
  background tab and gives the agent exactly ONE capability: a TYPED custom tool
  `browser` (opencode/tools/browser.js). The agent def **denies bash and every
  other built-in tool**, so the model has no shell at all — it calls the tool with
  structured args (`op` + optional `selector`/`url`/`js`), never a command string.
  The tab, instance, and `--deny/--allow-domains` are **forced on the tool via env
  the wrapper sets** — the model cannot choose the tab or reach a denied domain.
  The tab is closed on EVERY exit path (success, timeout, error).
  - **WHY typed, not bash (the PR #180 RCE fix):** the earlier design gave the
    agent opencode's bash tool scoped to `browser --tab <id> *`. A shell OUTPUT
    REDIRECT (`browser --tab N eval '…' >> ~/.zshenv`) is not a separate command
    node, so it rode the allowed `browser` command through opencode's wildcard
    glob and the shell performed the redirect → a hostile page could induce the
    model to write to a sourced dotfile → host RCE. The typed tool removes the
    shell entirely, so there is no `>`/`;`/`|`/`$()` surface to abuse.
- **Runtime fail-closed tool-set gate (makes an un-upgraded opencode SAFE):**
  before opening a tab or spending a model token, the wrapper runs `opencode debug
  agent browser-agent` (a read-only, **model-free** config dump) and refuses to run
  (`die`, model never invoked) unless the resolved `tools` map is browser-ONLY —
  `browser:true` AND every host tool (`bash`/`read`/`edit`/`write`/`webfetch`)
  present AND `false`. Any uncertainty (unparseable output, `browser` absent, a
  host tool `true`, or a host tool absent) fails closed. This is the one place the
  fail-closed property is *verified at runtime* rather than trusted — on any
  opencode where the host-tool denial didn't take, `browser agent` refuses instead
  of running the model unconfined. The gate runs BEFORE the tab is opened, so a
  gate failure leaks no tab. **Both hosts run opencode 1.18.4 and both resolve
  browser-only** — there is no version-skew caveat.
  - ⚠ **If you check the gate by hand, redirect to a FILE**
    (`opencode debug agent browser-agent > /tmp/gate.json`), never a pipe or
    `$(...)`: opencode doesn't reliably flush stdout before exiting into a pipe, so
    a capture can be TRUNCATED and the gate then (correctly) fails closed with
    `unparseable debug-agent tool set: Unterminated string…` — which looks like a
    version problem and is not. The wrapper itself now captures to
    `$SCRATCH/gate.json` for exactly this reason.
- **The agent's `whoami` is narrowed.** It sees its OWN profile + the host label +
  versions — never the operator's other profiles, never any `activeTabDomain`,
  never the git HEAD. (Otherwise a prompt-injected page could have the model report
  that your `banking` profile is on chase.com, then `nav` that to an attacker.) The
  `browser whoami` CLI you run by hand is unchanged and still shows everything.
- **No `upload` and no `activate` for the agent.** The autonomous model's op set
  is **11 ops** (`text`/`html`/`eval`/`nav`/`screenshot`/`frames`/`click`/`type`/
  `key`/`wake`/`whoami`). `upload` is operator-only — you can still `browser
  upload` by hand; the model gets `op_not_allowed:upload`, and it is re-enabled
  only by an explicit `BROWSER_AGENT_ALLOWED_OPS` opt-in. **`activate` is stricter
  still: it is not reachable by the model at ALL**, not even via that opt-in,
  because it takes the operator's screen. The agent gets `wake` instead — the
  un-throttling it actually needed, with no focus theft.
- **Hidden-tab AUTO-WAKE (deterministic, not modelled).** The agent's tab is always
  opened `active:false`, so it is always hidden and always throttled. The bridge
  already flags that (`data.hidden:true` + the throttle note on every read), but the
  tool's `summarizeResult` used to return a bare `data.text`/`data.html` and DISCARD
  both — so on a throttled SPA that rendered a plausible-looking shell the model read
  the shell and returned a **confident wrong answer with `status:"ok"`**, quoting the
  shell verbatim as evidence. Measured at **1 of 13 successful runs (7.7%)**, and it
  was the only failure mode found (`claudedocs/browser-bridge-deepseek-measurement-
  2026-07-31.md` §3.2). Now, in `opencode/tools/browser_tool_impl.mjs`:
  - the **first** hidden `text`/`html` read of a page makes the TOOL issue `wake`,
    re-read, and return the re-read — inside one model tool call, no step spent, no
    decision asked of the model. `wake`, never `activate`.
  - **once per PAGE**, keyed by the read's `url` per forced tab. The un-throttle dies
    with the CDP detach but the DOM it produced persists, so later reads are cheap.
    A successful `nav`/`click`/`key`/`eval` clears the tab's records — the URL key
    cannot see a same-URL document replacement (form POST, `location.reload()`, a
    same-URL SPA remount), and under-forgetting costs a wrong answer while
    over-forgetting costs one extra wake.
  - a `wake` the MODEL calls is deliberately **NOT** recorded as the page's wake. It
    looks like a free optimisation and is a correctness trap: the extension reports
    the url from a `chrome.tabs.get` taken AFTER the settle, so a URL change during
    the ~1.5s window (a redirect completing, `history.replaceState`, an SPA route
    settling) lands the verdict on document **B** while the settle was spent on **A** —
    and B's shell then gets the `note: … post-wake` banner. The auto path is immune
    because it keys on the PRE-wake read's url. Fixing it properly needs the extension
    to return the pre-attach url too (manifest bump + full Brave re-point), which is
    not worth saving one wake. Do not reintroduce it without that.
  - the **VERDICT** is stored, not just the key — `{ok, detail}`. A page whose wake
    FAILED is never later described as post-wake: `note:` is reserved for a wake the
    tool can vouch for (`woke === true`); a failed / unconfirmed / skipped / never-
    attempted page gets `WARNING:` and the recorded reason. Storing only the key made
    every later read of a failed page a *reassured* wrong answer — worse than the
    silent one this change exists to remove.
  - **fail-open**: a failed/forbidden wake, or a failed re-read, returns the ORIGINAL
    read prefixed with a loud `[browser-tool] WARNING: … 'wake' FAILED (<reason>)`.
    A read never becomes an error. A failed wake is not retried per-read.
  - **budget**: automatic wakes per tab per run are capped, independent of the page
    key, because a page that rewrites its URL on every read (hash routing, `?t=`
    cache-busters, `history.replaceState` on infinite scroll) yields a fresh key each
    time, degrading once-per-page into once-per-read: 3 bridge calls + a ~1.5 s settle
    each against the 5/s + burst-20 per-instance limiter. The cap is **the run's step
    budget** (`BROWSER_AGENT_STEPS`, exported by the `browser-agent` wrapper), floored
    at `AUTO_WAKE_CAP_FLOOR` (4), falling back to `AUTO_WAKE_TAB_CAP` (8) when unknown;
    `BROWSER_AGENT_AUTO_WAKE_MAX` overrides everything. A **fixed** 8 starved legitimate
    runs — 12 distinct pages reached by nav+text is not churn, yet pages 8–11 were never
    woken, silently reverting the back half of a default `--steps 12` run to
    `BROWSER_AGENT_AUTO_WAKE=0`. Binding it to STEPS gives the provable bound "at most
    one injected wake per model step". Past the cap it stops waking and says so loudly.
  - **dry-run**: `BROWSER_AGENT_DRY_RUN=1` skips the auto-wake and says so, **and
    `wake` is now one of the MUTATING ops** — so a *manual* `op="wake"` in a dry run is
    synthesized (`{"ok":true,"dryRun":true,"op":"wake"}`) rather than reaching the
    bridge, while still being policy-checked (a forbidden op or a disowned tab is
    still refused). A read is passive, but a wake attaches the debugger to the
    operator's live tab for up to a 6 s settle and raises Brave's "an extension is
    debugging this browser" banner — past what "logs, doesn't drive" promises. Both
    paths now match that promise; previously the doc was stronger than the code.
  - the banner **hedges**: `woke` is probed from `visibilityState` inside the CDP
    attach, so it proves the tab was un-throttled and says nothing about whether the
    app finished rendering inside the bounded settle. Every post-wake banner tells the
    model that a loading/placeholder state below is real.
  - both injected calls are audited (`auto_wake_exec`) and so is the outcome
    (`auto_wake_ok` / `auto_wake_unconfirmed` / `auto_wake_failed` / `auto_wake_capped`).
  - `hidden` + the server's note now survive `summarizeResult` for `text`/`html`/`eval`
    regardless, so a still-hidden read is visible rather than silent. (`eval` gets the
    signal but is never auto-re-run — an arbitrary expression is not idempotent.)
  - kill switch: `BROWSER_AGENT_AUTO_WAKE=0` disables the auto-wake; the warning stays.
- **Guardrails:** a step budget (`--steps`, default 12), a wall-clock `--timeout`
  (default 120s) enforced with a **process-group kill** (`setsid` + kill the whole
  group, so no opencode child survives), `--deny-domains`/`--allow-domains`
  enforced INSIDE the tool (a denied `nav` is refused before it reaches the
  bridge), a **non-http(s) nav scheme hard-denial** (a `nav` to `file:`/`data:`/
  `about:`/`javascript:`/`chrome:`/… is refused as `nav_scheme_denied:<scheme>`
  before any fetch — those have no host and would otherwise bypass
  `--allow-domains`), and `--dry-run` (intercepts every mutating op —
  `nav`/`eval`/`click`/`type`/`key`/`upload`/`wake` — logs, doesn't execute; reads
  still hit the browser, and the auto-wake is skipped with a WARNING). The full
  opencode JSON transcript + a metadata-only tool audit are kept
  in a scratch dir. **Domain deny is best-effort** (see note below).
- **⚠ Privacy:** the pages the agent reads are sent to **OpenRouter/DeepSeek**.
  Never point it at banking, private mail, a credential manager, or anything you
  would not hand to a third party. This is the one part of the agent-first default
  that is not a judgement call.
- **⚠ Domain deny is a mitigation, not a guarantee.** The tool refuses a `nav` to
  a denied host, but it cannot see a page's own client-side redirect (meta-refresh
  / `location=` after an allowed nav) — the bridge navigates the tab and the tool
  only sees the op it issued. Treat `--deny-domains` as best-effort defence in
  depth; the real isolation is the own-tab lock. (Follow-up: server-side
  enforcement against the tab's resolved post-nav URL would make it binding.)
- **Prereqs:** `opencode` on PATH with the OpenRouter key already in its auth
  store (`~/.local/share/opencode/auth.json`), the extension connected, and BOTH
  the agent def AND the custom tool symlinked into opencode's config (see README →
  Deploy). If any is missing you get a clean error and no orphaned tab.
