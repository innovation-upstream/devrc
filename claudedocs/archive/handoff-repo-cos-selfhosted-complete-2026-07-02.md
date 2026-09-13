# Handoff: repo chief-of-staff — self-hosted + feature-complete (3-intent steering) — 2026-07-02

## Goal
Continued the "agents bring me ideas / CEO model" loop (`repo-cos`). This session took it from "works, Gmail-based" to **fully self-hosted + feature-complete**: the digest sends from Zach's own relay, replies read from his own Postgres, and the reply now drives THREE deterministic intents (exclude repo / dismiss recommendation / approve→clawgate-Task). **All merged + verified live at every hop; both hosts converged.**

## State now (all on `main`, both hosts @ `d61d5f3`; operate via the `repo-cos` skill)
**Self-hosted mail loop (PR #44):**
- **Send** via Zach's **postfix relay** → `From: repo-cos@mail.zacx.dev` (DKIM-signed, clean Gmail deliverability), `Reply-To: repo-cos@inbox.zacx.dev`. Relay is in the **production** cluster, reached by a `kubectl port-forward` (STARTTLS hostname-verify OFF on the localhost hop, no SMTP auth). Fallback `REPO_COS_SEND=gmail`.
- **Reply-read** from the **homelab Postgres `mail` table** (his reply routes `repo-cos@inbox.zacx.dev` → MX → mail-receiver → PG; `feedback.py` reads it via `mail-actions/_db.py`). EXACT `from_addr` ownership gate. Fallback `REPO_COS_REPLY_SRC=imap`.

**Three deterministic reply intents (`exclusions.py`, positional map vs `last_emailed.json`):**
- **exclude repo** — "pause"/"not owner" → HARD-drop the repo (`"repos"`; `resume <repo>`).
- **dismiss recommendation (PR #45)** — "skip"/"not needed" → suppress only that proposal's evidence `file:line`, **repo stays in scope** (`"dismissed"`).
- **approve → clawgate Task (PR #46)** — "approve"/"yes"/"lgtm" → `clawgate.py` POSTs `POST /api/tasks` (durable, one-tap Dispatch; creds `~/.claude/clawgate.env`) + suppress-on-success (`"approved"`). repo-cos is now clawgate card-producer #3.
- Precedence: resume > repo-exclude > dismiss > approve (negative beats approve on a mixed line). `scan.py --show-exclusions` shows all sections.

**Weekly automation:** `repo-cos.timer` (Mon 08:00, `serverMode`-gated workbench) → `run-weekly.sh` (sets both kubeconfigs + OpenRouter env) → `scan.py --email`. 189 tests.

**Live-verified this session (with Zach's real reply, row 49613):** relay-send lands in Gmail; reply read from PG; `1. approve` → **clawgate Task #39** (devrc collector-tests) + suppressed; `2/3. skip` → civitai recs dismissed, civitai kept in scope (re-surfaced a *different* proposal); `4. skip` → datapacket rec dismissed; `5. kubeclaw is paused` → kubeclaw-embed excluded.

**Current exclusion state (`~/.config/repo-cos/exclusions.json`):** repos excluded — `baseball-manitoba-pitch`, `kubeclaw-cloud`, `kubeclaw-embed` (paused) + `civitai-orchestration` (permanent). Dismissed recs — civitai 3D-docs + Makefile, datapacket soft-delete. Approved — devrc collector-tests (→ clawgate #39).

## Next steps — BUILD IS DONE; the move is USE + WATCH
1. **Dispatch clawgate Task #39** (the approved devrc collector-tests) → closes approve→implement→PR for real = the artifact/outcome verifier finishing.
2. **Watch the FIRST Monday timer fire** — the systemd minimal-env reaching BOTH clusters (prod relay + homelab PG) is the one unproven bit. Fails LOUD (send error → rc=1 → unit `failed`), never silently drops.
3. **Adoption over the coming Mondays** is the real test — does the weekly email change what Zach does.
4. Noted, NOT built: thread-match replies (`In-Reply-To` → exact digest) so replying to an OLD digest maps right (current mapping assumes you reply to the latest).
5. **🔴 ROTATE the OpenRouter key** (`~/.config/repo-cos/env` + transcripts).
6. **🔑 DISCIPLINE: do NOT add more repo-cos features until a few real cycles show it earns its place.** The instrument is built; the value is in running it.

## Gotchas / decisions / dead-ends
- **🔑 EVERY meaningful bug this arc was caught by RUNNING it live, not by the passing mocked tests OR the code audits.** Each PR was BOTH adversarially audited AND live-verified: audits caught security 🔴s (unverified STARTTLS leaking the app-password; substring ownership gate); live runs caught wiring 🔴s (`mail-actions/llm.py` **shadowing** repo-cos's `llm.py` → synthesis died; the reply cutoff reading `latest.json` — clobbered every run — instead of `last_emailed.json`).
- **⚠ Never add `mail-actions/` to `sys.path`** — its `llm.py` shadows repo-cos's. `feedback.py` loads `_db.py` by explicit importlib path.
- **⚠ Reply cutoff/mapping MUST read `last_emailed.json`** (only `--email` writes it), not `latest.json` (every run overwrites it → cutoff drifts to "now" → reply unfindable after one dry-run).
- **Two-cluster dependency** (prod relay + homelab PG) + `kubectl`/`psycopg2` on the weekly send; best-effort (relay hiccup → loud rc=1; PG hiccup → no feedback that run).
- `mail.zacx.dev` (not apex `zacx.dev`) is the deliverable send domain (SPF/DKIM/DMARC published; Naida uses it). `@inbox.zacx.dev` is a catch-all → PG.
- "approve" suppresses only on a SUCCESSFUL clawgate POST (a failed POST re-proposes = natural retry, no lost approval).

## How to verify
```bash
export KUBECONFIG=~/workspace/homelab-talos/homelab-kubeconfig
export REPO_COS_PROD_KUBECONFIG=~/workspace/homelab-talos/production-kubeconfig
set -a; . ~/.config/repo-cos/env; set +a
cd ~/workspace/devrc
# full self-hosted flow (reads reply from PG, applies 3 intents; --email also relay-sends):
nix-shell -p 'python3.withPackages(p:[p.requests p.psycopg2])' kubectl sops --run \
  'python scripts/repo-cos/scan.py --dry-run'
nix-shell -p python3 --run 'python scripts/repo-cos/scan.py --show-exclusions'   # repos + dismissed + approved
systemctl --user list-timers | grep repo-cos
# tests (189): nix-shell -p 'python3.withPackages(p:[p.pytest p.requests p.psycopg2])' --run \
#   'python -m pytest scripts/repo-cos/tests -q'
```
