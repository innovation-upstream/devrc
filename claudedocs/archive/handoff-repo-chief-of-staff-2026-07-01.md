# Handoff: repo chief-of-staff ("agents bring me ideas" / CEO model) — 2026-07-01

## Goal
Zach's ask: *"use the telemetry to have agents generate ideas and bring them to me instead of me originating every task (modern-CEO model)."* Reframed honestly (the task-spec drafter already IS a generative arm whose adjudication leg never closed — bottleneck is adoption/relevance, not generation) → built a NEW, better-verified loop: **scan his codebases for improvement opportunities → email him ranked proposals → he steers by replying.** Surface = email (he reads it, unlike the clawgate Tasks queue). **All shipped + verified live on both hosts.**

## State now (all merged + live; both hosts @ `4c16a2b`)
**Tool: `~/workspace/devrc/scripts/repo-cos/` (devrc `main`). Operate via the new `repo-cos` skill.** Self-contained (local repo scan + OpenRouter + Gmail; no cluster dep).
- **Pipeline** (PR #41): deterministic pre-scan (`prescan.py` — TODO/FIXME, skipped tests, `latest` tags, churn, large files; `file:line` evidence; capped per-repo so civitai can't flood) → LLM synthesis (`llm.py`, OpenRouter `deepseek/deepseek-v4-flash`, survivors only, ~cents) → top-5 digest (`digest.py`) → email (`email_send.py`, Gmail SMTP + app-password). Anti-slop = HARD `--top` cap + `file:line` evidence validation (`_ref_known`, exact match) + CI-verifiable bias + retry-on-empty (DeepSeek rotates at temp=0).
- **Weekly timer** (commit `87f5276`): `repo-cos.timer` (systemd user, **Mon 08:00**, `serverMode`-gated = workbench-only) → `run-weekly.sh` → `scan.py --email`. Verified: manual `systemctl start` emailed 5 proposals, `Result=success`.
- **Persistence** (`fa26d6d`): every run writes `~/.config/repo-cos/latest.json` (+ `history/`, `emailed` flag) + `--email` writes `last_emailed.json`. Lets another session read the exact set.
- **Reply-feedback → EXCLUSIONS** (PRs #42 + #43): reply to the digest → `feedback.py` IMAP-reads it (same app-password) → `exclusions.py` maps positional lines to repos (against `last_emailed.json`) → **HARD-DROPS paused/not-yours repos from the scan** (`~/.config/repo-cos/exclusions.json`; `resume <repo>`; `--show-exclusions`). Plus context-injection for nuance. Verified live against his real reply — twice.
- **Cross-repo capability added:** the mailbox skill now documents **"send email AS Zach"** (Gmail SMTP + app-password, any recipient) via `email_send.py` — reusable for "email X on my behalf".
- **Creds:** `~/.config/repo-cos/env` (OpenRouter) + SOPS `mailbox-gmail-imap` (Gmail app-password). 117 tests.
- **Current exclusions** (from his live reply): `kubeclaw-cloud`, `baseball-manitoba-pitch`, `homelab-talos` (paused) + `civitai-orchestration` (permanent, "not owner"). `civitai` in scope.

## Next steps (ranked)
1. **ADOPTION is the real test** — the loop is built; does the weekly Monday email actually change what he does? Watch over the coming Mondays for reply/`resume` engagement. **Do NOT pile on more repo-cos features until a few real cycles show it's used** (the drafter-never-closed lesson).
2. **v2s noted, NOT built:** (a) thread-match replies (`In-Reply-To` → exact digest) so replying to an OLD digest maps right — the current position-mapping assumes you reply to the latest; (b) **outcome-verifier** (greenlit proposal → dispatch → PR shipped = the real artifact loop — the missing "acted-on" tracking); (c) signal-level (not just repo-level) exclusions.
3. **🔴 ROTATE the OpenRouter key** (`~/.config/repo-cos/env` + pasted in transcripts).

## Gotchas / decisions / dead-ends
- **🔑 THE LESSON (again): context/prose steering is too weak — deterministic wins.** Feedback v1 = pure context-injection; it FAILED live (Zach replied "these are paused", the model re-proposed them). Rebuilt as deterministic repo-exclusion (a hard filter before synthesis). Keep context only for nuance.
- **Position-mapping MUST use the emailed digest** (`last_emailed.json`), NOT `latest.json` — proposals rotate + every run overwrites latest, so a reply's "1./2./5." maps wrong against a later dry-run. (A live #5 mis-map happened because the emailed digest predated persistence; corrected + seeded `last_emailed.json`.)
- **Exclusion keys are normalized to basename** (`_canon_key`) — a repo referenced as a bare name vs `~/…`-path was creating dup entries (`4c16a2b`).
- **Security-flavored proposals are LEADS not verdicts** (e.g. "add auth to X" — the model inferred it; verify no middleware auth first).
- **Marker-driven ceiling** — it surfaces EXPLICIT signals, not deep architecture. Cheap + grounded by design.
- `naida`/`vetr` are laptop-only (`~/workspace/scratch/`) → the workbench scanner can't see them yet.
- Each repo-cos PR was adversarially audited pre-merge; the audits caught a real 🔴 (STARTTLS with no cert verification → app-password MITM) on #41 and 2 🟡s on #42.

## How to verify
```bash
export SOPS_AGE_KEY_FILE=~/workspace/homelab-talos/.secrets/age.key
set -a; . ~/.config/repo-cos/env; set +a
cd ~/workspace/devrc
# see the proposals (dry-run; applies exclusions + reply-feedback):
nix-shell -p 'python3.withPackages(p:[p.requests])' sops --run 'python scripts/repo-cos/scan.py --dry-run'
# current exclusions:
nix-shell -p python3 --run 'python scripts/repo-cos/scan.py --show-exclusions'
# the weekly timer:  systemctl --user list-timers | grep repo-cos
# read the last emailed/generated set (another session):  cat ~/.config/repo-cos/latest.json
# tests:  nix-shell -p 'python3.withPackages(p:[p.pytest p.requests])' --run 'python -m pytest scripts/repo-cos/tests -q'  # 117 pass
```
