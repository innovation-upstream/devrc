# Espanso typing toil — hand-typed long-form vs. existing shortcuts

**Date:** 2026-07-25
**Scope:** Read-only analysis. Goal — find cases where an espanso snippet **already
exists** but Zach hand-types the long-form expansion anyway (the trigger→habit
transfer failed). This is distinct from `/espanso-audit` (which decides
add/shorten/remove from transcript phrase-mining); here we correlate the **raw
keylog typing stream** against the **live snippet definitions**.

---

## TL;DR — top findings

| Rank | Snippet | Fires (used) | Hand-typed long-form | Miss rate | Chars saved/use | **Toil score** |
|---|---|---|---|---|---|---|
| 1 | `:ds` → `dispatch subagent to ` | **1** | **94** | 99% | 18 | **1692** |
| 2 | `:rns` → `recommend next steps` | **1** | **20** | 95% | 16 | **320** |
| 3 | `:acq` → `ask me clarifying questions and recommend anything you think would be useful to include` | 2 | 2 | 50% | 83 | 166 |

Everything else has **zero** hand-typed toil: `:eos` (44 fires / 0 typed) and
`:kickoff` (24 fires / 0 typed) are the shortcuts that **stuck**; all paths
(`:hlt` `:kuc` `:nixos` `:cc*` …) and all `:ssh*` snippets show 0 hand-typed
full-form occurrences (tab-completion / rarely typed verbatim).

**Headline:** the shortcuts that failed are the **short, mid-sentence prefixes**
(`:ds`, `:rns`); the ones that stuck are the **long, whole-message rituals**
invoked via the Ctrl+Space search UI (`:eos`, `:kickoff`).

---

## Method

### Data source and why reconstruction is possible (NOT a proxy)
The X11 keylogger (`scripts/collector/keylog/`) does **not** store raw keystrokes
— `chunker.py` reconstructs **typing units** (flushed on Enter / focus-change /
2s-idle / maxlen) and **applies BackSpace in place**, so the `text` column of
`source='keys', kind='typing'` rows holds *what was actually left standing on
screen*. That means true typed-text reconstruction is available — **this is
keylog-derived, not a transcript proxy.**

Critically, this **resolves the ambiguity** that `espanso-usage.py` flags for
`:ds`/`:rns` in the transcript signal. When a snippet *fires*, espanso backspaces
the trigger and inserts the expansion via a **clipboard paste** — a paste is not
a KeyPress, so RECORD never captures it. Therefore:

- **A fire → the expansion text never enters the keylog typing stream.**
- **The expansion text appearing in a `kind='typing'` chunk ⇒ it was physically
  typed** (i.e. the shortcut was *not* used).

### Queries run (ClickHouse `activity.events`, reader creds via SOPS)
1. **Fires:** `count()` of `source='keys' AND kind='espanso'` grouped by
   `text` (trigger) × `host` × `payload.method` (direct vs search).
2. **Hand-typed:** for each text-detectable expansion, `countIf(
   positionCaseInsensitive(text, '<distinctive-substring>') > 0)` over
   `source='keys' AND kind='typing'`. Distinctive substrings were chosen to avoid
   cross-matching (e.g. `:cc`'s `civit/civitai ` won't match `civitai-gpu-fleet`).
3. **Contamination check:** `countIf(position(text, ':ds ') …)` etc. — literal
   triggers in the typing stream (would indicate a *failed* expansion inflating
   nothing, or trigger typed in prose). **Result: 0 for every trigger** → no
   failed-expansion contamination.

### Window
Keylog is **forward-only** (detector deployed mid-June). Coverage:
- laptop: 2026-06-24 → 2026-07-26 (~32 days, 80.4k typing chunks)
- workbench: 2026-07-05 → 2026-07-26 (~21 days, 25.7k typing chunks)

### Confidence & false-positive/negative risk
- **Confidence: HIGH** for the ranking. Three independent corroborations:
  (a) fires paste via clipboard so expansion-in-keystream can only be manual;
  (b) literal triggers = 0 in the typing stream (no failed-fire noise);
  (c) manual inspection of `:ds`/`:rns`/`:acq` samples — all are genuine Claude
  prompts typed in Alacritty (e.g. `dispatch subagent to process feedback:`,
  `dispatch subagent to research kubeclaw…`), not config/doc editing.
- **False-positive risk (small):** the expansion phrase could be typed for
  another reason — quoting it, or editing `nix/home.nix` line 92 / RULES.md
  where `dispatch subagent to` literally appears. Bounded: a config edit is ≤1
  occurrence, and the samples are overwhelmingly prompts. A few could trace to
  the `/espanso-audit` sessions themselves. Net effect on 94 is minor.
- **False-negative risk (undercount):** if the expansion is split across a
  focus-change or 2s-idle chunk boundary it won't substring-match. So **94/20 are
  lower bounds** — real toil is ≥ these.
- **App split:** `:ds` = 92 Alacritty + 2 Brave; `:rns` = 20 Alacritty. Alacritty
  = the Claude Code CLI → these are prompt-composition toil.

---

## Full ranked table (all text-detectable existing snippets)

| Snippet | Expansion (abridged) | Fires (direct/search) | Hand-typed | Chars saved | Toil score | Interpretation |
|---|---|---|---|---|---|---|
| `:ds` | `dispatch subagent to ` | 1 (1/0) | **94** | 18 | **1692** | Shortcut exists but you type the long form ~every time → trigger never transferred |
| `:rns` | `recommend next steps` | 1 (1/0) | **20** | 16 | **320** | Same pattern — short end/mid-prompt phrase, typed not expanded |
| `:acq` | `ask me clarifying questions and recommend anything…` | 2 (1/1) | 2 | 83 | 166 | 50/50; low volume but big per-use save when used |
| `:eos` | `review work done this session and identify skills…` (192 ch) | 44 (0/44) | **0** | 188 | 0 | **Stuck** — invoked via Ctrl+Space search every time |
| `:kickoff` | `give me the kickoff message to copy paste…` | 24 (0/24) | **0** | 49 | 0 | **Stuck** — search-invoked ritual |
| `:hlt` | `…/workspace/homelab-talos ` | 2 (0/2) | 0 | — | 0 | No verbatim hand-typing (tab-completion) |
| `:kuc` `:nixos` `:cc` `:cdp` `:cgf` `:cmo` `:csc` `:cpk` `:subk` | paths | 0 | 0 | — | 0 | Not typed verbatim; low/zero usage |
| `:sshwn` `:sshwl` `:sshln` `:sshll` | `ssh zach@…` | 0 | 0 | — | 0 | No verbatim hand-typing captured |

Toil score = `hand_typed_occurrences × chars_saved_per_expansion` (the manual
keystrokes an existing shortcut would have eliminated). Not text-detectable
(excluded): `:date :time :datetime :iso :uuid :clip` (dynamic output) and the
typo-corrections `dashbaord`/`reocmmend` (correct spelling isn't toil).

---

## Findings — why the failures failed

1. **`:ds` is the dominant toil (99% miss).** It fired **once** in the whole
   window but the expansion was typed **94×**. Root cause is structural, not a
   bad trigger string: `:ds` is a **mid-sentence prefix** ("dispatch subagent to
   X"). Expanding requires deciding "expand" at the *start* of composing a
   thought, then continuing to type the object anyway. The saving is marginal
   (18 chars, 3-char trigger already), so there's little pull to build the habit.
   It also **duplicates a RULES.md standing default** ("dispatch a subagent … as
   the default implementation workflow"), so the phrase is already muscle-memory.

2. **`:rns` is the same failure mode, smaller** (95% miss, 20 typed). Short
   end-of-prompt phrase; typing 20 chars is barely slower than reaching for a
   trigger, and it overlaps conceptually with `/resume` (which already proposes
   ranked next steps).

3. **`:acq` half-sticks (2/2).** Low frequency, but each use saves 83 chars, so
   the ROI per fire is high. The two hand-typings were near-verbatim
   (`ask me clarifying questions and recommend anything…` /
   `ask me any clarifying questions and recommend…`) — the intent is habitual,
   the trigger just isn't top-of-mind at that moment.

4. **The snippets that stuck share a profile:** long expansion (49–192 chars
   saved), a *whole-message ritual* (not a prefix), and invocation via the
   **Ctrl+Space search UI** keyed on memorable search_terms (`handoff`, `wrap`,
   `kickoff`) rather than a typed trigger. Zach's espanso muscle-memory is
   **search-first**, not direct-trigger — every `:eos`/`:kickoff` fire was a
   search fire, and `:ds`/`:rns`'s only fires were direct. The direct-trigger
   habit essentially doesn't exist for the short prefixes.

---

## Recommendations

Deterministic where possible. This complements `/espanso-audit` (which already
pruned the 0-fire snippets on 2026-07-25) — it does **not** redo it.

- **`:ds` → DROP (primary) / accept as-is (fallback).** The trigger did not
  transfer (1 fire in a month), the per-use saving is marginal (18 chars), and
  it duplicates a RULES.md default Zach already types fluently 94×. A shorter
  trigger won't help — `:ds` is already 3 chars and the failure is behavioral
  (prefix-at-sentence-start), not length. Dropping loses ~nothing; keeping is
  harmless clutter. **Recommend drop** unless he wants it purely as a
  search-menu entry (then it's fine — search is how his espanso habit actually
  works).
- **`:rns` → DROP / repoint.** Same reasoning (95% miss, 16-char save, overlaps
  `/resume`). Lowest-effort: drop it. If kept, only worthwhile as a search entry.
- **`:acq` → KEEP as-is.** Only text-detectable snippet with genuinely high
  per-use value (83 chars) that Zach *does* reach for. Low frequency, no change
  needed; don't drop it.
- **`:eos`, `:kickoff` → KEEP.** Working exactly as intended (44 / 24 fires, 0
  toil). These are the model to emulate.
- **Paths & `:ssh*` → no typing-toil action here.** Zero verbatim hand-typing.
  (`:hlt` fires occasionally; the rest are near-dormant — a *usage* question for
  `/espanso-audit`, not a *toil* question.)
- **Structural takeaway for future snippets:** short mid-sentence prefixes don't
  build a direct-trigger habit for this operator. Snippets stick when they are
  (a) long, (b) a whole-message ritual, and (c) discoverable via Ctrl+Space
  search terms. Design new snippets to that profile; don't add short prefixes
  and expect trigger transfer.
