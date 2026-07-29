---
name: repo-cos
description: Operate the "repo chief-of-staff" — a weekly agent that scans Zach's codebases for improvement opportunities (TODO/FIXME, skipped tests, unpinned `latest` tags, churn hotspots, large files) → cheap LLM synthesis → ranked, evidence-backed proposals emailed to his Gmail. Its PRIMARY cross-session use: READ the latest proposals and COLLABORATIVELY EVALUATE them with him (which to act on → dispatch/PR). Also: run/regenerate the scan, tune the signals/model/repo-list, manage the weekly timer. Use when the user mentions repo proposals, the repo chief-of-staff, "the ideas the agent emailed me", evaluating/triaging the weekly repo suggestions, repo-cos, or improving the proposal quality.
---

# repo-cos — repo chief-of-staff

"Agents bring me ideas" (CEO model): a weekly job scans repos → LLM synthesizes the top ~5 **bounded, evidence-backed** proposals → emails Zach. Code: `~/workspace/devrc/scripts/repo-cos/` (devrc `main`). Deterministic-first (grep/git signals, capped per repo) → single cheap LLM call → digest. Built 2026-07-01 (PR #41 + follow-ups).

## ▶ Read + evaluate the latest proposals (the main cross-session entry point)
Every run persists its output so another session reads the **exact** set that was emailed — no re-rolling the (non-deterministic) LLM:
```bash
python3 -c 'import json; d=json.load(open("/home/zach/.config/repo-cos/latest.json")); \
print(d["generated_at"], "emailed=" , d["emailed"]); \
[print(f"\n{i+1}. {p[\"title\"]}\n   why: {p.get(\"why\",\"\")}\n   evidence: {p.get(\"evidence\")}") for i,p in enumerate(d["proposals"])]'
# prior weeks: ls ~/.config/repo-cos/history/    (dated copies)
```
Then **evaluate collaboratively:** walk the proposals with Zach, note which are worth doing, and for each greenlit one dispatch an implementation agent (subagent + tests + PR, per the standing default) or open the fix directly. `latest.json` fields: `generated_at`, `emailed` (was it the weekly send or a manual dry-run), `subject`, `candidate_count`, `proposals[{title, repo, evidence[file:line], why, effort, approach, ci_verifiable}]`.

## Run / regenerate (workbench; creds in ~/.config/repo-cos/env + SOPS)
```bash
export SOPS_AGE_KEY_FILE=~/workspace/homelab-talos/.secrets/age.key
set -a; . ~/.config/repo-cos/env; set +a          # OPENROUTER_API_KEY
cd ~/workspace/devrc
nix-shell -p 'python3.withPackages(p:[p.requests])' --run 'python scripts/repo-cos/scan.py --dry-run'      # scan → LLM → print (writes latest.json)
nix-shell -p 'python3.withPackages(p:[p.requests])' --run 'python scripts/repo-cos/scan.py --no-llm'        # FREE pre-scan smoke (no key/spend)
nix-shell -p 'python3.withPackages(p:[p.requests])' sops --run 'python scripts/repo-cos/scan.py --email'    # actually send (needs sops for the app-password)
# flags: --repos "p1,p2"  --top N  --limit-candidates N  --model <id>  --json
```

## The weekly automation (LIVE)
- **`repo-cos.timer`** (systemd USER timer, **Mon 08:00**, workbench-only via `serverMode`) → `repo-cos.service` → committed wrapper `scripts/repo-cos/run-weekly.sh` → `scan.py --email`. Home-manager unit in `devrc/nix/home.nix` (mirrors `mail-actions-archive`). Check/trigger:
  ```bash
  systemctl --user list-timers | grep repo-cos
  systemctl --user start repo-cos.service && journalctl --user -u repo-cos.service -n 30 --no-pager
  ```
- **SELF-HOSTED mail (defaults; verified live):** the digest **sends via Zach's own postfix relay** — `From: repo-cos@mail.zacx.dev` (DKIM-signed, clean Gmail deliverability), `Reply-To: repo-cos@inbox.zacx.dev`, `To:` his Gmail. The relay lives in the **production** cluster, reached by a `kubectl port-forward` (needs `REPO_COS_PROD_KUBECONFIG`); STARTTLS with hostname-verify OFF on the localhost hop, no SMTP auth (MYNETWORKS-trusted). `email_send.py`. Fallback: `REPO_COS_SEND=gmail` (the old Gmail-SMTP + app-password path). Send+DKIM are entirely his infra; Gmail is only the recipient.
- **REPLY-FEEDBACK (steer it by replying):** reply to the digest → it routes `repo-cos@inbox.zacx.dev` → his MX → mail-receiver → **homelab Postgres `mail` table**, and the NEXT run **reads it from Postgres** (`feedback.py` → `mail-actions/_db.py`, `kubectl port-forward` to homelab, needs `KUBECONFIG`=homelab). Fallback: `REPO_COS_REPLY_SRC=imap` (Gmail IMAP). Ownership gate = EXACT `from_addr` (not substring — the Reply-To is public + the receiver stores spoofable From:). Best-effort; `--no-feedback` skips it. It's used TWO ways: (1) **deterministic repo EXCLUSIONS** (`exclusions.py`) and (2) context-injection for nuance.
  - **⚠ IMPORT GOTCHA:** `feedback.py` loads `mail-actions/_db.py` by explicit **importlib path**, NOT `sys.path.insert` — because `mail-actions/llm.py` would shadow repo-cos's `llm.py` and break synthesis (`module 'llm' has no attribute 'synthesize'` — caught live). Don't "simplify" it back to a path insert.
  - **⚠ Two-cluster dependency:** the weekly send now needs the **production** cluster (relay) + **homelab** cluster (Postgres) + `kubectl`/`psycopg2` + both kubeconfigs (`run-weekly.sh` sets them). Best-effort: a relay hiccup fails the send loudly (rc=1 → unit `failed`, digest NOT silently dropped); a Postgres hiccup → no feedback that run. **Watch the FIRST Monday fire** — the systemd minimal-env reaching BOTH cluster APIs is the one thing not yet proven under the timer (manual runs work).
  - **THREE deterministic intents (`exclusions.py`, positional mapping against `last_emailed.json`) — this distinction matters:**
    - **Approve** = "yes, do this." Signals: `approve`/`yes`/`lgtm`/`ship it`/`do it`/`👍`/`+1` (with no negative on the line — a mixed "approve but skip" → dismiss, negative wins). → **POSTs a durable clawgate Task** (`clawgate.py` → `POST /api/tasks`, creds from `~/.claude/clawgate.env`, one-tap Dispatch) for that proposal — **the payload now carries the dispatch config** so the from-card Dispatch is a pre-filled confirm, not a blank form: `clawgate.py`'s `resolve_repo_fullname()` maps the proposal's `repo` basename → GitHub `owner/name` (via `DEFAULT_REPOS` path → git remote) and `post_task(…, repo=…, model=…)` sets it (model default = **deepseek**, clawgate migration 0014 `task_dispatch_config`) — and — **only on a successful POST** — suppresses its evidence so it won't re-nag (a failed POST re-proposes next week = natural retry). State: `exclusions.json` `"approved"` (evidence ref → `{clawgate_task_id, …}`). This is the CEO-model "act on approval" leg → clawgate is the dispatch queue.
    - **Repo-level exclusion** = "the whole project is on hold / not mine." Signals: `pause`/`paused`/`on hold`; `not (the|code) owner`/`deprecated`/`archived` (→ **permanent**). **HARD-DROPS the repo** from the scan. State: `exclusions.json` `"repos"`; `resume <repo>` (or edit file) to re-enable.
    - **Recommendation-level DISMISSAL** = "the repo's fine, just not THIS proposal." Signals: `skip`/`not needed`/`not relevant`/`dismiss`/`no` **with no pause/owner language**. Suppresses ONLY that proposal's **evidence `file:line`** (pre-scan drops those candidates) — the repo STAYS in scope for other ideas. State: `exclusions.json` `"dismissed"` (keyed by evidence ref); edit the file to un-dismiss.
    - **Precedence:** repo-pause > repo-owner/dead > recommendation-dismiss. So "kubeclaw is paused" = repo; "not needed, skip" / "we dont own the 3d model FEATURE, skip" = dismiss (keeps the repo). `scan.py --show-exclusions` shows BOTH sections; the digest footer lists them. Verified live end-to-end.
  - **⚠ CUTOFF GOTCHA (fixed):** the reply-fetch cutoff + positional mapping read `last_emailed.json` (only `--email` writes it), NOT `latest.json` (EVERY run incl. dry-runs overwrites it → its `generated_at` drifts to "now" and the reply becomes unfindable after one dry-run). Don't repoint it at `latest.json`.
  - **Context steering (nuance):** the reply text is also injected into the prompt ("focus on X within a repo"); the evidence/anti-slop validation still runs after, so a reply can't fabricate `file:line` refs. Weaker than the exclusion filter — don't rely on it for "stop proposing repo Y" (use the positional exclusion).
  - **Position-mapping caveat:** exclusions assume you reply to the LATEST digest (maps via `last_emailed.json`). Reply to an older one after a newer send → possible mis-map. Thread-matching (`In-Reply-To` → exact digest) is the noted v2. Confirm a run applied feedback via `feedback: applied reply …` + `excluding N repo(s):` in the journal.

## Tune it
- **Signals:** `prescan.py` — marker regex, skipped-test patterns (py/js/go/rust), churn window, large-file LOC, per-signal caps. Add a signal by adding a `scan_*` + wiring it into `scan_all`.
- **Anti-slop (the quality anchor):** `llm.py` — `_ref_known` (evidence must exactly match a real candidate ref), the hard `--top` cap, ci-verifiable-first sort, and `synthesize`'s retry-on-empty (DeepSeek rotates even at temp=0). Don't loosen `_ref_known` back to prefix-matching (that lets invented refs through).
- **Scope:** `DEFAULT_REPOS` in `scan.py` (his repos + civitai client repos). `naida`/`vetr` are laptop-only (`~/workspace/scratch/`) → not visible from the workbench yet.
- **Feedback/exclusions:** `feedback.py` (IMAP reply-fetch, de-quote), `exclusions.py` (`parse_reply` positional+keyword, `apply`, `filter_repos`, `_canon_key` basename-normalization, `load_last_emailed`). Reply intent keywords live in `parse_reply`.
- **Tests:** `nix-shell -p 'python3.withPackages(p:[p.pytest p.requests])' --run 'python -m pytest scripts/repo-cos/tests -q'` (117 pass).

## Honest limits (state these, don't oversell)
- **Marker-driven ceiling** — it prioritizes EXPLICIT signals (TODO/skip/`latest`/churn), not subtle architectural issues. Grounded + cheap by design.
- **Output rotates** run-to-run (LLM non-determinism); fine for a weekly digest, and retry-on-empty prevents a blank email when signals exist.
- **Security-flavored proposals are LEADS, not verdicts** (e.g. "add auth to X" — the model inferred it from a signal; verify there's no middleware-level auth before acting).
- **Feedback is reply-driven, not outcome-driven** — replying steers the next digest (above), but it does NOT yet track which proposals get ACTED ON → shipped (the remaining v1: acted-on→PR as a real artifact-verifier). **ROTATE** the OpenRouter key in `~/.config/repo-cos/env` (pasted in transcripts).
