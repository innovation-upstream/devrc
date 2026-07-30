# Passive as-you-type completion for the Claude Code TUI — feasibility & recommendation

Date: 2026-07-25
Author: research pass (deep-research agent)
Scope: Can a passive, as-you-type, cursor-anchored text-completion popup, fed by a learned frecency model of Zach's own recurring phrases, be delivered to the **Claude Code TUI** (terminal + tmux, X11 + i3, NVIDIA no-compositor, NixOS)?

---

## Executive verdict

**Conditionally NO for the strict spec; conditionally YES for a slightly relaxed one.**

- A truly **cursor-anchored** passive popup driven by the OS input stack (IME candidate window) is **infeasible** on this target. It fails on **two independent, well-sourced grounds**: (1) on X11 the IME candidate/preedit window is *not* cursor-anchored — it is pinned to the bottom of the terminal window by an Xlib/XIM limitation; and (2) a full-screen raw-mode TUI (Claude Code's class of app) **clobbers the terminal's in-application preedit display entirely**, so the as-you-type preview never renders inside the prompt. Confirmed by the fcitx5 maintainer on GitHub Copilot CLI, which is architecturally the same kind of Node full-screen TUI as Claude Code.
- Shell-native inline autosuggestion (zsh/fish gray-ghost-text, the exact desired UX) works only in the shell's own line editor. It **does not reach the Claude Code prompt**, which is Claude Code's own input widget, not zsh's `zle`.
- There is **no Claude Code extension point** for as-you-type input suggestions today. Claude Code has built-in inline completion for `/commands`, `@files`, and (since v2.1.217, 2026-07-21) `:emoji:` shortcodes — proving the machinery exists internally — but exposes no custom-completion / input-suggestion plugin API. Hooks (`UserPromptSubmit`) fire *on Enter*, after the fact, not as you type.

**The only path that delivers "passive, zero-invocation, accept-with-Tab" for the Claude Code prompt specifically is a small custom external X11 overlay** fed by the existing keylogger + a frecency engine, injecting accepted text with `xdotool`. It must **give up true dynamic cursor-anchoring** — but it does not need it: Claude Code's prompt box is always at the **bottom of the terminal window**, so a HUD anchored to the bottom of the focused terminal window sits right at the prompt without any per-keystroke cursor-pixel tracking. That is the recommended build. Confidence: HIGH that the IME/off-the-shelf routes fail; MEDIUM-HIGH that the overlay is buildable.

If even the overlay is judged not worth it, the honest fallback is a **faster explicit-invoke inline picker** (still a keystroke), or accepting the status quo (short phrases are cheap to type; that's why espanso lost).

---

## Feasibility matrix

| Mechanism | Passive (0-invoke)? | Works in Claude TUI? | Cursor-anchored? | Survives tmux? | No-compositor OK? | NixOS-packaged? | Effort | Verdict |
|---|---|---|---|---|---|---|---|---|
| **(a) IME candidate window** (fcitx5/ibus + typing-booster/presage) | Yes (by design) | **NO** — TUI kills preedit display | **NO** on X11 (pinned to window bottom) | Text yes, preview no | popup is own window, ok | Yes (`i18n.inputMethod`, system-level) | Med (system, relogin) | **FAIL** — dies twice over |
| **(b) Shell-native autosuggest** (zsh-autosuggestions/fish) | Yes | **NO** — only in shell `zle`, not the TUI prompt | Yes (in shell) | Yes (in shell) | Yes | Yes (HM) | Low | **N/A** — wrong surface |
| **(c) Claude Code native** (built-in / plugin / hook) | Would be ideal | Would be, if it existed | Would be (it renders inline emoji/slash today) | Yes | Yes | it's the app | n/a | **NOT AVAILABLE** — no input-suggestion API; file FR |
| **(d) External X11 overlay** (keylogger → frecency → override-redirect HUD → xdotool inject) | **Yes** | **Yes** (independent of TUI) | Near-cursor only (bottom-of-window HUD) | Yes | Yes (plain override-redirect, no transparency needed) | Yes (user-level, HM) | Med (custom build) | **VIABLE** — the recommended path |
| (e) Explicit-invoke inline picker (fallback) | No (1 keystroke) | Yes | Yes-ish | Yes | Yes | Yes | Low-Med | **FALLBACK only** |

---

## Per-question findings

### RQ1 — Core feasibility, per mechanism

**(a) IME route — FAIL (two independent failures).**

- *Failure 1: not cursor-anchored on X11.* Alacritty's own release notes state the IME popup "is stuck at the bottom of the window due to Xlib limitations" (0.11.0), and only in 0.13.0 did it start *trying* to "not obscure the current cursor line" — i.e. it still isn't placed *at* the cursor; it's a window-relative heuristic. This is an X11/XIM `over-the-spot` limitation, not an Alacritty bug per se; it recurs across X11 terminals. So even in the best case the candidate window would float at the bottom of the terminal, not at the cursor. (Alacritty CHANGELOG / issues #7341, #6313, PR #7883.)
- *Failure 2: the TUI destroys preedit display.* This is the make-or-break, and there is direct evidence. GitHub Copilot CLI — a Node full-screen raw-mode TUI, the **same architectural class as Claude Code** — makes the terminal's in-application preedit **disappear**. The fcitx5 maintainer's diagnosis (issue github/copilot-cli#735): *"Copilot CLI does cause Gnome Terminal's own preedit to disappear. Other fcitx5's input methods … are also affected."* The cause is the TUI's TTY/raw-mode control conflicting with the terminal's own preedit rendering. This is generic to full-screen raw-mode TUIs, so Claude Code is in exactly the same bucket. The only workaround (disable "Show preedit in application" → force a floating window) is **global** (degrades every GUI app) *and* on X11 that floating window is the non-cursor-anchored bottom-pinned one from Failure 1. Net: you'd type blind or with a mislocated preview — useless for a passive as-you-type suggestion.

  Confidence: HIGH. Two independent, sourced failures; the second is on an app of the same class as the target.

**(b) Terminal-native completion — right UX, wrong surface.** zsh-autosuggestions / fish deliver *exactly* the target feel: inline gray ghost text after the cursor, accept with →/End (and Tab configurable), partial-accept with Alt-→. But this lives in the shell's line editor (`zle`/fish reader). Claude Code draws its **own** prompt input on the alternate screen; the shell reader is not running. So this mechanism cannot touch the Claude Code prompt. It would only help at the bare zsh prompt. Confidence: HIGH (definitional).

**(c) External X11 overlay — the viable route.** Decomposed:
- *Rendering without a compositor:* fine. The popup is a plain **override-redirect** X11 window; it needs no transparency, shaping, or compositor. Draw a 1-line opaque box (gruvbox to match). The no-compositor constraint is a non-issue here.
- *Knowing the live prefix:* the existing X11 keylogger already taps every keystroke into `activity.events (source=keys)`. The overlay daemon subscribes to the same keystroke stream (or a local tap of it) to reconstruct the in-progress line in real time; i3 focus tells it the focused window is the Claude Code terminal (only show the HUD then). No new input hook needed — this is strong synergy with existing infra.
- *Text injection:* `xdotool type` / `xdotool key` synthesizes X key events to the focused terminal window; those are delivered to the PTY as ordinary key input, so Claude Code receives them even though it's in raw mode (raw mode blocks line-buffering, not real key delivery). Works. On Tab/Enter with a suggestion active, replace the typed prefix (backspaces) and inject the full phrase.
- *The hard part — cursor anchoring:* nobody exposes a TUI's cursor pixel position, and tmux adds a coordinate layer (status bar, pane offsets, scroll). Truly tracking the cursor per keystroke over TUI+tmux is fragile. **But you don't need it:** the Claude Code input box is always at the *bottom* of the terminal. Anchor the HUD to the bottom edge of the focused terminal window (window geometry is trivially queryable via X11), and it sits at the prompt. This trades "cursor-anchored" for "prompt-anchored," which for this target is equivalent in practice.

  Confidence: MEDIUM-HIGH. Each mechanism is individually known-good; integration risk is in focus/edge cases and the prefix-reconstruction plumbing.

### RQ2 — Terminal IME comparison (X11 preedit + candidate popup)

Moot for the primary goal because RQ3 kills the IME route inside a TUI regardless of terminal — but for completeness:

- **Alacritty:** inline preedit since 0.11; X11 candidate window pinned to window bottom (Xlib limit); 0.13 avoids obscuring the cursor line. Decent inline preedit for shell use; not cursor-anchored on X11.
- **wezterm:** most configurable IME (`use_ime`, `ime_preedit_rendering`, `xim_im_name`); known X11 quirk of preedit showing on all split panes (cosmetic). Best IME story of the GUI terminals, but still bound by the same TUI-preedit-clobber problem.
- **kitty:** IME deliberately **off by default**; requires `GLFW_IM_MODULE=ibus` (works for fcitx5 too via the ibus protocol; `fcitx`/`uim` unsupported). Maintainer has historically resisted IME. Weakest fit.
- **foot:** Wayland-only — irrelevant on this X11 host.
- **ghostty:** native-leaning IME; GTK/X11 support exists but no evidence it solves cursor-anchoring-in-TUI (the TUI problem is app-side, not terminal-side).
- **xterm/urxvt:** classic XIM `over-the-spot`; same X11 anchoring class, dated.

Best host *if you were forced down the IME path:* wezterm. But **switching terminal does not rescue the goal**, because the failure is the TUI clobbering preedit, not the terminal. So: no terminal switch is recommended. Keep Alacritty. Confidence: MEDIUM-HIGH.

### RQ3 — Does IME even work inside a full-screen TUI? (the make-or-break)

**No, not for a visible as-you-type preview.** This is the crux and the honest answer is negative. IME preedit works fine for shell line editing (bash/zsh/vim are cited as working) because those cooperate with the terminal's preedit. A full-screen raw-mode TUI (Copilot CLI, and by the same architecture Claude Code) takes over TTY control and the terminal's own preedit rendering **disappears** (fcitx5 maintainer, copilot-cli#735). Committed text may still arrive, but the whole point — a *visible* suggestion as you type — is exactly what breaks. Combined with the X11 non-anchoring, the IME route cannot satisfy the requirement. Confidence: HIGH.

### RQ4 — Existing-tools survey (currency verified July 2026)

- **ibus-typing-booster** (mike-fabian, actively maintained; latest 2.29.x line): genuinely passive prediction with inline completion (Tab accept) + learns a user dictionary + can be **trained from custom word/text files** — content-wise it's a near-perfect match for the frecency idea, and it's in nixpkgs (`ibus-engines.typing-booster`, `i18n.inputMethod`). **But** it renders through IBus/the IME candidate window, so it inherits RQ3's TUI-preedit death and RQ1's X11 anchoring. Great engine, wrong delivery channel for a TUI.
- **fcitx5 + presage** (predictive addon): same story — good predictor, IME-delivered, dies in the TUI.
- **espanso:** no passive inline-suggestion mode. Historically it *had* a "Passive Mode" that the maintainer considered **hacky and planned to remove** (espanso#540), and there's no roadmap (espanso#255) for ghost-text/inline suggestions. Espanso is trigger/replace, not predictive. Not a fit — and per Zach's own audit it already loses to hand-typing for short phrases.
- **AutoKey:** X11 hotkey/phrase expander (abbreviation → replace), no passive predictive ghost text; also X11-only and aging. Not a fit.
- **zsh-autosuggestions / fish:** perfect UX, shell-only (RQ1b).
- **Local-LLM inline completion tools** (e.g. shell copilots): all target the shell line or an editor buffer, not another app's TUI prompt; and an LLM is overkill for a ~10-phrase frecency list. Not a fit.

No off-the-shelf tool delivers passive as-you-type suggestions *into the Claude Code prompt*. Confidence: MEDIUM-HIGH.

### RQ5 — Claude-Code-native path (flagged prominently)

**This would dominate if it existed, but it does not today.**

- Claude Code *does* render inline prompt completions internally: `/slash` commands, `@file` paths, and `:emoji:` shortcodes (`:hea` → suggestions, added v2.1.217 on 2026-07-21, toggle `emojiCompletionEnabled`). So the inline-suggestion UI primitive is already in the product.
- But there is **no public extension point** to feed custom completions into the prompt: no "custom completion source", no as-you-type input plugin. The plugin/hook system operates at tool-execution and lifecycle events. The closest, `UserPromptSubmit`, fires **when you press Enter** (it can add context or block/modify the submitted prompt) — it cannot show suggestions *while typing*.
- The Claude Code TUI is shipped as a minified bundle, so patching the input widget directly is not a clean/maintainable route.

**Actionable recommendation:** file a feature request with Anthropic for a user-configurable prompt-completion source (frecency/history-backed inline autosuggest, accept-with-Tab) — they already have the rendering. If granted, it instantly beats every OS-level hack (in-app cursor anchoring, tmux-transparent, no injection). Until then it is not available. Confidence: MEDIUM-HIGH (can't fully prove a negative, but current docs show no such API).

### RQ6 — Frecency engine (KISS)

Minimal engine, no LLM:

1. **Corpus.** Two options, cheapest first:
   - Bootstrap from the already-measured phrase list ("proceed, dispatch" ~17, "yes dispatch" ~25, "merge it" ~19, "merged and deployed" ~21, "dispatch audit" ~12, "check again" ~9, "dispatch" ~7, "whats next" ~5, "yes proceed" ~5, …).
   - Then keep it live from `activity.events (source=keys)`: periodically (a `systemd --user` timer, e.g. every 15 min) query the keylog, reconstruct submitted prompt lines, and count/rank phrases.
2. **Model.** A **prefix trie** (or just a sorted list) of phrases, each with a frecency score `score = count * decay(age_of_last_use)` (e.g. exponential half-life of a few weeks). This is a few dozen entries — a Python dict is plenty; a trie only if the list grows.
3. **Query.** On the current live prefix (from the keystroke tap), return top-N phrases whose start matches the prefix (case-insensitive), ranked by frecency; show the top one as ghost text, cycle with a key if desired.

No LLM, no embeddings. Refresh the ranked table on a timer; the overlay just reads a small JSON/SQLite the timer writes (same pattern as `bar-status-poll` → `~/.cache/bar-status/*.json`). Confidence: HIGH.

### RQ7 — NixOS packaging & integration cost (for the recommended overlay path)

Entirely **user-level** — no sudo, no `/etc/nixos`, no IME framework, no relogin:

- **New scripts** under `scripts/` (consistent with the repo's `collector/`, `bar-status-poll`, `initiatives/` patterns):
  - `frecency-build` — ClickHouse query → ranked phrase table → `~/.cache/inline-frecency/phrases.json`, run by a `systemd --user` timer (serverMode-gated is optional; this is a graphical-only tool so gate on `graphical`).
  - `inline-overlay` — long-running `systemd --user` service (graphical-gated): taps keystrokes, tracks i3 focus (only active over the Claude Code terminal), renders the override-redirect HUD at the bottom of the focused terminal window, injects on Tab via `xdotool`.
- **Deps** via `home.packages` / `nix-shell`: `xdotool` (injection + window geometry), Python with `python-xlib` (or GTK) for the override-redirect window, `clickhouse` client or a small HTTP query (reuse the activity pipeline's auth). All in nixpkgs.
- **tmux interplay:** none problematic. Injection targets the terminal window; tmux forwards keys to the focused pane. The only tmux effect would be on cursor-cell math — sidestepped by bottom-of-window anchoring (no cell math needed).
- **No-compositor caveat:** irrelevant; opaque override-redirect window needs no compositor.
- **Nothing to stage as sudo.** (Contrast the dead IME route, which *would* have needed system-level `i18n.inputMethod` + relogin.)

Confidence: MEDIUM-HIGH.

---

## Recommendation

**Build the custom external X11 overlay (mechanism d), prompt-anchored to the bottom of the focused Claude Code terminal, fed by a trivial frecency table off the existing keylog, injecting with `xdotool`. In parallel, file the Claude Code feature request (RQ5) — if Anthropic ships native prompt autosuggest, retire the overlay.**

Rationale:
- It's the *only* route that puts a **passive, zero-invocation, accept-with-Tab** suggestion at the **Claude Code prompt** specifically.
- It reuses infrastructure Zach already runs (keylog → ClickHouse; timer→cache→consumer pattern; i3 focus).
- It's user-level, no sudo, no relogin, no terminal switch, no IME framework, compositor-independent.

Trade-offs / honest caveats:
- **Not truly dynamic-cursor-anchored** — it's anchored to the bottom of the terminal window. Acceptable because the prompt lives there, but it's a deliberate relaxation of the original spec. If a suggestion must literally track the caret column, this route can't guarantee it over TUI+tmux.
- It's a **custom build with moving parts** (keystroke tap, focus detection, injection edge cases). Injection replaces the typed prefix by sending backspaces then the phrase — must handle the race where you keep typing during injection.
- Duplicating the OS keyboard into a bespoke overlay is inherently a bit hacky vs. a native solution; treat the FR as the real long-term fix.

**Do NOT** switch terminals or install an IME framework for this — neither rescues the TUI-preedit-clobber, so both are wasted effort.

---

## Scoped custom-build spec (if you proceed with the overlay)

**Architecture (all user-level):**
```
activity.events (source=keys)  ──15min timer──▶  frecency-build ──▶ ~/.cache/inline-frecency/phrases.json
                                                                          │
live keystroke tap ──▶ inline-overlay daemon ◀────────────────────────────┘
        │  (reconstruct in-progress prefix)
        ├─ i3 focus == Claude Code terminal?  ──no──▶ hide
        ├─ prefix match in phrases.json?       ──no──▶ hide
        └─ yes ▶ draw override-redirect HUD at bottom of focused term window (gruvbox, JetBrainsMono)
                 on Tab/Enter ▶ xdotool: backspace×len(prefix-typed) + type(phrase)
```

**The three genuinely hard problems (de-risk in this order):**
1. **Live prefix reconstruction over the keylog + focus gating.** Verify you can, in real time, know "the user has typed `dis` in the Claude Code terminal right now." This is the make-or-break for the whole build — do it first.
2. **Injection correctness.** `xdotool` backspace-prefix + type-phrase into a raw-mode Ink TUI, including the keep-typing-during-injection race and multibyte/space handling. Verify accepted text lands correctly in the Claude Code prompt.
3. **HUD placement & redraw** without a compositor, at the bottom of the *focused* window, following window moves/focus changes on i3. Lowest risk, do last.

**Smallest viable prototype to de-risk (a few hours, throwaway):**
- Hard-code 3 phrases. Run a tiny Python script that: taps recent keystrokes (or reads a fifo you echo into), and when the buffer matches a prefix, pops a bare `tkinter`/Xlib override-redirect box at a fixed screen position, and on a hotkey runs `xdotool type "<phrase>"` into the focused window while a Claude Code session is open.
- If that single loop works end-to-end (see suggestion → Tab → text appears in the Claude prompt), the full build is justified. If prefix-tracking or injection is flaky, stop and fall back to an explicit-invoke picker.

**Fallback if the prototype is flaky:** a faster **explicit-invoke inline picker** — one hotkey (e.g. a spare `$mod` combo or a tmux binding) pops the frecency list filtered by whatever's typed, Enter injects. Still a keystroke (not passive), but robust and cheap. This is the honest floor if passive proves too fragile.

---

## Sources

- [Alacritty 0.11.0 release notes (inline IME; X11 popup pinned to window bottom)](https://alacritty.org/changelog_0_11_0.html)
- [Alacritty 0.13.0 release notes (IME tries not to obscure cursor line)](https://alacritty.org/changelog_0_13_0.html)
- [Alacritty issue #7341 — Update IME position opportunistically](https://github.com/alacritty/alacritty/issues/7341)
- [Alacritty issue #6313 — Can't clear preedit with inline IME](https://github.com/alacritty/alacritty/issues/6313)
- [github/copilot-cli #735 — fcitx5 preedit not visible in a CLI TUI (maintainer: TUI causes terminal preedit to disappear)](https://github.com/github/copilot-cli/issues/735)
- [wezterm use_ime / ime_preedit_rendering docs](https://wezterm.org/config/lua/config/ime_preedit_rendering.html)
- [wezterm issue #2569 — preedit shown on all panes](https://github.com/wezterm/wezterm/issues/2569)
- [Debian bug #990316 — enabling IME in kitty via GLFW_IM_MODULE (off by default)](https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=990316)
- [ibus-typing-booster (predictive IME, inline completion, custom-dictionary training)](https://github.com/mike-fabian/ibus-typing-booster)
- [nixpkgs manual — ibus-engines.typing-booster](https://nixos.org/manual/nixpkgs/stable/#sec-ibus-typing-booster)
- [NixOS Wiki — Fcitx5 / i18n.inputMethod](https://wiki.nixos.org/wiki/Fcitx5)
- [espanso #540 — maintainer considering removing "Passive Mode" (hacky)](https://github.com/federico-terzi/espanso/issues/540)
- [espanso #255 — long-term roadmap](https://github.com/espanso/espanso/issues/255)
- [zsh-autosuggestions (inline gray ghost text; accept with →/End; strategies)](https://github.com/zsh-users/zsh-autosuggestions)
- [Claude Code hooks reference (UserPromptSubmit fires on submit, not as-you-type)](https://code.claude.com/docs/en/hooks)
- Claude Code emoji-shortcode autocomplete (v2.1.217, 2026-07-21; `emojiCompletionEnabled`) — corroborated via 2026 Claude Code feature/changelog references (toolsbase.dev, blakecrosley.com).

*Unverified / flagged:* the exact current Claude Code version's completion internals and the absence of an input-suggestion plugin API are inferred from current public docs (hooks reference + feature roundups); Anthropic could add such an API — hence the feature-request recommendation. The overlay's injection-race and focus-edge behaviors are asserted from how `xdotool`/X11/raw-mode PTYs work, and should be confirmed by the prototype before committing to the full build.
