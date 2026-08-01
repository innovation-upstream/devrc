# initiatives viewer — board layout & interaction reference

Read this when you are **changing the viewer UI**, debugging why a card renders in
the wrong lane/state, or wiring a new per-card action. Day-to-day operation does
not need it. Live at **http://192.168.50.250:8899/**.

## Default view
**GROUPED by repo** (collapsible; `VIEW_KEY` v3; flat/recency in the toggle). Cards are
**two-line collapsed** (state glyph + slug + title + age; one actionable `line2`) →
**click to expand** the full current/start/you/live/PRs/investigations detail.

## State
**`derive_state`** per card, precedence `needs_you > stalled > slowing > active`:

| glyph | state | meaning |
|---|---|---|
| ⚠ orange | `needs_you` | blocked on Zach |
| ◑ gray | `stalled` | ≥7d |
| ~ yellow | `cooling` | 2–7d |
| → blue | `active` | <2d |

**`live` is a separate OVERLAY BADGE** (● green, render-time tmux overlay via
`buildLiveNow`), **NOT a state**.

**`needs_you` is SEVERITY-aware** — `assistant.SEVERITY_MARKERS` over `status`+`next_step`
promote an active RISK card with a `⚠ risk` cue (single-sourced from
`assistant._severity_hits`; `tool_blocked_on_me` = `_blocking_hits OR _severity_hits`, so
`/api/ask` "blocked/at-risk" has PARITY with the chip).

## Layout — 4 sections
1. §3 `⚠ Needs you` **PINNED top** (rendered once, excluded from groups).
2. §4 **`● N live · newest:<task>` ONE-LINE strip** (`LIVENOW_OPEN_KEY='-v2'`, click→top-6
   `＋more` activity-sorted; rows clickable→`focusCard`; scoped by the active filter).
3. §5 active cards grouped by repo (**live-badged float to top**, stable `vis.sort`).
4. §6 `~ Cooling` collapsed fold (slowing+stalled).

## Filters & search
**State chips** `[⚠ Needs you][◑ Stalled][~ Cooling][→ Active][All]` — filter, compose AND
with search; `⚠ Needs you` pulses when >0. A **SEPARATE `[✓ Archived]`** view toggle.

**Search** (`matchQ`) AND-composes with the chip, scopes the Live-now rows too, and
**auto-widens to All** when a filter hides every hit (`shouldWidenFilter`, sticky); the
active chip stays visible+highlighted at 0; matches show a `match: …snippet…` reason.

`matchQ` is client-side **FUZZY**: substring + bounded-Levenshtein ≥4-char +
ordered-subsequence ≥5-char, per-token AND, over
title/summary/status/opening/latest/`search_text`/next_step/slug/repo. A match in the
collapsed Emerging lane auto-expands it.

## Per-card actions (`cardActions`, state-driven)
- `needs_you` → `[resolve][⤴ dispatch?][⤓ archive]`. `[resolve]` prefills+submits the
  `/api/ask/stream` sidebar.
- `stalled`/`cooling` → `[⤴ resume?][drop][⤓ archive]`. `resume` = `/api/dispatch` with a
  RESUME-framed body via `dispatch.py _task_lead`.
- `active` → `[⤴ dispatch?][⤓ archive]`.
- `?` = only when a grounded rec exists. `drop`/`⤓ archive` are **two-tap** (`armConfirm`).

**Emerging/undocumented = an inline `emerging` badge**; the glyph **legend is behind a `?`**
toggle (`.legend-toggle`, hidden by default).

## Archive lifecycle
`POST /api/archive {repo,slug,reason?}` (viewer-side, never-500) hides the card + persists
to the standalone `initiatives.archived` table (`archive.py`:
`archive`/`unarchive`/`read_archived`/`list_archived`).

**Suppress IFF archived AND `last_touch <= archived_at`** → **auto-resurfaces on new
activity**. `[✓ Archived]` opens the archived view (`POST /api/unarchive` restores).

⚠ `load_latest()` returns **`(rows, archived)`** — 2 callers (`DataProvider.snapshot`,
`assistant.load_initiatives`) handle the tuple.

## Also on the board
Recaps (identity primary / status secondary), a `POST /refresh` ↻, `POST /api/dispatch`,
the `POST /api/ask` sidebar.

Full dogfood-evolution history:
`devrc/claudedocs/handoff-initiatives-nextstep-dispatch-shipped-2026-07-26.md`.
