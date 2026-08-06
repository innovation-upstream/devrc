# Browser-Bridge: Usage Audit, Token-Efficiency, and opencode "browser-agent" Design

_2026-07-28. Read-only analysis. Repo `/home/zach/workspace/devrc`, both hosts (laptop + workbench `192.168.50.250`). Numbers are **measured** from ClickHouse `activity.events` (source=`browser-bridge`), systemd journals, transcripts, and code unless labelled **(est.)**. Data window 2026-07-28 → 2026-07-29 02:31 UTC._

## Executive summary — top 5 actions

1. **The #178 backstop is deployed but has NEVER been exercised by a real storm — treat it as PRODUCTION-UNVERIFIED.** The 44,043-eval flood ran ~13/sec on 2026-07-28 17:xx UTC; the workbench service carrying the rate-limiter (re)started 20:01 CDT = 01:01 UTC 29th, ~8h *after*. Zero `throttled` events on either host (`Counter({'dispatch':44154,'cmd_ok':44154,'reject':1})`). The guard exists + is unit-tested but has never fired under load. **Action: synthetic load-test (`for i in $(seq 40); do browser eval '1' & done`) to confirm a real 429.**
2. **Ship `browser text [selector]`.** `getHtml` returns full `outerHTML`, no truncation (`service_worker.js:87-94`); ~139 KB ≈ ~35 K tokens. innerText → ~2 KB ≈ ~500 tok (**~98% cut**). Biggest single per-call lever.
3. **Build the opencode browser-agent — feasible today, cheap.** Both hosts have opencode with an **`openrouter` key in `~/.local/share/opencode/auth.json`** (the "no key" caveat was looking in env files; it's in opencode's auth store). `deepseek/deepseek-v4-flash` is real ($0.14/$0.28 per M, 1M ctx), listed by opencode. `opencode run --format json -m … --agent … --auto` is the headless primitive.
4. **Real browser-driving is ~nonexistent outside one pathological loop.** Laptop's 29 events are the bridge's own dev/test; workbench's 44K are the persona-fleet storm. No organic day-to-day usage yet — design is for latent demand.
5. **The subagent-shared-session-id gap is the top remaining correctness risk** and is exactly what the opencode agent (own isolated tab + #178 rate-limit) sidesteps. Prioritise it as the MVP.

---

## Part A — Usage audit (both hosts, measured)

### A.1 Volume & op distribution
| host | total | eval | nav | screenshot | getHtml | tabs | open | close | release |
|---|---|---|---|---|---|---|---|---|---|
| **workbench** | **44,140** | 44,043 | 50 | 14 | 14 | 13 | 6 | 0 | 0 |
| **laptop** | **29** | 1 | 4 | 0 | 11 | 3 | 4 | 4 | 2 |

`key` on workbench: `""` (implicit single-instance) = 44,049; `work` = 91 → the storm ran with **no `--instance`, no `open`** (the exact unisolated pattern SKILL.md warns against).

### A.2 The eval storm (the only heavy real usage)
- Sustained ~770–815 eval/min all afternoon 2026-07-28 17:xx UTC = ~13/sec.
- Driver: unisolated persona-fleet hammering the single shared active tab. Domain rotation confirms an un-pinned tab: civitai.com 30,327 · 192.168.50.250 (initiatives viewer) 5,793 · the client Grafana host 3,979 · discord.com 2,135 · empty 1,684 · google/github/youtube/claude.ai tails.
- **Not in transcripts** — the 44K storm was an external loop/persona process, not a logged Claude-Code session → un-attributable at session level (predates the coarse `sess` hash).
- Latency: eval avg=10.3ms p50=5 p95=23 p99=35 **max=5,525ms** — the queue-saturation balloon, produced with NO throttling (predates the guard).

### A.3 Outcome / error catalogue (every non-ok)
Only 2 non-ok in telemetry, both laptop: `close no_owned_tab` ×2 (domains a.test/b.test → **test-suite artifacts**, not organic friction — the intended `release → close(no_owned_tab)` path). The laptop journal `cmd_ambiguous` is from work/personal test instances. The workbench journal `reject unauthorized path:"/"` is a bare unauthenticated probe (auth gate working). **Net: no organic error patterns — the surface is untested-in-anger or exercised only by its own harness.**

### A.4 Throttle backstop status (#178) — NOT firing
Zero `throttled`/`sess`/`reason` rows either host; zero journal `throttled` lines. Deployed workbench `server.py` *contains* the guard (active since 20:01 CDT 28th; defaults 5/sec, burst 20, queue 32); post-restart traffic is 1–3/min, orders under the limit. **Verdict: deployed + unit-tested but PRODUCTION-UNVERIFIED. Don't claim it "protects against the storm" until a synthetic burst produces a real 429.**

### A.5 Toil / gaps — SKILL (`browser`)
| sev | finding | evidence |
|---|---|---|
| High | **Sibling subagents share session id → no auto-isolation.** Workaround (each subagent `open`+`--tab` on every op) is manual/verbose/forgettable — the gap the storm embodies. | `browser:75-84`, `server.py:127-150` |
| Med | **No cheap-read op** — `html` is all-or-nothing full outerHTML; no `text`/`--selector`/`--max-bytes`. | `browser:283-285`, `service_worker.js:87-94` |
| Med | **`eval` return unbounded** — structured-cloned raw; `innerHTML` eval as costly as `html`. | `service_worker.js:96-127` |
| Low | Discoverability fix **in place** (symlink + SKILL.md path-first). Resolved. | SKILL.md:6-11 |
| Low | `close` after release/expiry → `no_owned_tab` exit-1 reads as an error; a "nothing-to-close is success" flag would cut noise. | `server.py:401-403` |

### A.6 Toil / gaps — EXTENSION (`service_worker.js`)
| sev | finding | evidence |
|---|---|---|
| High | **Single serial connection ceiling (~13/sec)** — one long-poll, one in-flight command; #178 caps rather than widens it. | `server.py:157-166`, `service_worker.js:237-320` |
| Med | **Screenshot foreground-flicker under concurrency** (activate→capture→restore). | `service_worker.js:145-170` |
| Med | **Mandatory manual reload after any `extension/` edit** (Brave won't hot-reload unpacked). Real deploy toil. | `README.md`, SKILL.md |
| Med | **MV3 SW lifecycle risk** — keepalive = pending `/poll` + 1-min alarm; eviction between a 204 and next poll → silent gap until `/health` staleness (40s). | `service_worker.js:326-335` |
| Med | **`<all_urls>` host permission** — maximal scope (justified, flagged scope-down-later). | `manifest.json:7` |
| Low | `owned_tab_gone` handling solid; no silent-wrong-tab path found. | `service_worker.js:73-82`, `server.py:820-833` |

### A.7 Subagents vs top-level
Laptop transcripts' 10 subagent files referencing the CLI are the **build/audit subagents of this work**, not browser-drivers. **~0% of real browser-driving is organic top-level; ~100% of volume is the one external storm-loop (workbench) or the bridge's own dev/test (laptop).** The subagent-isolation gap is latent — the storm shows what it looks like when it bites.

---

## Part B — Token-efficiency proposals (ranked by savings × ease)
| # | proposal | before → after (est.) | effort | risk |
|---|---|---|---|---|
| B1 | **`browser text [selector]`** (innerText/Readability) | 139KB ≈ 35K tok → ~2KB ≈ 0.5K tok (~98%) | S | Low |
| B2 | **opencode offload (Part C)** — move nav→read→iterate off Claude's context | ~50K Claude in-tok → ~0 in Claude; ~300-tok result returns | M | Med |
| B3 | **`--max-bytes` / server truncation** for html & eval (default ~32KB) | caps worst-case; 35K→~8K tok | S | Low |
| B4 | **`--selector` scoping** on html/eval | 35K → ~1–3K tok | S | Low |
| B5 | **Structured extractors** (`links`/`forms`/`meta` → JSON) | html+parse → ~200-tok JSON | M | Low |
| B6 | **`eval` result cap + summary** | bounds accidental innerHTML evals | S | Low |

B1+B3+B4 turn the read path from ~35K to sub-2K tok for the common case; **B2 removes the iterative multiplier** (each nav→read cycle re-costs Claude today) — the structural win.

---

## Part C — opencode "browser-agent" DESIGN (design only)

**Locked:** model `openrouter/deepseek/deepseek-v4-flash` · full autonomy · own isolated tab (#175 `open`+`--tab` + #178 rate-limit). **Privacy:** page content goes to OpenRouter/DeepSeek — consciously accepted; don't route high-secret pages casually.

### C.1 Feasibility — VERIFIED
- opencode present: laptop 1.18.4, workbench 1.17.20.
- **OpenRouter key ALREADY on both hosts** in `~/.local/share/opencode/auth.json` (not env files — that's the correct store). No new key needed unless a dedicated/budget-capped one is wanted.
- Slug: `deepseek/deepseek-v4-flash` on OpenRouter (in $0.14/M · out $0.28/M · ctx 1,048,576); opencode lists `openrouter/deepseek/deepseek-v4-flash`. Fallbacks: `deepseek/deepseek-v3.2` ($0.27/$0.40) → `deepseek/deepseek-chat-v3.1`.
- Headless: `opencode run [msg] --format json -m <slug> --agent <name> --auto`.
- Custom tools: (a) built-in `bash` tool calling the `browser` CLI, permission-gated; or (b) a TS custom tool. **Recommend (a).**
- **Live model call UNTESTED** (would spend money) — invocation surface + key existence verified, not a real completion.

### C.2 Interface — recommend opencode's `bash` tool calling the existing `browser` CLI (least new code)
```
browser agent "<goal>" [--instance K] [--allow-domains …] [--deny-domains …] [--steps N] [--timeout S] [--dry-run]
```
Wrapper around `opencode run`: (1) `open about:blank` → capture `tabId` (agent's OWN tab); (2) `opencode run --format json -m openrouter/deepseek/deepseek-v4-flash --agent browser-agent --auto "<harness+goal+tabId>"`; (3) the `browser-agent` agent is permission-locked so its ONLY capability is `bash` matching `<abs>/browser --tab <tabId> *` (edit/read/webfetch/websearch denied) — it can run browser ops **only against its own tab**; (4) on exit `close` the tab, parse opencode's final JSON, return a compact structured result — never raw HTML.

**Agent def** (`~/.config/opencode/agents/browser-agent.md`): `mode: subagent`, `model: openrouter/deepseek/deepseek-v4-flash`, `temperature: 0.1`, `steps: 12`, `permission: {edit:deny, read:deny, webfetch:deny, websearch:deny, bash:{"*":"deny","<abs>/browser --tab *":"allow"}}`.

**Harness scaffolding ("bridge the gap"):** strict single-tool contract; forced own-tab (wrapper injects `--tab`); step budget (`steps:12` + wall-clock `--timeout` kill); **required final-answer schema** `{"answer","evidence":[…],"steps_used","status":"ok|partial|blocked"}`; retry-on-malformed once (`--continue`); prefer `browser text` over `html` (needs B1); domain allow/deny in prompt + defensively enforced by the wrapper.

### C.3 Guardrails (recommended defaults; user chose full autonomy)
Own-tab isolation caps blast radius (structural — can't touch the active tab); #178 rate-limit throttles runaways (verify it fires first); log every op (telemetry + opencode JSON transcript); step + wall-clock budget with hard kill; `--allow/deny-domains`; `--dry-run` intercept for form-submits (off by default); never the active tab (structural).

### C.4 Cost / latency **(est.)**
Per task ≈ open + 5–8 model steps + close ≈ 25–40K in-tok, 1–2K out. **DeepSeek v4-flash: ~$0.005–0.008/task, ~10–25s wall-clock.** Same loop in Claude/Opus ≈ ~$0.75+ **and burns the main session's context on transient HTML**. Offload ≈ ~100× cheaper on $ and frees Claude's context (only a ~300-tok result returns). Thesis holds.

### C.5 Complete test plan (design-first)
**Unit (opencode mocked — no live model in CI):** arg parsing; own-tab lifecycle (open→capture→inject→close on every exit path via a fake `browser` shim); final-answer schema parse + exactly-one retry then `blocked` (no infinite retry); guardrail enforcement (`--steps`, `--timeout` kill, `--deny-domains` blocks nav); failure modes (opencode missing → clean error, no orphaned tab; non-zero exit surfaced; `owned_tab_gone` mid-run → `partial`, still closes). **Integration/smoke:** fake `opencode` emitting a canned `--format json` tool-call stream against the in-process fake extension; assert ops routed to the owned tab, tab opened+closed once, result parsed. **Manual live (real key, real Brave):** `browser agent "go to news.ycombinator.com and report the top 3 titles"` → new background tab (not active), navigates+reads, returns compact schema, active tab untouched, tab closed; plus a 40-op burst → real 429/`throttled` in journal (closes A.4). Repo conventions: stdlib unittest + `node --test`, reuse fake extension, no live model in CI.

### C.6 Recommendation — **GO**
Feasibility confirmed; reuses the audited CLI + #175/#178 semantics with minimal new code.
**MVP slice:** (1) `browser text` (B1) — the agent needs a cheap read first or v4-flash drowns in HTML; (2) the permission-locked `browser-agent` opencode agent def + a thin `browser agent "<goal>"` wrapper (open own tab → `opencode run` → parse schema → close); (3) final-answer schema + one retry. **Defer:** MCP wrapper, structured extractors (B5), `--dry-run`.
**Open risks:** cheap-model reliability (mitigate: single-tool permission, step budget, retry; escalate model if >~1/5 tasks blocked/wrong); full-autonomy safety (bounded by own-tab + #178, but **#178 unverified** — verify first); privacy (page content → DeepSeek); opencode headless quirks (`--format json`/`steps`/permission semantics differ between 1.17.20 and 1.18.4 — pin/align versions).
**Escalate the model** on repeated schema-parse failures, stalls under `steps:12`, or wrong extractions → `deepseek/deepseek-v3.2` → a small Claude model.
