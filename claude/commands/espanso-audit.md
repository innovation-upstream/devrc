---
name: espanso-audit
description: "Re-run the espanso snippet usage audit — mine Claude transcripts on BOTH hosts for which expansions fire vs. which short phrases get hand-typed, then recommend removals/shortenings/additions. Use to periodically tune the espanso workflow snippets in nix/home.nix."
argument-hint: "[--since YYYY-MM-DD]   (default: since the last config change)"
allowed-tools: Bash, Read, Edit, Write
---

# /espanso-audit — tune the espanso workflow snippets from real usage

Goal: kill the hand-rebuilt "which snippets do I actually use" archaeology. Deterministic miner over `~/.claude/projects/**/*.jsonl` on both hosts + a fixed interpretation recipe.

Args: `$ARGUMENTS` (optional `--since YYYY-MM-DD`).

## Critical topology (read first — it's bitten before)

espanso runs on **BOTH hosts** since 2026-07-06 / PR #83 (the workbench was un-gated from serverMode — it IS graphical; espanso/dunst now key off the `graphical` predicate, not `!serverMode`). The old "laptop-only" rule is retired. So:
- **Combine** both hosts' `~/.claude/projects` counts — an expansion fires on whichever host you typed it on (you type on the workbench directly; and when you SSH laptop→workbench, laptop-espanso expands *before* the text travels and it lands in workbench transcripts). Summing both hosts is the true usage; never treat them as separate.
- **Deploy target is BOTH hosts** (`scripts/ship.sh`) — no longer just the laptop.
- Note the miner drifts from the live config and can silently misreport (2026-07-06: a stale `:aep` substring read 0; `:eos`/`:acq` were untracked). Always re-sync `SNIPPETS` before trusting counts (step 2).

## What to do

1. **Pick the window.** Default `--since` to the date espanso config last changed (check `git -C /home/zach/workspace/devrc log -1 --format=%cd --date=short -- nix/home.nix`, or ask). Short windows are fine — the user drives this all day, so even ~1 week is real signal.

2. **Sync the miner first.** `scripts/session-analysis/espanso-usage.py`'s `SNIPPETS` dict drifts from the live config. Diff it against the `services.espanso` block in `nix/home.nix` and update detection substrings BEFORE running, or counts will be wrong. Pick a *distinctive* substring of each expansion; mark snippets whose expansion equals a phrase the user hand-types as `ambiguous=True` (their counts conflate snippet + manual typing).

3. **Run on both hosts** (workbench locally; pipe the script over SSH to the laptop so it reads the laptop's transcripts):
   ```bash
   python3 /home/zach/workspace/devrc/scripts/session-analysis/espanso-usage.py --since DATE --host workbench
   ssh -o ConnectTimeout=5 zach@10.42.0.100 'python3 - --since DATE --host laptop' < /home/zach/workspace/devrc/scripts/session-analysis/espanso-usage.py
   ```

4. **Combine + interpret.** Present one merged table (workbench + laptop hits summed). Then apply the recipe:
   - **Paths** (`:cc :cdp :hlt …`) — almost always earn their keep; rarely touch.
   - **The recurring lesson:** a workflow snippet that expands to a **long steering paragraph goes UNFIRED** — the user hand-types the short phrase instead. Cross-reference each zero/low-fire prompt snippet against the "recurring short user messages" section: if the short form of its intent is being hand-typed a lot, that's the signal. (Measured 2026-06-23 `:rns`, again 2026-06-30 → PR #37.)
   - **Verdict per snippet:** keep-long (used + sticks, e.g. `:eos`/`:acq`), **shorten back** to the hand-typed form (zero fires + short-form demand — option (a); steering already lives in RULES.md / `/verify` / `/audit-pr`), repoint-to-skill (option (b)), remove (dead, no demand), or add (high-frequency phrase with no snippet).
   - Honest caveat to always state: keyword-packing is a probabilistic nudge, and new triggers need habit-formation time — but **zero fires + active short-form hand-typing** is strong signal the trigger→habit transfer failed.

5. **Implement (if the user picks changes).** Edit the `services.espanso` block in `nix/home.nix` on a feature branch. Re-sync the miner's `SNIPPETS` to whatever you changed (so the next run detects them). Validate: `nix-instantiate --parse nix/home.nix` + check no trigger is a prefix of another (espanso longest-matches, but avoid surprises). PR → merge → **`scripts/ship.sh`** converges both hosts.

6. **Verify honestly.** After ship, confirm each host's deployed `~/.config/espanso/match/base.yml` carries the new replace strings (note: home-manager emits `replace` before `trigger`) and `systemctl --user is-active espanso` (both hosts now run it). The one thing you **cannot** verify over SSH is the actual keystroke expansion — hand that final check to the user (type a trigger, watch it expand).

Notes:
- Edit `claude/commands/*.md` and `nix/home.nix` in the repo, NOT `~/.claude/*` (read-only nix-store symlinks). New command/script files must be `git add`ed before a switch (flakes only see tracked files).
- Background: [[espanso-usage-audit]] memory; the snippets live inline in `nix/home.nix` (`services.espanso.matches.base`).

Pair: `/find-session espanso` (recover prior audit sessions), `/devrc-dx` (broader dotfiles DX).
