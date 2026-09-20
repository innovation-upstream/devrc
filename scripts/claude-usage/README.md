# claude-usage

MV3 extension that snapshots every Claude account's usage while claude.ai is
open: toolbar badge, desktop toasts at the warn threshold, an all-accounts
popup, and an in-page widget on claude.ai itself.

Scope and the measured API recon: `claudedocs/proposal-claude-usage-tracker.md`.
Deploy: `nix/home.nix` copies this `extension/` tree to
`~/.local/share/claude-usage-ext`; registering it with Brave is a one-time
manual step (`brave://extensions` → Developer mode → Load unpacked). Nix keeps
the directory correct; it cannot register an unpacked extension.

🔴 **A new file under `extension/` must be `git add`ed before it will deploy.**
The flake ships only tracked files, so an untracked icon, lib or script is
silently omitted from the store copy and therefore from the deployed tree —
the switch succeeds and the file simply is not there.

## Regenerating the icons

`extension/icons/icon.svg` is the source of truth; the PNGs beside it are
generated and committed (nix copies the tree verbatim and Brave loads it
unpacked, so there is no build step between this repo and the browser).

```bash
cd scripts/claude-usage/extension/icons
nix-shell -p librsvg --run 'for s in 16 48 128; do rsvg-convert -w $s -h $s icon.svg -o icon-$s.png; done'
git add icon.svg icon-16.png icon-48.png icon-128.png
```

Same form `scripts/browser-bridge/README.md` uses for the same job. librsvg
rather than ImageMagick: IM delegates SVG rendering to whatever is installed
and silently rasterizes at the wrong size when it falls back to its internal
MSVG parser.

This is a documented command rather than a script, deliberately. A 59-line
generator was written and then cut by a round-0 audit: nothing invoked it, no
gate checked its output, and the one silent failure its verify step defended
against had been introduced by that generator's own first draft. A hand-run
`rsvg-convert` fails loudly to the terminal.

⚠ **Do not move this command into `icon.svg`'s comment.** It contains a literal
double hyphen (`--run`), which is illegal inside an XML comment and makes
librsvg refuse the entire file. That is measured, twice.

## Tests

```bash
nix develop ~/workspace/devrc -c node --test scripts/claude-usage/tests/*.test.mjs
```

Gated by `scripts/run-node-tests.sh` (`SUITES` entry
`scripts/claude-usage/tests`), which is what CI's `tekton/devrc-nodetests`
runs. Pass the `*.test.mjs` glob rather than the directory: `node --test <dir>`
silently reports a bogus `# tests 1`.

## Where each rule lives

| concern | file |
|---|---|
| raw API JSON → stored record | `extension/lib/normalize.js` |
| countdowns, staleness | `extension/lib/timefmt.js` |
| percent/credits display strings | `extension/lib/format.js` |
| **how bad is this usage** (badge + widget) | `extension/lib/severity.js` |
| in-page widget display model | `extension/lib/widget.js` |

`lib/severity.js` is the one both the toolbar badge and the in-page widget
read. They each decided it independently until a round-0 audit found they
disagreed — the badge coloured from the API's `severity` string, the widget
banded the raw percentages — and that a missing severity row painted a green
badge at 99%. It now takes the worse of the two signals.

⚠ **Two different claims, and collapsing them is how this went wrong twice.**
The *record* tone — worst of (API severity, percent band) — is what the badge,
the collapsed pill and the card's header dot show. The *bars* inside the card
show each window's own percent band, deliberately: a 5% session bar must not
turn red because the weekly window is at 97%. A round-2 audit caught the
in-between state where the bars had been given their own tone and the record
tone was rendered nowhere, so a `severity: "critical"` account showed a red
badge above three green bars.
