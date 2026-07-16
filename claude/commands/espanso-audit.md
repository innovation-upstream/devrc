---
name: espanso-audit
description: "Re-run the espanso snippet usage audit — mine Claude transcripts on BOTH hosts for which expansions fire vs. which short phrases get hand-typed, then recommend removals/shortenings/additions. Use to periodically tune the espanso workflow snippets in nix/home.nix."
argument-hint: "[--since YYYY-MM-DD]   (default: since the last config change)"
allowed-tools: Bash, Read, Edit, Write
---

# /espanso-audit — tune the espanso workflow snippets from real usage

Goal: kill the hand-rebuilt "which snippets do I actually use" archaeology. **Two signals** (espanso erases both trigger AND expansion on firing, so neither alone suffices):
- **PRIMARY — keylog TRUE fires.** The X11 keylogger's `EspansoDetector` (`scripts/collector/keylog/espanso_detect.py`) detects espanso usage AT CAPTURE TIME from raw keystrokes and writes one `source=keys, kind=espanso` row per fire to ClickHouse. This is the real per-trigger fire count (direct + Ctrl+Space-search). **FORWARD-ONLY** — no data before the detector deployed. Honest caveat: "keystrokes ended with a trigger" ≈ "espanso fired" except in per-app-disabled contexts.
- **SECONDARY — transcript miner (ADD-CANDIDATES).** Its unique durable value is the inverse view: recurring SHORT phrases you hand-type that are NOT yet snippets (candidates to ADD). Its per-trigger "hits" CONFLATE fire vs hand-typing → ambiguous cross-check only, not truth.

Args: `$ARGUMENTS` (optional `--since YYYY-MM-DD`).

## Critical topology (read first — it's bitten before)

espanso runs on **BOTH hosts** since 2026-07-06 / PR #83 (the workbench was un-gated from serverMode — it IS graphical; espanso/dunst now key off the `graphical` predicate, not `!serverMode`). The old "laptop-only" rule is retired. So:
- **Combine** both hosts' `~/.claude/projects` counts — an expansion fires on whichever host you typed it on (you type on the workbench directly; and when you SSH laptop→workbench, laptop-espanso expands *before* the text travels and it lands in workbench transcripts). Summing both hosts is the true usage; never treat them as separate.
- **Deploy target is BOTH hosts** (`scripts/ship.sh`) — no longer just the laptop.
- Note the miner drifts from the live config and can silently misreport (2026-07-06: a stale `:aep` substring read 0; `:eos`/`:acq` were untracked). Always re-sync `SNIPPETS` before trusting counts (step 2).

## What to do

1. **Pick the window.** Default `--since` to the date espanso config last changed (check `git -C /home/zach/workspace/devrc log -1 --format=%cd --date=short -- nix/home.nix`, or ask). Short windows are fine — the user drives this all day, so even ~1 week is real signal.

2. **Sync the miner first.** `scripts/session-analysis/espanso-usage.py`'s `SNIPPETS` dict drifts from the live config. Diff it against the `services.espanso` block in `nix/home.nix` and update detection substrings BEFORE running, or counts will be wrong. Pick a *distinctive* substring of each expansion; mark snippets whose expansion equals a phrase the user hand-types as `ambiguous=True` (their counts conflate snippet + manual typing).

3. **PRIMARY: keylog TRUE fires (do this first).** Export the ClickHouse reader creds (`CLICKHOUSE_URL/USER/PASSWORD` from SOPS — see the header of `espanso-usage.py` / `activity-scan.py`), then:
   ```bash
   python3 /home/zach/workspace/devrc/scripts/session-analysis/espanso-usage.py --since DATE --source keys
   ```
   This is the authoritative per-trigger fire count (direct + search). If it prints "(no keylog espanso events yet — detection is forward-only …)" the detector hasn't collected enough since deploy — fall back to the transcript signal and note the window is short. The keylog rows are host-tagged in CH (`host` column) so a single query already covers both hosts; no SSH needed for this signal.

4. **SECONDARY: transcript ADD-CANDIDATES.** Run the transcript miner on both hosts for the "phrases with no snippet" view (the per-trigger transcript hits are an AMBIGUOUS cross-check, not truth — prefer the keylog fires above):
   ```bash
   python3 /home/zach/workspace/devrc/scripts/session-analysis/espanso-usage.py --since DATE --source transcript --host workbench
   ssh -o ConnectTimeout=5 zach@10.42.0.100 'python3 - --since DATE --source transcript --host laptop' < /home/zach/workspace/devrc/scripts/session-analysis/espanso-usage.py
   ```
   (`--source both` — the default — shows keylog first, then the transcript sections.)

5. **Combine + interpret.** Use the keylog fire counts as the usage truth; use the transcript ADD-CANDIDATES for what to add. Then apply the recipe:
   - **Paths** (`:cc :cdp :hlt …`) — almost always earn their keep; rarely touch.
   - **The recurring lesson:** a workflow snippet that expands to a **long steering paragraph goes UNFIRED** — the user hand-types the short phrase instead. Cross-reference each zero/low-fire prompt snippet against the "recurring short user messages" section: if the short form of its intent is being hand-typed a lot, that's the signal. (Measured 2026-06-23 `:rns`, again 2026-06-30 → PR #37.)
   - **Verdict per snippet:** keep-long (used + sticks, e.g. `:eos`/`:acq`), **shorten back** to the hand-typed form (zero fires + short-form demand — option (a); steering already lives in RULES.md / `/verify` / `/audit-pr`), repoint-to-skill (option (b)), remove (dead, no demand), or add (high-frequency phrase with no snippet).
   - Honest caveat to always state: keyword-packing is a probabilistic nudge, and new triggers need habit-formation time — but **zero fires + active short-form hand-typing** is strong signal the trigger→habit transfer failed.

6. **Implement (if the user picks changes).** Edit the `services.espanso` block in `nix/home.nix` on a feature branch. The keylog detector auto-parses the LIVE config, so new/changed triggers are detected on the next `home-manager switch` with NO code change — but still re-sync the transcript miner's `SNIPPETS` (the ADD-CANDIDATE cross-check) to whatever you changed. Validate: `nix-instantiate --parse nix/home.nix` + check no trigger is a prefix of another (espanso longest-matches; the keylog detector mirrors that, emitting the shorter prefix). PR → merge → **`scripts/ship.sh`** converges both hosts.

7. **Verify honestly.** After ship, confirm each host's deployed `~/.config/espanso/match/base.yml` carries the new replace strings (note: home-manager emits `replace` before `trigger`) and `systemctl --user is-active espanso` (both hosts now run it). The one thing you **cannot** verify over SSH is the actual keystroke expansion — hand that final check to the user (type a trigger, watch it expand).

Notes:
- Edit `claude/commands/*.md` and `nix/home.nix` in the repo, NOT `~/.claude/*` (read-only nix-store symlinks). New command/script files must be `git add`ed before a switch (flakes only see tracked files).
- Background: [[espanso-usage-audit]] memory; the snippets live inline in `nix/home.nix` (`services.espanso.matches.base`).

Pair: `/find-session espanso` (recover prior audit sessions), `/devrc-dx` (broader dotfiles DX).
