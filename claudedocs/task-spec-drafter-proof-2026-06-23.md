# Task-spec drafter v1 — proof run (2026-06-23)

End-to-end shadow proof of the continuous deep-context task-spec drafter (TICKET
source), run against the **live** ClickUp "To Schedule" triage queue. Nothing was
sent to clawgate, nothing dispatched, nothing written to ClickUp/repos/cluster.

## Run

- Mode: `shadow` · `DRAFTER_MAX_TICKETS=8` · per-ticket `claude -p` with a tight
  read-only `--allowedTools` allowlist (clickup get/comments, git/gh reads,
  kubectl reads — no write verbs).
- Live queue size at run time: **76** tickets (paginated `/view/6-901111220963-1/task`).
- Processed: **8/8**, parse failures: **0**, wall time ~14 min (~1.7 min/ticket).
- Result: **5 TASK · 3 NEEDS-DECISION · 0 VERIFY** → would notify clawgate
  (logged, not sent).

Full structured output: `task-spec-drafter-proof-queue-2026-06-23.{md,jsonl}`.

## Classifications (live tickets)

| Ticket | Class | One-line |
|---|---|---|
| 868k49796 | NEEDS-DECISION ⚠ | Civitai Link on .red — safety-flagged: TLS/cert extension could be intentional post-split scoping; only the in-repo `clFetch` defensive fix is unconditionally safe. |
| 868k47a7x | TASK | LoRA 'Train Further' must inherit source base model (Illustrious/Anima), not fall back to SDXL. |
| 868k41p7d | TASK | Search ranks single-token matches — set explicit Meilisearch `matchingStrategy` (blast-radius noted: affects all indexes). |
| 868k41k2f | TASK | `sizeKB` recorded in MB (off by ~1024×) — fix units + backfill mis-scaled rows. |
| 868k41byy | NEEDS-DECISION ⚠ | Merch + Blue-Buzz code program — product/legal/currency scoping, not dispatchable; safety-flagged (financial/abuse exposure). |
| 868k412h2 | NEEDS-DECISION | Audio slider UX — subjective + truncated desc, no spec of desired behavior → escalate to define. |
| 868k3z53w | TASK | Hires-fix workflows stuck in 'processing'; also make the 1h auto-refund verify blob delivery before refunding. |
| 868k3yur6 | TASK | Like/save button in model-add menu has no optimistic visual feedback. |

## Spot-checks vs reality (verification is genuine, not hallucinated)

Three records cross-checked against the live civitai checkout:

1. **868k41k2f** — claimed `sizeKB / 1024` MB-conversion paths exist. Confirmed:
   `src/pages/3d-models/[id]/edit.tsx:49` `formatSizeMB = (sizeKB) => (sizeKB/1024)...`
   and `[[...slug]].tsx:264 f.sizeKB / 1024`.
2. **868k41p7d** — claimed `src/components/Search/search.client.ts` exists and
   has **no** `matchingStrategy` set (relying on the default). Confirmed: file
   exists, `grep -c matchingStrategy = 0`.
3. **868k49796** — claimed `civitai-link-api.ts` `clFetch`/`getLinkInstances` and
   the worker consume it. Confirmed at the exact cited lines: `civitai-link-api.ts:12`
   (`clFetch`), `:27` (`getLinkInstances`), `civitai-link.worker.ts:288` (call site).

The model reached real source files, git log, gh PR search, and the feature-flag
config — i.e. it actually performed the VERIFY step, which is the whole point.

## Safety rule fired correctly (the meili-cron lesson, generalized)

- **868k49796**: refused to draft a confident "extend the Link TLS cert/endpoint
  to .red" task — flagged that the cert mismatch could be *intentional* scoping
  post .com/.red split, which it could not verify. Drafted only the
  unconditionally-safe in-repo defensive fix as a sub-point, escalated the rest.
- **868k41byy**: refused to draft "build Blue-Buzz code issuance" — on-platform
  currency with unverified product approval + undefined economics/anti-abuse =
  financial/abuse exposure. Escalated as a product/legal decision.

These are exactly the misfires the naive title-only drafter would have produced.

## The reframe holds

Of 8 live inbound tickets, **0 were stale/already-done** in this batch (a fresher
slice than the validated 8/8 set), but **3 of 8 dissolved into NEEDS-DECISION on
verification** rather than becoming confident tasks — and **2 of those carried a
safety flag**. Zach adjudicates a pre-verified queue of 8 (5 ready specs with
done-verifiers + blast-radius, 3 framed decisions) instead of reading 8 raw
tickets and cross-checking each himself.

## Confirmed: nothing was written / sent / dispatched

- Shadow logged the exact clawgate payload it *would* POST; sent nothing. The
  payload validates against the same `/api/send` contract as audit-on-push;
  clawgate is up (0.7.30, health OK) — so `on` mode will deliver.
- No ClickUp comment/status/assign calls (allowlist excludes all write verbs).
- `civitai` repo unchanged, still on `main`, clean working tree.
- No `kubectl apply/edit/delete/scale`.

## Caveats surfaced by the proof

- **Nondeterminism**: across repeated runs the same ticket sometimes flips between
  adjacent classes (868k47a7x: VERIFY↔TASK; 868k49796: TASK↔NEEDS-DECISION). The
  borderline TASK/NEEDS-DECISION boundary is exactly where the safety rule should
  bias conservative — and it does — but it means the queue is not bit-stable
  run-to-run. Track the adjudication ratio over runs, not single records.
- **Cost**: 8 tickets ≈ 14 min of Opus with real tool calls = dollars, not cents.
  The full 76-ticket queue is materially more. Measure a full-queue cost before
  scheduling daily over everything; consider a cheaper reasoning model for the
  prod CronJob path (as standup-triage uses OpenRouter).
- **No de-dup / no accuracy write-back yet** — see README caveats.
