# browser-bridge usage audit — 2026-08-02

**Scope:** actual USAGE of the browser-bridge over the last 2 days, both hosts.
Companion to a separate static source/skill review — this document owns the *data*.

**Window:** `ts >= 2026-07-31 00:00:00 UTC` (ClickHouse) / transcript `mtime >= 2026-07-31`.
Report generated 2026-08-02 ~04:30 UTC (verified via `date -u`).

**Privacy:** metadata only — op names, counts, bare domains, error strings. No page
content, no `eval`/`js` source, no HTML, no URLs with path/query. Same contract as
`server.py:539-541`.

---

## 1. Corpus + method

### 1.1 Transcript corpus (source 2)

| | laptop (192.168.50.155) | workbench (192.168.50.250) |
|---|---|---|
| `.jsonl` modified since 2026-07-31 | 93 | 284 |
| …matching the brief's NARROW pattern | 50 | 60 |
| …matching the WIDENED pattern | **50** | **68** |
| sessions with a real `browser` invocation (miner) | **15** | **22** |
| `browser` CLI invocations extracted | **82** | **474** |

**My corpus differs from the brief.** The brief measured laptop 91/49 and workbench
284/60. I get 93/50 and 284/60-narrow. The laptop drift (+2 files, +1 match) is simply
that more transcripts were written between the brief being composed and this run — the
window is a live mtime filter.

The **workbench narrow→wide delta is real and material: 60 → 68 (+8 files, +13%)**. The
widened pattern adds `js|eval|open|close|health|key|upload|frames|emulate|activate|type`.
Those 8 extra files are dominated by `js` — which is the **single most-used op on the
workbench** (201 of 474 invocations). The brief's narrow pattern misses the busiest
sessions. **Recommendation: any future mining of this corpus must use the widened
pattern.**

### 1.2 ClickHouse corpus (source 1 — authoritative for counts)

`activity.events`, `source='browser-bridge'`, `kind='cmd'`. Reached from the laptop via
the **nebula** endpoint `http://10.42.0.10:30123` (the LAN `.94` address is unreachable
from here) with SOPS-decrypted `activity_reader` creds.

**Both hosts have rows in the window — no telemetry gap:**

| host | rows in window |
|---|---|
| laptop | **412** |
| workbench | **2040** |
| **total** | **2452** |

### 1.3 opencode corpus (source 3)

`~/.local/share/opencode/opencode-stable.db` — **copied to scratch before querying** on
both hosts (laptop 52 MB; workbench 275 MB). Queried the copy.

Schema found (relevant tables): `session(id, time_created, …)`,
`session_message(id, session_id, type, seq, time_created, data)`,
`part(id, message_id, session_id, time_created, data)`. **`session_message` is empty
(0 rows)** on the laptop — the live conversation record is entirely in `part`, whose
`data` column is a JSON blob with `type ∈ {text, reasoning, tool, step-start,
step-finish}`. Tool calls are `type='tool'` with `tool`, `state.input`, `state.output`,
`state.error`. Token/cost accounting lives in `step-finish`.

`~/.local/share/opencode/tool-output/` held only **3 files** on the laptop (last write
2026-08-01 15:53) — it is not a useful usage record and I drew nothing from it.

### 1.4 Scripts written, and the negative control run for each

All under the scratch dir. **Every one was validated against a fixture containing cases
it MUST report and cases it MUST NOT, before its output was trusted.**

| script | purpose | negative control | result |
|---|---|---|---|
| `mine.py` | extract every `browser <op>` Bash invocation + its result bytes/error | fixture of 10 shell commands: 6 MUST-match forms (bare, `--instance` flag, `js` alias, inside a pipeline, two in one `&&` chain, absolute-path form) and 2 MUST-NOT (`echo hello world`, `git status`), plus 2 FP probes | **7/7 MUST invocations found; both MUST-NOT correctly rejected.** One FP mode measured and disclosed: `browser` inside a **shell comment** (`# browser wake …`) matches. A quoted grep pattern (`rg 'browser text'`) correctly does NOT match. |
| CH op-extraction (`JSONExtractString(payload,'op')`) | op/outcome counts | (a) a fake op `zzz_fake_op` → **0 rows**; (b) **completeness check: the per-op counts sum to exactly 412 (laptop) and 2040 (workbench)**, i.e. every row parsed, no silent empty-string bucket | passed both |
| CH source check | prove both hosts present | `source='zzz_no_such_source'` → **0** | passed |
| `dup.py` | repeated-identical invocations per session; screenshot Reads | fixture with exactly 1 injected duplicate + 1 screenshot Read + 1 non-browser command → expected TOTAL=3 DISTINCT=2 WASTED=1 SHOT_READS=1 | **exact match** |
| `shot.py` | correlate `browser screenshot <path>` against a later `Read` of that path | fixture: 1 pathed-and-read, 1 pathed-never-read, 1 temp-path → expected CAPTURES=3 pathed=2 temp=1 READ=1 NEVER=1 | **exact match** |
| `ref.py` | Reads of `SKILL.md` / `reference/*.md` and their byte cost | implicit control: mean bytes/read for `SKILL.md` on the laptop = 244,858/20 = **12,243 B**, which matches the file's real size (11,996 B) — the extractor is reading whole-file results, not fragments | passed |
| `oc2.py` | opencode `browser` TOOL calls only | first-pass version counted `op_not_allowed`/`timeout` as **substring matches on any tool output**, which produced 87 "refusals" — **contaminated by agents reading `reference/errors.md`**. Corrected to `tool='browser'` + `state.error` only, dropping the count from 87 to **6**. Disclosed because it is exactly the class of false green this audit is meant to avoid. | corrected |

### 1.5 What I could NOT measure

- **Attribution of ClickHouse ops to a Claude session.** The emitter records op / key /
  outcome / domain but **no session or PID**. So CH tells me *what* happened and *how
  often*, never *who*. All sequence analysis therefore rests on transcripts (which
  under-count) — the two sources cannot be joined.
- **The 1,896-op gap.** CH counts **2,452** wire ops; transcripts show **556** CLI
  invocations and opencode shows **150** tool calls. ~1,750 wire ops originated outside
  any agent-visible call — the test suite, the OOPIF rig, shell loops, and the CLI's own
  internal multi-op expansions. **I cannot separate them.** This is the single biggest
  caveat on every CH number below; see finding F1.
- **Exact screenshot token cost.** Image token cost is estimated, not measured (method in
  F4).
- **Whether a `screenshot` could have been a `text`.** Requires page content, which the
  privacy contract forbids me from reading.

---

## 2. Findings, ranked by measured cost

### F1 — 61% of all bridge traffic is LOCAL/TEST, not real use — MEASURED (source 1)

Classifying every windowed op by its bare domain:

| class | laptop | workbench | total | share |
|---|---|---|---|---|
| LOCAL/TEST (`127.0.0.1`, `localhost`, `*.nip.io`, `*.sslip.io`, `x.test`, `example.com`, `newtab`, empty) | 318 | 1187 | **1505** | **61%** |
| REAL-SITE | 94 | 869 | **963** | 39% |

Top real domains: `civitai.com` (654), `aigeum.com` (122), `simpcity.cr` (47),
`news.ycombinator.com` (31), `turbo.cr` (18), `openrouter.ai` (15), `playwright.dev` (13).

**Consequence:** every raw op-frequency claim about "how the bridge is used" is ~60%
self-testing. The real-use op profile is materially different from the raw one:

| op | all ops | REAL-SITE only |
|---|---|---|
| eval/js | 1106 | 525 |
| wake | 250 | 106 |
| nav | 182 | 91 |
| text | 130 | 59 |
| click | 113 | 42 |
| screenshot | 169 | 41 |
| activate | 53 | 23 |

Note `screenshot` drops from 169 → 41 (76% of screenshots were test-rig captures) while
`eval` only halves. **Real use is overwhelmingly DOM-scripting, not visual.**

---

### F2 — `nav`/`open` have no `--wake`, forcing a documented 2-call round trip — MEASURED (sources 2+3)

`--wake` is implemented on `text`, `html` and `js` only (`browser` lines 967–1009). It is
**absent from `nav` and `open`** — verified by grep over the CLI.

Measured consequence:

- Claude transcripts, adjacent-pair analysis over both hosts: **`nav→wake` 7, `open→wake` 6
  = 13 occurrences**.
- opencode, the autonomous agent: session `ses_045f7d` is **literally `nav wake eval` ×5**
  — 15 tool calls that `nav --wake` + `eval` would make 10. Session `ses_0468b6` shows
  `nav text wake text` — navigate, read empty, wake, re-read.
- Across opencode both hosts, `wake` is 15 of 150 browser-tool calls (10%), and in every
  long sequence it directly follows a `nav` or a failed read.

This is a **design gap, not a doc gap** — the skill correctly tells you to wake
(`SKILL.md:75`, `reference/spa-wake.md`); there is simply no way to fold it into the
navigation that created the hidden tab. It is also the highest-frequency avoidable
round trip in the corpus.

---

### F3 — `activate` (screen theft) used 53× as a screenshot precondition; adoption of `wake` is real but incomplete — MEASURED (sources 1+2)

`SKILL.md:76` marks `activate` **"⚠⚠ STEALS THE OPERATOR'S SCREEN — the ONE intrusive op,
a LAST RESORT… NOT the fix for a hidden/unrendered tab."**

CH: **53 `activate` calls** in the window (49 workbench, 4 laptop; 23 against real sites).
Transcript bigrams: **`activate→screenshot` 10 + `screenshot→activate` 8 = 18 adjacent
pairs** — `activate` is being used as the *screenshot precondition*, repeatedly, in a loop.

Two clearly separate cohorts in the workbench sequences:

- **activate-cohort** (no `wake` at all): e.g. a 52-op session reading
  `… activate screenshot activate nav screenshot activate js nav activate screenshot …`
- **wake-cohort**: e.g. `… open wake js click js screenshot click js wake screenshot …`

Adoption is genuinely improving, and this is a ≥2-point measurement over time:

| date | `activate` | `wake` |
|---|---|---|
| 2026-07-30 | 8 | 0 |
| 2026-07-31 | 21 | 14 |
| 2026-08-01 | **1** | 10 |
| 2026-08-02 | 0 | 4 |

**Interpretation: mostly a fixed doc gap.** The `activate`-heavy sessions are 07-30/07-31,
before the current `spa-wake.md` guidance landed. But note the residual: `activate` still
fired once on 08-01, and `screenshot` has **no `--wake` flag either** (F2's gap applies to
it) — so an agent that needs a rendered screenshot of a hidden tab has *no* documented
non-intrusive one-call path. That residual is a **design gap**.

---

### F4 — screenshot→Read is the largest single token line item, and 11% of captures are never looked at — MEASURED capture counts, ESTIMATED token cost (source 2)

My first detector reported **zero** screenshot Reads on both hosts. That was **wrong** —
it only matched temp-file names, and agents overwhelmingly pass an explicit path. The
corrected `shot.py` correlates the captured path against a later `Read` of that basename:

| | laptop | workbench |
|---|---|---|
| `browser screenshot` captures | 1 | **63** |
| …to an explicit path | 0 | 63 |
| …to a temp file (no path) | 1 | 0 |
| **…later Read back** | 0 | **56** |
| **…NEVER read** | 0 | **7 (11%)** |

**Token estimate (method stated, uncertainty acknowledged):** one filename in the corpus
self-documents its dimensions — `pr-3492-my-submissions-1497x1152.png`. At the standard
~`(w×h)/750` image-token approximation that is ~2,300 tokens per capture. 56 reads →
**~90k–129k tokens**, depending on viewport. This dwarfs every text cost below. It is
**mostly legitimate** (visual verification of UI work), but:
- the **7 never-read captures** are pure waste (~14–16k tokens of capture work whose
  output was discarded — though the *read* cost was correctly avoided);
- F1 shows **76% of all screenshots were against test/loopback domains**, i.e. harness
  captures, not user-facing verification.

---

### F5 — documentation reads cost ~216k tokens, but the real CONSUMERS read almost none of it — MEASURED (source 2)

Reads of `SKILL.md` / `README.md` / `reference/*.md` / browser-bridge handoffs, with the
byte cost of the tool result:

**Laptop — 93 reads, 718,186 bytes (~180k tokens):**
`SKILL.md` ×20 (244,858 B), `README.md` ×19 (153,264 B), `reference/emulation.md` ×8
(57,266 B), a token-design claudedoc ×7 (38,511 B), `reference/agent.md` ×4, `errors.md`
×5, `spa-wake.md` ×4, plus 6 handoff/diagnosis docs.

**Workbench — 7 reads, 146,817 bytes (~37k tokens):** `SKILL.md` ×6, `spa-wake.md` ×1.

**This inversion is the finding.** The laptop is where the bridge is *developed* (PR #266,
the SKILL.md byte-cap work) — 180k tokens of doc reading there is development cost, not
usage cost. The workbench is where the bridge is *used* (2040 ops vs 412; 22 sessions vs
15) — and across all 22 of those sessions, **exactly one `reference/` file was read once.**

There are **11 reference files totalling 101,092 bytes**. In the window, **10 of the 11
were never read by a consumer on either host in the course of actually using the bridge**
(`x-fallback`, `auth-pages`, `read-envelopes`, `security-ops`, `css-hit-test`,
`tabs-instances`, `frames-cdp`, `errors`, `agent`, `emulation` — the last four read only
on the laptop, during bridge development).

**INFERRED, flagged as such:** the reference tier is being maintained for readers who, in
this window, did not read it. That is not proof it is worthless — it may be load-bearing
exactly when something breaks, and `spa-wake.md` *was* consulted once. But it is evidence
that the split has not yet paid off on the consumption side, and it directly overlaps
**PR #266** (in flight, touching `SKILL.md`, `reference/spa-wake.md`, `reference/errors.md`).

⚠ One anomaly I could not explain: the 2 Reads of the *deployed symlink*
`~/.claude/skills/browser/SKILL.md` on the workbench returned 100,232 bytes — ~50 KB per
read against a 12 KB file. Every other measurement in this table matches file size. I am
reporting this as **unexplained**, not as a finding.

---

### F6 — `unknown_instance` is the top error, and it comes in blind-retry bursts — MEASURED (source 1)

Windowed outcomes, all ops (sums verified to equal the row totals exactly):

| outcome | laptop | workbench | total |
|---|---|---|---|
| ok | 382 | 1964 | 2346 (95.7%) |
| **unknown_instance** | 3 | **49** | **52** |
| timeout | 5 | 15 | 20 |
| no_extension | 19 | 0 | 19 |
| ambiguous | 3 | 8 | 11 |
| not_owned_tab | 0 | 3 | 3 |
| no_owned_tab | 0 | 1 | 1 |
| **all failures** | 30 | 76 | **106 (4.3%)** |

**`unknown_instance` is bursty, not chronic** — 48 of the 52 land in two hours on the
workbench:

| hour (UTC) | host | count |
|---|---|---|
| 2026-07-31 17:00 | workbench | **35** |
| 2026-07-31 19:00 | workbench | **13** |
| 07-31 20/21/22:00 | laptop | 1 each |
| 2026-08-01 21:00 | workbench | 1 |

And the failing key is almost always the *legitimate* label, not a typo:
`key='work'` → `eval` 37, `wake` 5, `nav` 2, `click` 2, `tabs` 2 = **48**. Only 4 involve a
genuinely bogus label (`nosuchlabel`, `nosuchinstance`, `typo`) and those are clearly
deliberate test calls.

**Reading: this is a real bridge/UX defect, not user error.** A Brave profile dropped its
connection for ~2 hours, and agents responded by re-issuing `eval --instance work` **37
times** against a label that was correct but temporarily unregistered. Nothing in the
error path told them "the label is right, the profile is disconnected — restart Brave",
and nothing backed them off. `no_extension` shows the identical shape: **16 of 19 in a
single hour** (2026-07-31 03:00, laptop) across `wake`/`text`/`eval`/`getHtml`.

---

### F7 — `ping` has a 38% failure rate and takes 20 s to say "no" — MEASURED (source 1)

Per-op failure rates (ops with ≥10 calls), and this is where the bad ones cluster:

| op | n | failures | rate |
|---|---|---|---|
| **ping** | 34 | 13 | **38.2%** |
| emulate | 19 | 3 | 15.8% |
| tabs | 62 | 6 | 9.7% |
| getHtml | 38 | 3 | 7.9% |
| text | 130 | 10 | 7.7% |
| eval | 1113 | 49 | 4.4% |
| wake | 251 | 11 | 4.4% |
| nav | 183 | 4 | 2.2% |
| screenshot | 169 | 3 | 1.8% |
| click / close / open / frames / activate / upload / key | — | ≤2 | ≤1.7% |

`ping` breaks down as: 21 ok (avg 3–4 ms), 6 **timeout at exactly 20,000 ms**, 3
`unknown_instance`, 3 `ambiguous`, 1 `no_extension`.

`ping` is documented (CLAUDE.md) as **"the deterministic tell"** for extension staleness —
the thing you run *first* when you suspect the extension is stale. But when the extension
is in fact absent or wedged, `ping` **burns the full 20 s generic op timeout** before
answering. The diagnostic op is the slowest thing to fail. I confirmed the healthy path
live and read-only: `browser --instance personal ping` and `--instance work ping` both
return `pong` with `extensionVersion 0.7.0` in milliseconds on this laptop.

---

### F8 — `whoami` re-derivation is real but cheap; repeated-identical calls are NOT a problem — MEASURED (source 2)

`whoami`: **38 invocations** (17 laptop + 21 workbench) across 37 sessions, **31,173 bytes
of output (~7,800 tokens)**, mean ~820 B. One laptop session called it **7 times**; the
bigram `whoami→whoami` occurs **9 times** (~7.4 KB / ~1,850 tokens of pure duplication).
Live `whoami` output measured at **1,224 bytes**.

`whoami` and `health` **emit no telemetry at all** — neither appears anywhere in
`activity.events` despite 38 + 15 invocations. That is a small, real observability gap: the
orientation ops are invisible to the only structured source.

**Negative finding, and it matters:** I expected repeated identical reads to be a major
waste source. **They are not.** `dup.py` (validated to exactly detect an injected
duplicate) found, across the whole window:

| | laptop | workbench |
|---|---|---|
| distinct invocations | 63 | 379 |
| total invocations | 64 | 384 |
| **wasted duplicate calls** | **1** (`js`) | **5** (`click` 2, `open`, `screenshot`, `instances`) |

**6 wasted calls out of 448.** Agents are not re-reading the same page. The
"wake-once-per-page, the DOM persists" guidance in `SKILL.md:75` appears to be landing.

---

### F9 — the autonomous `browser agent` behaves markedly better than the interactive path — MEASURED (source 3)

opencode `browser` tool calls in the window:

| | laptop | workbench |
|---|---|---|
| browser-tool calls | **115** | **35** |
| sessions using it | 25 | 2 |
| **ok** | 109 (94.8%) | 33 (94.3%) |
| refused/errored | 6 | 2 |

Op mix (laptop): `text` 35, `nav` 29, `eval` 26, `html` 11, `wake` 8, `click` 3,
`screenshot` 2, `frames` 1. Workbench: `nav` 9, `eval` 9, `wake` 7, `screenshot` 3,
`html` 3, `click` 3, `text` 1.

The six laptop refusals, verbatim (redacted where needed):
- `op_not_allowed:wake` **×2** — the agent asked for `wake` and the allowlist refused it.
  `SKILL.md:45` states the agent tool has an **auto-`wake` on a hidden read**; these two
  refusals are that guarantee failing in practice. **Real defect signal.**
- `op_failed:Cannot access contents of url "about:blank"` ×1 and
  `op_failed:cdp_attach_refused:about:` ×1 — the `about:blank` readiness class that
  CLAUDE.md records as fixed in #234; still surfacing.
- `op_failed:SyntaxError: Failed to execute 'querySelector'` ×1 (+2 on the workbench) —
  model-authored bad selector.
- `domain_blocked:en.wikipedia.org` ×1 — allowlist friction on a benign public domain.

**No timeouts, no step-budget exhaustion, no `steps_used` overruns observed.** Longest
sequences are 12 and 20 calls. The agent's failure profile is *narrower* and its op
sequences are *tighter* than the interactive agents' — see the F2 evidence, where its worst
behaviour (`nav wake eval` ×5) is caused by a missing CLI flag, not by the agent.

Note: `type='step-finish'` accounting across all opencode work in the window (not just
browser) totals 76.9M tokens at **$0.8925** — the cheap-model offload is doing what it was
built to do.

---

### F10 — the interactive toil signature is `js → js → js`, not any bridge defect — MEASURED (source 2)

Top adjacent op pairs, per-session, both hosts:

| pair | n | | pair | n |
|---|---|---|---|---|
| **js→js** | **113** | | activate→js | 12 |
| js→screenshot | 33 | | whoami→open | 10 |
| click→js | 32 | | screenshot→click | 10 |
| screenshot→js | 23 | | **activate→screenshot** | **10** |
| js→nav | 20 | | **whoami→whoami** | **9** |
| js→click | 20 | | nav→screenshot | 9 |
| wake→js | 15 | | nav→js | 9 |
| js→activate | 12 | | **nav→wake** | **7** |

`js→js` at 113 is by far the dominant pattern — agents issue many small evals in sequence
rather than one composed script. Output cost: `js` produced **105,557 B on the workbench
(201 calls) + 16,327 B on the laptop (16 calls)** = 121,884 B ≈ **~30k tokens**.

Total browser-CLI text output captured in transcripts: **laptop 50,853 B + workbench
200,843 B = 251,696 B ≈ ~63k tokens.** Per-op the biggest contributors are `js` (121,884 B),
`whoami` (31,173 B), `click` (15,744 B), `wake` (15,123 B), `nav` (18,436 B).

**INFERRED:** `js→js` chains are mostly *legitimate* iterative DOM exploration, not toil —
each eval's result informs the next selector. There is no evidence in metadata alone that
these could have been batched. I flag it as the largest *pattern* without claiming it is
waste.

---

### F11 — low-traffic ops: candidates, but check the docs before calling them dead — MEASURED (source 1)

Windowed wire-op counts for the tail: `frames` 13, `upload` 15, `emulate` 19, `context` 7,
`type` 3.

- `type` — **3 calls, workbench only.** Lowest-traffic op in the window. But `key` (34
  calls) is its sibling and healthy; a form-filling op with a 0% error rate is cheap to keep.
- `context` — **7 calls.** Documented in `SKILL.md`; CLAUDE.md records it was only recently
  added and was **dead on `main`** until the `ALLOWED_OPS` fix. 7 calls is consistent with
  "newly working", not "dead". **Do not prune.**
- `emulate` — 19 calls but a **15.8% failure rate**, the second-worst of any op. Its
  reference file (`emulation.md`, 23,750 B) is the **largest** in `reference/` and was read
  8× on the laptop. Worst cost/benefit ratio in the tail.
- `upload`, `frames` — 15 and 13 calls, **0% failure**. Working as intended.

No op in the CLI's subcommand list was completely unused in the window except `instances`,
`release`, and `agent` (as a *wire* op — `agent` is a client-side driver, not a wire op, so
its absence from CH is correct, and it fired 3× in transcripts and 150× via opencode).

---

## 3. Proposed fixes

### SAFE — mechanical, low blast radius, CLI/wording/defaults only

**S1. Add `--wake[=MS]` to `nav` and `open`.** (Fixes F2; the flag's parse+plumb code
already exists verbatim in the `text`/`html`/`js` branches at `browser:967-1009`.)
- *Test that proves it works:* `browser nav <local-rig-url> --wake` against the OOPIF test
  rig, followed by `browser text` with **no** intervening `wake` — assert the read returns
  rendered content, and assert ClickHouse records a `wake` op in the same session window.
- *Negative control that proves the test isn't vacuous:* run the identical sequence with
  `nav` **without** `--wake` on a deliberately backgrounded tab and confirm the read comes
  back empty / `data.hidden:true`. If the no-`--wake` control *also* passes, the tab was
  never throttled and the test proves nothing about wake — pick a genuinely hidden tab.
  (This is exactly the "guard must be proven REACHABLE" trap: the happy path can resolve
  anyway.)

**S2. Give `ping` its own short timeout (~2 s) instead of the generic 20 s.** (Fixes F7 —
21 healthy pings averaged 3–4 ms, so 2 s is ~500× headroom.)
- *Test:* point `ping` at a registered-but-dead instance; assert it returns non-zero in
  <3 s wall time, and assert the error names the timeout.
- *Negative control:* run the same assertion against a **healthy** instance — it must pass
  in milliseconds and must NOT report a timeout. And separately assert the pre-change
  binary fails the <3 s bound (red at base, green at HEAD), or the test is an invariant
  guard, not a regression test.

**S3. Reword the `unknown_instance` error to distinguish "wrong label" from "known label,
disconnected profile".** (Fixes F6 — 48 of 52 failures used the *correct* label.) The
server already knows which keys have ever registered; when the requested key is unknown but
*was* seen this process lifetime, say so and name the recovery: "instance 'work' is known
but not currently connected — the Brave profile has dropped its long-poll; FULLY RESTART
Brave." When it is genuinely never-seen, list the connected keys.
- *Test:* register an instance, disconnect it, issue an op against its key; assert the
  error text contains the disconnected-profile wording and NOT the unknown-label wording.
- *Negative control:* issue an op against a never-registered key (`nosuchlabel`) and assert
  the **opposite** message. Without this second case the test passes with the branch
  hard-wired to one string.

**S4. Emit telemetry for `whoami` and `health`.** (Fixes the F8 observability gap — 53
invocations currently invisible.) Metadata-only, identical contract.
- *Test:* run `browser whoami`, then assert a `source='browser-bridge'` row with
  `payload.op='whoami'` appears in `activity.events` within the flush interval.
- *Negative control:* assert the row count for `op='whoami'` is **0 before** the call — a
  post-hoc "there is a row" assertion passes on any pre-existing row. Count before, count
  after, assert the delta is exactly 1.

**S5. Warn on a `screenshot` capture that is never read.** Cheap client-side hint: when
`screenshot` writes to an explicit path, print the path plus a one-line reminder that the
file must be `Read` to be seen. (Addresses the 7/63 never-read captures in F4.) Pure
stdout wording — zero behavioural blast radius.
- *Test:* assert the reminder string appears on stdout for an explicit-path capture.
- *Negative control:* assert it does **not** appear for `--data-url` and for the temp-path
  form, where it would be wrong. A single-case test here passes with the string
  unconditionally printed.

### RISKY — behaviour change, extension/CDP surface, needs live verification

**R1. Add `--wake` to `screenshot`.** F3's residual: there is no non-intrusive one-call
path to a rendered screenshot of a hidden tab, which is *why* `activate→screenshot` appears
18 times. This touches the CDP capture path (`Page.captureScreenshot` already shows
`cdp_timeout` failures in the corpus) and must be verified live on both hosts before it is
claimed to work.

**R2. Fix `op_not_allowed:wake` in the agent allowlist.** F9 measured the agent being
refused `wake` twice, contradicting `SKILL.md:45`'s auto-wake guarantee. This is
`ALLOWED_OPS` in `server.py` — the exact surface CLAUDE.md records as having shipped a
feature **dead on `main`** because the CLI/extension/manifest were updated and `server.py`
was not. Any fix here must be verified against the **committed** tree, not a deployed copy,
and requires a `home-manager switch` plus a full Brave restart before the probe means
anything.

**R3. Back off / circuit-break on repeated `unknown_instance`.** F6's 37 blind retries
argue for it, but auto-retry logic in a command channel is a behaviour change with real
failure modes and should not be bundled with S3's wording fix. Ship S3 first and re-measure.

**R4. Prune or fold `reference/emulation.md`.** F11: 23,750 B (largest reference file)
serving an op with 19 calls and the second-worst error rate. But this is squarely inside
**PR #266's** blast radius (in flight, touching `SKILL.md`, `spa-wake.md`, `errors.md`) and
the SKILL.md byte-cap gate owns the eviction accounting. **Do not touch until #266 lands.**

---

## 4. Open questions for the operator

1. **F1 — should the test rig emit telemetry at all?** 61% of `activity.events`
   browser-bridge rows are loopback/test traffic, which permanently distorts every
   usage query. A `BROWSER_BRIDGE_NO_TELEMETRY=1` in the test harness would make this
   table mean what it looks like it means. Is losing test-path observability acceptable?
2. **F5 — is the `reference/` tier earning its keep?** 10 of 11 files went unread by
   consumers in the window; the only reads were during bridge development on the laptop.
   Is one `spa-wake.md` read across 22 workbench sessions the expected hit rate, or is the
   tier not being discovered? (`SKILL.md` addresses them by repo-absolute path because
   `home.nix` symlinks only `SKILL.md` + the CLI — **are the reference files even readable
   from the deployed skill path on the workbench?** I did not verify this and it would
   fully explain the zero.)
3. **F9 — was `op_not_allowed:wake` already fixed?** The two refusals are laptop-side. If
   this is the known `ALLOWED_OPS` class, is it closed on `main` as of 0.7.0, or is
   `SKILL.md:45`'s auto-wake guarantee still aspirational?
4. **F4 — what viewport do workbench screenshots use?** I estimated image token cost from a
   single self-documenting filename (1497×1152). If the standard capture is materially
   larger or smaller, the ~90–129k token estimate moves proportionally.
5. **The unexplained 50 KB/read on the deployed `SKILL.md` symlink** (F5) — worth 5 minutes
   of someone's time, or noise?
6. **Should `activate` be gated behind an explicit `--i-mean-it` flag?** Adoption of `wake`
   is clearly working (F3: 21 activates on 07-31 → 1 on 08-01), so the remaining risk is
   small — but the failure mode (stealing the operator's screen) is uniquely bad.
