# browser-bridge — Token-Efficiency Plan + `browser agent`-as-Default Design

Date: 2026-07-30 · Status: **DESIGN ONLY — nothing implemented, awaiting operator approval**
Scope: `~/workspace/devrc/scripts/browser-bridge/`
Method: read-only analysis of `SKILL.md`, `README.md`, `browser`, `browser-agent`,
`opencode/browser-agent.md`, `opencode/tools/browser.js`,
`opencode/tools/browser_tool_impl.mjs`, `extension/protocol.js`, `extension/service_worker.js`,
plus ONE model-free `opencode debug agent browser-agent` run.

---

## 0. FIRST: the fail-closed gate on opencode 1.18.4 — settled

### 0.1 What I ran (read-only, model-free)

I reproduced exactly what `browser-agent` does before it opens a tab or spends a token:
built a scratch project (`.opencode/agents/` + `.opencode/agent/` + `.opencode/tools/` +
`.opencode/tool/`), copied `browser.js` + `browser_tool_impl.mjs`, templated the agent md
(`__STEPS__`→12, `__MODEL__`→`openrouter/deepseek/deepseek-v4-flash`), and ran
`opencode debug agent browser-agent` in it. No model was invoked, no tab was opened,
`browser agent` was never run.

`opencode --version` → **1.18.4** on this host (workbench).

### 0.2 The resolved `tools` map — what the gate actually inspects

```json
{"bash": false, "browser": true, "edit": false, "glob": false, "grep": false,
 "invalid": false, "question": false, "read": false, "skill": false, "task": false,
 "todowrite": false, "webfetch": false, "write": false}
```

Running the gate's **verbatim** Python predicate against this output:

```
GATE RC=0
```

**The gate PASSES on opencode 1.18.4.** `browser` is `true`; every host tool the gate
requires (`bash`, `read`, `edit`, `write`, `webfetch`) is **present and `false`**; no
non-`browser` tool is `true`. The tool set is browser-ONLY, exactly as designed.

### 0.3 The `permission` map — FALSE ALARM, killed cleanly

The dangling thread was that the resolved `permission` array's **first** entry is
`{"permission":"*","action":"allow","pattern":"*"}`, which looked like the agent def's
`permission: {"*": deny}` had failed to take. It had not. The array is an **ordered,
merged** list of 30 entries; the agent def's own entries are at the **end**:

```
 0  {'permission': '*',       'action': 'allow', 'pattern': '*'}   ← opencode built-in default
 1..26                                                             ← global/plugin defaults
27  {'permission': '*',       'action': 'deny',  'pattern': '*'}   ← THE AGENT DEF's deny-all
28  {'permission': 'browser', 'action': 'allow', 'pattern': '*'}   ← THE AGENT DEF's allow
29  {'permission': 'external_directory', …}
```

The agent's `"*": deny` and `browser: allow` **are present and are last**, i.e. they
override the built-in `*: allow` at index 0. Index 0 is opencode's default seed, not a
leak.

**More decisive than the ordering question:** `permission` and `tools` are two different
mechanisms, and the gate deliberately checks the **stronger** one. A tool with
`tools: false` is **not registered and is never advertised to the model at all** — there
is no tool call for a permission entry to adjudicate. So even if the permission ordering
were the other way round, `bash: false` in the `tools` map means the model has no shell.

**Verdict — plainly: `*: allow` in the permission map does NOT affect the `tools` map the
gate checks, and does NOT give the cheap model access to anything beyond `browser`. This
is a false alarm. The RCE-hardening the design rests on IS in force on opencode 1.18.4.**
The recommendation on making `browser agent` the default is **unchanged by this** — but
see §0.5 for a *different*, real finding.

### 0.4 The stale doc claim

`SKILL.md` L410-413 and `README.md` state *"Different opencode versions resolve the deny
differently (workbench 1.17.20, laptop 1.18.4)"*, implying the gate may refuse on the
workbench. **This is STALE.** Workbench is 1.18.4 and the gate passes. This stale sentence
is, in my assessment, a material contributor to zero adoption (§B.5) — it tells a reader
the feature is unreliable on one of the two hosts.

### 0.5 A DIFFERENT, real finding: `upload` op-surface mismatch (flag for the security audit)

While reading the typed tool I found an inconsistency across the three layers that
describe the agent's op set:

| layer | includes `upload`? | includes `whoami`? |
|---|---|---|
| `opencode/browser-agent.md` tool table (what the MODEL is told it can do) | **YES** (documented with a `path` arg) | **YES** |
| `opencode/tools/browser.js` — the typed `op` **enum** | **NO** — enum is `["text","html","eval","nav","screenshot","frames","click","type","key","activate"]` | **NO** |
| `opencode/tools/browser_tool_impl.mjs` — `ALLOWED_OPS_DEFAULT` | **YES** | **YES** |

So the model is **told** it has `upload` with an arbitrary caller-chosen `path`, and the
in-process enforcement layer would **allow** it, but the typed schema enum would reject
it — *if* opencode validates args against the declared schema before `execute()`.
**I did not verify whether opencode 1.18.4 enforces the enum**, and determining that would
require invoking the model, which is out of scope for this dispatch.

Either branch is a problem worth fixing before the agent becomes the default path:
- **If the enum IS enforced:** the agent md lies to the model about a capability it does
  not have — wasted steps and a confusing `refused` on any upload task.
- **If the enum is NOT enforced:** a prompt-injecting page can induce
  `upload(selector, path="/home/zach/.ssh/id_ed25519")` and exfiltrate a local file to the
  page's origin. The server audit-logs it, which is detection, not prevention.

**Recommendation (defer to the dedicated security audit for the fix, but gate the default
on it):** the wrapper already has the knob — `BROWSER_AGENT_ALLOWED_OPS` overrides
`ALLOWED_OPS_DEFAULT`. Set it to the read/drive ops only (drop `upload`) for the
`browser agent` path, and remove the `upload` row from `browser-agent.md`. That closes the
gap deterministically at the enforcement layer regardless of enum behaviour. Making the
agent the DEFAULT multiplies exposure of this surface, so **I recommend treating this as a
blocker for the default flip, not a follow-up.**

---

# PART A — Token efficiency

## A.1 The measured baseline

| artifact | bytes | ≈ tokens (bytes/4) | loaded when |
|---|---:|---:|---|
| `SKILL.md` | 41,177 | **~10,294** | **EVERY browser task** |
| `README.md` | 55,526 | ~13,882 | on demand (already) |
| `browser` CLI | 29,340 | ~7,335 | never loaded (executed) |

Section-by-section measurement of `SKILL.md` (byte spans, measured this session):

| bytes | lines | section |
|---:|---|---|
| 911 | 1-12 | frontmatter (name + description) |
| 920 | 13-28 | Quick start / binary path |
| 1,681 | 29-59 | ⚠ The LOADED extension may be older than this doc |
| 848 | 60-74 | `eval` gotchas — a `null` result… |
| 395 | 75-82 | The user is USING this browser |
| 801 | 83-95 | Concurrency / don't do this |
| 618 | 96-108 | `# browser — drive the live Brave session` (preamble) |
| **9,145** | 109-173 | **Entrypoint (the op table + `--frame` prose)** ← largest |
| 3,414 | 174-225 | Frame ops (webNavigation+scripting) & CDP ops (debugger) |
| 1,682 | 226-259 | The X-server fallback (xdotool/maim) |
| 2,016 | 260-295 | Driving a throttled/backgrounded SPA (`activate`) |
| 3,579 | 296-370 | Diagnosing a CSS/layout bug (hit-test playbook) |
| **4,606** | 371-436 | **`browser agent "<goal>"`** ← 2nd largest, at 61% depth |
| 2,994 | 437-480 | Per-session tab isolation |
| 1,224 | 481-500 | Multiple instances (per host) |
| 379 | 501-508 | Security contract (why it's safe) |
| 633 | 509-519 | Telemetry (metadata-only) |
| 557 | 520-529 | Before you rely on it |
| 2,446 | 530-563 | Error shapes (from `/cmd`) |
| 695 | 564-574 | Gotcha: reload the unpacked extension after any change |
| 999 | 575-589 | Changing the bridge: live-verify is the ONLY gate |
| 634 | 590-602 | One-time setup |
| **41,177** | | **total** |

## A.2 Proposed split — lean core + on-demand references

**Every gotcha is PRESERVED. Nothing is deleted — only relocated, with an explicit
trigger line in the core telling the reader when to load it.**

### A.2.1 Core `SKILL.md` budget

**Budget: 12,000 bytes / ~3,000 tokens as a hard CEILING. Draft below lands at ~8,400
bytes / ~2,100 tokens.**

Justification for 12,000 as the ceiling (not smaller, not larger):
- The frontmatter alone is 911 bytes and is non-negotiable (it is the skill's trigger text).
- The op table cannot shrink below ~2,600 bytes without losing an op name or its
  one-line "what it does", and an op the reader doesn't know exists is an op they
  re-derive expensively or replace with a worse one.
- The **silent-wrong-result** gotchas MUST stay in the core, because a reader only learns
  they need the reference file *after* they have already been misled. There are exactly
  four of these: `eval` takes one expression (silent `null`); page-CSP blocks `eval`
  (silent `null`, notably GitHub); a background tab returns a shell-only DOM
  (indistinguishable from a broken site); a stale extension returns `unknown_op` and reload
  ↻ is unreliable. Anything that produces a **loud** error can live in a reference file,
  because the error text itself is the trigger to go read it.
- The agent-first decision rule is new core content (~1,200 bytes) and is the whole point
  of Part B.
- 12,000 leaves ~3,600 bytes of headroom above the draft for future core-worthy gotchas
  without needing another restructure.

### A.2.2 Core outline (the actual proposed structure)

```
frontmatter (unchanged)                                              ~911 B
## Quick start — orient first                                        ~700 B
    BB= path; whoami; health; the orientation rule (both hosts are 'nixos')
## FIRST DECISION: agent or direct?          ← NEW, see Part B      ~1,200 B
    the decision rule + one worked example each side
## Ops (one line each)                                               ~2,600 B
    table: op | what it does | ⚠marker
    ⚠markers point at the reference file, e.g. "⚠frames→ref/frames.md"
## Four traps that return a WRONG answer silently                    ~1,100 B
    1. eval takes ONE expression → wrap in (function(){…})()
    2. page CSP blocks eval (GitHub) → use text/html
    3. a backgrounded tab is throttled → shell-only DOM → activate
    4. a stale extension answers unknown_op; ↻ is unreliable → full Brave restart
## This is the user's live session                                     ~395 B
## Concurrent drivers → each `open` + thread its own --tab              ~350 B
## Before you rely on it (health; confirm it's the AUTHENTICATED page)  ~400 B
## Reference files — load ONE only when its trigger fires              ~750 B
                                                                  ≈ 8,406 B
```

### A.2.3 Reference files and their trigger conditions

All under `scripts/browser-bridge/reference/` (skill dir), pointed at from the core.

| file | absorbs (bytes) | **load it when…** |
|---|---:|---|
| `ref/frames-cdp.md` | Frame ops & CDP 3,414 + the `--frame` picking prose lifted out of Entrypoint (~1,900) = **~5,300** | you need to read/drive INSIDE an iframe; you saw `ambiguous_frame` or `frame_not_found`; you need to know why in-frame input is synthetic; you hit nested-OOPIF `frame_not_found` |
| `ref/spa-activate.md` | 2,016 | a read came back empty/half-built, or `data.hidden:true`, or an SPA is stuck "Loading…"; before driving any heavy JS app |
| `ref/css-hit-test.md` | 3,579 | you are diagnosing a CSS/layout/paint-order/z-index bug — "it's there but you can't see/click it" |
| `ref/x-fallback.md` | 1,682 | CDP `screenshot` is unsatisfactory and you must capture the raw X window (needs `DISPLAY`/`XAUTHORITY`) |
| `ref/agent.md` | 4,606 − ~1,200 promoted to core = **~3,400** | you are running `browser agent` and need flags, guardrails, the RCE-fix rationale, the domain-deny caveat, or prereqs; or the agent returned `blocked` |
| `ref/tabs-instances.md` | per-session tabs 2,994 + instances 1,224 = **4,218** | you got `ambiguous_instance` / `unknown_instance` / `superseded` / `owned_tab_gone` / `no_owned_tab`; or you are designing a multi-session or multi-profile workflow |
| `ref/errors.md` | error catalogue 2,446 + reload gotcha 695 = **3,141** | any op returned an error you don't recognise — look up the exact string |
| `ref/security-ops.md` | security contract 379 + telemetry 633 + live-verify gate 999 + setup 634 = **2,645** | you are MODIFYING browser-bridge (the live-verify gate is mandatory); or the user asks how/whether it's safe; or first-time setup |

**Design note on the trigger lines:** each is phrased as an *observable symptom* (an error
string, an empty read, a visible behaviour), not a topic. A topic-phrased trigger ("read
this to understand frames") gets skipped; a symptom-phrased one ("you saw
`ambiguous_frame`") fires deterministically. The error catalogue is deliberately the
catch-all: the core says "any unrecognised error → `ref/errors.md`", so no error string can
strand a reader.

## A.3 Estimated saving per browser task — arithmetic

```
Baseline SKILL.md                     41,177 B  → 41,177/4 = 10,294 tokens
Proposed core                          8,406 B  →  8,406/4 =  2,102 tokens
Gross saving per task                 32,771 B  →              8,192 tokens
```

Net of reference loads. I have no measurement of how often a reference will be needed, so
I state the assumption explicitly: **assume 30% of browser tasks load exactly one
reference file, at the mean reference size of (5,300+2,016+3,579+1,682+3,400+4,218+3,141+2,645)/8 = 3,248 B ≈ 812 tokens.**

```
Expected reference cost = 0.30 × 812 = 244 tokens
NET saving per browser task = 8,192 − 244 = ~7,950 tokens
```

Sensitivity — this holds up under pessimistic assumptions:
```
if 100% of tasks load one reference:  8,192 − 812   = 7,380 tokens saved
if 100% load TWO references:          8,192 − 1,624 = 6,568 tokens saved
if every task loaded ALL eight:       8,192 − 6,496 = 1,696 tokens saved  (still positive)
```
Even the absurd worst case is net-positive, which is the property that makes progressive
disclosure the right call here rather than a gamble.

**Caveat I want on the record:** the `bytes/4` token estimate is a standard rule of thumb,
not a measurement. For English prose with markdown tables it is typically accurate to
±15%. All figures above should be read as ±15%.

## A.4 Other token levers

Ranked by expected saving. Each shows its arithmetic and a risk note.

---

### Lever 1 — `getHtml` has NO byte cap while `text` has one (VERIFIED IN CODE)

**Finding.** `extension/protocol.js` defines `TEXT_MAX_BYTES_DEFAULT = 32 * 1024` and
`normalizeText()` applies it to every `text` read. The `getHtml` path
(`service_worker.js`: `func: () => document.documentElement.outerHTML`) applies **no cap at
all** — there is no `maxBytes` handling on the html op anywhere in the extension, server,
or CLI. Confirmed by grep: `--max-bytes` exists only on the `text` subcommand in the CLI
(`browser` L417-434).

**Arithmetic.** Telemetry: `getHtml` 57 calls, `text` 100 calls over 3 days.
- A `text` read is bounded: ≤ 32,768 B → ≤ **8,192 tokens**, hard ceiling.
- An `html` read is unbounded. A modern SPA's `outerHTML` is routinely 300 KB–1.5 MB.
  At 400 KB: 400,000/4 = **100,000 tokens in one call.** At 1.5 MB: **375,000 tokens** —
  which exceeds most context windows outright.
- The `~98% smaller` claim in `protocol.js` L10 implies a ~50:1 ratio, consistent with
  400 KB html ↔ 8 KB text.

**One uncapped `html` on a heavy page costs more than 12× the entire SKILL.md saving from
§A.3.** This is the single largest *variance* in the system.

**Proposal.** (a) Give `html` the same `--max-bytes` flag, **defaulting to 65,536**
(2× the text cap — html is legitimately denser), `0` = uncapped, with the same truncation
note appended. (b) When `html` is called with no selector on a page whose `text` would fit
under the cap, print a one-line **stderr** nudge: `hint: 'text' would have returned ~N B
(vs ~M B of html) — use text unless you need markup`. stderr keeps stdout JSON unbroken
(the CLI already uses this pattern for the `hidden:true` warning, `browser` L340).

**Expected saving.** If half of the 57 `html` calls could have used `text` or a cap:
`28 × (100,000 − 8,192) ≈ 2.6M tokens / 3 days`. I flag this number as **directionally
right but not measured** — I have no per-call byte log. The defensible claim is the
structural one: an unbounded op exists next to a bounded one, and the unbounded one has no
guardrail.

**Risk.** Medium-low. Truncating html can cut off the element someone needed — mitigated
by the appended truncation note (same contract as `text`) and `--max-bytes 0`. Real risk is
a *silently* truncated html read being parsed as complete; the truncation note must be
inside the returned data, not only on stderr.

---

### Lever 2 — `screenshot` with no path prints the base64 data URL to stdout (VERIFIED IN CODE)

**Finding.** `browser` L465-482: `resp="$(cmd_op screenshot "$full")"`; **if `$out` is
empty** (no path argument) it falls to `printf '%s\n' "$resp" | pretty` — i.e. the entire
JSON response **including `data.dataUrl`** (a `data:image/png;base64,…` string) goes to
stdout, straight into Claude's context. The base64 decode-to-file path runs **only** when a
path argument was supplied.

**Arithmetic.** A 1920×1080 PNG screenshot is typically 300 KB–2 MB; `--fullpage` on a long
document is larger. Base64 inflates by 4/3:
- 300 KB PNG → 400 KB base64. Base64 tokenizes poorly (~3 chars/token, worse than prose's
  ~4): 400,000/3 ≈ **133,000 tokens**.
- 2 MB PNG → 2.7 MB base64 → ≈ **890,000 tokens** — exceeds a 1M context on its own.

Telemetry shows **53 screenshot calls** in the window. I cannot tell from telemetry how
many omitted the path. **A single path-less screenshot costs 16× the entire SKILL.md
restructure saving.** And the data URL is *useless* in context anyway — Claude cannot see
an image from a base64 string in a bash result; it must Read the `.png` file.

**Proposal (highest leverage / lowest risk change in this document).** Invert the default:
with no path argument, write to `${TMPDIR}/browser-screenshot-<ts>.png` and print
`{"ok":true,"path":"…","bytes":N,"width":W,"height":H}`. Add `--stdout-data-url` as an
explicit opt-in for the rare programmatic caller. Update the one SKILL.md line.

**Expected saving.** Bounded by usage, but per-occurrence it is 133,000–890,000 tokens →
~40 tokens. If even 10 of the 53 calls omitted a path, that is **~1.3M–8.9M tokens** over
three days.

**Risk.** Very low. No information is lost — the file is on disk and Claude reads it with
the Read tool, which is the *only* way it could ever see the image. The only breakage is a
caller that pipes the data URL somewhere, preserved behind the opt-in flag.

---

### Lever 3 — unwrap the envelope + stop pretty-printing large payloads

**Finding.** Every op returns `{"ok":true,"result":{"id":"…","ok":true,"data":{…}}}` and
the CLI pipes it through `pretty()` = `jq .` (`browser` L179-181).

**Arithmetic.**
- *Envelope:* ~60 B of wrapper per call. Telemetry total ops ≈ 700+131+100+85+57+53+50+40+21+13+6+5+4+3 = **1,268 calls**. `1,268 × 60 = 76,080 B ≈ 19,000 tokens / 3 days`. Real but small.
- *Pretty-printing:* `jq .` adds indentation + a newline per key. On a deep object this is
  typically **+15–30%** of payload bytes. On a 32 KB `text` read: `32,768 × 0.20 ≈ 6,550 B ≈ 1,640 tokens per read`. Across 100 `text` calls (assuming they average even a quarter of the cap): `100 × 8,192 × 0.20 / 4 ≈ 41,000 tokens / 3 days`.
- *Combined:* **~60,000 tokens / 3 days ≈ 20,000/day.**

**Proposal.** (a) Default the CLI to printing **`.result.data` only**, with `--raw` for the
full envelope. Non-zero exit + a stderr message already carries failure, so `ok` in stdout
is redundant for the CLI's own callers. This *also* deletes the "results land under
`.result.data`" instruction from SKILL.md. (b) Pretty-print only when the payload is under
~2 KB; emit compact JSON above that.

**Risk.** Medium — this is a **breaking output-shape change**. `browser-agent` parses
`["result"]["data"]["tabId"]` from `open` (L211-215) and `browser-agent-parse.py` may
depend on shapes; the tests almost certainly assert on the envelope. Needs a coordinated
change + `--raw` used internally by `browser-agent`. Given it's the smallest saving of the
top three and the highest breakage risk, **I rank it third and would ship it last.**

---

### Lever 4 — a composite `read` command (open → nav → activate → text in ONE call)

**Finding.** Telemetry shows exactly this shape dominating: `nav` 131, `open` 85,
`text` 100, `activate` 13. The `activate` count being *far* below `open` is itself a
signal — most sessions `open` a background tab and read it **without** activating, which is
precisely the shell-only-DOM trap (`SKILL.md` L122).

**Proposal.** `browser read <url> [selector] [--no-activate]` = `open` → `nav` → `activate`
→ `text`, returning ONE result: `{url, title, hidden, text}`. Similarly `browser grab <url>`
= the same but ending in a screenshot-to-file.

**Arithmetic.** Each collapsed call removes a full agent round-trip. Per round-trip in
Claude's harness: the command echo (~150 B) + the JSON envelope/result framing (~80 B) +
the model's own tool-call and reasoning overhead (~100–150 tokens, conservatively). Three
calls → one saves **~2 round-trips ≈ 250 tokens**. At ~85 `open`-led sequences per 3 days:
`85 × 250 = ~21,000 tokens / 3 days`.

The *bigger* win is correctness, not tokens: it makes `activate` un-forgettable, killing
the shell-only-DOM failure that currently produces confidently-wrong reads.

**Risk.** Low — purely additive; the primitive ops stay. Risk is `activate` **stealing the
user's focus** on a task that didn't need it — hence `--no-activate`, and the composite
should skip activation when the first read already returns `hidden:false` with substantive
text.

---

### Lever 5 — make the `eval` one-expression trap a loud error instead of a silent `null`

**Finding.** `eval` is by far the most-used op (**~700 calls**, excluding the excluded
43,740-eval storm hour). `SKILL.md` spends bytes on this trap **twice** (L62-65 and again at
L366-369, "Reiterating, because it bites hardest here") — the doc itself is evidence of how
often it costs a debugging loop.

**Proposal (deterministic, per the standing preference over prose).** In the CLI, before
dispatch, detect a body that is a **multi-statement script** — contains a top-level `;`
that is not inside a string/regex, or starts with a statement keyword (`const`/`let`/`var`/
`if`/`for`/`return`) — and **refuse with a non-zero exit + the exact corrected command**:
```
browser eval: this is a script, not an expression — eval returns null (no error) for a
multi-statement body. Re-run wrapped:
  browser eval '(function(){ <your body> })()'
```
**Detect-and-refuse, not auto-wrap.** Auto-wrapping would change semantics for a
legitimate comma-sequence expression and would silently alter what the operator asked to
run — worse than the disease.

**Arithmetic.** Not a per-call byte saving; it removes retry loops. One trap costs (bad
eval + a `health` check + a re-read + the corrected eval) ≈ 4 round-trips ≈ 600 tokens,
plus the SKILL.md bytes spent warning about it twice (848 + 900 = 1,748 B ≈ 437 tokens,
which the core split already reduces to one mention). If the trap fires on even 2% of 700
evals: `14 × 600 = ~8,400 tokens / 3 days`, plus removing the second warning entirely.

**Risk.** Low-medium. False positives on an expression containing a `;` inside a string
literal — mitigated by a string/regex-aware scan, and by making the refusal overridable
with `--force-expression`.

---

### A.5 Part A summary table

| # | lever | est. saving | risk | breaking? |
|---|---|---|---|---|
| 0 | **SKILL.md progressive disclosure** | **~7,950 tok / task** | low | no |
| 2 | screenshot → file by default | 133K–890K tok **per occurrence** | very low | opt-in flag preserves old |
| 1 | cap `html` + stderr nudge to `text` | ~100K tok per avoided call | med-low | new default cap |
| 3 | unwrap envelope + compact large JSON | ~20K tok/day | **medium** | **YES** |
| 4 | composite `read`/`grab` | ~21K tok / 3 days + correctness | low | no |
| 5 | eval script-detection refusal | ~8.4K tok / 3 days | low-med | overridable |

---

# PART B — `browser agent` as the default for open-ended browsing

## B.1 THE DECISION RULE

> **Dispatch `browser agent "<goal>"` when the task is an open-ended READ** — "go find X
> and tell me Y" — where you do **not** need to see the page yourself and the answer fits
> in a few sentences. **Drive ops directly** when the task is **precise** (you already know
> the exact URL and selector/JS and it's 1–3 ops), **interactive** (click/type/submit/
> upload, driving an SPA), **diagnostic** (you must look at a screenshot or hit-test paint
> order), or touches a **high-secret authenticated page** (agent-read pages go to
> OpenRouter/DeepSeek). **When a read task is ambiguous, dispatch the agent first** —
> escalation costs ~200 tokens, so agent-first wins even at a low success rate (§B.4).

Concrete task shapes:

| **→ `browser agent`** | **→ drive ops directly** |
|---|---|
| "what are the top 3 HN stories right now" | "read the active tab" (one `text`, done) |
| "find the current price of X on this site" | "click the Grid tab in the benchmark app" |
| "does their docs page mention feature Y" | "fill in this form and submit it" |
| "summarise what's on my dashboard" | "screenshot this and tell me if the popover is covered" |
| "search their changelog for when Z shipped" | "upload this file to the input" |
| "go through these 3 pages and tell me which mentions Q" | "run this specific JS in the page and give me the value" |
| research spanning several unknown-in-advance URLs | anything on a page with secrets you would not send to a third party |

The discriminator, stated once so it doesn't get re-litigated: **do you need to SEE the
page, or do you need to KNOW something from it?** See → drive. Know → agent.

## B.2 Proposed SKILL.md wording (draft for review)

This is the `## FIRST DECISION: agent or direct?` core section from §A.2.2 (~1,200 B),
placed **immediately after Quick start** — i.e. before the op table, so it is read before
the reader has picked an op.

---

```markdown
## FIRST DECISION: agent or direct?

**For open-ended reading — "go find X and tell me Y" — reach for `browser agent` FIRST.**
It runs an autonomous cheap model (DeepSeek flash) in its OWN isolated tab and returns a
compact `{answer,evidence,steps_used,status}` — the page HTML never enters YOUR context.
A direct read of a heavy page costs you 10K–100K tokens; the agent costs ~$0.006 and
about 200 tokens of your context.

    browser agent "go to news.ycombinator.com and report the top 3 story titles"

**Use the agent when** the task is an open-ended READ and you don't need to see the page
yourself: research across pages you don't know in advance, "does this site mention X",
"what's the current value of Y", "summarise this dashboard".

**Drive ops directly when** the task is:
- **precise** — you already know the URL and the selector/JS, and it's 1–3 ops
- **interactive** — click / type / submit / upload, or driving an SPA
- **diagnostic** — you must LOOK at a screenshot, or hit-test paint order
- **secret** — pages the agent reads are sent to OpenRouter/DeepSeek. Do not point it at
  banking, credentials, private mail, or anything you wouldn't hand a third party.

The discriminator: **do you need to SEE the page, or to KNOW something from it?**
See → drive. Know → agent. When a read task is ambiguous, try the agent first — taking
over afterwards costs you ~200 tokens, so it's cheaper than deciding carefully.

**Checking the agent's answer (do this every time):**
- `status:"blocked"` → non-zero exit. Take over and drive directly.
- `status:"partial"` → it found some of it. Read `evidence`; drive directly for the rest.
- `status:"ok"` **but `evidence` is empty or has no concrete quote/URL** → treat it as
  `partial`. Verify the claim with ONE direct `text` read before you report it.

Flags and guardrails: `--instance`, `--allow-domains`, `--deny-domains`, `--steps`,
`--timeout`, `--dry-run` → `ref/agent.md`.
```

---

Note on placement: the current doc buries this at L371 of 601. Placing it at ~L30 of a
~200-line core is the single structural change most likely to move adoption.

## B.3 Where deepseek-flash will be too weak — and the harness scaffolding to bridge it

Based on `opencode/browser-agent.md` and `opencode/tools/browser.js` as they actually are.
**I have not run the model** (out of scope for this dispatch), so each item below is a
*structural* prediction from the tool surface, plus a cheap test to confirm it.

### B.3.1 Blind selector synthesis — STRUCTURAL, the clearest weakness

The agent def tells the model to **prefer `op="text"`** (L36) and warns that `html` "will
drown you" (L23). But `text` returns innerText — **all selectors are stripped**. So for any
`click`/`type` task the model must **invent** a CSS selector having never seen the markup.
Its only recourse is `html`, which the def actively discourages and which will blow its
step budget. There is no middle op.

- **Bridge (highest value): add a `links` / `interactive` op** returning a compact
  `[{text, tag, selector, href}]` list — a few KB instead of 400 KB, and it hands the model
  the exact selectors it cannot otherwise guess. This is a *typed, bounded* op, so it does
  not widen the security surface.
- **Cheap test:** `browser agent "on <page>, click the 'Docs' link and report the first
  heading"`. If it fails or falls back to `html`, confirmed.

### B.3.2 The throttled-tab trap — STRUCTURAL, and the most dangerous

The agent's tab is **always** background (the wrapper `open`s it with `active:false`). The
def tells the model to call `activate` "when a heavy JS app is stuck on 'Loading…'" (L46-49).
But a throttled SPA usually does **not** say "Loading…" — it renders a sparse but plausible
shell. A flash-class model will read the shell and report a **confident wrong answer with
`status:"ok"`**. This is the failure mode that makes a default dangerous.

- **Bridge (deterministic, do NOT leave it to the model): the bridge already returns
  `data.hidden:true` on reads from a hidden tab** (`SKILL.md` L122). Have
  `browser_tool_impl.mjs` act on it: on the first `text`/`html` where `hidden===true`,
  auto-issue `activate`, re-read, and return the re-read. The model never has to notice.
  Alternatively have the *wrapper* `activate` once immediately after the tab is opened.
- **Cheap test:** point the agent at the known-bad case already documented in SKILL.md
  (`civitai.com/apps/run/model-benchmarking`) and see whether it reports content or a blank.

### B.3.3 Cross-origin frames — two-step indirection it will skip

Reading inside an OOPIF requires `frames` → pick an id → pass `frame`. Flash models
commonly skip the discovery call, read the top frame, and report "the page is empty."
Same class of failure as B.3.2: a confident wrong `ok`.

- **Bridge:** when a `text` read returns fewer than ~200 bytes **and** the tab has >1
  frame, have the tool append a deterministic note to the returned string: *"top frame is
  nearly empty; this tab has N frames — call op=frames and re-read with `frame`."* Nudging
  the model with a fact it can act on beats hoping it read rule #2.

### B.3.4 Needle-in-a-haystack over 32 KB innerText

With a 12-step budget the model can pull up to ~384 KB of innerText. The 1M context holds
it; flash-class attention over long noisy text does not. Expect failures on dashboards and
long tables where the answer is one number.

- **Bridge:** encourage `selector` scoping (already supported on `text`) in the per-run
  message; and `maxBytes` is already exposed. Lower-confidence bridge — this one is a
  genuine model-capability limit, not a harness gap.
- **This is where an escalate-to-Claude path matters most** (§B.4).

### B.3.5 Self-reported `steps_used` is not trustworthy

The schema asks the **model** for `steps_used` (`browser-agent.md` L67). A model that
miscounts its own turns is routine. The wrapper already writes a metadata-only tool audit
(`BROWSER_AGENT_AUDIT="$AUDIT_LOG"`).

- **Bridge (trivial, deterministic):** compute `steps_used` from the audit log in
  `browser-agent-parse.py` and **overwrite** whatever the model claimed.

### B.3.6 Evidence grounding — the anti-confabulation lever

`evidence` is currently whatever the model types. Nothing checks it came from a page.

- **Bridge:** have the tool record the (truncated) strings it returned, and in the parser
  verify each `evidence` entry is a **substring** of something the tool actually returned
  (or a URL the tool actually navigated to). If not: **downgrade `ok` → `partial`** and
  append a note. This is exactly the contract already used for Layer-B session insights
  ("deterministic Python does the plumbing… an anti-confabulation contract forbids the
  model from inventing counts"), so it is a proven in-house pattern, not a new invention.
- This is what turns "a default that silently returns bad answers" into a default that
  **fails loudly**. **I consider it a prerequisite for the default flip, alongside §0.5.**

### B.3.7 Step budget

12 is likely fine for a 1–2 page read and too small for "check 3 pages". Don't tune blind:
once the audit log yields real step counts, raise the default only if runs are hitting the
ceiling. Add a `budget_exhausted:true` field so it's visible rather than inferred.

## B.4 Failure / escalation design

### Detection (deterministic, cheap)

The wrapper emits exactly one JSON object and its exit code is meaningful
(`browser-agent` L61-77): exit 0 for `ok`/`partial`, non-zero for `blocked`/errors. So:

| signal | Claude's action |
|---|---|
| non-zero exit / `status:"blocked"` | take over: drive directly from scratch |
| `status:"partial"` | read `answer`+`evidence`; drive directly for the missing part only |
| `status:"ok"` + grounded, concrete `evidence` | accept |
| `status:"ok"` + empty/vague `evidence` | **treat as `partial`** — verify with ONE `text` read |

The last row is the human-side guard for the confident-wrong-answer case; §B.3.6 is the
machine-side guard. **Ship both** — B.3.6 is deterministic and should carry the weight;
the SKILL.md rule is the backstop for whatever B.3.6 can't check.

### Cost of escalation — this is the argument for agent-first

Let `C_d` = tokens to do the task by driving directly, `C_a` ≈ 200 tokens (the bash call +
the one-line JSON result) + ~$0.006, `p` = agent success rate.

```
cost(agent-first) = C_a + (1 − p) × C_d
cost(direct)      = C_d

agent-first is cheaper whenever   C_a + (1 − p)·C_d  <  C_d
                            i.e.  C_a  <  p · C_d
                            i.e.  p    >  C_a / C_d  =  200 / C_d
```

For open-ended browsing `C_d` is realistically **10,000–100,000 tokens** (one uncapped
`html` alone is ~100K — §A.4 Lever 1). So:

```
C_d = 10,000  → agent-first wins if p > 200/10,000  = 2%
C_d = 100,000 → agent-first wins if p > 200/100,000 = 0.2%
```

**Agent-first is the right default even if the cheap agent only works 5% of the time.**
That is the strongest argument in this document, and it holds *because* escalation is
cheap — which in turn holds *only if* detection is reliable. **The whole case rests on
B.3.6 + the B.4 detection table.** If a wrong answer can pass as `ok`, the expected cost
isn't 200 tokens, it's the downstream cost of acting on bad information, which is
unbounded. **Do not flip the default without the grounding check.**

Latency is the one cost this arithmetic ignores: a `--timeout 120` agent run that fails
costs up to 2 minutes of wall clock. For an interactive session that is a real irritation
even when the tokens are free. Suggest lowering the default timeout to ~60s for the
default path (a "go read X" task that needs >60s is usually one the agent will fail anyway).

## B.5 Zero-adoption diagnosis

**Zero invocations on both hosts.** Ranked causes, with evidence:

**1. (RULED OUT) The gate fails closed.** Disproved this session: `opencode debug agent
browser-agent` on 1.18.4 resolves a browser-only tool set and the gate's verbatim predicate
returns **RC=0**. The gate is not the blocker. Killing this hypothesis matters because it
was the most plausible one and it would have changed the recommendation.

**2. (PRIMARY) The doc tells the reader it won't work on the workbench.** `SKILL.md`
L410-413: *"Different opencode versions resolve the deny differently (workbench 1.17.20,
laptop 1.18.4), so this is the one place the fail-closed property is verified at runtime…
on a version where the host-tool denial didn't take, `browser agent` refuses instead of
running the model unconfined."* A careful reader on the workbench concludes: *this will
probably refuse, don't bother.* **This is now factually false** (both hosts 1.18.4, gate
passes) but it has been sitting in the always-loaded doc. Combined with the "Prereqs" para
(L432-435: opencode on PATH, key in the auth store, BOTH the agent def AND the tool
symlinked) it reads as a fragile, probably-broken feature.

**3. (PRIMARY) There is no decision rule, and it is never framed as a default.** The
section opens *"Offload an open-ended … task"* — an **option**, not a preference. Nothing in
the doc says *prefer this*, and the ~$0.006-vs-~$0.75 cost comparison that makes the case
lives in `README.md`/the audit doc, **not in SKILL.md**. Meanwhile the op table on L117-138
lists `agent` as **one row among twenty**, next to ops the reader already knows work. Absent
an explicit rule, a model picks the deterministic primitive it understands. This is the
mechanism; #2 is the deterrent.

**4. (AMPLIFIER) Burial.** The section starts at **line 371 of 601** — 61% into a
41 KB doc — and the **Quick start (L13-28) does not mention it at all**. Anything a reader
must reach line 371 to discover is effectively opt-in.

**5. (CONTRIBUTING) Two consecutive ⚠ blocks read as "this is risky."** L424-431 is
`⚠ Privacy: pages … sent to OpenRouter/DeepSeek` immediately followed by `⚠ Domain deny is
a mitigation, not a guarantee`. Both are **correct and must be preserved**, but stacked
back-to-back with no counterweight they bias a cautious model away. The fix is not to
soften them — it's to put the *benefit* (cost + context savings) adjacent, and scope the
privacy warning to what it actually covers (don't point it at secrets) rather than leaving
it as a general aura of risk.

**6. (MINOR) No worked example beyond one HN one-liner.** L379 is the only example, and
it's inside the buried section. No example anywhere shows the *choice* between agent and
direct.

**Diagnosis in one line:** it was never a capability failure — the gate works. It is a
**documentation-position and framing failure (#2 + #3, amplified by #4)**, with a stale
factual claim (#2) actively telling readers not to try it on one of the two hosts. That is
good news: the fix is cheap and is exactly the SKILL.md restructure Part A already
proposes.

## B.6 Security interaction with the default flip

Covered in §0.5 and not repeated. The recommendation stands: **the `upload`-with-arbitrary-
path surface must be closed at the enforcement layer (`BROWSER_AGENT_ALLOWED_OPS` /
`ALLOWED_OPS_DEFAULT`) and removed from `browser-agent.md`'s tool table BEFORE the agent
becomes the default path.** Making it the default multiplies how often a prompt-injecting
page gets to talk to a cheap, credulous model that has been *told* it can upload arbitrary
local files. Audit-logging is detection, not prevention.

---

# PART C — Combined ranked implementation plan

Ordered by (value ÷ risk), with blockers first.

| # | item | effort | risk | test coverage needed |
|---|---|---|---|---|
| **1** | **Close the agent `upload` gap** — drop `upload` (and `whoami` if unused) from the agent's effective op list via `ALLOWED_OPS_DEFAULT`/`BROWSER_AGENT_ALLOWED_OPS`; remove the `upload` row from `browser-agent.md`. **BLOCKER for #5.** | S (~1h) | low | unit: `runBrowserOp({op:"upload"})` throws a refusal; assert the agent md table matches `ALLOWED_OPS_DEFAULT` (a test that catches future drift) |
| **2** | **Screenshot → file by default**, `--stdout-data-url` opt-in | S (~1h) | very low | CLI test: no-path → writes a png + prints `{path,bytes}`, no `data:` in stdout; with `--stdout-data-url` → old behaviour; with explicit path → unchanged |
| **3** | **Fix the stale opencode-version claim** in SKILL.md + README + project memory (both hosts 1.18.4, gate passes) | XS (~15m) | none | none (doc); optionally a CI check that the gate predicate passes against a recorded `debug agent` fixture |
| **4** | **SKILL.md progressive-disclosure split** (§A.2) — core ≤12 KB + 8 reference files, every gotcha relocated | M (~4h) | **low but irreversible-ish** — a lost gotcha is expensive | a mechanical check that every heading/⚠ paragraph in today's SKILL.md appears in exactly one of {core, ref/*}; **a human diff review is the real gate** |
| **5** | **Agent-default wording** (§B.2) into the new core, at position 2 | S (~1h) | med — behaviour change | manual: a fresh session given a "go find X" prompt reaches for `browser agent`; and given "click this button" does not |
| **6** | **Evidence-grounding check** in `browser-agent-parse.py` (downgrade ungrounded `ok`→`partial`) + compute `steps_used` from the audit log | M (~3h) | low | unit over canned transcripts: grounded `ok` survives; fabricated evidence → `partial` + note; model-claimed `steps_used` is overwritten. **Prerequisite for #5 being safe** |
| **7** | **Auto-activate on `hidden:true`** in `browser_tool_impl.mjs` (+ the "N frames, top frame empty" hint) | M (~3h) | med — `activate` steals focus | unit: a `hidden:true` read triggers one `activate` + one re-read, and only once per run; live-verify against the documented `model-benchmarking` case |
| **8** | **Cap `html`** (`--max-bytes`, default 65536, truncation note **in the payload**) + stderr nudge toward `text` | S (~2h) | med-low | unit: cap applied, note present in data, `0` uncaps; regression: an existing `html` caller isn't silently truncated without the note |
| **9** | **Composite `browser read <url>` / `grab <url>`** | M (~3h) | low | integration against the fake extension: one call performs open→nav→activate→text and returns one payload; `--no-activate` skips |
| **10** | **`eval` script-detection refusal** (`--force-expression` override) | S (~2h) | low-med | unit: multi-statement bodies refused with the wrapped suggestion; expressions containing `;` inside a string literal are NOT refused |
| **11** | **Add a `links`/`interactive` op** (typed, bounded) — bridges the blind-selector gap | M (~4h) | low | unit + live-verify; agent-level: a click task that fails today succeeds |
| **12** | **Unwrap the envelope** to `.result.data` by default + compact large JSON | M (~3h) | **HIGH — breaking** | full suite; `browser-agent` switched to `--raw`; every internal parser audited. **Ship last, or not at all** |

**Reminder that applies to every code item here:** per the subsystem's own rule, a green
test suite is a *prerequisite, not verification*. Items 2, 7, 8, 9, 11 all need
**live-verify against real Brave** before they can be called done.

---

# PART D — Open questions for the operator

1. **§0.5 — does opencode 1.18.4 enforce the typed `op` enum before `execute()`?**
   `browser.js`'s enum omits `upload`; `browser_tool_impl.mjs` allows it; the agent md
   advertises it. I could not determine which wins without invoking a model. **Do you want
   me to dispatch a narrowly-scoped test (a stub tool that logs whether an out-of-enum arg
   reaches `execute`, no browser, no page)?** Regardless of the answer I recommend closing
   it at the enforcement layer (item #1).

2. **Reference-file location and format.** Do you want `reference/*.md` inside
   `scripts/browser-bridge/` (symlinked with the skill), or as a `SKILL.md` + `references/`
   pair following the pattern used by the bundled skills? This affects the paths in the
   trigger lines.

3. **Envelope unwrap (item #12) — worth the breakage?** It's ~20K tokens/day but it's the
   only breaking change in the plan and it touches `browser-agent`'s own parsing. I lean
   **defer**; your call.

4. **Auto-activate (item #7) steals focus.** The agent's tab is always background, so
   auto-activating on `hidden:true` means an autonomous background agent can grab the
   operator's screen. Acceptable, or should it be opt-in per run (`browser agent --activate`)?

5. **Default `--timeout` for the agent path.** 120s is fine for a deliberate call; as a
   *default* for every open-ended read it's a 2-minute stall on failure. Lower to 60s?

6. **How should the agent's success rate be measured?** The case for agent-first is robust
   to a low `p` (§B.4) but I'm guessing at `p` entirely. Cheapest honest measurement: run
   the agent on ~10 representative real goals, record `status` + whether the answer was
   right. ~$0.06 and ~20 minutes. **Worth doing before the default flip?** I'd say yes —
   it also directly tests B.3.1/B.3.2/B.3.3.

7. **Privacy scoping.** Should the default path carry a standing `--deny-domains` list
   (bank, mail, credential managers) so the "don't point it at secrets" rule is enforced
   rather than advisory? Note the doc's own caveat that domain-deny is best-effort against
   client-side redirects.

---

## Confidence and what is guessed

- **Measured / verified in code:** all SKILL.md byte figures; the gate result on 1.18.4;
  the permission-array ordering; the `tools` map; `TEXT_MAX_BYTES_DEFAULT = 32768` and the
  **absence** of any html cap; the screenshot no-path → data-URL-to-stdout path; the
  `upload` three-layer mismatch; `pretty()` = `jq .`.
- **Rule-of-thumb:** every `bytes/4 = tokens` conversion (±15%).
- **Estimated, not measured:** typical page `outerHTML` size (300 KB–1.5 MB) and PNG
  screenshot size — these drive the Lever 1 and Lever 2 magnitudes. The *structural*
  findings (no cap; data URL to stdout) are certain; the *magnitudes* are estimates.
- **Guessed:** the 30% reference-load rate in §A.3 (sensitivity analysis provided);
  deepseek-flash's actual success rate `p` (see open question #6); the per-round-trip
  harness overhead (~100–150 tokens) used in Lever 4.
- **Not tested, deliberately:** deepseek-flash behaviour. Every §B.3 claim is derived from
  the tool surface and agent def, with a cheap confirming test named for each.
