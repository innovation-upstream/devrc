# Decision record — the hosted `subsystem-store-api` was built, shipped, and RETIRED

**Status:** retired on evidence, 2026-08-25. Supersedes the "hosted is an entry-level
advisory" decision of 2026-08-20 (`claudedocs/handoff-subsystem-store.md`).
**What was removed:** `scripts/subsystem-store-api/` (server, README, Dockerfile,
build-push, seed, byte-identity verifier) and `scripts/tests/test_subsystem_store_api.py`.
**What was NOT removed:** the local index store and its readers —
`scripts/lib/subsystem_recall.py`, `subsystem_touch.py`, `subsystem_resolver.py`. Those are
alive, heavily used, and are not what this record retires.

Read this before proposing a hosted store again. It was built properly, it worked, and it
lost on measurement — the same way `claudedocs/decision-subsystem-store-rejected-2026-08-11.md`
lost, and for a related reason.

---

## What was built, and why it was reasonable

`/analyze-service` and `/handoff` write per-scope markdown entries into a local index at
`~/.claude/analyze-service-index/<scope>/`. The goal stated at the top of the handoff was
**"reachable from anywhere"** — the index is the most useful thing a session can read first,
and it lived on one disk.

So a read-only HTTP layer was put over the existing reader: two routes (`recall`, `search`)
serving `render_text()`/`render_search()` verbatim, a bearer token, a required
`CF-Connecting-IP`, a trusted-proxy chain, per-request audit logging, a snapshot-freshness
header, and a byte-identity verifier that proved the hosted rendering matched the local one.
It shipped across nine merged PRs in devrc and homelab-infra, went public at `store.zacx.dev`
on 2026-08-18, ran with restarts=0, and survived a real exposure incident that produced a
CI gate (`check-relay-guard.py`) still in use.

**None of that was wasted and none of it was wrong.** The engineering was sound. The
premise underneath it was not.

## The measurement that retired it

The server audits every `/api/*` request, successes included — the `_audit(path, 200, status)`
call sits on the 200 path, and `/healthz` is the single deliberate exemption. **So an absence
of 200s in that log is real evidence, not a gap in instrumentation.** That property is what
makes the numbers below a measurement rather than an impression.

Read from the live pod's audit log on 2026-08-25:

| Measurement | Value |
|---|---|
| Successful authenticated requests, pod's entire life | **309** |
| Distinct days on which they occurred | **1** (2026-08-20, 19:07–23:45) |
| Distinct source IPs among them | **1** — the session that built it |
| Legitimate requests since that window | **0** |
| Legitimate requests in the last 24h | **0** |
| Everything else in the log | internet credential scanners, all 401 |

The 309 include a `search/nosuchscope` negative control, which is the tell: that is a build
session exercising its own service, not a consumer using it.

The command that produced them, recorded for provenance:

```bash
KUBECONFIG=$KC_HOMELAB kubectl -n subsystem-store logs deploy/subsystem-store-api \
  | grep audit | grep 'result=200'
# then bucket by date and by ip=; compare the ip= set against the seed/verify credential's
# own source. `grep -c "peer=untrusted"` beside a NON-zero `peer=trusted` is the positive
# control that the log is wired to something — report the pair, never a bare zero.
```

🔴 **This command stops working, and that is not a defect in the record.** The pod is being
torn down in a separate `homelab-talos` PR; when it goes, so does the audit log that is the
only source for these numbers. They are reproducible only until then. A future reader who
wants to re-derive rather than trust this table must do it against a *rebuilt* service and
its own traffic — which is exactly the condition at the bottom of this file.

## No client was ever written

The 2026-08-20 decision (`claudedocs/handoff-subsystem-store.md`, the "hosted is an
ENTRY-LEVEL ADVISORY" block) settled a genuine three-way question and settled it correctly:
local stays authoritative and always rendered; hosted is consulted for a **manifest** of
refs it holds that local does not; the difference is *communicated*, never merged. Two
alternatives were rejected with measurement behind each — reading hosted outright was a
regression on both hosts, and a per-scope fallback reached 9 of 70 entries because the loss
is at entry granularity and a scope-granular trigger cannot see it.

**That client was never implemented.** `subsystem_recall.py` has no hosted path, no advisory
path, no manifest path, and no `--source hosted` flag. Nor does any other reader:

```bash
# ANCHORED. An import statement, not a word anywhere in the file.
grep -rnE '^[[:space:]]*(import|from)[[:space:]]+(urllib|requests|httpx|aiohttp|http)([. ]|$)' \
  scripts/lib/subsystem_*.py scripts/subsystem-*.py
```

returns nothing (exit 1) as of 1f7019e0, over the four readers that glob matches —
`subsystem_recall.py`, `subsystem_resolver.py`, `subsystem_touch.py`, `subsystem-audit.py`.
Every consumer of the index (`/resume`, `/handoff`, `/analyze-service`, `/prune-index`,
`subsystem-index`, `resume-state.sh`, `subsystem-audit.py`, the collector) imports the reader
as a **local-disk library**. The handoff block itself flagged this, in its own words:
*"THE MANIFEST DOES NOT EXIST YET, AND THIS IS THEREFORE NOT A CLIENT-ONLY PHASE 2."*
It was right, and it stayed true for five days.

🔴 **The `-E` and the `^[[:space:]]*(import|from)` anchor are the whole command.** The first
version of this record printed the unanchored `grep -rn 'urllib\|requests\|httpx\|urlopen'`
and asserted it "returns nothing". Running it returns **10 matches, exit 0** — nine in
`subsystem_touch.py` and one in `subsystem-audit.py`, every one of them the prose phrase
"pull requests". That is the identical false positive `scripts/tests/test_present_measure.py
::test_the_http_client_predicate_is_an_import_not_a_word` was written against, handed to the
reader as the way to check the claim. The authority is
`scripts/present/measure.py` → `reaches_http_client()`, which the suite drives against both
controls; this grep is the anchored hand version of its first two clauses.

⚠ **Neither the grep nor the predicate proves "no network call".** Both
`subsystem_touch.py` and `subsystem-audit.py` shell out to `gh pr view`, which is a network
hop. What is pinned is narrower and is the thing that matters here: **no direct HTTP client
that could reach a hosted index store.** `reaches_http_client()` enumerates exactly what it
sees and what walks past it.

Measured against the live guard, mutation by mutation:

| shape injected | verdict | killed by |
|---|---|---|
| `import requests` in a new `scripts/lib/subsystem_hosted.py` | RED | the client count |
| `subprocess.run(["curl", …])`, no import statement | RED | the client count |
| `importlib.import_module("requests")` | RED | the client count |
| `from . import _hosted_client` in a **new** module | RED | the reader-set ledger |
| `from . import _hosted_client` appended to the **existing** `subsystem_recall.py` | **GREEN — not covered** | — |
| `scripts/subsystem-audit.py` renamed out of the glob | RED | the reader-set ledger |

The one green is the whole residual, stated rather than papered over: **a client added to a
reader that ALREADY EXISTS, through indirection the predicate cannot follow, is invisible to
this row.** Everything arriving as a new file is caught whatever it contains, because the
reader set is globbed and pinned two-way — which is why the fourth and fifth rows differ by
nothing except which file the identical line went into.

So the service was complete, correct, well-tested and hosted, with a client set of size
zero — **and every gate stayed green the entire time.** A test suite cannot fail for want of
a caller. This is the shape worth carrying forward; it is now a measured row on the
explainer page (`seam.store_api` in `scripts/present/measure.py`) rather than only prose.

## The missing piece was a SYNC, not more server

This is the part that would be re-derived wrongly, so it is stated flatly.

The hosted copy was populated by `seed.sh --push`, **by hand**. Nothing synced it. It
mirrored the workbench pile only, and the two local stores were measured **disjoint** on
2026-08-20 — overlap zero across all 21 laptop entries, seven laptop scopes present on no
other copy, the laptop a live writer.

So the entire stated use case — *reach the index away from the workbench* — was the one
thing the deployment could not do. From the laptop, the hosted store returned a healthy
`200 scope-absent` for scopes that host had written itself. Two independent-looking sources
agreeing "nothing recorded" about entries that demonstrably existed.

**More server would not have fixed that.** A manifest, a read-through cache, a write path,
a second route — each adds surface to a copy that is stale by construction because no
mechanism updates it. What the problem needed was replication between two disks. That is a
sync, and building an HTTP read API is not a step toward one.

The generalizable error: **an availability mechanism was built for what was actually a
consistency problem.** The hosted store made a stale copy reachable. Reachability was never
the binding constraint.

## What it would take to rebuild this

Not a prohibition — a bar, with the check named.

Rebuild when **both** hold:

1. **Measured demand to read the index away from the workbench.** Not "it would be nice
   to have"; a count. Sessions that ran on the laptop, tried to recall a scope the laptop
   does not hold, and were blocked by it. The local reader already exits non-zero and
   prints `scope-absent`, so the signal is producible without any new service.
   **The check:** a query over `activity.events` for laptop-host sessions hitting
   `scope-absent`/`scope-empty` on a scope the workbench holds, reported as a count over a
   stated window — with the positive control that the query returns non-zero for a case
   known to exist, since a zero from a query wired to nothing looks the same.

2. **A sync exists first, or is built first.** Bidirectional replication of the two local
   stores — each is a per-scope git repo already, which is the cheap path — so that whatever
   is served is not stale by construction. **The check:** an entry written on either host
   appears on the other without a human running a push.

Condition 2 is the real one. If it is met, ask again whether an HTTP layer is needed at
all: two synced disks may make the hosted read redundant, which is the outcome this record
expects and the reason it does not carry a rebuild plan.

**Who settles it:** whoever is holding the "I cannot reach my index" complaint, against the
count from condition 1. Absent that count, there is no work item here and this record does
not mint one.

## Errors worth not re-deriving

1. **"It is deployed and healthy" is not "it is used."** The pod reported ready with
   restarts=0 for five days while serving nobody. Liveness and adoption are independent, and
   only one of them was instrumented for.
2. **A decision to build a client is not a client.** The advisory design was recorded as
   DECIDED and read afterwards as though the behaviour existed — the same "a type
   declaration is not a code path" failure this repo keeps re-learning, one level up.
3. **The audit log was the cheapest possible adoption check and was never read until the
   day it retired the service.** It existed from day one. Reading it on 2026-08-21 would
   have cost one command.

## Pointers

- `claudedocs/handoff-subsystem-store.md` — the full build history, the exposure incident,
  and the 2026-08-20 advisory decision. Carries a superseding note pointing here.
- `claudedocs/decision-subsystem-store-rejected-2026-08-11.md` — the earlier rejection. Its
  "opt-in survives when it rides an existing ritual and dies when it *is* the ritual" finding
  is the same failure mode arriving by a different route.
- `claudedocs/proposal-subsystem-store-homelab.md` — the hosting design. Historical.
- `scripts/lib/subsystem_recall.py` — the reader that was always the real interface.
- `scripts/present/measure.py` → `m_store_api_clients` — the measured row that pins **zero
  direct HTTP clients** across every reader `scripts/lib/subsystem_*.py` and
  `scripts/subsystem-*.py` matches, so a reintroduced client is visible **including one that
  arrives as a new module**: the reader set is globbed, not listed. Read
  `reaches_http_client()` for what the predicate sees — it is narrower than "no network
  hop", and deliberately so, since two of these readers already run `gh pr view`.
