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
4. Propose changes, then run **`--gate <candidate base.yml>`** — the one
   pre-ship command. Offline, no creds. It lints the candidate AND resolves a
   probe universe (single-token prefixes + within-snippet two-token queries)
   against the deployed config. **Two things FAIL it, both user-facing:**
   - **queries that stop working** — a WHOLE WORD that used to find a snippet
     finds nothing now, and that snippet still exists. Four earlier rules each
     tried to infer whether the loss was DELIBERATE and each was walked by a
     one-line edit; intent is not in the config, so state it: acknowledge
     deliberate losses with **`--accept word,word`**. A PRUNE needs no
     acknowledgement — the word went with the snippet.
   - **expansion changed** — a query that resolved to one snippet now resolves
     to another that types DIFFERENT text. A plain trigger rename is not this.
   Everything else is **reported, never graded**: ambiguity costs telemetry, not
   reach (espanso lists every match as a row), and pruning or rewording drops
   words by design. Grading those made the gate red on this skill's own primary
   actions, and a permanently-red gate teaches you to ignore it.
   🔴 **Known limits — a PASS is "no regression under our model", not a
   guarantee.** It models the picker with the KEYLOG matcher, not espanso, and
   nothing in this repo checks that proxy. Multi-word queries beyond
   within-snippet pairs, and non-prefix substrings, are outside the universe.
   Correcting a typo whose right spelling was ALREADY reachable is flagged —
   a real if trivial loss; the report names the queries, so a glance settles it.
   `--diff-config` is the diff without the lint; `--replay --config <candidate>`
   cross-checks against terms he really typed — narrower, but real data.
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
- 🔴 **AMBIGUOUS IS NOT DEAD. `--lint`'s "can never fire from the search UI" is
  about the TELEMETRY, not about espanso** — and acting on it literally makes
  the snippets worse. espanso's picker lists EVERY match and the user arrows to
  one, so two matches means two rows. The only thing uniqueness buys is
  `_attribute` being able to NAME the snippet; without it the fire is still a
  fire, just recorded UNATTRIBUTED (`_close_search` emits a row either way).
  A snippet with no `label` is worse off, not better: espanso falls back to
  showing its raw expansion as the row text. On 2026-08-19 a pass stripped
  `label`+`search_terms` from the two nebula ssh snippets to force uniqueness
  and took `'nebula'`/`'mesh'`/`'remote'` from 2 picker rows to **0**. **Fix
  ambiguity by changing which WORDS a snippet spells, never by removing its
  label.** And judge any candidate on BOTH axes — picker rows *and* attribution;
  a diff that improves attribution while blanking rows is a regression.
- 🔴 **A zero-fire snippet is not an unused one — never infer USE from the
  search stream.** `--terms` does report attributed searches (it prints an
  `### attributed` section, so it is *not* only a sample of failures), but the
  fires it cannot attribute vanish into an UNATTRIBUTED bucket, so a heavily
  used snippet reachable only by an ambiguous query reads as 0. On
  2026-08-19 every observed ssh query was host-only (`lap`, `ssh wor`), which I
  read as "he doesn't care about the network axis" and proposed collapsing the
  four `:ssh*` to nebula-only. Wrong twice over: those queries were AMBIGUOUS,
  not dead — they listed two picker rows and their fires were logged with no
  trigger — and `activity.events` showed all four endpoints live
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
  consequences: (a) when two snippets both **DECLARE** the same word (in
  `search_terms` or the trigger), **no** `search_terms` edit makes a bare query
  resolve — one of them must stop spelling it, or the word needs an
  `_AMBIGUOUS_TERM_OWNER` entry. 🔴 **But a snippet that spells the word only in
  its LABEL no longer competes**: `_attribute` gives DECLARED matches precedence
  over label-only ones, so the fix for that case IS a `search_terms` edit — add
  the word to the snippet that should own it. The picker still lists both rows
  either way; precedence is attribution-only. (b) always gate with
  `--replay --config <candidate>` and **diff
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
  **Pin a term the snippet's LABEL does not spell**, or the guard passes with
  every `search_terms` entry deleted (three such pins shipped on 2026-08-19).
  🔴 **Neither sweeps the whole input space on its own — that is what
  `--gate` is for** (step 4). It replaced the hand-rolled prefix-universe
  diff this rule used to describe; do not re-derive it by hand.
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
