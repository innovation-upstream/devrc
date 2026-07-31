# browser-bridge — session audit (how it was ACTUALLY used)

**Date:** 2026-07-30 · **Scope:** all Claude Code transcripts on both hosts (laptop 192.168.50.155, workbench 192.168.50.250) · **Mode:** read-only

---

## 1. Corpus & method

### Corpus selection

The bare string `browser-bridge` matches ~708 transcript files, almost all false positives (the devrc project `CLAUDE.md` names `scripts/browser-bridge/` and is injected into every devrc session). The corpus is files containing a CLI invocation:

```
grep -rlE '(browser-bridge/browser|\$BB) +(--instance|--tab|health|whoami|html|text|eval|tabs|nav|
screenshot|frames|click|type|key|upload|activate|agent|open|close)' ~/.claude/projects --include=*.jsonl
```

→ **20 files (laptop) + 26 files (workbench) = 46 candidate files.**

That grep still over-counts: it matches **documentation text** (SKILL.md/README.md quoted inside a transcript) as well as executed commands. I re-filtered by parsing the JSONL and counting only `tool_use` blocks where `name == "Bash"` and the `command` field contains an invocation, then joined each command to its `tool_result` by `tool_use_id` so every error is attributed to the op that produced it.

**After that filter: 7 of 20 laptop files and 1 of 26 workbench files contain ZERO real invocations** — they only quote the docs. Real corpus: **13 laptop + 25 workbench = 38 files, 630 invocations.**

### Use-vs-build classification

Classified by the **project directory** of the transcript, which is a clean proxy here:

| Population | Projects | Files | Invocations |
|---|---|---|---|
| **USE** — driving the browser for an unrelated task | `civit-datapacket-talos`, `civit-civitai-manager`, `homelab-talos` | 22 | **469 (74%)** |
| **BUILD** — developing browser-bridge itself | `devrc` (laptop `829b4ba7…` arc, workbench `f408ad47…`) | 15 | **147 (23%)** |
| **META** — this audit's own orchestration session | `devrc` `e339cf06…` | 1 | 14 (2%) |

⚠ **This corrects the brief's framing.** By *file count* the build arc dominates the laptop (13/13 laptop files are BUILD). By *invocation volume* the picture inverts: **74% of all real browser-bridge commands ever run were USE, and every one of them ran on the workbench.** The laptop has never driven the browser in anger — it is the development host only. Findings below are weighted accordingly.

### Sources

- **Transcripts** for *why* and *what it cost* (only source before 2026-07-28).
- **`activity.events` ClickHouse telemetry** for *what / how often*, window 2026-07-28 → 07-30 only. Every rate below **excludes the 2026-07-28 17:00 workbench storm hour** (43,740 eval events, the pathological run that motivated the rate limiter).

### Honesty about measurement quality

- Op counts, error-to-op attribution, n-gram sequences, and telemetry counts are **MEASURED**.
- The `null`-result counts are a **noisy text proxy** (a page can legitimately evaluate to `null`). I report them as a bucket but do not rank on them alone.
- Anything labelled INFERRED is my reading of the evidence, not a count.

---

## 2. Findings

### 2.1 Turn-burn: Claude misdiagnosing the bridge (highest value)

#### A. The background-tab false production incident — the single most expensive event in the corpus

Session `4acd5483-e6cb-4629-b138-d6d1aa3ae97b.jsonl` (workbench, datapacket-talos, USE, 1043 messages, 102 browser invocations — the largest single USE session).

Claude read a civitai page through an **owned background tab**, saw an empty DOM, and concluded production was broken site-wide. It then retracted:

> **"I was wrong when I said the `/apps` store is broken — it isn't, and neither is anything else.** I read a *background* tab the whole time (`visibilityState: "hidden"`). Background tabs never painted, so content never rendered and queries never fired. Everything I inferred from that — 'empty grid', 'site-wide breakage', 'hydration crash' — was an artifact of my own measurement, not production."

Corroborating measurement from the same session: `bodyLen: 214`, `trpcCount: 0`, `swControllers: false` — i.e. a shell-only DOM that Claude read as real.

**Cost:** a full false-positive incident investigation — the fabricated symptoms ("hydration crash", React error #423) were themselves generated *by* the bridge (`next.router.push` inside a hidden tab), and disproving them required pulling 23,906 Faro RUM LCP samples and a 45/45 SSR health check. This is the highest-cost failure in the entire corpus and it is a **direct consequence of the bridge's own semantics**: `open` deliberately does not foreground, and `text`/`html`/`eval` silently return the unpainted DOM with no signal that the tab never rendered.

MEASURED: the retraction and the diagnostic values. INFERRED: the turn count (the arc spans messages ~93→249 of that session, but not all are browser turns).

#### B. The stale-extension source hunt

Same session. `screenshot` failed with `not visible on-screen`; Claude did **not** treat it as the documented stale-extension symptom and instead debugged the *repo source*:

> "Extension predates the CDP screenshot path. Let me get the decisive diagnostics another way."

then, over the following turns: read `protocol.js` lines 190–240, ran `git log` on the extension dir, and finally launched a filesystem-wide hunt for a second extension copy —

> `find ~ -name "service_worker.js" -path "*extension*" …` → **`Exit code 143  Command timed out after 2m 0s`**

Only after `frames` also returned `unknown_op` did it land on the right answer:

> "`frames` → `unknown_op`. The running service worker is **still the old build** — the reload didn't take (MV3 workers are sticky, and the manifest newly gained `debugger`)."

Critically, **the user had already said "extension reloaded"** earlier in that arc — and it was still stale. This is exactly the documented `↻`-reload-is-unreliable gotcha, and Claude still burned a source-code investigation plus a 2-minute command timeout on it.

**Why the docs didn't save it (MEASURED):** SKILL.md's stale-extension section illustrates the symptom with `open`. The failures actually seen were on `frames` (×4), `text` (×2), `upload` (×1), `open` (×1). Only the **`upload`** path emits the helpful message —

> `browser: op 'upload' returned unknown_op — your loaded browser-bridge extension is OLDER than this CLI.`

— while `frames`/`text`/`open` emit the bare, uninterpretable:

> `browser: op 'frames' failed in the browser: unknown_op`

**`browser health` could not help either:** its output in that very session reads `work loaded=None   personal loaded=None`. The one command whose job is "is the extension OK" reports the loaded build as `None`.

#### C. No misdiagnosis of the *server*

Searched for server-blaming: `systemctl --user restart browser-bridge` appears in **1** laptop BUILD file and **0** workbench files; `journalctl` in 5 laptop / 4 workbench files, all BUILD-context. **No USE session ever wrongly restarted or debugged the rendezvous server.** Stated plainly: this category is clean — the failure mode is always the *extension*, never the server.

---

### 2.2 Toil — repeated hand-assembled sequences

From n-gram analysis over the ordered op sequence of every USE session:

| Sequence | Occurrences (workbench USE) | What it is |
|---|---|---|
| `eval → eval` | **105** | scrape-loop; 87 3-grams, **73 4-grams** of consecutive evals |
| `nav → eval` / `eval → nav` | **16 / 12** | navigate, then poll the page for readiness |
| `nav → eval → eval` | 11 | as above, plus a retry |
| `html → html` | 8 (5 as 3-grams) | paginating/re-reading a doc |
| `eval → screenshot` / `screenshot → eval` | 8 / 6 | screenshot, then extract what the screenshot couldn't show |
| `health → health` | 6 | redundant orientation |
| `open → activate`, `tabs → activate`, `activate → eval` | 3 each | the foreground dance before a screenshot |

**Longest observed unbroken eval chain: 27 consecutive `eval` calls** in `4acd5483…` (messages 96–123), and 18 consecutive in `agent-a5335fc72438de0d9.jsonl`.

Telemetry confirms the shape: **`eval` is 728 of ~1,280 non-storm ops (57%)**. The bridge is, in practice, a JS-injection pipe — the higher-level ops are a thin veneer.

Two concrete toil signatures worth collapsing:

1. **`nav` then poll** (28 bigram occurrences). Every one is Claude hand-writing a readiness check because `nav` returns as soon as navigation is issued. A `nav --wait-for <selector>` (or `--settle`) removes 1–3 commands per navigation.
2. **`eval 'document.body.innerText…'` as a substitute for `text`.** Seen every time `text` returned `unknown_op` (2×) *and* independently. This is a 2-command dance where 1 should do.

---

### 2.3 Errors & failure modes

Counted by joining each browser `tool_use` to its `tool_result` (USE population unless noted):

| Bucket | Count | Recovery |
|---|---|---|
| **`unknown_op` (stale extension)** — `frames` ×4, `text` ×2, `upload` ×1, `open` ×1 | **8** | Mixed. `text`→`eval` fallback was **immediate** (1 turn, twice). `frames`/`screenshot` cost the multi-turn source hunt in §2.1B. |
| **Result contains literal `null`** (multi-statement eval proxy) | ~27 | Usually 1 retry with a wrapped IIFE. *Noisy metric.* |
| **`eval` timeout** (`timeout waiting for the extension to answer (is Brave focused / responsive?)`) | **4** (all in `314e6d60…`) | Retried; no diagnosis attempted. |
| **`screenshot` not-visible** | **3** | Poor — triggered §2.1B. |
| **Worktree-guard refusal of `eval`** (see §2.4) | **18** across 6 files | Fell back to non-eval ops or gave up on the check. |
| **`rate_limit` / throttled** | 2 (`whoami`) | Immediate, no cost. |
| **`ambiguous`** | 1 | Immediate. |
| **`element_not_found:input[type=file]`** (`upload`) | 1 | Immediate. |
| **CLI `usage:` error** (`upload` arg order) | 1 | Immediate. |

BUILD population adds: `frame_not_found` ×2 (both deliberate negative tests), `owned_tab_gone` ×1, `activate` Traceback ×1, `nav` timeout ×1.

**Not observed anywhere in the corpus:** auth/token failures, `no_extension` in a USE session, wrong-instance/wrong-host confusion causing a visible error, CSP-blocked eval on GitHub. Telemetry records `*_unknown_instance` ×7 and `no_extension` ×3 in the window, but **no transcript shows them costing a turn** — they were absorbed silently.

---

### 2.4 Tool gaps

#### Gap 1 — worktree-isolated subagents cannot use `browser eval` (NEW, high impact)

**18 refusals across 6 workbench USE subagent transcripts.** The harness guard rejects it:

> "This agent is isolated in the worktree `…/.claude/worktrees/agent-a1861933e709b89ad`, but **this command runs a string through eval, which can't be verified to stay inside the worktree**; run the command directly instead. Refusing to run it."

Files: `agent-a2b9359565aab4a45` (3), `agent-a6d4d9bb639600e4f` (2), `agent-a2ee1bcdbca1ed9da` (1), `agent-a55c5ae81bb9de137` (1), `agent-a5ab3da52fb411aeb` (1), `agent-ac41edb5184f4dec9` (1) — 9 refusal *events*, 18 string occurrences.

This collides head-on with the standing global rule that **any file-modifying subagent runs in an isolated worktree**. The result: the default agent configuration cannot use the bridge's single most-used op (57% of all traffic). Immediately after one refusal, the agent fell back to `html` and then abandoned the check.

#### Gap 2 — no way to read a *rendered* page without stealing focus

The bridge offers `activate` (best-effort, and the source itself concedes "activating it does NOT guarantee its Brave WINDOW is raised/composited — Chrome can't force i3 to raise a window") or nothing. There is no `open --foreground`, no `--wait-for-paint`, and no op that *reports* `visibilityState`. §2.1A is the direct consequence.

#### Gap 3 — X-server fallbacks are BUILD-only, not a USE workaround

`xdotool` appears in 199 workbench / 61 laptop occurrences and `maim` in 60 / 34. I checked the file distribution: on the laptop all 5 files are the BUILD arc and the meta session (`agent-a76859b5ddd8a7323` alone has 12). **These are the browser-bridge test harness driving i3/X to verify `activate`, not a session working around a missing op.** Stated plainly: there is no evidence of a USE session falling back to xdotool/maim/scrot because the bridge couldn't do something. Category is smaller than the brief anticipated.

---

### 2.5 Docs-vs-reality drift

1. **opencode version gate (CONFIRMED, already known).** Memory recorded workbench at 1.17.20 with the `browser agent` fail-closed gate refusing to run there. Verified live in `e339cf06…`: `laptop 1.18.4`, `workbench 1.18.4`. The blocker is stale; the memory file was corrected during this audit's parent session.

2. **`browser health` reports `loaded=None`.** MEASURED in `4acd5483…`: `connected: True  work loaded=None  personal loaded=None  repo manifest version: 0.2.0`. The docs position `health` as the "is the extension connected/OK" check, but it cannot report the loaded build — the exact fact needed to diagnose the #1 failure mode. Docs imply a capability the output does not deliver.

3. **The stale-extension symptom is documented for `open` only.** Real failures were on `frames`, `text`, `upload`, `open`. Only `upload` emits the "your loaded extension is OLDER than this CLI" hint (MEASURED, §2.1B).

4. **Orientation order.** `CLAUDE.md` says "run `browser whoami` FIRST". Measured first-op across the 22 USE sessions: **`health` is the opening op in 13 of them; `whoami` in 2** (both in one civitai session). The `whoami`-first instruction is not being followed — and SKILL.md's own Quick-start block, which is injected verbatim at the top of every browser task, leads with `$BB health`. The two docs contradict each other, and the injected one wins.

5. **Possible manifest-version inconsistency (INFERRED, low confidence).** In `4acd5483…`, `browser health` printed `repo manifest version: 0.2.0` while a direct read of `extension/manifest.json` in the same session printed `version 0.1.0`. These reads are separated in time and the extension files were modified mid-session (`Jul 29 18:58`), so this may be an artifact. Flagging for a check, not asserting a bug.

6. **`screenshot --fullpage` semantics.** Claude reasoned: "`--fullpage` bypasses `captureVisibleTab` entirely in the current source, so a CDP failure could never produce that readback message." It still did — because the *loaded* build was old. The docs describe `--fullpage` as CDP-backed with no caveat that the guarantee is void on a stale worker.

---

### 2.6 Adoption — which ops are alive

Telemetry, excluding the storm hour (workbench + laptop):

| Op | workbench | laptop | Total |
|---|---|---|---|
| `eval` | 641 | 87 | **728** |
| `nav` | 117 | 6 | 123 |
| `text` | 91 | 9 | 100 |
| `open` | 68 | 23 | 91 |
| `getHtml` | 43 | 14 | 57 |
| `key` | 47 | 0 | 47 |
| `tabs` | 38 | 19 | 57 |
| `screenshot` | 43 | 11 | 54 |
| `close` | 24 | 21 | 45 |
| `frames` | 6 | 17 | 23 |
| `activate` | 15 | 3 | 18 |
| `click` | 2 | 4 | 6 |
| `release` | 2 | 2 | 4 |
| `upload` | 2 | 1 | 3 |

**15 ops carry all traffic. Dead in the telemetry window: `type`, `agent`** — plus every op in the CLI's 34-op surface that never reaches the extension.

**`browser agent`: zero real dispatches, confirmed.** My raw regex initially credited ~10 `agent` hits on the laptop; on inspection **all are path false positives** from the sibling files `browser-agent`, `browser-agent-guard`, `browser-agent-parse.py` (13 + 8 + 10 mentions). The parent session's independent grep for `browser agent "<task>"` returned **0 on both hosts**. It was built, RCE-hardened, documented at ~$0.006/task vs ~$0.75 in Claude context — and never once used.

**Why (INFERRED, no direct evidence):** nothing in any transcript shows a session considering and rejecting `browser agent`. The absence is consistent with it being buried inside a 41 KB SKILL.md whose injected quick-start block shows only `health`/`open`/`html` — an agent reading the top of the skill never learns the op exists. `type` is likely dead for the same reason plus `key` covering the same ground (47 uses).

**Also notable:** `click` was used **6 times total** across all history while `eval` was used 728 times. Sessions synthesise clicks in JS rather than using the trusted-input op. In `agent-a55c5ae81bb9de137` both `click` calls returned `null` results.

---

## 3. Ranked improvement opportunities

Score = frequency × cost-per-occurrence. Ordered by total expected value.

| # | Opportunity | Label | Frequency (measured) | Cost/occurrence | Evidence |
|---|---|---|---|---|---|
| **1** | **Surface `visibilityState` / never-painted status in every `text`/`html`/`eval` envelope**, and add a loud warning when a read comes from a tab that has never painted. Optionally `open --foreground` / `--wait-for-paint`. | `extension-change` + `cli-change` | 1 catastrophic occurrence, but it is a *systemic* property of the default `open` path used 91× | Catastrophic — a retracted false production-incident diagnosis | §2.1A |
| **2** | **Map every `unknown_op` to the stale-extension message** the `upload` path already emits, with the remediation inline ("a FULL Brave restart, not ↻"). One-line change in the CLI's error handler. | `cli-change` | 8 failures | 1 turn (best) to a multi-turn source hunt + a 2-min timeout (worst) | §2.1B, §2.3 |
| **3** | **Make `health` report the LOADED extension build** (version in the SW hello, compared to the repo manifest). It currently prints `loaded=None`. Turns the #1 failure mode into a one-command diagnosis. | `extension-change` | Every session opens with `health` (13/22 USE sessions) | Removes the need for #2 entirely | §2.1B, §2.5.2 |
| **4** | **Unblock `eval` for worktree-isolated subagents** — e.g. `browser eval --file <path>` (no inline string for the guard to reject), or a documented sanctioned pattern. | `cli-change` + `doc-fix` | 9 refusal events / 6 sessions; blocks the op that is 57% of all traffic, under the *standing default* agent config | Agent abandons the check or degrades to `html` | §2.4 Gap 1 |
| **5** | **`nav --wait-for <selector>`** (and/or `--settle`) to collapse the navigate-then-poll dance. | `new-op` | 28 `nav↔eval` bigrams | 1–3 commands each | §2.2 |
| **6** | **Rewrite SKILL.md's injected quick-start.** It is 41 KB (~10K tokens) loaded on every browser task, leads with `health` (contradicting `CLAUDE.md`'s `whoami`-first rule), and never mentions `browser agent`. Lean core + reference files. | `doc-fix` | Every browser task, both hosts | ~10K tokens/task; causes #7 and the `whoami` drift | §2.5.4, §2.6 |
| **7** | **Decide `browser agent`'s fate** — promote it to the quick-start as the default for open-ended browsing, or delete it. Zero uses since it shipped. | `doc-fix` (or removal) | 0 uses vs a built, hardened, documented feature | Sunk cost + maintenance + test surface | §2.6 |
| **8** | **Test gap: no test asserts the `unknown_op`→stale-extension message on `frames`/`text`/`open`.** The `upload` path has it; the others regressed silently into bare `unknown_op`. | `test-gap` | Latent behind 8 real failures | — | §2.1B |
| **9** | **Test gap: nothing exercises a hidden/unpainted tab.** The audit that shipped the CDP screenshot path did not catch that background reads return a shell-only DOM with no signal. | `test-gap` | Latent behind finding #1 | — | §2.1A |
| **10** | **Fix `screenshot` docs** to state that `--fullpage`'s CDP guarantee is void on a stale worker, since that exact reasoning misled a session. | `doc-fix` | 3 failures | Contributed to §2.1B | §2.5.6 |
| **11** | **Investigate the `eval` timeout cluster** — 4 timeouts all in one session (`314e6d60…`), all `is Brave focused / responsive?`. Possibly the same background-tab root cause as #1. | (investigate) | 4 | 1 retry each | §2.3 |
| **12** | **Check the `0.2.0` vs `0.1.0` manifest-version report.** Low confidence, cheap to settle. | `cli-change`? | 1 observation | — | §2.5.5 |

---

## 4. What I could NOT determine

- **Whether the ~27 `null` results were genuine failures.** I used a text-match proxy on the tool result; a page can legitimately evaluate to `null`. Settling this needs per-case reading of ~27 windows. The multi-statement-eval-returns-null mode is real and documented, but I cannot give it a trustworthy count.
- **Exact turn counts for the two turn-burn incidents.** I can bound the message-index range of each arc (§2.1A spans messages ~93→249 of a 1043-message session) but not all messages in the range are browser turns, and I declined to read the transcript whole.
- **Anything before 2026-07-28** from telemetry — the table starts then. All earlier history is transcript-only, and transcripts do not record ops that succeeded silently in a `for` loop.
- **Why `browser agent` was never used.** No transcript shows it being considered and rejected. My SKILL.md-burial explanation is INFERRED from the injected quick-start's contents, not observed.
- **Whether the telemetry's `*_unknown_instance` ×7 and `no_extension` ×3 cost anything.** They appear in `activity.events` but I found no matching transcript turn, which suggests they were absorbed by retry logic or occurred outside a Claude session.

---

*All counts in this document derive from parsing `tool_use`/`tool_result` pairs in the 38-file corpus and from `activity.events` with the 2026-07-28 17:00 workbench storm hour excluded. Sections are complete; no section was truncated for budget.*
