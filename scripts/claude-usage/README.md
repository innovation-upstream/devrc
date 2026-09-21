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
| **which account can I switch to** | `extension/lib/availability.js` |
| in-page widget display model | `extension/lib/widget.js` |

## The `free` verdict, and why the inference is safe

`content_probe.js` fetches `/api/organizations` with the **current session
cookie**, so a stored account can only ever be re-measured while you are
logged into it. `formatCountdown()` renders an already-elapsed reset as
`resets soon` on the strength of "the next snapshot will correct it" — true
of the active account, false of every other one. A second account therefore
sat forever displaying `Session 92% · resets soon` at exactly the moment it
had freed up: the one state the switch-accounts workflow depends on, backwards.

`lib/availability.js` reads the reset time as **evidence** instead: once
`session.resetsAt` is in the past, the window it described has closed, so the
account is presumed **free**. The inference runs in one direction only — a
reset cannot un-happen — and the row never dresses it up as a fresh reading:
`AVAILABLE — reset 2h ago (was 92%, measured 6h ago)` states the inference and
keeps the last *measured* value and its age on screen. Nothing fabricates a 0%.

🔴 **Both surfaces read that one predicate.** The in-page widget was fixed
first and the popup was not, so for a round the same stored record at the same
`now` read `AVAILABLE` on the card and `Session 92% · resets soon` in the
popup — one rule, fixed at one of its two call sites. `popup.js`'s
`sessionLine()` now calls `availability()` too; `formatCountdown()`'s
`"resets soon"` is unreachable from it except for the **active** account,
which is the one account the probe really does re-measure within seconds.
The two surfaces still differ in *layout* (the widget lists switch candidates
most-available-first; the popup keeps its own freshness ordering) — that is
deliberate, and it is not a difference of verdict.

🔴 **The exemption is withdrawn for every STATE, not just `free`.** For a
round the widget's card applied it as `!exempt && state === FREE`, so the
state that most needed saying was the one it missed: with `lastActiveOrg`
naming an org that has no stored record — a real state, `service_worker.js`
writes the key whether or not the `/usage` fetch produced a record —
`pickRecord()` falls back to the freshest one, and a weekly-blocked fallback
rendered `95% · resets soon` on the card while the popup read `BLOCKED ·
Weekly limit reached. · frees up in 4d0h`. With no lock *string* on the
record the card said nothing about the block at all. The card now states the
verdict for `blocked` too, through the same `blockedBecause`/`blockedUntil`
helpers the other-account rows use, and its lock **banner** reads the verdict
rather than the raw record for a non-exempt account — a session lock spent by
its own five-hour reset was painting red under an `AVAILABLE` row. The active
account keeps its live countdown and its raw banner, which is what the
exemption is for.

🔴 **Evidence is spent by its OWN window's reset — including the weekly
percentage.** The session rule is this module's founding inference; the
weekly one is its twin and was missing from the row *colour* for a round.
`toneForRow()` read the stored weekly percentage unconditionally, so an
account whose seven-day window had reset an hour ago still painted crit off
that spent 100%: `AVAILABLE`, in red, with nothing in the row explaining the
colour. `availability()` now returns `weeklyBindingPct` — the weekly reading
with the rule already applied.

🔴 **The rule lives in one place; two consumers are exempt from it BY NAME,
and for a round a third was exempt by accident.** This paragraph used to end
"the tone reads only that, so the rule lives in one place rather than at each
consumer", which was false as implemented: it was true of the other-account
rows and of nothing else. The CARD went on banding the raw record in two
places — its headline `tone`, which paints the collapsed pill *and* the header
dot, and its weekly row's colour. Measured at `3f0a5506` on a record with the
session window reset 2h ago at 95% and the weekly window reset 30m ago at
100%, shown as the fallback because `lastActiveOrg` named an org with no
stored record: the card's verdict was `free`, its session row read
`AVAILABLE` in green, and its headline was **`crit`** — a red pill for a free
account — while the same record's other-account row, same storage and same
`now`, was `ok`. Both card reads now go through the verdict for a non-exempt
record. The two consumers that still read the record raw are the ones the
exemption names: the card of the **active** account (the probe re-measures it
within seconds) and the **toolbar badge**, which reads
`accounts[lastActiveOrg]` and nothing else, so it is only ever about that same
active account.

⚠ **There is no `+N more` cap.** The widget drew at most four other-account
rows and summarised the rest as a count that nothing could expand, so any row
past the fourth was unreachable by any click. Making the count clickable
would have added a button inside the card, which is forbidden (see the
pointer-events note below), so every row is painted and the height is bounded
by CSS instead: `.otherlist` scrolls past ~170px. ⚠ Whether the cap was
already firing on the operator's own store is **not measured** — a dead end
does not need to be occupied today to be a dead end. `lib/widget.js` records
what *was* measured about that store, and what was not.

⚠ **Staleness must not grey a `free` row**, and that is not a style
preference. The account worth switching to is by construction the one measured
longest ago, so the 6h staleness rule washes out precisely the most actionable
row. The availability verdict outranks staleness for styling; `measured` and
`unknown` rows still grey, because for those the stored percentage *is* the
claim. The mechanism is a CSS one: `.card.stale` dims the active account's own
elements rather than the card, because `opacity` on an ancestor cannot be
undone by a descendant.

Per-account labels (`accountLabels` in `chrome.storage.local`) are **written by
the popup only**. The in-page widget reads them and has no editor: a text input
in a card floating over claude.ai's composer is the wrong affordance, and a
direct route back to the `pointer-events` bug that shipped in round 1.

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
