# Handoff: Claude Code productivity tooling — 2026-06-16

**Repo:** devrc (harness/dotfiles) + `~/.claude` (global commands/config). Spans both the **main** host and the **laptop** (`zach@10.42.0.100`).
**Origin:** a session that analyzed all Zach's Claude Code transcripts → themes → built productivity commands. This continues that thread (and explicitly drops the unrelated prod-infra rabbit hole that the same session drifted into — see `civit/datapacket-talos/claudedocs/handoff-grafana-alert-provisioning-drift-2026-06-16.md`, separate owner).

---

## Goal
Reduce the two friction sinks the transcript analysis surfaced — **repeated ritual typing** and **cross-session continuity** — by turning hand-typed genesis patterns into commands, and finish per-project/per-host gaps. (Corrections were only 1.9% of messages, so **do NOT invest in guardrails/mistake-prevention** — that's not the bottleneck.)

## What was DELIVERED (done, verify before re-doing)
- **`/handoff` + `/resume`** — `~/.claude/commands/{handoff,resume}.md`, synced to BOTH hosts. Canonical handoff doc → `claudedocs/handoff-<topic>.md` + re-entry that re-verifies against live state.
- **`/audit-pr`** — `~/.claude/commands/audit-pr.md`, both hosts. Zach's verbatim 9-point adversarial checklist (risks/regressions/assumptions/gaps/bugs/issues/behaviour-changes/leaks/second-order).
- **Standing CLAUDE.md rule** — "implementation tasks default to subagent + test coverage + branch/PR" — now on **BOTH** hosts (`~/.claude/CLAUDE.md`). Laptop append done 2026-06-16 (`zach@10.42.0.100`, hostname `nixos`; the LAN addr `192.168.50.155` was unreachable today — use the nebula addr).
- **`/analyze-service <name> [then …]`** — `~/.claude/commands/analyze-service.md`, BOTH hosts. Live (not frozen-map) recon of an infra subsystem: locate across `homelab-talos`/`datapacket-talos`, load config, check live state (kubectl/flux, states the context used, never fabricates), recent git changes → dense brief, then proceeds to the follow-on (impl → subagent per standing rule). Retires the "analyze the X setup, then …" genesis (~31).
- **`/find-session <terms>`** — `~/.claude/commands/find-session.md` + `devrc/scripts/find-session.py`, BOTH hosts. Deterministic ranked search over `~/.claude/projects/**/*.jsonl` (AND by default, `--any`/`--project`/`--since`/`--limit`/`--json`, phrase via quotes). Prints date/project/branch/genesis/snippet + `claude --resume <id>`. Verified: `"pr 235"` returns the exact "find the session where we did pr 235" session. Retires the session-archaeology genesis (~5). NOTE: `find-session.py` is untracked in devrc working tree on both hosts (not committed — same as `session-analysis/`).
- **`/ux-audit <app/flow> [url]`** — `~/.claude/commands/ux-audit.md`, BOTH hosts (md5 `517975f8…`). Captures the verbatim, 9×-hand-typed "dispatch a subagent to use playwright to click through … and evaluate" UX sweep. Dispatches a subagent that drives the **Playwright MCP** through every view, screenshots all, and scores against the user's standing 7-point rubric (intuitive-for-a-non-technical-easily-deterred-user / broken / overwhelming / confusing / walls / unnecessary friction / simpler?) → prioritized 🔴/🟡/🟢 report + top-3. Distinct from the built-in `/verify` (that = "does this fix work"; this = "is this UX good for a first-timer"). Targets the product projects (vetr/naida/app onboarding), mostly worked on the laptop. NOT adoption-measured.
- **Rules-layer deletion (the Algorithm step 2, applied + LIVE on BOTH hosts)** — `~/.claude/RULES.md` 331→90L, `PRINCIPLES.md` 60→8L (governing layer 405→~120L incl. untouched 14-line CLAUDE.md). Deleted: PM-Agent meta-layer, `/sc:` session-lifecycle prose, the parallelization fake-metric rule (self-contradictory), SOLID/decision-tree recitations. Kept verbatim (the earned ~10%): Token Hygiene, Verification Honesty, Memory-is-Hypothesis, Deterministic/Push-Back; trimmed Git/Failure/Honesty/Temporal/Scope/Files. Both hosts md5-identical (`RULES` `0fd559cc…`, `PRINCIPLES` `5162398e…`). Reversible: `~/.claude/{RULES,PRINCIPLES}.md.bak-2026061{6}-2049*` on each host; staged copy in `claudedocs/proposed-rules-cut/`. Full analysis: `claudedocs/the-algorithm-applied-2026-06-17.md`. Laptop RULES had drifted (319L, missing Token Hygiene) — the cut net-added that section back.
- **Analysis pipeline preserved** → `devrc/scripts/session-analysis/` (was ephemeral in /tmp):
  - `extract_user_msgs.py` — pulls genuinely-typed messages from `~/.claude/projects/**/*.jsonl` (filters sidechain/meta/task-notifications/system-reminders/command-stdout; dedups). Run on each host, combine. Produces ~6,028 records.
  - `extract_genesis.py` + `classify_genesis.py` + `genesis_detail.py` — first-message-per-session ("genesis") extraction + continuation-vs-fresh classification + taxonomy.
  - `analyze.py`, `prod.py` — theme buckets, per-project friction (steering %, correction %), repeated-prompt clustering.
  - Data (`user_msgs_clean.jsonl`, `genesis_only.jsonl`) is **regenerable** — re-run the scripts; don't rely on /tmp.

## Key findings to carry forward (the evidence base)
- **6,028 typed messages**, median 46 chars — short imperative directives. Top themes: delegation-to-subagents, an adversarial-audit ritual, PR→deploy→verify loop, terse steering, multi-day handoff continuity.
- **Genesis analysis:** 273 fresh-start sessions; **53% open with a slash command** (your command library IS your launch surface — and it's well matched). Signature unbuilt pattern: **"analyze the X setup, then …"** recon (31 geneses).
- **Friction is NOT mistakes** (1.9% corrections). It's (a) re-typing rituals and (b) finding/rebuilding context across sessions.
- **Methodological caveat (from `close-the-loop/STATE.md`):** mining transcripts is *recency-biased* — great for "what hand-typed ritual can become a command," useless for "what's the highest-leverage NEW thing." For net-new, run `/close-the-loop` explore-F (interview), not more mining.

## Backlog — the highest-ROI UNBUILT items (ranked)
1. ✅ **DONE — `/analyze-service`** (see DELIVERED). Built live-discovery, not a frozen map (per Zach's deterministic/memory-is-hypothesis principles). NOT yet adoption-measured.
2. ✅ **DONE — `/find-session`** (see DELIVERED). NOT yet adoption-measured.
3. ✅ **DONE — standing rule synced to laptop** (see DELIVERED).
4. **Per-project: wire civitai `verify` skill to real click-paths.** `civit-civitai` is the highest-correction big repo (2.7%) and the only one with UI regressions ("you broke the sample-carousel"). Make "done" require reproducing the actual user click-path (Playwright) before claiming fixed.
5. **Measure adoption (the validation loop).** In ~1 week (≈2026-06-23), re-run `extract_user_msgs.py` on BOTH hosts and recount the ritual stems. If a command isn't used, fix its description/trigger or kill it — don't let it rot. **Captured BASELINE (hand-typed ritual occurrences, 2026-06-17, both hosts, BEFORE the commands could displace them):** `/analyze-service` 33 · `/find-session` 5 · `/ux-audit` 11 · `/audit-pr` 52 · `/handoff` 80. Re-grep these same patterns post-adoption; a drop = the command displaced the hand-typing, a flat/rising count = adoption failure (fix the trigger or kill it). Recount recipe is in `the-algorithm-applied-2026-06-17.md` appendix + the grep in this session's transcript.

## Explicitly DEFERRED / out of scope here
- Scheduled homelab health-check (Zach didn't pick it).
- Guardrail hooks (correction rate too low to justify).
- The `cronjob-stale` Grafana alert + GoAlert paging outage — different thread, own handoff doc (above). Note: the cronjob-stale alert (talos-infra PR #158) is merged + verified.

## How to verify when "taken further"
- New commands exist on BOTH hosts (`ls ~/.claude/commands/`), laptop CLAUDE.md has the rule.
- Re-run `scripts/session-analysis/extract_user_msgs.py` post-adoption: the targeted hand-typed patterns (recon "analyze X", "write the handoff", session-hunting) measurably decline, or the new commands appear in the command-frequency table.
- `/analyze-service` and `/find-session` each retire a named genesis mode (recon / session-archaeology).

## Gotchas
- `~/.claude/commands/*.md` are global (both hosts) — additive syncs are safe (`scp`), but CLAUDE.md differs per host (don't overwrite wholesale).
- Don't re-run the heavy extraction inside a transcript you're also writing to (it reads `~/.claude/projects/**`).
- `/tmp` is ephemeral — the analysis data there from the original session is gone/going; regenerate from `scripts/session-analysis/`.
