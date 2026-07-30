# Handoff: repo-cos precision iteration — the 0/5 week (3 FP classes fixed) — 2026-07-13

## What this session was
First real "read + evaluate the weekly digest with Zach" cycle since the self-hosted build (see `handoff-repo-cos-selfhosted-complete-2026-07-02.md`). The instrument ran two Mondays (2026-07-06, 2026-07-13); this session evaluated the **2026-07-13** digest collaboratively. Outcome doubled as the first hard test of the CEO-model loop's *precision*.

## Headline: the 2026-07-13 digest was **0/5 actionable**
None of the 5 proposals were worth doing. Breakdown + disposition:

| # | Proposal | Verdict | Disposition |
|---|----------|---------|-------------|
| 1 | homelab-talos: enable 7 "skipped" clawgate e2e tests via Docker/Postgres in CI | **False positive** — `test.skip(!dockerAvailable(),…)` is a *conditional* graceful-degradation guard; specs **already run in CI** (Docker present since PR #83, 2026-07-06). Removing guards = local-run regression, 0 CI gain. Subagent pushed back + refused to ship; independently verified (`clawgate-ci.yml` `docker info`+`playwright test`, PR #83 `bc5b6e04`, CI run 28797316953 = 72/75 pass). | Dismissed (7 refs) |
| 2 | civitai: fix `RETURN 'XXX'` "placeholder" in DB init SQL | **False positive** — `'XXX'` is a real NSFW-rating enum value (`PG/PG13/R/X/XXX/Blocked`) | Dismissed |
| 3 | civitai: enable 5 app-auth/preview-apps e2e via stub OAuth + minted key | Real but **out of lane** (client *app* code, not Zach's infra) + deeper than effort:M | Dismissed |
| 4 | devrc: remove "stale TODO" from RULES.md | **False positive** — the line is a rule *about* TODOs; `.md` subject-match. Proposal wanted to delete a real behavioral rule. | Dismissed |
| 5 | datapacket: resolve TODO markers in handoff `*.md` docs | Low value — TODOs in dated handoff docs, not live work | Dismissed |

## Three false-positive CLASSES → all fixed in code (not just dismissed)
The 3 FPs came from 3 distinct structural blind spots in `scripts/repo-cos/prescan.py`:
1. **rg/walk `.md` divergence** — `_walk_markers` honored `SCAN_EXTS` (no `.md`); `_rg_markers` ignored it and grepped *every* file. Results depended on whether `rg` was installed (non-deterministic). Leaked `.md` matches → #4, #5. **Fixed: PR #106** (`_rg_markers` now drops non-`SCAN_EXTS` files).
2. **quoted string literals** — marker regex matched `XXX`/`TODO` inside `'…'`/`"…"`/`` `…` `` (enum values, not comments) → #2. **Fixed: PR #106** (`_has_unquoted_marker` — keep line only if a marker occurrence is NOT quote-wrapped).
3. **conditional skip-guards** — `SKIP_PATTERNS` `js.skip` matched `test.skip(` regardless of whether it's the *conditional* runtime form `test.skip(<expr>, …)` (runs in CI) or the *unconditional* disabled-block form `it.skip('name', fn)` → #1. **Fixed: PR #107** (`_is_conditional_js_skip` — first arg is a string literal/empty → disabled → flag; anything else → conditional guard → drop).

## State now (all on `main`, both PRs merged + workbench deployed + verified)
- **PR #106** `fix(repo-cos): stop two prescan marker false positives` — merged `7cd3dc8`, tests 203→210.
- **PR #107** `fix(repo-cos): don't flag conditional test.skip() runtime guards` — merged `0422af8`, tests 210→214.
- **Workbench HEAD = `0422af8`**, `ship.sh --no-laptop` ×2 (HM-switched + verified at `origin/main`). Both helpers confirmed live in the checkout the weekly timer runs from. **Laptop NOT deployed** (doesn't run the repo-cos timer; picks up on its next normal `ship.sh`).
- **`~/.config/repo-cos/exclusions.json` — 26 dismissals** guarding recurrence: #1 (7), #2 (1), #4 (3) added first; #3 (5), #5 (3) added after. #4/#5 are `.md` → also structurally excluded by #106 (belt-and-suspenders); #1/#3 (`.spec.ts`) are the ones that genuinely needed the explicit dismissal.

## Next steps
1. **Watch the 2026-07-20 digest** — the real verifier: did precision improve? Prediction after #106+#107: the `.md`/quoted/conditional-skip noise is gone; whatever surfaces should be closer to real signal. If it's *still* mostly noise → escalate to signal **down-weighting** (see below).
2. **If ≥2 more weeks come back mostly-noise:** the deeper move is to down-weight the string-match signals (`marker`, `skipped_test` — "the token appears" ≠ "a defect exists") relative to `churn`/`large_file`/`stale_lock` (no string-match failure mode). Do NOT do this preemptively — let #106+#107 run a cycle first. This is the honest read on repo-cos's ceiling: it's grounded + cheap by design, but marker/skip signals are inherently FP-prone.
3. **Nothing dispatched to implement** from this digest — correctly, it was all noise/out-of-lane.

## Gotchas / notes
- **The two most valuable moves this session were REFUSALS**, not builds: the clawgate subagent refused to remove the guards (would've been a regression), and I reversed my own "dispatch #1" after the evidence. Evaluate-with-Zach caught what the LLM synthesis + passing tests would not have.
- Dismissals are keyed on the exact prescan `.ref` = `{repo}/{file}:{line}` (matches digest evidence strings verbatim); `_canon_ref` only normalizes whitespace/slashes. `resume <repo>` / edit the JSON to reverse.
- `exclusions.json` edits were done directly (skill-supported) rather than via the reply loop — immediate + reliable vs the async mail round-trip.

## How to verify
```bash
cd ~/workspace/devrc
git log --oneline -2        # expect 0422af8 (#107) + 7cd3dc8 (#106)
grep -n "_is_conditional_js_skip\|_has_unquoted_marker" scripts/repo-cos/prescan.py
nix-shell -p 'python3.withPackages(p:[p.pytest p.requests p.psycopg2])' --run 'python -m pytest scripts/repo-cos/tests -q'   # 214
python scripts/repo-cos/scan.py --show-exclusions    # 26 dismissed + the repo/approved sections
```
