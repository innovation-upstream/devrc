---
name: repo-cos
description: "READ the weekly repo chief-of-staff proposals and evaluate them WITH Zach; also re-run the scan, tune the signals/model/repo-list, manage the timer. Use for: repo proposals, the repo chief-of-staff, \"the ideas the agent emailed me\", triaging the weekly repo suggestions, repo-cos."
---

# repo-cos — repo chief-of-staff

"Agents bring me ideas" (CEO model): a weekly job scans repos → LLM synthesizes the top ~5
**bounded, evidence-backed** proposals → emails Zach. Code: `~/workspace/devrc/scripts/repo-cos/`
(devrc `main`). Deterministic-first (grep/git signals, capped per repo) → single cheap LLM call
→ digest. Design/internals live in `scripts/repo-cos/README.md`; this file is the operator view.

## ▶ Read + evaluate the latest proposals (the main cross-session entry point)
Every run persists its output so another session reads the **exact** set that was emailed — no
re-rolling the (non-deterministic) LLM:
```bash
python3 -c 'import json; d=json.load(open("/home/zach/.config/repo-cos/latest.json")); \
print(d["generated_at"], "emailed=" , d["emailed"]); \
[print(f"\n{i+1}. {p[\"title\"]}\n   why: {p.get(\"why\",\"\")}\n   evidence: {p.get(\"evidence\")}") for i,p in enumerate(d["proposals"])]'
# prior weeks: ls ~/.config/repo-cos/history/    (dated copies)
```
Then **evaluate collaboratively:** walk the proposals with Zach, note which are worth doing, and
for each greenlit one dispatch an implementation agent (subagent + tests + PR, per the standing
default) or open the fix directly. `latest.json` fields: `generated_at`, `emailed` (weekly send
vs manual dry-run), `subject`, `candidate_count`,
`proposals[{title, repo, evidence[file:line], why, effort, approach, ci_verifiable}]`.

## Run / regenerate (workbench; creds in ~/.config/repo-cos/env + SOPS)
```bash
export SOPS_AGE_KEY_FILE=~/workspace/homelab-talos/.secrets/age.key
set -a; . ~/.config/repo-cos/env; set +a          # OPENROUTER_API_KEY
cd ~/workspace/devrc
nix-shell -p 'python3.withPackages(p:[p.requests])' --run 'python scripts/repo-cos/scan.py --dry-run'      # scan → LLM → print (writes latest.json)
nix-shell -p 'python3.withPackages(p:[p.requests])' --run 'python scripts/repo-cos/scan.py --no-llm'        # FREE pre-scan smoke (no key/spend)
nix-shell -p 'python3.withPackages(p:[p.requests])' sops --run 'python scripts/repo-cos/scan.py --email'    # actually send (needs sops for the app-password)
# flags: --repos "p1,p2"  --top N  --limit-candidates N  --model <id>  --json  --candidates-only  --no-fetch
```

## The weekly automation (LIVE)
- **`repo-cos.timer`** (systemd USER timer, **Mon 08:00**, workbench-only via `serverMode`) →
  `repo-cos.service` → committed wrapper `scripts/repo-cos/run-weekly.sh` → `scan.py --email`.
  Home-manager unit in `devrc/nix/home.nix` (mirrors `mail-actions-archive`). Check/trigger:
  ```bash
  systemctl --user list-timers | grep repo-cos
  systemctl --user start repo-cos.service && journalctl --user -u repo-cos.service -n 30 --no-pager
  ```
- **SELF-HOSTED mail (default; verified live):** sends via Zach's own postfix relay —
  `From: repo-cos@mail.zacx.dev` (DKIM-signed), `Reply-To: repo-cos@inbox.zacx.dev`, `To:` his
  Gmail. The relay lives in the **production** cluster, reached by `kubectl port-forward` (needs
  `REPO_COS_PROD_KUBECONFIG`). `email_send.py`; fallback `REPO_COS_SEND=gmail` (old Gmail-SMTP +
  app-password). Send+DKIM are entirely his infra; Gmail is only the recipient.
- **⚠ Two-cluster dependency:** the weekly send needs **production** (relay) + **homelab**
  (Postgres) + `kubectl`/`psycopg2` + both kubeconfigs (`run-weekly.sh` sets them). A relay
  hiccup fails the send loudly (rc=1 → unit `failed`, digest NOT silently dropped); a Postgres
  hiccup → no feedback that run. **Watch the FIRST Monday fire** — the systemd minimal-env
  reaching BOTH cluster APIs is the one thing not yet proven under the timer (manual runs work).

## REPLY-FEEDBACK (steer it by replying)
Reply to the digest → routes `repo-cos@inbox.zacx.dev` → his MX → mail-receiver → **homelab
Postgres `mail` table**; the NEXT run reads it from Postgres (`feedback.py` →
`mail-actions/_db.py`, `kubectl port-forward` to homelab, needs `KUBECONFIG`=homelab). Fallback:
`REPO_COS_REPLY_SRC=imap` (Gmail IMAP). Ownership gate = EXACT `from_addr` (not substring — the
Reply-To is public + the receiver stores spoofable From:). Best-effort; `--no-feedback` skips it.
Used TWO ways: deterministic exclusions (`exclusions.py`) and context-injection for nuance.

- **⚠ IMPORT GOTCHA:** `feedback.py` loads `mail-actions/_db.py` by explicit **importlib path**,
  NOT `sys.path.insert` — because `mail-actions/llm.py` would shadow repo-cos's `llm.py` and
  break synthesis (`module 'llm' has no attribute 'synthesize'` — caught live). Don't "simplify"
  it back to a path insert.
- **⚠ CUTOFF GOTCHA (fixed):** the reply-fetch cutoff + positional mapping read
  `last_emailed.json` (only `--email` writes it), NOT `latest.json` (EVERY run incl. dry-runs
  overwrites it → its `generated_at` drifts to "now" and the reply becomes unfindable after one
  dry-run). Don't repoint it at `latest.json`.
- **Position-mapping caveat:** exclusions assume you reply to the LATEST digest (maps via
  `last_emailed.json`). Reply to an older one after a newer send → possible mis-map.
  Thread-matching (`In-Reply-To` → exact digest) is the noted v2. Confirm a run applied feedback
  via `feedback: applied reply …` + `excluding N repo(s):` in the journal.
- **Context steering (nuance):** the reply text is also injected into the prompt ("focus on X
  within a repo"); the evidence/anti-slop validation still runs after, so a reply can't fabricate
  `file:line` refs. Weaker than the exclusion filter — don't rely on it for "stop proposing repo
  Y" (use the positional exclusion).

### THREE deterministic intents (`exclusions.py`, positional mapping against `last_emailed.json`)
This distinction matters. State for all three: **`~/.config/repo-cos/exclusions.json`**,
hand-editable, three keys `"repos"` / `"dismissed"` / `"approved"`. `scan.py --show-exclusions`
shows all sections; the digest footer lists them. Verified live end-to-end.

- **Approve** = "yes, do this." Signals: `approve`/`yes`/`lgtm`/`ship it`/`do it`/`👍`/`+1` with
  no negative on the line (a mixed "approve but skip" → dismiss — **negative wins**). → POSTs a
  durable **clawgate Task** (`clawgate.py` → `POST /api/tasks`, creds from
  `~/.claude/clawgate.env`, one-tap Dispatch) — clawgate is the dispatch queue, so this is the
  CEO-model "act on approval" leg. The payload carries the dispatch config (so Dispatch is a
  pre-filled confirm, not a blank form: `resolve_repo_fullname()` maps the proposal's `repo`
  basename → GitHub `owner/name`, `post_task(…, repo=…)` sets it, clawgate migration 0014
  `task_dispatch_config`) plus an `initiative:<slug>` TAG (clawgate 0.7.75+) read
  POSITIONALLY out of `last_emailed.json` — never re-resolved at approve time. **Only on a
  successful POST** is its evidence suppressed so it won't re-nag (a failed POST re-proposes next
  week = natural retry). State key `"approved"` (evidence ref → `{clawgate_task_id, …}`).
  - ⚠ **no `model` is ever posted** — `scan.py` is the only caller and never passes one, so the
    card inherits clawgate's own default. (`DEFAULT_MODEL = deepseek/deepseek-v4-flash` in
    `scan.py`/`llm.py` is the **OpenRouter synthesis** model — a different thing; don't conflate
    them.)
  - 🔴 **A tag must NEVER cost an approval.** Suppression is POST-success-gated, so a tagged POST
    retries EXACTLY ONCE with `tags` removed **on HTTP 400 ONLY**. Not 4xx generally: 401/403/404/429
    just burn a second doomed call, and **408 is the one shape where a retry could double-POST two
    Task cards** (a proxy timeout raised after the origin already created the Task). ⚠ that 400 is
    a **cross-repo wire contract with NO test on either side** — a one-off live check against a
    hard-validated `runbook:` 400 is the only evidence. No `runbook:` tags are emitted.
  - The slug matches the `↳ relates to:` breadcrumb he saw in the digest. Tags are filtered by
    `routing.taggable_slug` — a **six-reason denylist, all logged**: `empty` / `too-short` (<3) /
    `no-letters` / **`not-lowercase`** / `opaque-id` (ClickUp-style tokens) / `generic` (all
    filler) — then by `clawgate.guard_tags` (per-tag, byte-equality) → `normalize_tags`. Full
    grammar and the `runbook:` seam: **`scripts/repo-cos/README.md` § "Approve → clawgate Task
    (Stage 0.5b)"**; the non-list/non-tuple shape check (a bare tag string must be passed as
    `[tag]` or ALL tags are dropped) is in the `clawgate.py` `guard_tags` docstring. Two things to
    carry in your head: **`not-lowercase` drops ANY uppercase**, not just SCREAMING-CASE
    (`Remix-Platform` drops too) — because the tag grammar lowercases, so an uppercase slug would
    emit a tag that no longer equals its ledger slug and **the initiatives-side join would
    silently miss**; and `initiatives/dispatch.py` carries a **deliberate duplicate** of
    `normalize_tag`/`normalize_tags` (this repo is the documented source of truth) but has **no
    `guard_tags` at all**, so "change both together" is already only half-true — re-check both
    when the grammar moves.
- **Repo-level exclusion** = "the whole project is on hold / not mine." Signals:
  `pause`/`paused`/`on hold`; `not (the|code) owner`/`deprecated`/`archived` (→ **permanent**).
  **HARD-DROPS the repo** from the scan. State key `"repos"`; `resume <repo>` (or edit the file)
  to re-enable.
- **Recommendation-level DISMISSAL** = "the repo's fine, just not THIS proposal." Signals:
  `skip`/`not needed`/`not relevant`/`dismiss`/`no` **with no pause/owner language**. Suppresses
  ONLY that proposal's **evidence `file:line`** (pre-scan drops those candidates) — the repo
  STAYS in scope for other ideas. State key `"dismissed"` (keyed by evidence ref); edit the file
  to un-dismiss.
- **Precedence:** repo-pause > repo-owner/dead > recommendation-dismiss. So "kubeclaw is paused"
  = repo; "not needed, skip" / "we dont own the 3d model FEATURE, skip" = dismiss (keeps the repo).

## Tune it
- **Signals:** `prescan.py` — marker regex, skipped-test patterns (py/js/go/rust), churn window,
  large-file LOC, per-signal caps. Add a signal by adding a `scan_*` + wiring it into `scan_all`.
- **Anti-slop (the quality anchor):** `llm.py` — `_ref_known` (evidence must exactly match a real
  candidate ref), the hard `--top` cap, ci-verifiable-first sort, and `synthesize`'s
  retry-on-empty (DeepSeek rotates even at temp=0). Don't loosen `_ref_known` back to
  prefix-matching (that lets invented refs through).
- **Scope:** `DEFAULT_REPOS` in `scan.py` (his repos + civitai client repos). `naida`/`vetr` are
  laptop-only (`~/workspace/scratch/`) → not visible from the workbench yet.
- **Feedback/exclusions:** `feedback.py` (**Postgres reply-read by default** —
  `REPO_COS_REPLY_SRC=imap` is the fallback — de-quote), `exclusions.py` (`parse_reply`
  positional+keyword, `apply`, `filter_repos`, `_canon_key` basename-normalization,
  `load_last_emailed`, plus the approve/tag surface: `apply_approvals`, `attach_related`,
  `write_last_emailed`, `filter_candidates`, `approved_entries`). Reply intent keywords live in
  `parse_reply`.
- **Tests:** `nix-shell -p 'python3.withPackages(p:[p.pytest p.requests p.psycopg2])' --run 'python -m pytest scripts/repo-cos/tests -q'`
  — **330 passed, 1 skipped** (the skip is the live drift check).
  - **Corpus assertions are PROPERTIES, not counts** (`test_routing.py`): they used to pin
    absolute totals (`len(corpus) == 142`) and rotted on three consecutive days as the live store
    grew 139→144. Assert floors/set-equality/ratios (`len(corpus) > 100`, `emitted == taggable`,
    `set(dropped) <= KNOWN_DROP_REASONS`) — never a number that tracks live data.
  - **Opt-in live drift check:** `REPO_COS_LIVE_DRIFT_CHECK=1` un-skips
    `test_fixture_matches_the_live_initiatives_store` (a real cross-cluster read). `""`/`0`/
    `false`/`no`/`off` = OFF — an earlier `not os.environ.get(...)` made every non-empty value
    opt IN, so `=0` and `=false` performed a live read from an otherwise hermetic suite.

## Honest limits (state these, don't oversell)
- **Marker-driven ceiling** — it prioritizes EXPLICIT signals (TODO/skip/`latest`/churn), not
  subtle architectural issues. Grounded + cheap by design.
- **Output rotates** run-to-run (LLM non-determinism); fine for a weekly digest, and
  retry-on-empty prevents a blank email when signals exist.
- **Security-flavored proposals are LEADS, not verdicts** (e.g. "add auth to X" — the model
  inferred it from a signal; verify there's no middleware-level auth before acting).
- **Feedback is reply-driven, not outcome-driven** — replying steers the next digest (above), but
  it does NOT yet track which proposals get ACTED ON → shipped (the remaining v1: acted-on→PR as
  a real artifact-verifier). **ROTATE** the OpenRouter key in `~/.config/repo-cos/env` (pasted in
  transcripts).
