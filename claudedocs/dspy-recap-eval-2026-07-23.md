# DSPy vs. the hand-written prompt for initiative recaps — a measured evaluation

**Date:** 2026-07-23
**Scope:** Does programming + optimizing the recap generator with **DSPy** measurably
improve recap quality over the current hand-written prompt in
`scripts/initiatives/recap.py`? This is a **measured evaluation → evidence + a
recommendation**, not a production integration. Nothing here is wired into the sync;
DSPy is not added as a dependency.

**Verdict: (b) keep the hand-written prompt.** On the current `recap.py` `SYSTEM_PROMPT`
and the homelab `vllm-recap` model (Qwen2.5-7B-Instruct-AWQ), DSPy does **not** measurably
improve recap quality. The best DSPy configuration (ChainOfThought + BootstrapFewShot)
lands **+0.012 composite** over the baseline — inside the ~0.011 noise floor (≈1 std) —
while *simpler* DSPy usage (plain `Predict`) is **clearly worse** (−0.071). Adopt the one
transferable insight — **(c)** nudge the hand prompt to always state current standing —
as a one-line prompt edit, with **no** DSPy runtime. Details below.

---

## 1. Setup

- **Inputs:** the real `recap_context(ini)` dicts the production sync feeds the model,
  pulled from a frozen `initiative-scan.py --days 30 --json` capture (112 initiatives;
  telemetry off in this shell, so `momentum` is git/handoff-derived — this degrades both
  systems identically, so the comparison is unaffected). Curated, disjoint split:
  **10 train / 8 test** (`eval_set.json`), with the test set deliberately loaded with the
  documented weak cases: doc-meta summaries (`remix-session`, `remix-platform`,
  `next-session`, `app-blocks-arc-complete`) and thin context (`next-session`,
  `app-blocks-arc-complete`, `remix-templates`), plus rich controls
  (`initiatives-consolidation`, `clawgate-chat-polish`, `dp-prod-rightsizing-node-drain`).
- **LM:** homelab `vllm-recap`, `Qwen/Qwen2.5-7B-Instruct-AWQ`, served id `recap`,
  OpenAI-compatible, reached over `kubectl -n promptver port-forward svc/vllm-recap`.
- **Baseline** = a faithful reproduction of production: `recap.build_messages(ctx)`
  (`RECAP_INSTRUCTIONS` + `ANTI_CONFABULATION_CONTRACT`), temperature 0.2, max_tokens 160.
- **DSPy candidate** = a `dspy.Signature` (initiative_context → recap) whose docstring is
  at rough parity with the production intent, run as `Predict` and `ChainOfThought`, then
  optimized with `BootstrapFewShot`. DSPy 3.2.1.

---

## 2. The metric (the crux)

A recap is scored on four dimensions → a 0..1 composite. Two are **deterministic**
(defensible, reproducible, zero model cost); two are a **blind LLM-judge** (the same
vllm-recap model, shown only the context + a recap with no hint of which system produced
it). Implemented in `metric.py`.

| dim | how | weight | rationale |
|---|---|---:|---|
| `describes_work` | **deterministic** regex penalty for doc-meta (`handoff`, `.md`, `supersedes`, `read this/first`, `kickoff message`, meta openers like "This initiative…") — each distinct family −0.5 | 0.30 | directly targets the **documented weak spot** |
| `concision` | **deterministic** sentence count (1–2 → 1.0, 3 → 0.6, else 0.2) − 0.3 for bullets/markdown/quotes | 0.15 | recap contract is 1–2 plain sentences |
| `faithfulness` | **LLM-judge** 0–1 (every claim supported by context) **hard-capped at 0.4** by a deterministic guard when the recap cites a number/PR# absent from context | 0.35 | the anti-confabulation core; a wrong recap is worse than a bland one |
| `status_awareness` | **LLM-judge** 0–1 (conveys where the work stands, consistent with `momentum`) | 0.20 | "…and where it stands" is half the recap's job |

`composite = 0.30·describes_work + 0.15·concision + 0.35·faithfulness + 0.20·status`

**Honest limitations of the metric:**
- **Judge is the same 7B model** that writes the recaps → correlated blind spots. A
  stronger, independent judge (GPT-4-class) would be more trustworthy. It is at least
  *blind* to the producing system, so it can't prefer "the DSPy one."
- The judge turned out **fully deterministic** at temperature 0 here (std 0.0000 over 5
  repeats on fixed recaps — see §4), so judge *noise* is not a factor; judge *bias* still is.
- The deterministic `describes_work` regex can false-positive (a recap legitimately about
  a doc feature) or miss doc-meta phrased without a trigger word.
- The number guard catches fabricated *numbers*, not subtler hallucinations.
- **N = 8 test items** → wide confidence intervals. Treat composite deltas below the
  measured noise floor (~0.011) as noise.

---

## 3. Results — baseline vs DSPy (3 runs, LM cache off, mean ± std)

Each system was generated + scored **3 times** with generation caching disabled, so the
spread reflects true run-to-run variance (temperature 0.2 generation + demo re-selection).

| system | composite (mean) | std | Δ vs baseline | describes_work | concision | faithfulness | status_awareness |
|---|---:|---:|---:|---:|---:|---:|---:|
| **baseline (production prompt)** | **0.947** | 0.010 | — | 1.000 | 1.000 | 0.967 | 0.792 |
| dspy_zeroshot (`Predict`) | 0.876 | 0.021 | **−0.071** | 1.000 | 1.000 | 0.867 | 0.613 |
| dspy_zeroshot_cot (`ChainOfThought`) | 0.921 | 0.017 | −0.026 | 1.000 | 1.000 | 0.904 | 0.771 |
| dspy_bootstrap (`Predict` + fewshot) | 0.953 | 0.010 | +0.007 | 1.000 | 1.000 | 0.983 | 0.796 |
| **dspy_bootstrap_cot (`CoT` + fewshot)** | **0.959** | 0.005 | **+0.012** | 1.000 | 1.000 | 0.975 | 0.838 |

**Reading it honestly:**
1. **`describes_work` and `concision` are saturated at 1.000 for every system.** The
   documented doc-meta weak spot **did not reproduce**: across 32 baseline generations
   (8 items × 4 runs) not one recap parroted "handoff"/".md"/"supersedes". **The current
   production prompt already prevents it** (its instructions explicitly forbid meta). So
   the eval could not measure a doc-meta improvement, because there was no gap to close.
   This is the single most important caveat — and a useful result on its own.
2. The only discriminating dimensions are the two judge dims. **Plain DSPy `Predict` is
   significantly worse** (−0.071, ~3–7 std): it drops both faithfulness and, especially,
   status-awareness. CoT recovers most of it; only **CoT + BootstrapFewShot together**
   reach the hand prompt, at **+0.012** — inside the noise floor.
3. The one consistent (if small) DSPy signal is **status_awareness** (baseline 0.792 →
   bootstrap_cot 0.838, +0.046): CoT+fewshot more reliably states *where the work stands*.
   That is the only transferable insight — and it's a prompt tweak, not a runtime.

---

## 4. Noise floor (`variance.json`)

Over the 8 test items, K=5 repeats:

| source | composite mean | std | spread |
|---|---:|---:|---:|
| judge only (re-judge fixed recaps) | 0.9537 | **0.0000** | 0.0000 |
| generation + judge (regenerate baseline) | 0.9515 | 0.0045 | 0.0112 |

The judge is deterministic; all run-to-run movement comes from temperature-0.2
generation. A single fixed system swings up to **0.011** composite. The
baseline-vs-best-DSPy Δ of **0.012** is ≈1 std of the difference (√(0.010²+0.005²)=0.011)
→ **not distinguishable from noise** at N=8.

---

## 5. Five side-by-side examples (baseline vs dspy_bootstrap_cot, run 1)

`st` = status_awareness, `fa` = faithfulness.

**remix-session** (doc-meta case, momentum=active) — *the one clear DSPy win:*
- BASE (c=0.83, st=0.5, fa=0.8): "Active development focusing on UX changes and testing connectors." *(judge: invents "testing connectors")*
- DSPy (c=1.00, st=1.0, fa=1.0): "Testing and adjusting the 'clip it' button UX and resolving content security policy issues for image loading."

**next-session** (thin + doc-meta, active) — *tie; baseline keeps the concrete PR#s:*
- BASE (c=1.00): "Continue the dp-prod perf/reliability arc. Verify the #3266 CPU fix (#3322) at the next early-PM-UTC peak."
- DSPy (c=1.00): "Continue improving dp-prod performance and reliability by verifying the CPU fix at the next early-PM-UTC peak."

**app-blocks-arc-complete** (thin, 0 msgs/commits, slowing) — *tie:*
- BASE (c=1.00): "The big App Blocks arc is shipped end-to-end; everything is merged."
- DSPy (c=1.00): "The big App Blocks arc is fully shipped, and the next steps involve merging changes for the next release."

**remix-templates** (commits present, slowing) — *both fail status-awareness identically:*
- BASE (c=0.80, st=0.0): "Feature: user-selectable render templates for the remix video app."
- DSPy (c=0.80, st=0.0): "Develop user-selectable render templates for the remix video app, allowing multi-clip layouts and transitions."

**initiatives-consolidation** (rich, active) — *baseline is arguably better (leads with status):*
- BASE (c=1.00, st=1.0): "Active; dual-host merge deferred. Consolidating on-demand initiative-scan.py output into a durable, queryable database."
- DSPy (c=1.00, st=1.0): "Consolidate the on-demand initiative-scan.py output into a durable, queryable format as part of phase 1."

Takeaway: DSPy helped on exactly one case (remix-session, where the baseline hallucinated),
tied on the rest, and neither system fixed the genuinely hard thin case (remix-templates).

---

## 6. The optimization that was run

- **Optimizer:** `BootstrapFewShot` (metric = the composite above, `metric_threshold=0.7`,
  `max_bootstrapped_demos=4`, `max_labeled_demos=4`). Deliberately modest per the brief.
- **Train set:** 10 initiatives. It bootstrapped **4 full traces after 4 examples**
  (1 round) — i.e. the first 4 train items each produced a recap scoring ≥0.7, which became
  the few-shot demonstrations (`spend-analytics`, `app-blocks-ux`, `task-spec-drafter`,
  `tekton-control-plane-ha`).
- **LM calls:** optimization ≈ **4 generation + 4 judge** calls per compile (Predict) /
  same for CoT. A full eval run (baseline + 4 variants + 2 compiles) is **≈70–90 total**
  LM calls against the shared 7B GPU.
- **Wall-time (per run):** baseline ≈3.5 s; each zero-shot pass ≈15–21 s (8 items);
  each BootstrapFewShot compile ≈9–11 s. A whole run ≈90–110 s. `MIPROv2` was **not** run
  (the brief said only if time permits, and BootstrapFewShot already reached parity — a
  larger instruction-search sweep is not justified by a within-noise result).

---

## 7. Recommendation

**(b) Keep the hand-written prompt (as-is / with the sibling task's tightening).**
Reasoning:
- DSPy does not measurably beat it (+0.012, within noise), and the *straightforward* way
  to use DSPy (`Predict`) is materially worse (−0.071). Getting to parity required
  stacking CoT **and** few-shot — more moving parts and more tokens for no measured gain.
- The weak spot that motivated the eval (describing the doc) is already handled by the
  current prompt on this model, so DSPy's headroom to help is small by construction.

**(c) Adopt the one insight without the runtime.** The only consistent DSPy edge was
**status-awareness**. Fold it into the hand prompt as a one-line instruction — e.g.
*"Lead with or include the current standing (in progress / shipped / blocked / stalled),
consistent with `momentum` and `next_step`."* This captures the ≈+0.05 status gain at zero
runtime cost. (Cheap to A/B with this same harness by editing `recap.SYSTEM_PROMPT`.)

**If (a) integrate DSPy anyway — the honest cost.** Even setting aside the null result:
- **New dependency:** `dspy-ai` + `litellm` pull a heavy transitive tree (tokenizers,
  pyarrow, pandas — native wheels that need `libstdc++`/`zlib` on `LD_LIBRARY_PATH`,
  awkward on NixOS and not in nixpkgs). Production `recap.py` today has **zero** third-party
  deps (stdlib `urllib`), which is a real virtue for a best-effort sync step.
- **A compiled-program artifact** to version/store and load at inference. There is a
  precedent in this fleet: `promptver/dspy-service` runs an **hourly `dspy-optimization`
  CronJob** and persists compiled programs to a **MinIO `dspy-models` bucket**
  (`MINIO_MODEL_BUCKET`), LM via an OpenAI base — so the pattern exists, but it is a whole
  optimize-store-load subsystem to stand up and keep fed with fresh train data.
- **An optimization step in CI/deploy** (or a timer), plus **inference overhead**: CoT
  emits reasoning tokens and few-shot inflates every prompt by ~4 demos — more latency and
  GPU per recap, on a step that is explicitly best-effort and cached-on-change.
- All of that to move a within-noise +0.012. Not worth it.

---

## 8. Residual risk / what a fuller eval would need

- **A stronger, independent judge.** The 7B judging itself is the biggest threat to
  validity. Re-scoring with a GPT-4-class judge (or a small set of **human labels** on the
  8×5 recaps) would confirm whether the +0.05 status signal is real and whether baseline's
  faithfulness edge holds.
- **Bigger N.** 8 test items gives ~0.011 noise on the composite; 30–50 items would tighten
  it enough to trust a 0.02 delta.
- **Reproduce the live doc-meta failure.** The weak spot didn't appear on the *current*
  prompt. If it shows up in *live* production recaps, capture those exact contexts and add
  them to the test set — the eval can't credit a fix for a bug it never observed.
- **MIPROv2 / instruction optimization.** BootstrapFewShot only tunes demos. MIPROv2 also
  searches instructions and could, in principle, do better — but chasing it is only
  warranted if a stronger judge first shows real headroom over the hand prompt.
- **Telemetry-on inputs.** This capture had telemetry off (degraded `momentum`). Re-running
  against store rows with live telemetry would test status-awareness under richer signal
  (affects both systems equally, but worth confirming).

---

### Reproduce
Harness + this doc: `scripts/initiatives/dspy-eval/` (see its `README.md`). Raw outputs:
`results-run{1,2,3}.json` (every recap + per-dim score), `variance.json`, `eval_set.json`,
`scan-days30.json` (frozen input capture).
