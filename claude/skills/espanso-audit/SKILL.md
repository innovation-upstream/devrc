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
- DEMAND reads the LOCAL transcripts only; re-run on the other host if you need
  its demand. Retune-vs-prune is a judgement call, so the tool never edits
  `nix/home.nix`. The keystroke expansion itself can only be checked by the user
  typing a trigger.

Notes:
- Edit `claude/skills/<name>/SKILL.md` + `nix/home.nix` in the repo, NOT `~/.claude/*`
  (read-only nix-store symlinks). New files must be `git add`ed before a switch.
- `keylog.service` pins the espanso config store paths in `X-Restart-Triggers`
  (#347), so an espanso-only switch DOES restart the detector; `--verify-deploy`
  re-checks it at runtime anyway.
- Background: [[espanso-usage-audit]] memory; snippets live inline in
  `nix/home.nix` (`services.espanso.matches.base`).

Pair: `/find-session espanso` · `/devrc-dx`.
