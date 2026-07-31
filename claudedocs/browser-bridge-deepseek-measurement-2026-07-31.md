# Measuring `browser agent` (deepseek-v4-flash) — 14 real goals on the live laptop Brave, 2026-07-31

**Question:** is deepseek-flash good enough for `browser agent` to become the DEFAULT for
open-ended reads? This gates the designed-but-unshipped default flip
(`claudedocs/browser-bridge-token-and-agent-design-2026-07-30.md` §B.1–B.4).

**Setup (name the scope):** host = **laptop** (`192.168.50.155`, verified via `browser
whoami`), instance = **`personal`**, extension `0.2.0`, server `whoami-1 (2026-07-30)` at
git `039955d`, `opencode` 1.18.4, model `openrouter/deepseek/deepseek-v4-flash` (the
wrapper default), default `--steps 12` / `--timeout 120`. Every run got a tight
`--allow-domains`. All runs used `BROWSER_AGENT_KEEP_SCRATCH=1`, so every verdict below is
backed by the run's `tool-audit.jsonl` + full opencode transcript, not by the agent's
self-report. No repo source file was modified. No `activate` was issued at any point.

---

## 1. Results

| # | Goal | Target | status | steps claimed / **actual tool calls** | wall | **correct?** | notes |
|---|---|---|---|---|---|---|---|
| A | Top 3 HN story titles | news.ycombinator.com | ok | 2 / 2 | 16.9s | ✅ | exact match + order vs `curl` of HN |
| B | Do Playwright docs mention "Trace viewer"? | playwright.dev | ok | 2 / 2 | 20.5s | ✅ | truth: string appears 4× in `/docs/intro`. Evidence was **paraphrase, not verbatim** |
| C | deepseek-v4-flash price + context on OpenRouter | openrouter.ai | ok | 2 / 2 | 18.7s | ✅ | `$0.0896/$0.1792 per 1M`, 1M ctx — matches the **page** (a discounted price; the `/api/v1/models` figure `0.14/0.28` would have been the wrong ground truth) |
| D | Follow a link from nixos.org → stable release version | nixos.org | ok | 5 / **7** | 22.6s | ✅ | `26.05`. Its `click` reported `ok:true` but did **not** navigate; it read **stale text and did not notice**, then recovered via `eval` href → `nav` |
| E | **B.3.1** — click the Docs link, report H1 | playwright.dev | ok | 8 / **12** | 59.0s | ✅ | `Installation`. See §3.1 — right answer, 6× the median cost |
| F | civitai model-benchmarking SPA | civitai.com | ok | 3 / 3 | 22.7s | ✅ (degenerate) | Page is now a **real 404** — I confirmed with my own woken read (`title: "Page Not Found"`). The documented known-bad case no longer exists; **did not exercise the trap** |
| **F2** | **B.3.2 (substitute)** — `wake-rig.html`, exact `#app` text | 127.0.0.1:8901 | **ok** | 3 / 3 | 32.3s | ❌ **WRONG** | Answered `WAKE-RIG-SHELL (waiting for frames)`. Truth (my read, same tab, before/after `wake`): `WAKE-RIG-SHELL…` → **`WAKE-RIG-RENDERED`**. Confident wrong `ok` |
| G | **B.3.3** — RIG_SECRET in the deepest nested OOPIF | 127.0.0.1 + sslip.io + nip.io | ok | 7 / 7 | 23.9s | ✅ | `grandchild-reached`. **Never called `frames`** — it top-level-`nav`'d to each iframe URL instead |
| G2 | Same, `--allow-domains 127.0.0.1` only (workaround forbidden) | 127.0.0.1 | ok | 5 / **7** | 30.0s | ✅ | Called `frames`, picked `frameId 170`, `eval --frame 170` → correct |
| H | **Guardrail** — Wikipedia goal, `--allow-domains example.com` | — | **blocked** | 2 / 2 | 11.9s | ✅ | Clean `status:"blocked"`, **exit 1**, empty evidence, no fabrication, no hang |
| I | `requests` latest version + license | pypi.org | ok | 2 / 2 | 15.5s | ✅ | `2.34.2`, `Apache-2.0` — matches `pypi.org/pypi/requests/json` |
| J | Multi-page: HN #1 → its comments page → submitter + comment count | news.ycombinator.com | ok | 4 / **6** | 47.7s | ✅ | `Jrh0203`, `142 comments`; HN API read minutes later showed `descendants 143`, `score 430` vs the agent's quoted `428 points` — consistent drift, page-accurate at read time |
| K | First 3 models on civitai.com/models (virtualised SPA) | civitai.com | ok | 7 / **8** | 48.7s | ✅ | Called `wake` **twice** on its own, then `html`. Verified against my own `html --wake`: first three `/models/<id>/<slug>` in DOM order are `one-obsession`, `velvets-mythic-fantasy-styles…`, `lustify-nsfw-checkpoint` — exact match |
| L | Top 3 on OpenRouter rankings (SPA) | openrouter.ai | ok | 2 / 2 | 18.1s | ✅ | `MiMo-V2.5 / DeepSeek V4 Flash / Hy3` + token figures — exact match vs my woken read |

---

## 2. Measured success rate

**p = 13/14 = 0.93** over all runs (the denominator is 14 agent invocations, each run once).

Excluding H (a guardrail-by-design case, not a capability measure): **p = 12/13 = 0.92**.

By task shape:

| shape | goals | p | median wall | notes |
|---|---|---|---|---|
| simple single-page read | A, B, C, I, L | **5/5 = 1.00** | 18.1s | always 2 tool calls |
| single-page, page is genuinely gone | F | 1/1 | 22.7s | correctly reported the 404 |
| multi-page / follow-a-link | D, J | **2/2 = 1.00** | 35.2s | both needed a recovery step |
| interactive (blind `click`) | E, K | **2/2 = 1.00** | 53.9s | 8–12 tool calls; 5–6× median cost |
| cross-origin frames | G, G2 | **2/2 = 1.00** | 27.0s | see §3.3 |
| **throttled/unrendered shell** | **F2** | **0/1 = 0.00** | 32.3s | **confident wrong `ok`** |
| guardrail (out-of-allowlist) | H | 1/1 | 11.9s | clean `blocked`, exit 1 |

⚠ **Each goal ran exactly once** — this is a point estimate with no variance. 13/14 has a
95% binomial CI of roughly **0.66–0.998**. Treat `p ≈ 0.9` as the order of magnitude, not
as a precise number. Throttling itself is nondeterministic: my own probe of
`openrouter.ai/rankings` in a hidden tab returned **78 chars**, yet run L read the full
2,002-char rendered text from a hidden tab ~10 minutes later without waking.

---

## 3. Hypothesis verdicts

### 3.1 B.3.1 — blind selector synthesis: **CONFIRMED as a cost/latency weakness, REFUTED as a correctness failure** (2 points: E, K; plus D)

The predicted mechanism is real and observable:

- **E, call 3:** `click` with `selector: 'a:has-text("Docs")'` — a Playwright-ism the model
  invented having never seen the markup. The tool returned a **clean, actionable error**:
  `op_failed:SyntaxError: … 'a:has-text("Docs")' is not a valid selector`. (This is a
  harness *strength*, not the silent failure I expected.)
- **D, call 3:** `click` with `a[href*="download"]` returned `ok:true, x:1215, y:76` — but
  the tab did **not** navigate. The model's next `text` returned the **stale homepage**, and
  it did not notice; it only recovered because the content obviously lacked a version.
- **E, call 5:** `click a[href*="docs"]` returned `ok:true, x:200, y:30` and again did not
  navigate.

What the prediction got wrong: the model **routes around the missing op**. In E it ran
`Array.from(document.querySelectorAll('a')).filter(a => a.textContent.trim()==='Docs')
.map(a => ({href, text, outer}))` — i.e. it **hand-rolled the proposed `links` op** in
`eval`, got `<a class="navbar__item navbar__link" href="/docs/intro">Docs</a>`, and
proceeded. It never fell back to a full `html` dump on E.

**The cost is where it hurts:** E used **12 tool calls / 59.0s / $0.005** and K **8 calls /
48.7s / $0.005**, against a simple-read baseline of **2 calls / ~18s / ~$0.001**. A `links`
op would be a **5–6× efficiency win on interactive goals**, not a correctness fix.

### 3.2 B.3.2 — the throttled-background-tab trap: **CONFIRMED**, and I found the harness bug that causes it

**F2 is the single measured failure and it is exactly the predicted shape:** a confident
**wrong** answer with `status:"ok"`, produced from an unrendered shell, with faithful
evidence. The agent read `#app` twice (once by `eval`, once by `text --selector`), both
returned the shell string, and it reported it as fact. My own read of the same fixture
proved the correct answer is `WAKE-RIG-RENDERED` (before `wake`: `WAKE-RIG-SHELL (waiting
for frames)`, `hidden:true`; after `wake`: `WAKE-RIG-RENDERED`).

**Root cause (read from source, not inferred from behaviour) —
`scripts/browser-bridge/opencode/tools/browser_tool_impl.mjs`, `summarizeResult()`:**

```js
if (op === "text") return typeof data.text === "string" ? data.text : JSON.stringify(data);
if (op === "html") return typeof data.html === "string" ? data.html : JSON.stringify(data);
```

The server **does** return `hidden:true` and the full "tab is hidden — background tabs are
throttled…" `note` (I see it on every one of my own direct CLI reads). `summarizeResult`
**discards both** for `text` and `html` and hands the model the bare string. The nudge
reaches the model **only by accident**, via the `eval` branch when the value is
`null`/non-string and it falls through to `JSON.stringify(v ?? data)`.

That accident is visible in the data, and it is what saved runs E and K:

- **E, call 9** (`eval` of `.click()` → `null`) → the whole `data` object dumped, note
  included → the model's very next call was `wake`, then the correct read.
- **K, call 5** (`eval` returning `null`) → note leaked → `wake`, `wake` again, correct
  answer.
- **F2** used `eval` returning a **string** and `text` — the note was stripped both times.
  The model had no signal at all.

**The discriminator is whether the shell looks plausible.** In K the shell was obviously
missing the content the goal asked for, so the model retried; in F2 the shell was a
complete, confident-looking string, so it stopped. This is precisely why the fix must be
**deterministic, not modelled**.

### 3.3 B.3.3 — cross-origin frames: **REFUTED at both measured points**

- **G** (frame URLs standalone-navigable, all three domains in `--allow-domains`): the model
  **skipped `frames` entirely** and instead `nav`'d the top-level tab to each iframe URL in
  turn. Right answer, 7 calls. The discovery skip was predicted — the predicted
  *consequence* ("reads the top frame and reports the page is empty") did not occur.
- **G2** (same goal, `--allow-domains 127.0.0.1` only, so the nav workaround is refused):
  the model called `frames`, read
  `[{frameId:0,…},{frameId:168,…mid…},{frameId:170,…leaf…}]`, correctly identified 170 as
  the deepest, and issued `eval --frame 170` → `grandchild-reached`. Textbook.

No wrong answer, no "page is empty" at either point. **Note the dependence I measured:**
G's workaround only worked because the frame hosts were in the allowlist; the frames path
is what you get when the allowlist is tight.

### 3.4 B.3.5 — self-reported `steps_used`: **CONFIRMED** (bonus finding)

`steps_used` disagreed with the audit log in **5 of 14 runs**, and **always undercounted**:
D 5 vs 7, E 8 vs 12, G2 5 vs 7, J 4 vs 6, K 7 vs 8. The deterministic fix already proposed
(compute from `tool-audit.jsonl` in `browser-agent-parse.py`) is correct and trivial.

### 3.5 B.3.6 — evidence grounding: **would NOT have caught the one wrong answer**

This is the most important negative result in the report. The design doc calls the
evidence-substring check "a prerequisite for the default flip". Against this sample:

- It **would** have flagged **B**, whose evidence is a paraphrase ("Sidebar navigation lists
  'Trace viewer' under both 'Getting Started' and 'Guides' sections") that is not a
  substring of anything the tool returned — a **false positive**, since B was correct.
- It would **not** have flagged **F2**, the only wrong answer, because F2's evidence
  (`document.getElementById('app').innerText returned 'WAKE-RIG-SHELL (waiting for
  frames)'`) is a **faithful verbatim quote of what the tool actually returned**. The model
  did not confabulate; the *page was in the wrong state*.

Evidence grounding defends against confabulation. It does **not** defend against a
correctly-quoted unrendered page. Different failure, different fix.

---

## 4. How many `status:"ok"` answers were actually WRONG

**1 out of 13 `ok` runs (7.7%).** That run is **F2**, and its cause is a single
deterministic, already-diagnosed harness gap (§3.2) — not a general reasoning failure. No
other run produced a wrong `ok`; no run hallucinated an answer for a page it could not
reach (H returned `blocked` rather than guessing, with empty evidence).

---

## 5. Cost and latency

- **Campaign credit delta:** `total_usage` 1017.931128 → 1018.001309 = **$0.0702** across
  the whole window. ⚠ This OpenRouter key is **shared with other tooling** (repo-cos et al.),
  so $0.070 is an **upper bound**.
- **Sum of per-run attributed deltas:** **$0.0345 over 14 runs ≈ $0.0025/run mean.**
  Per-run attribution is unreliable — OpenRouter's usage accounting lags the request by
  more than the polling window, so cost smears into the next run's reading (run A read
  $0.00000 and its ~$0.0022 landed in the following sample).
- **Budget:** well inside the $0.06–0.10 target; nowhere near the $0.25 stop.
- **Median wall-clock: 22.65s** (n=14; range 11.9s–59.0s).
  - simple single-page read: **median 18.1s**, always 2 tool calls
  - interactive (blind click): **48.7s and 59.0s** — the expensive shape

At ~$0.0025 and ~20s for a read that would otherwise put 10K–100K tokens of page HTML into
context, the economic case in §B.4 holds up under measurement.

---

## 6. Recommendation: **SHIP WITH NAMED GUARDRAILS**

`p ≈ 0.93` with one wrong `ok` traced to a fixable determinism gap is good enough to flip
the default for open-ended reads — **but not before the hidden-tab signal reaches the
model**. The wrong-`ok` rate is the number that decides this, and it is entirely
attributable to §3.2.

### Prerequisite (blocker) — must ship *with* or *before* the flip

1. **Propagate `hidden`/`note` through `summarizeResult()` for `text` and `html`.**
   Today the model is *structurally blind* to throttling on exactly the two ops the agent
   def tells it to prefer. Minimum fix: when `data.hidden === true`, return the text
   **plus** the note (or a compact `{text, hidden:true, note}`), so the signal is not
   contingent on an `eval` happening to return `null`.
   *Stronger and preferred:* **deterministic auto-`wake`-and-re-read on the first
   `text`/`html` where `hidden === true`**, returning the re-read. `wake` does **not** move
   focus (that's the whole point of #225), so unlike the design doc's original auto-
   `activate` proposal this carries **no screen-theft risk** — open question #4 in the
   design doc is resolved by `wake` and should be closed. This alone converts the one
   measured failure into a pass.

### Guardrails to state in SKILL.md alongside the flip

2. **Do not default to the agent for virtualised/lazy list content.** K only worked because
   the model woke the tab twice and then read `html`; I could not reproduce the card list
   at all through `text` on a hidden tab even after waking. Off-viewport virtualised rows
   need the real foreground and are outside what the agent can be trusted with.
3. **Keep `--allow-domains` tight and *include the frame hosts you expect*.** G showed the
   model will substitute top-level navigation for frame reads when the allowlist permits;
   G2 showed a tight allowlist is what pushes it onto the correct `frames` path.
4. **The existing "empty `evidence` → treat as `partial`" rule stays** (H behaved exactly as
   documented), but §3.5 means it must **not** be sold as protection against wrong answers.
5. **Escalation stays cheap and explicit:** on any answer that depends on rendered SPA
   state, verify with one direct `text --wake` before reporting. That is ~200 tokens.

### Should ship, not blocking

6. **Deterministic `steps_used`** computed from `tool-audit.jsonl` (§3.4) — 5/14 runs
   currently report a number that is wrong, always low.
7. **A `links` / `interactive` op** (compact `[{text, tag, selector, href}]`). Measured
   value: it collapses the 8–12-call interactive shape toward the 2-call read shape, a
   **5–6× cost/latency win on E- and K-type goals**. It is an efficiency lever, **not** a
   correctness prerequisite — the model already hand-rolls it in `eval` and gets the right
   answer.
8. **Evidence-substring grounding (B.3.6)** — worth having as anti-confabulation hygiene,
   but **demote it from "prerequisite"**: on this sample it produces one false positive (B)
   and zero true positives. Do not let it stand in for #1.

---

## 7. What I did NOT measure

- **B.3.4 (needle-in-a-haystack over long innerText).** No goal targeted a long dashboard or
  a big table where the answer is one number among thousands. Untested; the §B.3.4
  prediction remains unmeasured.
- **Variance.** Every goal ran **once**. No repeat runs, so no per-goal flakiness estimate
  and a wide CI on `p` (§2). Throttling is demonstrably nondeterministic (L vs my probe of
  the same URL), so a rerun of F2 might pass and a rerun of L might fail.
- **The step-budget and timeout boundaries.** All runs used the defaults (`--steps 12`,
  `--timeout 120`) and none exhausted either — max observed was 12 tool calls (E) and 59.0s
  (E). I never saw a `partial`, a timeout, or a process-group kill. **`status:"partial"` was
  never produced by any run**, so its behaviour is unmeasured.
- **Failure/error paths** other than the domain guardrail: no disconnected extension, no
  tab-gone, no gate failure, no non-http(s) `nav` scheme denial.
- **Authenticated/high-secret pages** — deliberately excluded per the brief. Civitai was the
  only logged-in surface touched, and only for public model listings.
- **The other host and the other profile.** Laptop `.155` / `personal` **only**. Nothing
  here is evidence about the workbench (`.250`) or the `work` profile.
- **Model identity over time.** `openrouter/deepseek/deepseek-v4-flash` as OpenRouter routed
  it on 2026-07-31. No comparison against a second model, and no re-measurement if
  OpenRouter re-points that slug.
- **Prompt injection / hostile pages.** No test of whether a page can steer the agent's
  `nav` or its reported answer. The §B.3 security caveats (best-effort domain deny vs
  client-side redirects) were not exercised.
- **The `civitai.com/apps/run/model-benchmarking` case named in the brief** — it is now a
  genuine 404 (verified), so the documented known-bad SPA no longer exists and F is a
  degenerate data point. F2/K/L are the substitutes, and only F2 is deterministic.
- **`activate` behaviour** — never invoked, per the hard constraint.
