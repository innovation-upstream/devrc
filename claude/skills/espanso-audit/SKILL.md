---
name: espanso-audit
description: "Re-run the espanso snippet usage audit — cross real keylog fire counts against transcript demand for a per-snippet keep/retune/prune VERDICT, then tune the snippets in nix/home.nix."
argument-hint: "[--since YYYY-MM-DD]   (default: since the last config change)"
allowed-tools: Bash, Read, Edit, Write
---

# /espanso-audit — tune the espanso snippets from real usage

Espanso erases BOTH the trigger and the expansion when it fires, so no single
signal works. `scripts/session-analysis/espanso-usage.py` crosses two:

- **FIRES** — the keylogger's `EspansoDetector` records every fire at CAPTURE
  TIME into ClickHouse (`source=keys, kind=espanso`), host-tagged, so ONE query
  covers both hosts. Forward-only: no data before the detector deployed.
- **DEMAND** — transcript occurrences of each expansion. It cannot tell a fire
  from hand-typing or a clipboard paste, and that is the point: *0 fires +
  demand* means undiscoverable, not dead.

It prints a **VERDICT + action per snippet** — read that column instead of
re-deriving keep/kill by hand. `DEAD` is the ONLY prune verdict; a zero-fire
snippet is far more often `UNFINDABLE` (retune `label`/`search_terms`).

Args: `$ARGUMENTS` (optional `--since YYYY-MM-DD`; default = the last espanso
config change — `git -C ~/workspace/devrc log -1 --format=%cd --date=short -- nix/home.nix`).

## Run it

Needs PyYAML plus the ClickHouse READER creds (SOPS → env only; see the script
header):

```bash
nix-shell -p python3Packages.pyyaml --run \
  "python3 ~/workspace/devrc/scripts/session-analysis/espanso-usage.py --since DATE"
```

1. default → FIRES + ADD-CANDIDATES + the VERDICT matrix.
2. `--terms` / `--replay` → what he actually typed into the search bar, and
   whether each term resolves to one snippet, none, or several.
3. `--lint` → offline, no creds. Flags a snippet that NO term resolves uniquely
   to: `_attribute` returns None on ambiguity, so it can never fire from the
   search UI.
4. Propose changes, then run **`--replay --config <candidate base.yml>` as a
   PRE-SHIP gate** — prove the new `search_terms` resolve BEFORE shipping.
5. Edit `services.espanso` in `nix/home.nix` on a branch → PR → merge →
   `scripts/ship.sh`.
6. `--verify-deploy` → both hosts: deployed trigger set, `espanso` active, and
   the detector-staleness check.

The tool never reports an unmeasured signal as a zero: an unreachable
ClickHouse, or a config it cannot parse, is a loud banner and **exit 3**. Do not
prune anything from a run that printed one.

## What the tool cannot tell you

- **Keep/kill is SHAPE, not length.** A whole standalone message survives
  (`:eos` is 700+ chars and the top firer at 72); a mid-sentence fragment dies
  (`:ds`, `:rns`, `:pst`, `:rnx` — all pruned despite heavy hand-typing of their
  phrase). Read ADD-CANDIDATES for standalone-shaped phrases.
- **~97% of fires go through the Ctrl+Space SEARCH UI**, so `label` +
  `search_terms` ARE the interface. A zero-fire snippet is usually a
  discoverability bug, not a content one (`:acq`, 2026-08-05).
- 🔴 **The SEARCH stream only records searches that FAILED — never infer what he
  USES from it.** `--terms` shows queries that resolved to nothing, so it is a
  sample of failures, and reading a preference off it inverts the meaning. On
  2026-08-19 every observed ssh query was host-only (`lap`, `ssh wor`), which I
  read as "he doesn't care about the network axis" and proposed collapsing the
  four `:ssh*` to nebula-only. Wrong: those queries fired nothing, so he fell
  back to hand-typing — and `activity.events` showed all four endpoints live
  with **LAN ahead of nebula** (laptop-LAN 4 shell invocations, workbench-LAN 3,
  workbench-nebula 1, laptop-nebula 0). The proposal would have deleted the two
  most-used. **0 fires is equally consistent with "unused" and "used constantly
  but unreachable" — both predict zero.** Before touching a snippet, query the
  USAGE signal for what it expands to:
  `SELECT countIf(text LIKE '%<expansion>%') FROM activity.events WHERE
  source IN ('zsh','claude','opencode') AND kind IN ('command','prompt')`.
- 🔴 **`search_terms` are not free-form — a new snippet can STEAL an existing
  one's searches.** `espanso_detect._token_matches` is a SUBSTRING test over the
  trigger, every **label word**, and every `search_term`, so `'bench'` ⊂
  `'workbench'`, `'la'` ⊂ `'nebula'`, `'ask'` ⊂ `'task'`. A `:cgt` labelled
  "task" would have silently hijacked all 58 of `:acq`'s `'ask'` fires. Two
  consequences: (a) when two snippets both spell the same word, **no**
  `search_terms` edit makes a bare query resolve — one of them must stop
  spelling it; (b) always gate with `--replay --config <candidate>` and **diff
  it against the deployed config for REGRESSIONS**, not just for new
  resolutions. Validate that diff with a planted mutant before believing a
  clean result.
  🔴 **The replay diff is NECESSARY, NOT SUFFICIENT — it only replays terms he
  ACTUALLY TYPED in the window.** A term he never happened to search is
  invisible to it, so "0 regressions" from `--replay` is a claim about the
  observed stream, not about the config. On 2026-08-19 it reported 0 while a new
  `:pdt` label had taken `'dispatch'` away from `:acq`; the repo's own pinned
  list caught it. **Always also run
  `pytest scripts/collector/keylog/tests/test_espanso_detect.py`**, which pins
  `_EXISTING_RESOLUTIONS` — and add the new snippet's own terms to it.
  🔴 That file's `test_live_scraper_observes_the_real_config` is a POSITIVE
  CONTROL pinned to a LONG `search_terms` list, so a scraper regex matching
  nothing cannot make the other guards vacuously true. If your edit strips the
  pinned snippet's terms, **MOVE the pin to another long list — never relax it
  to `== []`**, which exercises no list-splitting and silently disarms the
  control.
- DEMAND reads the LOCAL transcripts only; re-run on the other host if you need
  its demand. Retune-vs-prune is a judgement call, so the tool never edits
  `nix/home.nix`. The keystroke expansion itself can only be checked by the user
  typing a trigger. **Path snippets read as huge demand with near-zero fires
  (`:cdp` 300/3) — that is the `$DEVRC`/`$CIVITAI` env handles doing the job,
  not a discoverability bug. Do not retune them on demand alone.**

Notes:
- Edit `claude/skills/<name>/SKILL.md` + `nix/home.nix` in the repo, NOT `~/.claude/*`
  (read-only nix-store symlinks). New files must be `git add`ed before a switch.
- `keylog.service` pins the espanso config store paths in `X-Restart-Triggers`
  (#347), so an espanso-only switch DOES restart the detector; `--verify-deploy`
  re-checks it at runtime anyway.
- Background: [[espanso-usage-audit]] memory; snippets live inline in
  `nix/home.nix` (`services.espanso.matches.base`).

Pair: `/find-session espanso` · `/devrc-dx`.
