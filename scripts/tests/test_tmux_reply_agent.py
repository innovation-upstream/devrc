"""Behavioural tests for scripts/tmux-reply-agent — the host half of clawgate's
terminal WRITE surface.

Everything here runs against a STUB HTTP server bound to loopback on an ephemeral
port and a STUB tmux binary that records its own argv. Nothing touches the real
clawgate, either host's tmux server, any pane, or the network.

WHAT THIS SUITE IS FOR
----------------------
🔴 ONE DELIVERY BY THIS AGENT IS ARBITRARY COMMAND EXECUTION AS THE OPERATOR.
It runs `tmux send-keys` into a named pane and then presses Enter, on text that
arrived over a LAN route with no human auth. So the load-bearing tests are not
the happy path — they are the four ways that execution can go wrong in a way
nobody would notice:

  1. THE TEXT MUST NEVER REACH A SHELL. It is arbitrary operator input, so
     `$(…)`, backticks, `;` and quotes in it are code the moment anything
     interpolates them into a command string. `test_the_text_is_delivered_as_one
     _argv_element_with_no_shell` asserts the exact bytes arrive as a single
     argument.

  2. `-l` AND `--` ARE PART OF THE COMMAND, NOT DECORATION. Without `-l`,
     `send-keys` reads its argument as KEY NAMES — a reply containing "C-c"
     interrupts whatever is running instead of typing three characters. Without
     `--`, text starting with `-` is parsed as a flag.

  3. THE WRITE-TIME GUARD MUST ACTUALLY REFUSE. A tmux server restart re-mints
     pane ids from %0, so a stored `%12` names a different pane. The refusal has
     to happen with NO send-keys at all — a guard that runs after the write is
     not a guard.

  4. THE TEXT MUST NEVER BE LOGGED. The operator chose a full audit log INCLUDING
     the reply text, and was told the cost: a reply may contain a secret. The
     audit log lives in the server's table behind the terminal token; the
     journal does not.

  5. THE POSITIVE CONTROL. `test_a_claimed_write_is_delivered_and_reported`
     proves the stub tmux and the stub server can observe anything at all. Every
     "no send-keys happened" assertion below is meaningless without it.

⚠ THE AGENT SHIPPED DISABLED AND IS NOW ARMED — `enableTmuxReplyAgent` is true —
but that changes NOTHING about how these tests are built. They still drive the
script directly against stubs rather than asserting anything about a running
system: a test that reached the live agent would be sending keystrokes into the
operator's real panes. The two switches remain independent — the server's routes
answer 503 until CLAWGATE_TERMINAL_TOKEN is provisioned on the POD, and the unit
is wired into `default.target` only while `enableTmuxReplyAgent` is true.
"""
from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import json
import os
import re
import pathlib
import shutil
import subprocess
import tempfile

# 🔴 RESOLVED AT IMPORT, BEFORE ANY FIXTURE CAN RUN. Several suites in this tree
# legitimately clobber os.environ["PATH"] (see
# test_no_real_launchers.PINNED_PATH_CLOBBERS), so a `shutil.which("tmux")` call
# inside a test body reports on the run order rather than on the host. Measured:
# the two task-524 tests passed alone and in every pair, and failed only in the
# full four-suite sweep, for exactly this reason.
_TMUX_EXE_AT_IMPORT = __import__("shutil").which("tmux")
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "tmux-reply-agent"
COLLECTOR = REPO_ROOT / "scripts" / "session-manager"
HOME_NIX = REPO_ROOT / "nix" / "home.nix"

sys.path.insert(0, str(REPO_ROOT / "scripts"))

# 🔴 EVERY RUNTIME STUB GOES THROUGH `testlib.mockbin.write_exec`, WHICH OWNS THE
# SHEBANG. The nix check sandbox — the tier that gates the merge — has no
# `/usr/bin/env`, so a hand-written shebang cannot exec there, and the failure
# does not present as a fixture fault: the stub fails, the code under test
# correctly reports an error, and the assertion that fires points at production.
from testlib.mockbin import write_exec  # noqa: E402
from testlib import nix_units  # noqa: E402

# A synthetic marker used wherever a test needs to prove a specific string did or
# did not travel. Pairwise distinct from every other literal in this file, so a
# match cannot come from somewhere else.
MARKER = "ZZ-SYNTHETIC-REPLY-MARKER-4471-ZZ"


# --------------------------------------------------------------------------- #
# The pane states that DECIDE how a reply must be delivered.
# --------------------------------------------------------------------------- #
# 🔴 EVERY SHAPE THAT CHANGES THE ANSWER IS EXPRESSIBLE HERE, and that is the
# point rather than thoroughness for its own sake. A fake that could only build
# ONE pane state would run every guard below in the one configuration where the
# defect cannot appear: the whole bug is that a MENU needs different keys from a
# PROMPT, so a fixture that cannot render a menu can only ever prove the prompt
# path. The eight states are the two that must deliver (a shell prompt, a Claude
# text prompt), the one that must deliver DIFFERENTLY (a single-question menu),
# the two that must be REFUSED (a multi-question tab strip, a multiSelect list),
# and the three that decide which branch of the menu path runs (no row matches,
# the rows are in a different order from the ones clawgate sent, the pane cannot
# be read at all — that last one via `tmux_stub.fail_capture`).
#
# ⚠ SYNTHETIC, and they have to be: this repo is PUBLIC and a capture of the
# operator's real pane is captured text. These reproduce the SHAPE recorded in
# the live measurement (Claude Code 2.1.232) — the `❯` cursor, the numbered rows,
# the `☐` question marker, the tab strip, the `[ ]` checkboxes — with invented
# questions and options.
DEFAULT_PROMPT_CAPTURE = (
    "devrc git:(main) $ ls\n"
    "flake.nix  nix  scripts\n"
    "devrc git:(main) $ \n"
)

CLAUDE_TEXT_PROMPT_CAPTURE = (
    "● I have read the two files and the change is ready.\n"
    "\n"
    "╭──────────────────────────────────────────────────────────────╮\n"
    "│ >                                                            │\n"
    "╰──────────────────────────────────────────────────────────────╯\n"
    "  ? for shortcuts\n"
)

#: 🔴 THE FOOTERS BELOW ARE MEASURED VERBATIM AND THEY USED TO BE INVENTED.
#: Every menu fixture in this file carried `↑↓ to select · enter to confirm` or
#: `enter to confirm · ctrl+g to edit in Nvim`, and Claude Code 2.1.232 renders
#: NEITHER. Re-measured by raising real modals in a scratch tmux session:
#:
#:   one question          Enter to select · ↑/↓ to navigate · Esc to cancel
#:   one question, the     Enter to select · ↑/↓ to navigate · ctrl+g to edit in
#:     free-text row         Nvim · Esc to cancel
#:     focused
#:   a question CHAIN      Enter to select · Tab/Arrow keys to navigate · Esc to
#:                           cancel
#:   tool-approval         Esc to cancel · Tab to amend
#:   trust-folder /        Enter to confirm · Esc to cancel
#:     bypass warning
#:
#: A fabricated footer is not a harmless detail here: the footer is now the
#: signal that decides whether the pane is an ask at all, so a wrong one makes
#: every menu fixture a test of a render that does not exist. This is the same
#: lesson `rows_are_one_block`'s docstring records about synthetic ROW SPACING,
#: one field over — and it was live in this file the whole time that lesson was.
#: The option LABELS stay invented (this repo is public; a real capture is
#: captured text). The CHROME is measured, because the code reads the chrome.
MENU_FOOTER_SINGLE = "Enter to select · ↑/↓ to navigate · Esc to cancel"
MENU_FOOTER_FREE_TEXT = ("Enter to select · ↑/↓ to navigate · ctrl+g to edit in Nvim · "
                         "Esc to cancel")
MENU_FOOTER_CHAIN = "Enter to select · Tab/Arrow keys to navigate · Esc to cancel"
MENU_FOOTER_APPROVAL = "Esc to cancel · Tab to amend"
MENU_FOOTER_STARTUP = "Enter to confirm · Esc to cancel"

#: One question, four rows. The measured single-question render: a `☐` marker,
#: NO tab arrows and NO Submit tab, cursor on row 1, and the ask's own footer.
SINGLE_MENU_CAPTURE = (
    "● Which store should the queue use?\n"
    "\n"
    "  ☐ Store\n"
    "\n"
    "  ❯ 1. Ledger\n"
    "    2. Flatfile\n"
    "    3. Type something.\n"
    "    4. Chat about this\n"
    "\n"
    + MENU_FOOTER_SINGLE + "\n"
)

#: 🔴 THE SHAPE A REAL ASK ACTUALLY HAS, AND THE ONE THE DETECTOR COULD NOT SEE.
#: MEASURED over all 57 live tmux panes on this host: the three panes showing a
#: real `AskUserQuestion` modal all render each option's DESCRIPTION on the lines
#: beneath it, indented further, with NO blank line anywhere in the run — so
#: consecutive option rows sat 7, 5, 4 and 2 lines apart. At `MENU_ROW_GAP = 4`
#: `menu_block` returned ZERO rows for every one of them, the pane classified as
#: `text`, and a sweep of the whole fleet found not one menu this agent could
#: drive. The first inter-row span here is 7 lines — the measured MAXIMUM, chosen
#: to OVERSHOOT the old bound of 4 rather than sit on it: at a span of 3 or 4 a
#: mutant restoring `MENU_ROW_GAP = 4` passes this fixture and SURVIVES, having
#: never executed the thing under test. The two trailing rows are the short
#: standard ones, matching the measurement (label lengths 38/38/38/15/15).
DESCRIBED_MENU_CAPTURE = (
    "● Which store should the queue use?\n"
    "\n"
    "  ☐ Store\n"
    "\n"
    "  ❯ 1. Ledger\n"
    "     keeps an append-only journal and replays it on start\n"
    "     costs one extra fsync per write\n"
    "     survives a crash mid-batch without a repair pass\n"
    "     needs a compaction job once the journal is large\n"
    "     the compaction job is not written yet\n"
    "     so this is the slower of the two today\n"
    "    2. Flatfile\n"
    "     one file per row, no journal at all\n"
    "     simplest thing that could work\n"
    "    3. Type something.\n"
    "     write your own answer\n"
    "    4. Chat about this\n"
    "\n"
    + MENU_FOOTER_SINGLE + "\n"
)

#: The same render carrying a `✔ Submit` tab strip AND the chain's own footer — a
#: MULTI-question chain, which must still be refused once its rows become visible.
DESCRIBED_MULTI_QUESTION_CAPTURE = DESCRIBED_MENU_CAPTURE.replace(
    "  ☐ Store\n", "  ←  ☐ Store  ☐ Region  ✔ Submit  →\n").replace(
    MENU_FOOTER_SINGLE, MENU_FOOTER_CHAIN)

#: The SAME four options in a DIFFERENT order. A digit derived from the order
#: clawgate sent — rather than from the order the pane shows — answers the wrong
#: row here and nowhere else.
REORDERED_MENU_CAPTURE = SINGLE_MENU_CAPTURE.replace(
    "  ❯ 1. Ledger\n    2. Flatfile\n",
    "  ❯ 1. Flatfile\n    2. Ledger\n")

#: The free-text row after its digit FOCUSED it: the cursor has moved to it and
#: the hint has grown the Nvim affordance.
SINGLE_MENU_FREE_TEXT_FOCUSED_CAPTURE = (
    "● Which store should the queue use?\n"
    "\n"
    "  ☐ Store\n"
    "\n"
    "    1. Ledger\n"
    "    2. Flatfile\n"
    "  ❯ 3. Type something.\n"
    "    4. Chat about this\n"
    "\n"
    + MENU_FOOTER_FREE_TEXT + "\n"
)

#: What the pane shows once the ask has been ANSWERED and the tool has returned.
ANSWERED_CAPTURE = (
    "● User answered Claude's questions:\n"
    "  ⎿  · Which store should the queue use? → Flatfile\n"
    "\n"
    "● Wiring the flatfile store now.\n"
)

#: The measured MULTI-question render: a tab strip with arrows and a Submit tab,
#: plus the chain's own footer.
MULTI_QUESTION_MENU_CAPTURE = (
    "● Two things before I start.\n"
    "\n"
    "  ←  ☐ Store  ☐ Region  ✔ Submit  →\n"
    "\n"
    "  ❯ 1. Ledger\n"
    "    2. Flatfile\n"
    "    3. Type something.\n"
    "    4. Chat about this\n"
    "\n"
    + MENU_FOOTER_CHAIN + "\n"
)

#: 🔴 THE SAME LIVE CHAIN AFTER A SHORT PANE CLIPPED ITS TAB STRIP, which is the
#: A1 blocker's own shape. Reproduced from a real render: a 2-question chain in a
#: 104x14 window showed the cursored rows and the footer, and NOTHING above them —
#: no tab strip, no `☐` marker, no `Review your answers`. The shipped classifier
#: read this `menu`, pressed a digit, answered question 1, watched question 2
#: appear, and reported `delivered`. The footer is the only surviving signal.
CLIPPED_CHAIN_CAPTURE = (
    "  ❯ 1. Ledger\n"
    "     keeps an append-only journal and replays it on start\n"
    "    2. Flatfile\n"
    "     one file per row, no journal at all\n"
    "────────────────────────────────────────────────────\n"
    "  5. Chat about this\n"
    "\n"
    + MENU_FOOTER_CHAIN + "\n"
)

#: 🔴 THE SAME CLIPPING ONE STEP FURTHER, where the numbered block is DESTROYED.
#: Reproduced from a real render at 104x13: row 1 has left the viewport and the
#: modal marks the scrolled edge row with `↓`, which stands exactly where
#: `MENU_OPTION_RE` expects whitespace or the cursor — so ZERO rows parse. The
#: shipped classifier read this `text` and TYPED THE REPLY INTO A LIVE MODAL.
#: ⚠ The `↓` is measured, not decorative: it is the fourth route to an empty
#: block and no earlier round named it.
SCROLLED_SINGLE_ASK_CAPTURE = (
    "↓ 2. Flatfile\n"
    "     one file per row, no journal at all\n"
    "────────────────────────────────────────────────────\n"
    "  5. Chat about this\n"
    "\n"
    + MENU_FOOTER_SINGLE + "\n"
)

#: 🔴 THE LIVE TOOL-APPROVAL PROMPT, CAPTURED. A cursored numbered menu that the
#: shipped classifier drove by digit — so a reply of `Yes` pressed row 1 and
#: APPROVED A TOOL CALL THE OPERATOR NEVER SAW, and `No` pressed row 3 and denied
#: it. BOTH were deliverable: the deny row's label is bare here, which an earlier
#: round believed impossible. Its footer names neither `Enter to select` nor
#: `Enter to confirm`, and that is the discriminator — not the labels.
PERMISSION_PROMPT_CAPTURE = (
    "● Write(queue-schema.sql)\n"
    "\n"
    "────────────────────────────────────────────────────\n"
    " Create file\n"
    " queue-schema.sql\n"
    "╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌\n"
    "  1 create table queue ()\n"
    "╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌\n"
    " Do you want to create queue-schema.sql?\n"
    " ❯ 1. Yes\n"
    "   2. Yes, allow all edits during this session (shift+tab)\n"
    "   3. No\n"
    "\n"
    " " + MENU_FOOTER_APPROVAL + "\n"
)

#: 🔴 THE LIVE STARTUP DIALOGS, CAPTURED, AND THE SECOND ONE IS INVERTED. The
#: trust-this-folder prompt reads `❯ 1. Yes, I trust this folder` / `2. No, exit`;
#: the bypass-permissions warning that follows it reads `❯ 1. No, exit` /
#: `2. Yes, I accept`. So a reply of `Yes` would have pressed 1 on one and 2 on
#: the other — the row a label sits on is not a property of the word. Both carry
#: `Enter to confirm`, never `Enter to select`.
STARTUP_DIALOG_CAPTURE = (
    " Do you trust the files in this folder?\n"
    "\n"
    " ❯ 1. Yes, I trust this folder\n"
    "   2. No, exit\n"
    "\n"
    " " + MENU_FOOTER_STARTUP + "\n"
)
INVERTED_STARTUP_DIALOG_CAPTURE = (
    " Bypass permissions mode disables all permission checks.\n"
    "\n"
    " ❯ 1. No, exit\n"
    "   2. Yes, I accept\n"
    "\n"
    " " + MENU_FOOTER_STARTUP + "\n"
)

#: 🔴 THE TWO LIVE CHOOSERS THAT **DO** CARRY `Enter to select`, AND THE REASON THE
#: SUBSTRING PAIR WAS NEVER A DISCRIMINATOR.
#:
#: ⚠ PROVENANCE, STATED, BECAUSE THE TWO HALVES ARE NOT THE SAME KIND OF EVIDENCE.
#: The live captures were taken by the REVIEW round at 104 columns on Claude Code
#: 2.1.232; this round read them rather than re-raising the dialogs (an isolated
#: `CLAUDE_CONFIG_DIR` has no credentials, and using the real one would have fired
#: the operator's clawgate/notify Stop hooks). What this round measured itself is the
#: stronger half: both footers are WHOLE LITERALS in the shipped bundle —
#: `grep -aoP '.{0,120}Enter to select.{0,120}' .claude-wrapped` returns exactly
#: these two strings and nothing else — so they are structural rather than a version
#: accident, and the two sources agree byte for byte.
#:
#: Under `Enter to select` + `↑/↓ to navigate` both classified `menu` and both were
#: driven by digit: a reply naming a `/sandbox` row CHANGED THE SANDBOX/PERMISSION
#: MODE, and `/permissions`' row 1 ends in `…` so `label_matches`' truncation rule
#: accepted ANY reply beginning `Add a new rule`. Neither dialog was anything the
#: operator was shown.
#:
#: ⚠ THE ORDER OF THE FIELDS IS THE FACT THAT SEPARATES THEM, and it is not a
#: heuristic: in BOTH ask renderers `Enter to select` is field 1, and in both of
#: these it is field 2 or 3, behind a nav or switch hint. `/sandbox` also terminates
#: on `Esc to close` rather than `Esc to cancel`.
#: ⚠ The option labels are invented (this repo is public); the CHROME is measured,
#: because the chrome is what the code reads.
SANDBOX_PICKER_FOOTER = ("←/→ to switch · ↑/↓ to navigate · Enter to select · "
                         "Esc to close")
SANDBOX_PICKER_CAPTURE = (
    " Sandbox mode\n"
    "\n"
    "   1. Sandbox BashTool, with auto-allow\n"
    "   2. Sandbox BashTool, with regular permissions\n"
    " ❯ 3. No Sandbox ✔\n"
    "\n"
    " " + SANDBOX_PICKER_FOOTER + "\n"
)
PERMISSIONS_RULE_LIST_FOOTER = ("↑/↓ to navigate · Enter to select · ←/→ to switch · "
                                "Esc to cancel")
#: ⚠ THE INDENTATION AND THE `↓` SCROLL MARKER ARE MEASURED, the rule names are not.
#: The live render indents the cursor row by two and pads the number column
#: (`  ❯ 1.   Add a new rule…` / `    2.   …`), and it marks the scrolled edge row with
#: `↓` standing exactly where `MENU_OPTION_RE` expects whitespace or the cursor — the
#: same fourth route to a broken block this file records for a scrolled ask. Reproduced
#: because the pane this fixture stands for was scrolled. The rule names themselves are
#: INVENTED: the real ones are the operator's own permission entries and real paths,
#: which this public repo must not carry.
PERMISSIONS_RULE_LIST_CAPTURE = (
    " Permissions · Allow\n"
    "\n"
    "  ❯ 1.   Add a new rule…\n"
    "    2.   Bash(git status)\n"
    "    3.   Bash(git diff:*)\n"
    "    4.   Read(~/notes/**)\n"
    "    5.   Bash(cat:*)\n"
    "    6.   Bash(chmod:*)\n"
    "    7.   Bash(curl:*)\n"
    "    8.   Bash(bash:*)\n"
    "    9.   Bash(dig:*)\n"
    "  ↓ 10.  Bash(find:*)\n"
    "\n"
    "  " + PERMISSIONS_RULE_LIST_FOOTER + "\n"
)

#: Its end screen, reached after every question has an answer.
MULTI_QUESTION_REVIEW_CAPTURE = (
    "● Review your answers\n"
    "\n"
    "  Store: Flatfile ✔\n"
    "  Region: 1. Frankfurt ✔\n"
    "\n"
    "  ❯ 1. Submit answers\n"
    "    2. Cancel\n"
)

#: 🔴 THE MULTISELECT RENDER, CORRECTED — THIS FIXTURE USED TO BE A RENDER THAT
#: DOES NOT EXIST. It read `❯ [ ] 1. Ledger` with the box BEFORE the number, and it
#: carried NO footer. Both were wrong, and together they made every multiSelect test
#: in this file a test of an invented pane:
#:
#:   * the real row is `❯ 1. [ ] Ledger` — number, then box. Taken from the
#:     renderer itself (Claude Code 2.1.232 `.claude-wrapped`, the `Ltl` component
#:     reached from `rB.multiSelect ? h6(...)`), which composes a flex row with
#:     `gap:1` as `<cursor-or-space> {`${index}.`.padEnd(w)} {["[", tick|" ", "]"]}
#:     {label}`. A live capture of pane %170 agreed. ⚠ NO LIVE MULTISELECT WAS
#:     CAPTURED FOR THIS ROUND — a read-only sweep of all 58 panes on this host
#:     (viewport + 2000 scrollback lines) found zero; the renderer is the source,
#:     and it is the stronger one because it produces every capture.
#:   * a real multiSelect ask DOES render the ask footer, so omitting it let this
#:     fixture pass through a code path no live pane takes.
#:
#: ⚠ WHY THE `Submit` ROW CARRIES NO `✔`: the renderer draws it as
#: `<cursor-or-space><marginLeft:3><bold>{submitButtonText}</bold>`, and
#: `submitButtonText` is `"Submit"` on the last question and `"Next"` before it —
#: no tick anywhere. So `MENU_SUBMIT_TAB` (`✔ Submit`) cannot see it, which is
#: exactly why the checkbox ROW pattern has to be the thing that refuses this pane.
MULTI_SELECT_MENU_CAPTURE = (
    "● Which stores should be enabled?\n"
    "\n"
    "  ☐ Stores\n"
    "\n"
    "  ❯ 1. [ ] Ledger\n"
    "    2. [ ] Flatfile\n"
    "    3. [✔] Archive\n"
    "\n"
    "      Submit\n"
    "\n"
    + MENU_FOOTER_SINGLE + "\n"
)

#: A numbered, cursored menu with NO free-text row. A reply naming one of its rows
#: EXACTLY is delivered by that row's digit — the row is right there on screen; a
#: reply naming nothing has no route and is refused BY THE DELIVERY, after the
#: match has been tried. Both directions are pinned below.
NO_FREE_TEXT_MENU_CAPTURE = (
    "  ☐ Proceed?\n"
    "\n"
    "  ❯ 1. Ledger\n"
    "    2. Flatfile\n"
    "\n"
    + MENU_FOOTER_SINGLE + "\n"
)

#: Numbered rows with TWO cursor glyphs — whether a live menu is up at all cannot
#: be decided, so this is the shape `PANE_UNKNOWN_MENU` now names.
TWO_CURSOR_MENU_CAPTURE = (
    "  ☐ Proceed?\n"
    "\n"
    "  ❯ 1. Ledger\n"
    "  ❯ 2. Flatfile\n"
    "\n"
    + MENU_FOOTER_SINGLE + "\n"
)

#: 🔴 THE FOUR LIVE SHAPES THAT USED TO BE REFUSED, taken from a sweep of all 53
#: tmux panes on this host: every one carries a question marker, a `✔ Submit`
#: strip or a checkbox WITHOUT a single numbered option row and WITHOUT a cursor
#: row — quoted text in a transcript, not a live modal. They are ordinary panes a
#: reply belongs in, and the shipped classifier refused all four.
QUOTED_MENU_SHAPES = (
    # A transcript quoting an earlier single-question ask's marker.
    ("● I asked you about the store earlier:\n"
     "  ☐ Store\n"
     "\n"
     "● Carrying on with the flatfile plan.\n", "a quoted ☐ question marker"),
    # A transcript quoting a multi-question TAB STRIP. Measured at visible lines 4
    # and 8 of 31 on two live panes, with zero numbered rows and zero cursor rows.
    ("● Earlier I put up a two-part ask:\n"
     "\n"
     "  ←  ☐ Store  ☐ Region  ✔ Submit  →\n"
     "\n"
     "● Both are answered; wiring it now.\n", "a quoted ✔ Submit tab strip"),
    # Prose naming the review screen.
    ("● The chain ends on a `Review your answers` screen.\n"
     "\n"
     "devrc git:(main) $ \n", "the words Review your answers in prose"),
    # A markdown task list — two `[ ]` on adjacent lines, no cursor anywhere.
    ("● The remaining work:\n"
     "  - [ ] wire the store\n"
     "  - [x] read the pane\n"
     "\n"
     "devrc git:(main) $ \n", "a markdown task list"),
)


def load_agent():
    """Import the agent as a module so its pure functions can be tested directly.

    It has no `.py` extension (matching `session-manager` and the rest of
    `scripts/`), so it is loaded by path.
    """
    loader = importlib.machinery.SourceFileLoader("tmux_reply_agent_undertest", str(SCRIPT))
    spec = importlib.util.spec_from_file_location("tmux_reply_agent_undertest", str(SCRIPT),
                                                  loader=loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


AGENT = load_agent()


def _load_collector():
    """Import `scripts/session-manager` so its VALUES can be read.

    🔴 THE VALUE, NOT THE SOURCE TEXT. A seam guard that greps the collector's
    source for an exact literal fails on every legitimate edit to that literal
    and passes anything spelled the same way — see
    `test_the_server_id_format_matches_the_collectors` for the instance that
    made this necessary. Loading it costs nothing: the module has no import-time
    side effects (its own suite loads it exactly this way, autouse fixtures and
    all), and it is the same by-path loader `load_agent` uses because
    `session-manager` likewise has no `.py` extension.
    """
    loader = importlib.machinery.SourceFileLoader("session_manager_seamcheck", str(COLLECTOR))
    spec = importlib.util.spec_from_file_location("session_manager_seamcheck", str(COLLECTOR),
                                                  loader=loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# The stub server: hands out a fixed claim batch, records every request.
# --------------------------------------------------------------------------- #
class _Recorder(HTTPServer):
    def __init__(self, addr, handler):
        super().__init__(addr, handler)
        self.requests = []
        self.claim_batches = []      # popped one per claim; [] when exhausted
        self.claim_status = 200
        self.result_status = 200
        # The transcript DELTA STREAM rides the same poll, on the hook tier.
        # Separate status knobs from the claim's, because the whole point of the
        # seam tests below is that one surface failing must not disturb the other.
        self.stream_status = 200
        self.cursor_status = 200
        #: How many times the agent has polled (claim requests), and an optional
        #: callback fired with that count — see the hook in do_POST.
        self.polls = 0
        self.on_poll = None


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 — name fixed by BaseHTTPRequestHandler
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        self.server.requests.append({
            "path": self.path,
            "body": raw,
            "auth": self.headers.get("Authorization"),
        })
        if self.path.endswith("/claim"):
            # 🔴 A DETERMINISTIC HOOK FOR STAGGERING A FIXTURE, replacing a
            # wall-clock timer — and it is on the CLAIM, which is the agent's ONE
            # guaranteed request per poll. The obvious hook (the delta POST) does
            # not fire at all when the only thing to report is a SKIP: run_round
            # returns early with sent=0 and posts nothing, so a counter there
            # never advances and the staggered file is never written.
            #
            # Why not a timer: measured, under this box's normal load a
            # `threading.Timer(0.5, …)` fired before the agent's first poll in 3
            # of 12 runs. Both reasons were then present at poll 1 — the exact
            # configuration in which the mutant this fixture exists to kill
            # SURVIVES — and the test passed anyway, so a green run was
            # indistinguishable from one that proved nothing.
            self.server.polls += 1
            if self.server.on_poll:
                self.server.on_poll(self.server.polls)
            status = self.server.claim_status
            if status != 200:
                self._reply(status, b'{"error":"no"}')
                return
            batch = self.server.claim_batches.pop(0) if self.server.claim_batches else []
            self._reply(200, json.dumps({"writes": batch}).encode())
            return
        if self.path == "/api/transcripts/stream":
            if self.server.stream_status != 200:
                self._reply(self.server.stream_status, b'{"error":"no"}')
                return
            # 🔴 THE CURSOR IS THE SERVER'S ANSWER, so this stub must actually
            # answer one — a fake that returned only `{"ok":true}` would leave the
            # host with no cursor to adopt and the next poll would reseed for ever,
            # which is not the behaviour under test.
            frame = json.loads(raw.decode() or "{}")
            cursors = [
                {"sessionId": s["sessionId"],
                 "offset": s.get("offset", 0) + len(s.get("data", "").encode()),
                 "reason": "accepted"}
                for s in frame.get("sessions", [])
            ]
            self._reply(200, json.dumps(
                {"ok": True, "applied": len(cursors), "cursors": cursors}).encode())
            return
        self._reply(self.server.result_status, b'{"ok":true}')

    def do_GET(self):  # noqa: N802 — name fixed by BaseHTTPRequestHandler
        """The transcript stream's one READ: where the server's cursors are.

        🔴 IT EXISTS SO A MISSING do_GET CANNOT BE MISTAKEN FOR A DEFECT IN THE
        AGENT. Without it BaseHTTPRequestHandler answers 501, the stream backs
        off, and every streaming test below would be measuring the stub.
        """
        self.server.requests.append({
            "path": self.path, "body": b"", "auth": self.headers.get("Authorization"),
        })
        if self.server.cursor_status != 200:
            self._reply(self.server.cursor_status, b'{"error":"no"}')
            return
        self._reply(200, b'{"sessions":[]}')

    def _reply(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002 — signature fixed by the base class
        pass


@pytest.fixture
def server():
    srv = _Recorder(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def base_url(server) -> str:
    return f"http://{server.server_address[0]}:{server.server_address[1]}"


@pytest.fixture
def tmux_stub(tmp_path):
    """A stub `tmux` that appends its own argv, one JSON array per invocation.

    🔴 IT RECORDS ARGV, NOT A COMMAND LINE. The whole point of the shell test is
    that the text arrives as ONE argument; a stub that recorded `"$*"` would
    flatten exactly the distinction under test and report success either way.
    """
    log = tmp_path / "tmux-argv.jsonl"
    log.write_text("")
    server_id = tmp_path / "tmux-server-id"
    server_id.write_text("1234:5678\n")
    rc_file = tmp_path / "tmux-sendkeys-rc"
    rc_file.write_text("0")
    err_file = tmp_path / "tmux-sendkeys-err"
    err_file.write_text("")
    new_window_out = tmp_path / "tmux-new-window-out"
    new_window_out.write_text("%77\n")
    # 🔴 A STUB THAT CAN LIE ABOUT WHERE THE WINDOW LANDED. The agent reads back
    # the session and path tmux actually chose; without a stub that can report
    # something OTHER than the request, that read-back is unpinned — measured, as
    # two surviving mutants, because `=` and `isdir` each fire first in the
    # ordinary case. These files override the derived values when non-empty.
    landed_session = tmp_path / "tmux-landed-session"
    landed_session.write_text("")
    landed_path = tmp_path / "tmux-landed-path"
    landed_path.write_text("")
    # 🔴 A STUB THAT CAN SHOW THE AGENT A DIFFERENT PANE EACH TIME IT LOOKS. The
    # agent reads the pane BEFORE typing (is this an AskUserQuestion menu, where
    # typed characters are discarded?) and reads it AGAIN afterwards to check the
    # menu actually moved. A stub with ONE fixed capture cannot express either the
    # advance or its absence, so `delivered` would be unpinned and a mutant that
    # skipped the read-back entirely would survive — which is the prior-defect
    # shape `landed_session`/`landed_path` above already record.
    captures = tmp_path / "tmux-captures"
    capture_n = tmp_path / "tmux-capture-n"
    capture_rc = tmp_path / "tmux-capture-rc"
    capture_err = tmp_path / "tmux-capture-err"
    capture_n.write_text("0")
    capture_rc.write_text("0")
    capture_err.write_text("")
    # The default is an ORDINARY SHELL PROMPT, so every test that predates the
    # menu path exercises the unchanged text delivery rather than an empty screen.
    captures.write_text(DEFAULT_PROMPT_CAPTURE, encoding="utf-8")

    path = tmp_path / "bin" / "tmux"
    path.parent.mkdir(exist_ok=True)
    # Python rather than sh, because the records are separated by a sentinel LINE
    # and the counter has to advance on every call — `sed`/`awk` in the stub body
    # would put a second quoting layer between the fixture and the bytes the agent
    # reads, which is the distinction `write_exec`'s own comment is about.
    capture_helper = tmp_path / "bin" / "capture-pane-stub.py"
    capture_helper.write_text(
        "import sys\n"
        "SEP = '\\n@@NEXT-CAPTURE@@\\n'\n"
        "recs = open(sys.argv[1], encoding='utf-8').read().split(SEP)\n"
        "try:\n"
        "    n = int(open(sys.argv[2], encoding='utf-8').read().strip() or '0')\n"
        "except ValueError:\n"
        "    n = 0\n"
        "open(sys.argv[2], 'w', encoding='utf-8').write(str(n + 1))\n"
        # The LAST record repeats for ever: a test that wants "the menu never
        # moves" supplies one record, and one that wants an advance supplies two.
        "sys.stdout.write(recs[min(n, len(recs) - 1)])\n",
        encoding="utf-8")
    # POSIX-sh body, no shebang — write_exec owns that. Python does the JSON
    # quoting so an argument containing quotes, backslashes or newlines is
    # recorded faithfully rather than through a shell's idea of escaping.
    write_exec(path, (
        f'''if [ "$1" = "-V" ]; then echo 'tmux 3.4'; exit 0; fi\n'''
        f'''python3 -c 'import json,sys; open(sys.argv[1],"a").write(json.dumps(sys.argv[2:])+"\\n")' '''
        f'''{log} "$@"\n'''
        # ⚠ THIS BRANCH MATCHES **ANY** `display-message`, INCLUDING the
        # `#{pane_current_path}` RE-READ `open_window` now makes when tmux returns
        # an empty path. Unreachable today ONLY because this stub cannot express an
        # empty third field. If you extend the stub to cover that path,
        # DISCRIMINATE ON THE FORMAT ARGUMENT FIRST — otherwise the re-read is
        # handed a server id (`1234:5678`) as its directory and the run refuses
        # with the misleading "tmux fell back" message.
        # 🔴 BEFORE the display-message branch, and discriminating on the VERB.
        # `capture-pane` is a READ — it types nothing — and it is the one the
        # branch below would otherwise swallow by matching too early.
        f'''if [ "$1" = "capture-pane" ]; then\n'''
        f'''  crc="$(cat {capture_rc})"\n'''
        f'''  if [ "$crc" != "0" ]; then cat {capture_err} >&2; exit "$crc"; fi\n'''
        f'''  python3 {capture_helper} {captures} {capture_n}\n'''
        f'''  exit 0\n'''
        f'''fi\n'''
        f'''if [ "$1" = "display-message" ]; then cat {server_id}; exit 0; fi\n'''
        # 🔴 THE STUB NOW ECHOES WHAT THE AGENT READS BACK. The agent verifies the
        # session and working directory tmux ACTUALLY chose, so a stub printing a
        # bare pane id would fail every new-session test for the wrong reason.
        # It derives both from its OWN argv, which is also what makes a
        # deliberately mismatching stub expressible.
        f'''if [ "$1" = "new-window" ]; then\n'''
        f'''  python3 -c 'import sys;a=sys.argv[1:];\n'''
        f'''c=a[a.index("-c")+1] if "-c" in a else "";\n'''
        f'''c=open("{landed_path}").read().strip() or c;\n'''
        f'''t=a[a.index("-t")+1].strip("=:") if "-t" in a else "keep";\n'''
        f'''t=open("{landed_session}").read().strip() or t;\n'''
        f'''p=open("{new_window_out}").read().strip();\n'''
        f'''sys.stdout.write("" if p=="" else p+"\\t"+t+"\\t"+c+"\\n")' "$@"\n'''
        f'''  exit 0\n'''
        f'''fi\n'''
        f'''if [ "$1" = "send-keys" ]; then cat {err_file} >&2; exit "$(cat {rc_file})"; fi\n'''
        f'''exit 0\n'''
    ))

    class Stub:
        binary = path
        argv_log = log

        @staticmethod
        def calls():
            return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]

        @staticmethod
        def send_keys_calls():
            return [c for c in Stub.calls() if c and c[0] == "send-keys"]

        @staticmethod
        def set_server_id(value):
            server_id.write_text(value + "\n")

        @staticmethod
        def fail_send_keys(rc=1, stderr=""):
            rc_file.write_text(str(rc))
            err_file.write_text(stderr)

        @staticmethod
        def set_new_window_output(value):
            new_window_out.write_text(value)

        @staticmethod
        def lie_about_landing(session=None, path=None):
            """Make the stub report a landing that differs from the request."""
            if session is not None:
                landed_session.write_text(session)
            if path is not None:
                landed_path.write_text(path)

        @staticmethod
        def new_pane_id():
            return new_window_out.read_text().strip()

        @staticmethod
        def set_captures(*records):
            """What successive `capture-pane` calls will show, last one repeating."""
            captures.write_text("\n@@NEXT-CAPTURE@@\n".join(records), encoding="utf-8")
            capture_n.write_text("0")

        @staticmethod
        def fail_capture(rc=1, stderr="can't find pane: %12"):
            capture_rc.write_text(str(rc))
            capture_err.write_text(stderr)

        @staticmethod
        def capture_calls():
            return [c for c in Stub.calls() if c and c[0] == "capture-pane"]

        @staticmethod
        def reset():
            log.write_text("")

    return Stub


def write(**kw):
    """One claim-batch entry, with defaults that are pairwise distinct."""
    out = {
        "id": "w-abc123",
        "kind": "send-keys",
        "cwd": "",
        "tmuxSessionName": "",
        "host": "workbench",
        "pane": "%12",
        "text": "yes, go ahead",
        "submit": True,
        "expectTmuxServerId": "",
    }
    out.update(kw)
    return out


def real_dir(tmp_path, name="work"):
    """A directory that EXISTS, for a new-session fixture.

    🔴 `/tmp/some/dir` USED TO DO, AND THAT WAS THE HOLE. `new-window -c <missing
    path>` returns rc 0 and opens the window in the HOME DIRECTORY — measured on
    real tmux — so every stubbed fixture here was describing a delivery that, in
    production, would have run the command somewhere else entirely. The agent
    refuses a non-existent cwd now, so a fixture has to name a real one.
    """
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    return str(d)


def run_agent(server, tmux_stub, tmp_path, *, env_extra=None, conf_text=None,
              token="not-a-real-terminal-token-not-a-real-token", timeout=30,
              expect_requests=2):
    """Run the agent for ONE productive round and then let it stop.

    The loop is driven to completion by the stub server: the first claim returns
    the batch, the second returns an empty one, and TMUX_REPLY_MAX_ROUNDS is not
    a thing the agent has — so the process is stopped with SIGTERM once the
    expected traffic has been seen.
    """
    conf = tmp_path / "clawgate.env"
    conf.write_text(conf_text if conf_text is not None else "")
    env = dict(os.environ)
    # Start from a clean slate so an operator's real credentials in the ambient
    # environment can never leak into a test run and aim it at production.
    for k in ("CLAWGATE_API_URL", "CLAWGATE_TERMINAL_TOKEN", "CLAWGATE_HOOK_TOKEN", "ACTIVITY_HOST"):
        env.pop(k, None)
    env["CLAWGATE_CONF_FILE"] = str(conf)
    env["HOME"] = str(tmp_path)
    env["CLAWGATE_API_URL"] = base_url(server)
    if token:
        env["CLAWGATE_TERMINAL_TOKEN"] = token
    env["TMUX_REPLY_TMUX_BIN"] = str(tmux_stub.binary)
    env["TMUX_REPLY_POLL_SECONDS"] = "0.05"
    env["TMUX_REPLY_BACKOFF_SECONDS"] = "0.05"
    # The menu read-back waits for a TUI redraw. Against a stub there is nothing
    # to wait for, so the wait is shortened rather than removed: an attempt count
    # of 1 would make "the menu never moved" indistinguishable from "we only
    # looked once", which is the fact the failure path reports on.
    env["TMUX_REPLY_MENU_SETTLE_SECONDS"] = "0.01"
    env["TMUX_REPLY_MENU_VERIFY_ATTEMPTS"] = "3"
    env["ACTIVITY_HOST"] = "workbench"
    env.update(env_extra or {})

    proc = subprocess.Popen([sys.executable, str(SCRIPT)], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    # Wait until the stub has seen enough traffic, then stop the loop.
    deadline = threading.Event()
    for _ in range(int(timeout / 0.05)):
        if proc.poll() is not None:
            break
        if len(server.requests) >= expect_requests:
            break
        deadline.wait(0.05)
    proc.terminate()
    out, _ = proc.communicate(timeout=timeout)
    return proc.returncode, out


# --------------------------------------------------------------------------- #
# 1. The positive control.
# --------------------------------------------------------------------------- #
def test_a_claimed_write_is_delivered_and_reported(server, tmux_stub, tmp_path):
    """THE POSITIVE CONTROL for this whole file.

    Every "no send-keys happened" and "the text was not in X" assertion below is
    a claim about an instrument that must first be shown to observe SOMETHING.
    """
    server.claim_batches = [[write()]]
    rc, out = run_agent(server, tmux_stub, tmp_path)

    sends = tmux_stub.send_keys_calls()
    assert len(sends) == 2, f"expected the text send and the Enter send, got {sends}\n{out}"
    assert sends[0] == ["send-keys", "-t", "%12", "-l", "--", "yes, go ahead"], sends[0]
    assert sends[1] == ["send-keys", "-t", "%12", "Enter"], sends[1]

    results = [r for r in server.requests if r["path"].endswith("/result")]
    assert len(results) == 1, f"the agent did not report an outcome: {server.requests}"
    body = json.loads(results[0]["body"])
    assert body["state"] == "delivered", body
    assert body["agent"].startswith("workbench:"), body

    claims = [r for r in server.requests if r["path"].endswith("/claim")]
    assert claims, "the agent never claimed"
    assert json.loads(claims[0]["body"])["host"] == "workbench"
    assert claims[0]["auth"] == "Bearer not-a-real-terminal-token-not-a-real-token"


# --------------------------------------------------------------------------- #
# 2. The text never reaches a shell, and the flags are part of the command.
# --------------------------------------------------------------------------- #
def test_the_text_is_delivered_as_one_argv_element_with_no_shell(server, tmux_stub, tmp_path):
    """🔴 THE SINGLE MOST IMPORTANT TEST IN THIS FILE.

    The text is arbitrary operator input that arrived over the network. If
    anything between this process and execve interpolates it into a command
    string, `$(…)` and backticks are code that runs on THIS host with the
    operator's privileges, before tmux ever sees them.

    The fixture is deliberately hostile AND pairwise distinct in its parts, so a
    partial mangle (quotes eaten, `;` split, `$(` expanded) is visible as a
    difference rather than absorbed.
    """
    hostile = """-n $(id) `id` "q" 'p' ;echo x |tee /dev/null && true \\n $HOME"""
    server.claim_batches = [[write(text=hostile)]]
    run_agent(server, tmux_stub, tmp_path)

    sends = tmux_stub.send_keys_calls()
    assert sends, "nothing was sent; this test would prove nothing"
    assert sends[0][-1] == hostile, (
        "the text did not arrive byte-identical as ONE argument.\n"
        f"sent:     {sends[0][-1]!r}\nexpected: {hostile!r}\n"
        "Anything that changes here means a shell, a quoting layer or a split is "
        "standing between this agent and execve.")
    # And it really is one element, not several.
    assert len(sends[0]) == 6, f"the text was split across arguments: {sends[0]}"


def test_send_keys_is_literal_and_ends_option_parsing(server, tmux_stub, tmp_path):
    """🔴 `-l` AND `--` ARE THE COMMAND, NOT STYLE.

    Without `-l`, `send-keys` reads its argument as KEY NAMES: a reply containing
    "C-c" would interrupt whatever is running in the pane instead of typing three
    characters, and "Enter" would submit in the middle of a sentence. Without
    `--`, a reply beginning with `-` is parsed as a flag and either errors or
    changes what the command does.

    Asserting the flags' PRESENCE is not enough — their ORDER relative to the
    text is what makes them apply — so this pins the exact argv.
    """
    server.claim_batches = [[write(text="-l C-c Enter q")]]
    run_agent(server, tmux_stub, tmp_path)
    sends = tmux_stub.send_keys_calls()
    assert sends, "nothing was sent"
    assert sends[0] == ["send-keys", "-t", "%12", "-l", "--", "-l C-c Enter q"], sends[0]


def test_type_only_sends_no_enter(server, tmux_stub, tmp_path):
    """`submit: false` must type and stop.

    The operator chose that clawgate PRESSES ENTER by default, and the server
    defaults the field accordingly — but the field exists, and honouring it is
    the difference between a reply that sits visibly in a pane and one that runs.
    Without this test a mutant that ignores `submit` entirely (always Enter)
    survives every other case in this file, because they all set it true.
    """
    server.claim_batches = [[write(submit=False)]]
    run_agent(server, tmux_stub, tmp_path)
    sends = tmux_stub.send_keys_calls()
    assert len(sends) == 1, f"expected the text send alone, got {sends}"
    assert "Enter" not in sends[0]


# --------------------------------------------------------------------------- #
# 3. The write-time guard.
# --------------------------------------------------------------------------- #
def test_a_moved_tmux_server_is_refused_with_no_send_at_all(server, tmux_stub, tmp_path):
    """🔴 THE GUARD MUST RUN BEFORE THE WRITE, NOT AROUND IT.

    Within one tmux server a pane id `%N` is never reused, so a stale one simply
    fails to resolve. The dangerous case is a tmux server RESTART: ids are minted
    again from %0, so a `%12` the enqueuer saw an hour ago now names a completely
    different pane — and a submitted reply would execute there.

    The assertion is that NO send-keys happened, not merely that the outcome says
    `refused`: a guard that reports a refusal after the keys have gone is not a
    guard, and a status-only assertion cannot tell the two apart.
    """
    tmux_stub.set_server_id("9999:1111")
    server.claim_batches = [[write(expectTmuxServerId="1234:5678")]]
    run_agent(server, tmux_stub, tmp_path)

    assert tmux_stub.send_keys_calls() == [], (
        "the agent sent keys into a pane on a tmux server that is not the one the write "
        "was addressed to")
    results = [r for r in server.requests if r["path"].endswith("/result")]
    assert results, "the refusal was not reported"
    body = json.loads(results[0]["body"])
    assert body["state"] == "refused", body
    assert "identity moved" in body["detail"], body


def test_a_matching_tmux_server_is_delivered(server, tmux_stub, tmp_path):
    """The control for the refusal above: with the expectation SATISFIED the
    write goes through, so the refusal is the guard firing and not the agent
    declining everything that carries an expectation."""
    tmux_stub.set_server_id("1234:5678")
    server.claim_batches = [[write(expectTmuxServerId="1234:5678")]]
    run_agent(server, tmux_stub, tmp_path)
    assert len(tmux_stub.send_keys_calls()) == 2
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "delivered", body


def test_an_absent_expectation_is_not_checked(server, tmux_stub, tmp_path):
    """An empty expectation means NOT CHECKED, not "matches".

    The collector reports its `tmux_server_id` as UNMEASURED in real conditions,
    so refusing every write that carries no expectation would make the feature
    fail exactly when the read model is degraded — while protecting against
    nothing, since a caller that cannot measure the id cannot supply a right one.
    """
    tmux_stub.set_server_id("9999:1111")   # different from anything, and irrelevant
    server.claim_batches = [[write(expectTmuxServerId="")]]
    run_agent(server, tmux_stub, tmp_path)
    assert len(tmux_stub.send_keys_calls()) == 2


def test_the_guard_refuses_when_the_identity_cannot_be_measured():
    """An unmeasurable live identity is a REFUSAL, not a pass.

    Driven through the predicate directly so every branch is covered, including
    the pair the behavioural tests above cannot both reach in one run.
    """
    assert AGENT.should_refuse("", "1234:5678") == (False, "")
    assert AGENT.should_refuse("", "") == (False, "")
    refuse, why = AGENT.should_refuse("1234:5678", "")
    assert refuse and "could not be measured" in why
    refuse, why = AGENT.should_refuse("1234:5678", "9999:1")
    assert refuse and "identity moved" in why
    assert AGENT.should_refuse("1234:5678", "1234:5678") == (False, "")


# --------------------------------------------------------------------------- #
# 4. The text is never logged, and never leaves in a diagnostic.
# --------------------------------------------------------------------------- #
def test_the_reply_text_is_never_written_to_the_journal(server, tmux_stub, tmp_path):
    """🔴 A CREDENTIAL-HANDLING GUARD, PINNING A RELATIONSHIP.

    The operator chose a full audit log INCLUDING the reply text over a
    metadata-only one, and was told the cost: a reply may contain a secret. That
    log lives in the server's table behind the terminal token. This unit's output
    goes to the journal, which is a different and much wider readership.

    The failing case is not hypothetical — the equivalent guard on the SERVER
    side caught a real leak in its first draft, where an agent-supplied `detail`
    string was logged and tmux quotes its arguments in its own errors. So this
    drives a delivery that FAILS, with tmux's stderr echoing the text back, and
    asserts the marker appears nowhere in the agent's output.
    """
    tmux_stub.fail_send_keys(rc=1, stderr=f"can't find pane: sending {MARKER} failed")
    server.claim_batches = [[write(text=MARKER)]]
    rc, out = run_agent(server, tmux_stub, tmp_path)

    # The positive control: the agent must have LOGGED SOMETHING about this write,
    # or "the marker is absent" is a claim about an empty buffer.
    assert "tmux-reply-agent:" in out, f"the agent logged nothing at all:\n{out}"
    assert "w-abc123" in out, f"the agent's log does not name the write:\n{out}"
    assert "failed" in out, f"the agent did not log the outcome:\n{out}"
    # …and the control that the marker was really in play.
    assert tmux_stub.send_keys_calls(), "no send was attempted; the fixture is inert"
    assert tmux_stub.send_keys_calls()[0][-1] == MARKER

    assert MARKER not in out, (
        "the operator's literal reply text reached this unit's journal. It is "
        "credential-grade and the journal is not the audit log.\n" + out)


def test_the_reported_detail_is_redacted_of_the_text(server, tmux_stub, tmp_path):
    """The same hazard, one hop further out: `detail` is stored in the audit log
    AND logged, and tmux quotes its arguments in its own errors.

    The redaction is an EXACT substitution — the agent knows the precise string —
    rather than a secret-shaped pattern, so it cannot miss the way a heuristic
    misses and cannot claim to have scrubbed something it did not.
    """
    tmux_stub.fail_send_keys(rc=1, stderr=f"can't find pane: {MARKER}")
    server.claim_batches = [[write(text=MARKER)]]
    run_agent(server, tmux_stub, tmp_path)
    results = [r for r in server.requests if r["path"].endswith("/result")]
    assert results, "no outcome was reported"
    body = json.loads(results[0]["body"])
    assert body["state"] == "failed", body
    assert MARKER not in body["detail"], body
    assert "<text>" in body["detail"], body
    # The control: the rest of the diagnostic survives, so redaction is not just
    # "throw the detail away".
    assert "can't find pane" in body["detail"], body


def test_redact_is_exact_and_bounded():
    """The redaction helper, directly. A bounded detail matters because it lands
    in a retained column and in a log line."""
    assert AGENT.redact("before secret after", "secret") == "before <text> after"
    assert AGENT.redact("a\nb\tc", "") == "a b c"
    assert len(AGENT.redact("x" * 2000, "")) == 400
    # An empty text must not redact everything.
    assert AGENT.redact("nothing to hide", "") == "nothing to hide"


# --------------------------------------------------------------------------- #
# 5. At-most-once, and the states that are NOT errors.
# --------------------------------------------------------------------------- #
def test_a_failed_result_post_does_NOT_re_execute_the_write(server, tmux_stub, tmp_path):
    """🔴 AT MOST ONCE. A retried `send-keys` runs a command twice.

    The server's claim moved this row out of `pending` permanently, so if the
    outcome report fails the correct behaviour is to LOSE the record of it — the
    row is relabelled `abandoned`, whose meaning is "delivery UNKNOWN" — and
    never to re-run the send. This is the one place in the whole path where a
    duplicate execution could be introduced.
    """
    server.result_status = 500
    server.claim_batches = [[write()]]
    _rc, out = run_agent(server, tmux_stub, tmp_path)

    text_sends = [s for s in tmux_stub.send_keys_calls() if "-l" in s]
    assert len(text_sends) == 1, (
        f"the agent executed the text {len(text_sends)} times for ONE claimed write; it is "
        f"retrying a send after a failed report: {tmux_stub.send_keys_calls()}")
    results = [r for r in server.requests if r["path"].endswith("/result")]
    assert len(results) == 1, (
        f"the agent re-POSTed the outcome {len(results)} times. A retry of the REPORT is "
        "harmless in itself, but it is one line away from a retry of the SEND, and the "
        "server refuses a replayed completion anyway.")
    # And what it says about the loss is the truthful thing, not "did not run".
    assert "UNKNOWN" in out, out


def test_an_unarmed_server_is_backed_off_from_not_crashed_on(server, tmux_stub, tmp_path):
    """🔴 503 IS THE SHIPPED STATE, NOT A FAULT.

    The server's write routes are behind a fail-closed wrapper that answers 503
    until CLAWGATE_TERMINAL_TOKEN is provisioned on the pod. That is how this
    change is deployed, so the agent must treat it as a quiet, logged-once
    condition — never as an error that fails the unit or spams the journal.
    """
    server.claim_status = 503
    rc, out = run_agent(server, tmux_stub, tmp_path)
    assert tmux_stub.send_keys_calls() == [], "the agent sent keys off a 503"
    assert "NOT ARMED" in out, out
    # Logged ONCE per condition, not per tick: the poll here is 50ms, so a
    # per-tick line would appear many times over the run.
    assert out.count("NOT ARMED") == 1, (
        "a permanent condition was logged on every tick; at the real cadence that is "
        f"17,280 lines a day and buries every real event:\n{out}")


def test_no_credentials_exits_two_and_makes_no_request(server, tmux_stub, tmp_path):
    """A configuration error that cannot self-heal, so this one EXITS.

    It is also the state the change ships in on the host side, which is why the
    message says so rather than just "missing token".
    """
    rc, out = run_agent(server, tmux_stub, tmp_path, token=None)
    assert rc == 2, f"rc={rc}\n{out}"
    assert server.requests == [], f"the agent contacted the server with no token: {server.requests}"
    assert "not armed" in out


def test_the_environment_beats_the_credential_file(tmp_path):
    """🔴 A REGRESSION GUARD FOR A MEASURED INCIDENT, NOT A PREFERENCE.

    `clawgate-stop-hook.sh` sources the same credential file with `set -a`, which
    makes the FILE beat the environment. The measured consequence over there was
    a probe aimed at a harmless address silently POSTing to PRODUCTION. On THIS
    surface the equivalent mistake sends a reply into a live pane.
    """
    conf = tmp_path / "clawgate.env"
    conf.write_text("CLAWGATE_API_URL=http://from-the-file:1\n"
                    "export CLAWGATE_TERMINAL_TOKEN='from-the-file'\n")
    env = {"CLAWGATE_API_URL": "http://from-the-env:2"}
    assert AGENT.resolve("CLAWGATE_API_URL", str(conf), env=env) == "http://from-the-env:2"
    # …and the file is still read for keys the environment does not set, in both
    # spellings a sourceable file actually carries.
    assert AGENT.resolve("CLAWGATE_TERMINAL_TOKEN", str(conf), env=env) == "from-the-file"


def test_the_credential_reader_takes_the_last_assignment(tmp_path):
    """As sourcing would. A file that sets a key twice is ordinary."""
    conf = tmp_path / "clawgate.env"
    conf.write_text("CLAWGATE_TERMINAL_TOKEN=first\n  export CLAWGATE_TERMINAL_TOKEN=\"second\"\n")
    assert AGENT.read_conf_key("CLAWGATE_TERMINAL_TOKEN", str(conf)) == "second"


def test_the_token_is_never_in_argv(server, tmux_stub, tmp_path):
    """Everything on this box can read /proc/<pid>/cmdline, and a URL lands in
    access logs — so the credential travels in a header.

    Asserted through the stub tmux's recorded argv and through the request the
    stub server received, not by reading the source: a structural check would
    type-check past a token added to a URL somewhere else.
    """
    server.claim_batches = [[write()]]
    run_agent(server, tmux_stub, tmp_path, token=MARKER + "-token-longer-than-32-chars")
    for call in tmux_stub.calls():
        assert not any(MARKER in a for a in call), f"the token reached tmux's argv: {call}"
    for req in server.requests:
        assert MARKER not in req["path"], f"the token reached the URL: {req['path']}"
        assert req["auth"] and MARKER in req["auth"], "the token was not sent as a header"


# --------------------------------------------------------------------------- #
# 6. The seams: the host label and the tmux server-id spelling.
# --------------------------------------------------------------------------- #
def test_the_host_label_follows_the_same_rule_as_the_collector(tmp_path, monkeypatch):
    """🔴 ONE RULE, AND A DISAGREEMENT HERE IS SILENT.

    The server's queue is keyed on the host label the READ MODEL stores, which
    the collector computes from ACTIVITY_HOST (`hostname` is "nixos" on BOTH
    machines, so it cannot be the source of truth). An agent that computed a
    different label would poll for a host nobody enqueues to and deliver nothing
    at all, with no error anywhere.

    🔴 `HOST_LABEL_ADDRS=""` IS SET FOR EVERY CASE BELOW, AND IT IS NOT
    BOILERPLATE. Since #1601 the shared rule has a THIRD feeder — an address this
    machine holds — which is a cross-check on the two named here. Without pinning
    it, `env={"ACTIVITY_HOST": "WORKBENCH"}` would raise `HostLabelConflict` when
    this suite runs on the LAPTOP and pass on the workbench: a test whose verdict
    depends on which machine ran it. Set-but-empty says "holds none of them", so
    what is measured here is exactly the env-vs-file rule this test is named for.
    The third feeder gets its own suite, `test_host_label_identity.py`.
    """
    env_file = tmp_path / "collector-env"
    env_file.write_text('ACTIVITY_HOST="laptop"\n')
    monkeypatch.setenv(AGENT.HOST_LABEL.HOST_LABEL_ADDRS_ENV, "")
    assert AGENT.local_host_label(env={}, env_file=str(env_file)) == "laptop"
    assert AGENT.local_host_label(env={"ACTIVITY_HOST": "WORKBENCH"}, env_file=str(env_file)) == "workbench"
    # 🔴 AN UNRECOGNISED LABEL NO LONGER "FALLS BACK" — THERE IS NOWHERE TO FALL.
    # This assertion used to read `== "workbench"`, which is the #1601 defect
    # written down as an expectation: on the laptop, a typo'd ACTIVITY_HOST and
    # an absent env file made this agent claim the WORKBENCH's termwrite queue,
    # poll a host nobody enqueues to and deliver nothing, silently. The typo is
    # still ignored rather than passed through (it must never mint a third host);
    # what changed is that ignoring every signal now ends in a refusal.
    with pytest.raises(AGENT.HostLabelError):
        AGENT.local_host_label(env={"ACTIVITY_HOST": "nixos"},
                               env_file=str(tmp_path / "absent"))
    # And the vocabulary is the collector's own, read from its source rather than
    # restated here.
    collector_src = COLLECTOR.read_text()
    assert 'HOST_NAMES = ("workbench", "laptop")' in collector_src, (
        "the collector's host vocabulary changed; this agent's HOST_NAMES must follow it")
    assert AGENT.HOST_NAMES == ("workbench", "laptop")
    # 🔴 STRUCTURAL, NOT SPELLED. A substring check for `DEFAULT_LOCAL_HOST`
    # would fire on the COMMENT that explains why the constant was deleted —
    # the guard would then be un-satisfiable by anything except silence about
    # its own history. What must not come back is an ASSIGNMENT.
    import ast as _ast
    assigned = {t.id for node in _ast.walk(_ast.parse(collector_src))
                if isinstance(node, _ast.Assign)
                for t in node.targets if isinstance(t, _ast.Name)}
    assert "DEFAULT_LOCAL_HOST" not in assigned, (
        "#1601 deleted the workbench default from the collector; a constant by "
        "that name coming back is the defect coming back")
    assert not hasattr(AGENT, "DEFAULT_LOCAL_HOST")


def test_the_server_id_format_matches_the_collectors():
    """🔴 A SEAM GUARD: two spellings of one token would refuse EVERY write.

    The enqueuer takes its expectation from the read model, whose
    `tmux_server_id` the collector builds as `<pid>:<start_time>` from the
    server-level tmux formats `#{pid}` and `#{start_time}`. If this agent asked
    tmux for a differently-shaped value it would compare apples to oranges and
    refuse everything — which reads exactly like "the feature does not work",
    with no error naming the cause.

    The check reads the COLLECTOR'S OWN VALUE rather than restating the format,
    so a change there fails here instead of drifting silently.

    🔴 IT ASSERTS THE RELATIONSHIP, NOT THE WHOLE LITERAL — AND THAT IS A FIX,
    NOT A WEAKENING. This used to compare the collector's `WINDOW_FORMAT` SOURCE
    LINE against one exact string, which made it fail on any legitimate
    ADDITION to that format while proving nothing extra about the seam it
    guards. It went red the first time a field was appended (`#{window_activity}`,
    a per-window activity time that has nothing to do with the server id) even
    though both tokens this test actually cares about were still there, still
    server-level, and still joined the same way. A guard that fails on changes
    it does not care about, and would pass a reordering it does, is pinned to a
    SPELLING rather than to the invariant.

    What the seam needs is exactly two things: the collector's format still asks
    tmux for BOTH `#{pid}` and `#{start_time}`, and it still joins them with a
    single `:` in that order. Both are asserted against the collector's loaded
    VALUE and its joiner, so a rename, a removal or a re-ordering fails here,
    and an unrelated new field does not.
    """
    src = COLLECTOR.read_text()
    collector = _load_collector()
    fmt = collector.WINDOW_FORMAT
    for token in ("#{pid}", "#{start_time}"):
        assert token in fmt, (
            f"the collector's WINDOW_FORMAT no longer asks tmux for {token} "
            f"(it is {fmt!r}); re-derive TMUX_SERVER_ID_FORMAT from it")
    # ORDER, not mere presence: the id is built `<pid>:<start_time>`, so a
    # collector that swapped the two fields would store the halves the other way
    # round and every expectation this agent sends would be refused.
    assert fmt.index("#{pid}") < fmt.index("#{start_time}"), (
        f"the collector's WINDOW_FORMAT puts #{{start_time}} before #{{pid}} ({fmt!r}); "
        "the server id is built pid-first and the two would be transposed")
    assert 'return f"{pid}:{started}", None' in src, (
        "the collector no longer joins the server id as `<pid>:<start_time>`")
    assert AGENT.TMUX_SERVER_ID_FORMAT == "#{pid}:#{start_time}", (
        f"the agent asks tmux for {AGENT.TMUX_SERVER_ID_FORMAT!r}, which is not the shape the "
        "collector stores; every write carrying an expectation would be refused")
    # And the two spellings are DERIVED from one another rather than merely both
    # correct today: the agent's format is exactly the collector's two tokens
    # joined by the collector's separator.
    assert AGENT.TMUX_SERVER_ID_FORMAT == "#{pid}:#{start_time}" and all(
        t in fmt for t in AGENT.TMUX_SERVER_ID_FORMAT.split(":")), (
        "the agent's server-id format is built from tokens the collector's window "
        f"format does not carry: agent={AGENT.TMUX_SERVER_ID_FORMAT!r} collector={fmt!r}")


def test_an_unmeasurable_server_id_reads_as_empty(tmp_path, monkeypatch):
    """tmux renders an unknown format verbatim, so an old tmux that does not know
    `#{start_time}` yields a bare ':'. That must read as "cannot measure" — which
    the guard turns into a REFUSAL — and never as the literal identity ':'."""
    calls = []

    def fake_run(args):
        calls.append(args)
        return 0, ":\n", ""

    monkeypatch.setattr(AGENT, "run_tmux", fake_run)
    assert AGENT.tmux_server_id() == ""
    monkeypatch.setattr(AGENT, "run_tmux", lambda a: (0, "1234:5678\n", ""))
    assert AGENT.tmux_server_id() == "1234:5678"
    monkeypatch.setattr(AGENT, "run_tmux", lambda a: (1, "", "no server running"))
    assert AGENT.tmux_server_id() == ""


# --------------------------------------------------------------------------- #
# 7. The unit is ARMED (it shipped disabled), and carries what the child needs.
# --------------------------------------------------------------------------- #
def home_nix() -> str:
    return HOME_NIX.read_text()


def test_the_agent_unit_IS_ARMED():
    """🔴 THE CENTRAL CLAIM OF THE ARMING CHANGE, ASSERTED AGAINST THE CONFIGURATION.

    This guard used to pin the flag `false` and is now pinned `true`, and the
    SYMMETRY is the point rather than a weakening: what it has always enforced is
    that the armed state is a deliberate, reviewed edit in BOTH directions. While
    it read `false`, arming meant editing this test; now that it reads `true`,
    DISARMING means editing this test. An accidental revert to `false` would stop
    every queued reply from ever executing while the server kept accepting them
    and the UI kept looking healthy — a silent reopening of the loop rank 32
    closed, which is exactly the shape a config guard exists to catch.

    🔴 BUT THE FLAG IS NOT A LIVE KILL SWITCH, AND THIS GUARD MUST NOT BE READ AS
    ONE. Flipping it `false` and shipping does NOT stop a RUNNING agent: the unit
    definition is emitted unconditionally, so the flag only removes `[Install]`,
    and `sd-switch` reads that as a CHANGED unit and plans Stop/Start. What this
    pins is the DECLARED state at next login. Stopping it now is
    `systemctl --user stop tmux-reply-agent`, on both hosts.

    What this agent delivers is arbitrary command execution as the operator on
    this host. Building it and ARMING it were deliberately separated so the write
    path could be merged, deployed and audited before it could execute anything;
    that separation did its job across seven audit rounds and is now history.

    The flag is read through the comment-stripping reader, not a substring
    search: this file's blocks quote directives verbatim in prose, so a raw
    `in src` check cannot tell a declaration from a comment describing one — and
    that exact failure once left a guard green over a deleted unit.
    """
    src = nix_units.strip_nix_comments(home_nix())
    assert "enableTmuxReplyAgent = true;" in src, (
        "the terminal-write agent's master switch is not true. Disarming is an operator "
        "act and a reviewed edit, not a drive-by revert: with this false the unit is "
        "wanted by nothing, every reply typed in the web UI queues for ever, and nothing "
        "in the server or the UI says so.")
    assert "enableTmuxReplyAgent = false;" not in src


def test_the_agent_unit_is_declared_independently_of_the_flag():
    """The SERVICE definition is emitted unconditionally; only `Install.WantedBy`
    is gated. That is what let an operator start it by hand and watch it before
    it was wired into the target, and it is what makes disarming a matter of the
    unit no longer being WANTED rather than no longer existing."""
    src = home_nix()
    assert nix_units.declares("systemd.user.services.tmux-reply-agent", src)
    unit = nix_units.strip_nix_comments(
        nix_units.unit_source("systemd.user.services.tmux-reply-agent", src))
    # 🔴 A RESIDENT SERVICE, NOT A TIMER. A ~5s poll driven by a timer would fork
    # a process, an interpreter and a connection 17,280 times a day.
    assert nix_units.directive("Type", unit) == '"simple"', unit
    assert not nix_units.declares("systemd.user.timers.tmux-reply-agent", src)


def _wanted_by_expr(nix_src: str) -> tuple:
    """-> (flag_value, condition_source, target_list_source) lifted FROM home.nix.

    🔴 THE PREVIOUS GUARD PINNED WORDS AND THE ONE MUTATION THAT MATTERS WALKED
    THROUGH IT. It asserted that the unit's text CONTAINS "enableTmuxReplyAgent"
    and "default.target"; the mutant

        WantedBy = lib.optionals (!enableTmuxReplyAgent) [ "default.target" ];

    keeps both substrings and SURVIVES — an inversion that would start the agent
    on both hosts on the next switch, i.e. the single thing this whole change
    claims cannot happen. `claude/RULES.md`: "a guard can be SPELLED rather than
    STRUCTURAL — ask whether it can pass while the hazard exists in a different
    shape."

    So the CONDITION is extracted and pinned as a whole normalised string rather
    than searched for. `!enableTmuxReplyAgent` is not `enableTmuxReplyAgent`, and
    neither is `(enableTmuxReplyAgent || true)`.
    """
    flag = re.search(r"^  enableTmuxReplyAgent = (true|false);", nix_src, re.M)
    assert flag, "the master switch declaration was not found in home.nix"
    unit = nix_units.strip_nix_comments(
        nix_units.unit_source("systemd.user.services.tmux-reply-agent", nix_src))
    m = re.search(r"WantedBy\s*=\s*lib\.optionals\s+(.+?)\s*(\[[^\]]*\])\s*;", unit, re.S)
    assert m, f"no `WantedBy = lib.optionals <cond> [ ... ];` in the unit:\n{unit}"
    cond = " ".join(m.group(1).split())
    targets = " ".join(m.group(2).split())
    return flag.group(1), cond, targets


def test_default_target_wants_the_agent_and_ONLY_via_the_bare_flag():
    """🔴 THE CENTRAL CLAIM, PINNED AS AN EXPRESSION RATHER THAN AS SUBSTRINGS.

    Two facts together determine that `default.target` wants this unit, and BOTH
    are asserted: the condition is the BARE flag (not a negation, not a wider
    expression), and the flag is `true`. A mutant that changes either is a
    different string here.

    🔴 THE CONDITION ASSERTION IS THE HALF THAT DID NOT CHANGE MEANING WHEN THE
    FLAG FLIPPED, AND IT IS THE MORE IMPORTANT HALF. Pinning the bare flag is
    what keeps the flag the ONLY thing deciding whether the agent runs: a
    disjunction like `(enableTmuxReplyAgent || isNixOS)` would want the unit on
    every host regardless of the switch, so setting the flag `false` would no
    longer disarm anything. That mutant mentions the flag's name and passes a
    word-pinning guard, which is why the whole condition is normalised and
    compared as one string.

    🔴 DETERMINISTIC, AND THERE IS DELIBERATELY NO `nix eval` BESIDE IT. A first
    draft added one as "confirmation". It PASSED on the dev host and turned the
    nix check sandbox RED — not by failing, but by SKIPPING: that sandbox has no
    recursive nix, and this repo's runner treats an unpinned skip as an error,
    because "a skip is a test that did not run". Pinning it would have bought a
    permanent EXPECTED_SKIPS entry for a test that can never run in the tier that
    gates, i.e. reading as coverage while providing none.

    It was removed rather than pinned because it confirmed nothing this test does
    not already determine: given `cond == "enableTmuxReplyAgent"` and the flag
    `false`, `lib.optionals` yields `[]` by definition. And a change to a
    DIFFERENT combinator (`lib.optional`, singular, with different semantics)
    fails the regex above rather than slipping past it.
    """
    flag, cond, targets = _wanted_by_expr(home_nix())
    assert cond == "enableTmuxReplyAgent", (
        f"the WantedBy condition is `{cond}`, not the bare flag. Anything else — a negation, a "
        "disjunction, a different flag — can want this unit while still mentioning the flag's "
        "name, which is exactly how the previous guard was walked past.")
    assert flag == "true", (
        f"enableTmuxReplyAgent is `{flag}`. The agent is ARMED: with this false the unit is "
        "wanted by nothing, and every reply typed in the web UI queues and is never executed "
        "— silently, because the server still accepts the write. Disarming is a deliberate "
        "operator act and edits this assertion with it.")
    assert targets == '[ "default.target" ]', targets


def test_the_wanted_by_guard_can_SEE_an_inverted_flag():
    """The positive control, and the whole reason the test above is shaped this
    way. The previous, word-pinning guard passed over both of these mutants."""
    src = home_nix()
    inverted = src.replace(
        'WantedBy = lib.optionals enableTmuxReplyAgent [ "default.target" ];',
        'WantedBy = lib.optionals (!enableTmuxReplyAgent) [ "default.target" ];')
    assert inverted != src, "the WantedBy line this control mutates was not found verbatim"
    _flag, cond, _t = _wanted_by_expr(inverted)
    assert cond != "enableTmuxReplyAgent", (
        "the inverted-flag mutant reads identically to the shipped condition; this guard cannot "
        "see the one mutation that would start the agent on both hosts")

    # 🔴 THE FLAG CONTROL MUTATES IN THE DIRECTION THAT IS NOW THE HAZARD.
    # It used to flip false -> true (arming by accident); with the agent armed
    # the accident to catch is the reverse — a revert to `false`, which disarms
    # the host half while the server keeps accepting writes.
    #
    # ⚠ TWICE-CORRECTED, and the SECOND correction is the one worth reading,
    # because a fix for the first one CREATED the hazard the first one denied.
    #
    # Round 1 of this change claimed the pre-flip direction would have gone
    # "green and blind". An audit refuted it: `assert flipped != src` already
    # existed, `enableTmuxReplyAgent = false` occurred ZERO times in the armed
    # file, so the stale direction failed loudly. True — at that commit.
    #
    # 🔴 THEN THE FIX FOR THAT CLAIM MADE IT FALSE. The same round wrote a
    # DISARM EXAMPLE into `nix/home.nix`'s comments:
    #
    #     #     enableTmuxReplyAgent = false;   # then merge + ship.sh, THEN:
    #
    # A comment — but `str.replace` cannot tell a declaration from a comment
    # describing one, and that line's `  #     ` prefix ENDS IN SPACES, so the
    # two-space-prefixed literal matches INSIDE it. MEASURED: reverting this
    # control to its pre-flip direction on that tree gives `1 passed` — the
    # replace hits the comment, `flipped != src` is satisfied, `_wanted_by_expr`
    # reads the untouched real declaration, and the control passes about a file
    # whose flag was never mutated. Exactly "green and blind", arrived at by
    # fixing the claim that it could not happen.
    #
    # 🔴 SO THE MUTATION IS ANCHORED, NOT SUBSTRING-MATCHED. `^  <flag> = …;$`
    # under re.M is the same shape `_wanted_by_expr` already uses, and it is why
    # THAT function was never fooled by the comment. `subn`'s count is asserted
    # to be exactly 1, so a SECOND DECLARATION fails loudly with `substituted 2`
    # rather than silently taking an extra substitution.
    #
    # ⚠ Precisely, because an earlier draft of this sentence overstated it: a
    # comment at COLUMN 0 (`#  enableTmuxReplyAgent = true;`) is INVISIBLE to the
    # anchor, not a failure — MEASURED `1 passed`. That is the wanted behaviour
    # (a comment must not be mutated), but it is not detection, and claiming
    # detection would be the same overclaim this whole control exists to record.
    #
    # The documentation is NOT the thing to delete here: the disarm example is
    # the only written rollback for a surface that executes commands. Fix the
    # test, not the sentence.
    flipped, n_flipped = re.subn(r"^  enableTmuxReplyAgent = true;$",
                                 "  enableTmuxReplyAgent = false;", src, flags=re.M)
    assert n_flipped == 1, (
        f"expected exactly ONE anchored flag declaration to mutate, substituted {n_flipped}. "
        "Zero means the declaration moved or changed spelling; more than one means there is a "
        "second declaration and this control no longer names which one it tested.")
    assert flipped != src
    assert _wanted_by_expr(flipped)[0] == "false"


def test_the_unit_PATH_carries_tmux_and_python():
    """🔴 EVERY BINARY THE CHILD NEEDS, because under the user manager there is no
    login-shell PATH to fall back on — a lesson drift-check.service already paid
    for, reporting COULD NOT MEASURE forever from a unit that looked correct.

    The agent is stdlib Python plus tmux. It deliberately does NOT carry curl,
    openssh or gawk: it uses urllib and never leaves this host. That is the
    opposite trim from tmux-snapshot-push's list, and copying that list wholesale
    would be carrying binaries to justify later.
    """
    unit = nix_units.strip_nix_comments(
        nix_units.unit_source("systemd.user.services.tmux-reply-agent", home_nix()))
    # 🔴 READ THE makeBinPath LIST, NOT THE WHOLE UNIT. The previous version
    # asserted `"pkgs.python3" in unit`, which is satisfied by the ExecStart line
    # (`${pkgs.python3}/bin/python3 …`) — so deleting python3 from the PATH list
    # left it green while the CHILD lost its interpreter directory. A substring
    # test over a block that mentions a package for another reason is not a test
    # of the list; the list has to be extracted first.
    m = re.search(r"PATH=\$\{lib\.makeBinPath \[([^\]]*)\]\}", unit)
    assert m, f"no PATH=...makeBinPath[...] in the unit:\n{unit}"
    entries = m.group(1).split()
    assert "pkgs.tmux" in entries, f"without tmux every delivery fails; PATH list = {entries}"
    assert "pkgs.python3" in entries, f"the agent is a Python program; PATH list = {entries}"
    assert "pkgs.coreutils" in entries, entries
    # Deliberately absent: the agent uses urllib and never leaves this host.
    for copied in ("pkgs.openssh", "pkgs.gawk", "pkgs.curl"):
        assert copied not in entries, (
            f"{copied} is in the PATH list; the agent never uses it — carried, not needed")


def test_the_unit_gives_tmux_its_SOCKET_directory():
    """This host's tmux socket is at $XDG_RUNTIME_DIR/tmux-UID/, not tmux's
    compiled-in /tmp/tmux-UID/. Without TMUX_TMPDIR the agent cannot connect and
    every delivery fails."""
    unit = nix_units.strip_nix_comments(
        nix_units.unit_source("systemd.user.services.tmux-reply-agent", home_nix()))
    assert nix_units.directive("TMUX_TMPDIR", unit) or "TMUX_TMPDIR=%t" in unit, unit


def test_the_unit_does_not_wire_the_DND_defeating_failure_toast():
    """🔴 notify-failure@ toasts are wired to DEFEAT do-not-disturb, and that
    bypass is justified by a MEASURED rate of about one firing in nine days.

    This unit restarts on failure and polls every few seconds, so any sustained
    condition — the surface not armed, which since arming means the pod's secret
    was REMOVED — would fire a DND-bypassing toast on every restart and burn down
    the one alert channel that has to keep its meaning.
    """
    unit = nix_units.strip_nix_comments(
        nix_units.unit_source("systemd.user.services.tmux-reply-agent", home_nix()))
    assert "OnFailure" not in unit, unit
    assert "notify-failure" not in unit, unit


def test_the_unit_runs_the_script_this_suite_tests():
    """A unit pointing at a different path would make every test above a claim
    about a file nothing runs."""
    unit = nix_units.strip_nix_comments(
        nix_units.unit_source("systemd.user.services.tmux-reply-agent", home_nix()))
    assert "scripts/tmux-reply-agent" in unit, unit
    assert SCRIPT.exists() and os.access(SCRIPT, os.X_OK)


# --------------------------------------------------------------------------- #
# 8. The second kind: new-session.
# --------------------------------------------------------------------------- #
def test_a_new_session_opens_a_window_at_the_cwd_and_types_into_THAT_pane(server, tmux_stub, tmp_path):
    """🔴 THE NEW PANE IS ADDRESSED BY ID, NEVER "the active pane".

    `new-window` is followed by a `send-keys`, and between the two the operator
    may have switched windows. A relative target would then type the caller's
    command into whatever they moved to — the wrong-pane failure this whole
    surface is careful about, arriving through the one path that creates its own
    target. So the agent asks tmux to PRINT the new pane's id (`-P -F
    '#{pane_id}'`) and sends to that id.
    """
    server.claim_batches = [[write(kind="new-session", pane="", cwd=real_dir(tmp_path),
                                   text="claude 'do the thing'")]]
    run_agent(server, tmux_stub, tmp_path)

    calls = tmux_stub.calls()
    new_windows = [c for c in calls if c and c[0] == "new-window"]
    assert len(new_windows) == 1, f"expected one new-window, got {calls}"
    assert new_windows[0] == ["new-window", "-P", "-F", AGENT.NEW_WINDOW_FORMAT,
                              "-c", real_dir(tmp_path)], new_windows[0]

    sends = tmux_stub.send_keys_calls()
    assert len(sends) == 2, f"expected the text send and the Enter send, got {sends}"
    # The stub's `new-window` prints nothing, so the agent must have failed
    # rather than guessed a pane — see the companion test below. Here the stub is
    # configured to print one.
    assert sends[0][:4] == ["send-keys", "-t", tmux_stub.new_pane_id(), "-l"], sends[0]
    assert sends[0][-1] == "claude 'do the thing'", sends[0]
    assert sends[1] == ["send-keys", "-t", tmux_stub.new_pane_id(), "Enter"], sends[1]

    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "delivered", body


def test_a_new_session_that_cannot_report_a_pane_FAILS_rather_than_guessing(server, tmux_stub, tmp_path):
    """If tmux does not hand back a pane id there is no safe target.

    Guessing — "the active pane", "the last window" — is exactly the class of
    fuzzy target the pane-id rule exists to forbid, and here it would run a
    command in a pane the operator is looking at. Failing is the correct outcome
    and it must produce NO send-keys at all.
    """
    tmux_stub.set_new_window_output("")
    server.claim_batches = [[write(kind="new-session", pane="", cwd=real_dir(tmp_path), text="echo hi")]]
    run_agent(server, tmux_stub, tmp_path)
    assert tmux_stub.send_keys_calls() == [], (
        "the agent sent keys without a pane id from tmux; that lands in whatever pane happens "
        "to be active")
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "failed", body


def test_a_new_session_with_no_text_just_opens_the_window(server, tmux_stub, tmp_path):
    """A bare window is a complete delivery — the caller asked for one, and
    typing nothing into it is the right amount of typing."""
    server.claim_batches = [[write(kind="new-session", pane="", cwd=real_dir(tmp_path), text="")]]
    run_agent(server, tmux_stub, tmp_path)
    assert [c for c in tmux_stub.calls() if c and c[0] == "new-window"]
    assert tmux_stub.send_keys_calls() == []
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "delivered", body


def test_an_unknown_kind_is_refused_with_no_tmux_call_at_all(server, tmux_stub, tmp_path):
    """🔴 REFUSE, DO NOT GUESS.

    A kind this agent does not recognise means the server is asking for something
    a newer build knows how to do. Falling back to the default kind would run a
    command nobody on this host reviewed; refusing is visible in the audit log and
    costs one write.
    """
    server.claim_batches = [[write(kind="reboot-the-host")]]
    run_agent(server, tmux_stub, tmp_path)
    assert [c for c in tmux_stub.calls() if c and c[0] in ("send-keys", "new-window")] == []
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "refused", body
    assert "unknown write kind" in body["detail"], body


def test_an_absent_kind_still_means_send_keys(server, tmux_stub, tmp_path):
    """Backwards compatibility is the default, not a migration: a write with no
    `kind` at all is what every caller predating the field sent, and it means
    send-keys."""
    w = write()
    w.pop("kind", None)
    server.claim_batches = [[w]]
    run_agent(server, tmux_stub, tmp_path)
    assert len(tmux_stub.send_keys_calls()) == 2


# --------------------------------------------------------------------------- #
# 9. The trust boundary: the host refuses what the server should never have sent.
# --------------------------------------------------------------------------- #
#
# 🔴 THIS IS NOT DUPLICATED VALIDATION, IT IS A BOUNDARY. "One rule, one place"
# governs two copies of a rule on the same side of a trust boundary. Here the
# server is a pod reachable from an unauthenticated LAN NodePort and THIS process
# is the thing that runs commands as the operator — the two sides fail
# independently, and the side that executes is the one that must refuse.
#
# Every case below is something the server also rejects. The point is that the
# host does not depend on that.

@pytest.mark.parametrize("mutation,expect_in", [
    # A name-shaped target does not FAIL in tmux — it resolves fuzzily and runs
    # the command in some other pane.
    ({"pane": "scratch:1"}, "pane id"),
    ({"pane": "@41"}, "pane id"),
    ({"pane": ""}, "pane id"),
    ({"pane": "-t"}, "pane id"),
    # A newline makes ONE write into TWO submissions.
    # 🔴 The message now DIAGNOSES the codepoint (offset + name + category)
    # instead of saying "control character", because the gate is an allowlist and
    # the refused set is far wider than the controls. The assertion follows the
    # gate rather than the old wording.
    ({"text": "yes\nrm -rf /"}, "a newline at offset"),
    ({"text": "yes\x1b[A"}, "non-printable character U+001B"),
    ({"text": ""}, "no text"),
    ({"text": "x" * 4000}, "size limit"),
])
def test_the_host_refuses_a_write_the_server_should_never_have_sent(
        server, tmux_stub, tmp_path, mutation, expect_in):
    server.claim_batches = [[write(**mutation)]]
    run_agent(server, tmux_stub, tmp_path)
    assert tmux_stub.send_keys_calls() == [], (
        f"the agent EXECUTED a write with {mutation} — the host is trusting the server's "
        "validation, and the host is where commands run")
    results = [r for r in server.requests if r["path"].endswith("/result")]
    assert results, "the refusal was not reported"
    body = json.loads(results[0]["body"])
    assert body["state"] == "refused", body
    assert expect_in in body["detail"], body


@pytest.mark.parametrize("cwd", ["workspace/devrc", "/home/../root", "/tmp/a\nb", ""])
def test_the_host_refuses_a_new_session_with_an_unsafe_cwd(server, tmux_stub, tmp_path, cwd):
    """A relative cwd resolves against THIS process's directory, which differs per
    host and after every restart — the silent-wrong-place failure in its purest
    form, on the one code path that creates its own target."""
    server.claim_batches = [[write(kind="new-session", pane="", cwd=cwd, text="echo hi")]]
    run_agent(server, tmux_stub, tmp_path)
    assert [c for c in tmux_stub.calls() if c and c[0] in ("new-window", "send-keys")] == [], (
        f"the agent opened a window at {cwd!r}")
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "refused", body


def test_an_oversized_claim_batch_is_refused_WHOLE(server, tmux_stub, tmp_path):
    """🔴 REFUSE THE ROUND, DO NOT TRUNCATE IT.

    The server caps a claim at 4. A batch of a thousand — from a server that is
    wrong, compromised, or simply newer than this agent — is a thousand commands,
    and nothing downstream would stop them. Executing the first four and dropping
    the rest would be worse than refusing: it runs commands the server believes
    are in flight while silently abandoning others, a partial delivery nobody can
    reason about. Refusing leaves every row to be relabelled `abandoned`
    (delivery UNKNOWN), which is the honest record.
    """
    server.claim_batches = [[write(id=f"w-{i}") for i in range(40)]]
    _rc, out = run_agent(server, tmux_stub, tmp_path)
    assert tmux_stub.send_keys_calls() == [], (
        f"the agent executed part of an oversized batch: {tmux_stub.send_keys_calls()}")
    assert [r for r in server.requests if r["path"].endswith("/result")] == [], (
        "the agent reported outcomes for a batch it refused as a whole")
    assert "over the" in out and "writes in one claim" in out, out


def test_a_batch_at_the_cap_is_still_executed(server, tmux_stub, tmp_path):
    """The control for the refusal above: exactly at the cap goes through, so the
    refusal is the bound and not an unconditional no."""
    server.claim_batches = [[write(id=f"w-{i}") for i in range(4)]]
    run_agent(server, tmux_stub, tmp_path)
    text_sends = [s for s in tmux_stub.send_keys_calls() if "-l" in s]
    assert len(text_sends) == 4, f"expected 4 deliveries, got {text_sends}"


def test_the_validator_is_exercised_directly():
    """Every branch, including the accepting ones — without which the table above
    is satisfied by a validator that refuses everything."""
    ok = {"id": "w-abc123"}
    assert AGENT.validate({**ok, "kind": "send-keys", "pane": "%12", "text": "hi"}) == ""
    assert AGENT.validate({**ok, "pane": "%12", "text": "hi"}) == ""  # absent kind
    assert AGENT.validate({**ok, "kind": "new-session", "cwd": "/tmp", "text": "hi"}) == ""
    assert AGENT.validate({**ok, "kind": "new-session", "cwd": "/tmp", "text": ""}) == ""  # bare window
    assert "unknown write kind" in AGENT.validate({**ok, "kind": "exec", "pane": "%1", "text": "hi"})
    # Non-string fields from a malformed payload must not raise.
    assert AGENT.validate({**ok, "kind": "send-keys", "pane": 12, "text": "hi"}) != ""
    assert AGENT.validate({**ok, "kind": "new-session", "cwd": None, "text": "hi"}) != ""
    # An id this agent will not put in a URL is refused before anything else.
    assert "write id" in AGENT.validate({"kind": "send-keys", "pane": "%1", "text": "hi"})


# --------------------------------------------------------------------------- #
# 10. The text gate is an ALLOWLIST, and it is the SAME one session-write uses.
# --------------------------------------------------------------------------- #
#
# 🔴 THE DENYLIST THIS REPLACED WAS WRONG IN THE MEASURED WAY. `ord(ch) < 0x20 or
# ord(ch) == 0x7F` refuses NUL, \n, \r, ESC and DEL — and passes every C1 control
# (U+0080–U+009F), U+0085 NEL, U+2028 and U+2029. `session-write` had already
# fixed exactly this class, with a docstring recording that U+000F (Ctrl-O) got
# through its own earlier denylist and executed a pane's buffer with no Enter
# sent. Two sites, one rule, wrong at the one reachable from the network.

@pytest.mark.parametrize("ch,why", [
    ("\x0f", "Ctrl-O — the character session-write's docstring records as executing a pane's buffer"),
    ("\x85", "U+0085 NEL — a line break to some terminals, and >= 0x20 so a denylist misses it"),
    ("\x9b", "U+009B CSI — a C1 control that starts an escape sequence"),
    (" ", "U+2028 LINE SEPARATOR"),
    (" ", "U+2029 PARAGRAPH SEPARATOR"),
    ("​", "U+200B ZERO WIDTH SPACE — invisible, so the audit row and the pane disagree"),
    ("", "U+E000 private use — renders identical to its neighbour"),
    ("\n", "a newline: one write becoming TWO submissions"),
    ("\x00", "NUL"),
    ("\x1b", "ESC"),
])
def test_the_text_gate_refuses_what_a_denylist_would_pass(ch, why):
    reason = AGENT.validate({"id": "w-abc123", "kind": "send-keys", "pane": "%1",
                             "text": "echo hi" + ch})
    assert reason, f"the allowlist ACCEPTED {ch!r} — {why}"


def test_the_text_gate_still_accepts_ordinary_text():
    """The positive control. Without it every case above is satisfied by a gate
    that refuses everything, which would make the feature inert rather than safe.
    """
    # ⚠ A TAB IS DELIBERATELY ABSENT from this list — the shared policy permits
    # one, and THIS surface refuses it anyway because shell completion rewrites
    # the buffer after the audit row is written. See
    # test_the_host_refuses_a_tab_even_though_the_shared_policy_allows_one.
    for text in ("yes, go ahead", "café — naïve ünïcode ✓",
                 "run `make test` && echo $HOME; then stop", "  spaced  "):
        assert AGENT.validate({"id": "w-abc123", "kind": "send-keys",
                               "pane": "%1", "text": text}) == "", text


def test_a_trailing_tmux_separator_is_refused():
    """tmux EATS a trailing `;` off an argv token, so the pane would receive
    something other than what the audit log records — a payload silently
    corrupted somewhere nobody looks."""
    assert AGENT.validate({"id": "w-abc123", "kind": "send-keys", "pane": "%1",
                           "text": "echo hi;"}) != ""
    # …but a `;` in the MIDDLE is ordinary shell and must pass.
    assert AGENT.validate({"id": "w-abc123", "kind": "send-keys", "pane": "%1",
                           "text": "echo hi; echo there"}) == ""


def test_the_agent_and_session_write_share_ONE_predicate():
    """🔴 ONE RULE, ONE PLACE — asserted as a RELATIONSHIP, not as two green suites.

    Both programs deliver literal text to a tmux pane. Before this they each had
    their own answer to "what may be delivered", and the network-facing one was
    the weaker: a denylist that passed every C1 control. The rule now lives in
    `scripts/lib/tmux_text_policy.py`.

    🔴 THE ASSERTION IS ON `__code__.co_filename`, NOT ON OBJECT IDENTITY. Both
    programs are SCRIPTS loaded by path, so each gets its own module instance of
    the policy and the two function objects are legitimately different — an `is`
    check fails while the rule is perfectly shared, which is a guard that cries
    wolf until someone deletes it. What actually matters is the SOURCE both
    predicates were compiled from, and that is what this reads.
    """
    sw = _load_session_write()
    shared = str((REPO_ROOT / "scripts" / "lib" / "tmux_text_policy.py").resolve())
    for name, fn in (("session-write", sw.TEXT_IS_ALLOWED),
                     ("tmux-reply-agent", AGENT.TEXT_POLICY.TEXT_IS_ALLOWED)):
        got = str(pathlib.Path(fn.__code__.co_filename).resolve())
        assert got == shared, (
            f"{name}'s TEXT_IS_ALLOWED was compiled from {got}, not the shared policy at "
            f"{shared} — it has its own copy again, and a second copy of this rule has "
            "already been wrong once")
    assert sw.TEXT_EXTRA_ALLOWED == AGENT.TEXT_POLICY.TEXT_EXTRA_ALLOWED == ("\t",)
    # The separator the two could have disagreed about is pinned at
    # session-write's import against session-resolve's own constant.
    assert sw.TMUX_ARGV_SEPARATOR == AGENT.TEXT_POLICY.TMUX_ARGV_SEPARATOR


def test_neither_script_defines_a_SECOND_text_predicate():
    """The other half: no third copy, and no resurrection of the denylist.

    AST, not grep — this file's own prose names `has_control` repeatedly, and a
    raw-text scan would fire on the comment explaining why it is gone. Only
    function DEFINITIONS are examined.
    """
    for script in ("session-write", "tmux-reply-agent"):
        tree = ast.parse((REPO_ROOT / "scripts" / script).read_text())
        defined = {n.name for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for banned in ("TEXT_IS_ALLOWED", "has_control"):
            assert banned not in defined, (
                f"{script} defines its own {banned}(); the text policy has ONE home "
                "(scripts/lib/tmux_text_policy.py) and a second copy of it has already "
                "shipped wrong once")


def test_the_shared_predicate_agrees_with_itself_across_the_whole_BMP():
    """A behavioural sweep beside the structural check above.

    The structural check says both callers import one file; this says the file's
    answer is the one the callers' own gates actually produce, over a range no
    hand-written fixture covers. It is what catches a consumer that imports the
    predicate and then second-guesses it.
    """
    policy = AGENT.TEXT_POLICY
    disagreements = []
    for cp in range(0x20, 0x2100):
        ch = chr(cp)
        allowed = policy.TEXT_IS_ALLOWED(ch)
        refused_by_agent = AGENT.validate(
            {"id": "w-abc123", "kind": "send-keys", "pane": "%1", "text": "a" + ch + "b"}) != ""
        if allowed == refused_by_agent:
            disagreements.append(f"U+{cp:04X}")
    assert not disagreements, (
        "the agent's gate disagrees with the shared allowlist at: "
        + ", ".join(disagreements[:20]))
    # The positive control: the sweep must have seen BOTH answers, or it is a
    # loop that proves nothing.
    assert policy.TEXT_IS_ALLOWED("A") and not policy.TEXT_IS_ALLOWED("\u200b")


def test_the_agent_REFUSES_TO_START_without_the_policy_module():
    """🔴 NO FALLBACK. A missing policy module must stop the program, not leave
    it with a weaker check — a silent fallback is how the denylist would come
    back, and it would come back at the network-facing site."""
    with pytest.raises(FileNotFoundError):
        AGENT._load_tmux_text_policy("/nonexistent/tmux_text_policy.py")


def _load_session_write():
    """Import session-write by path.

    🔴 REGISTERED IN sys.modules BEFORE exec_module. It defines dataclasses, and
    `dataclasses` resolves a string annotation through `sys.modules[cls.__module__]`
    — which is None for a module that is being executed but not registered, and
    the failure is an AttributeError inside the stdlib rather than anything that
    names the real cause.
    """
    name = "session_write_undertest"
    if name in sys.modules:
        return sys.modules[name]
    path = REPO_ROOT / "scripts" / "session-write"
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        loader.exec_module(mod)
    except Exception:
        del sys.modules[name]
        raise
    return mod


# --------------------------------------------------------------------------- #
# 11. The host's OWN at-most-once, the id shape, and the new-session submit.
# --------------------------------------------------------------------------- #
def test_the_host_refuses_a_write_id_it_has_already_executed(server, tmux_stub, tmp_path):
    """🔴 THE HOST'S OWN at-most-once, and it is not redundant with the server's.

    The server's guarantee is a property of its claim predicate. This agent is
    the thing that RUNS the command — so a server that is wrong, rolled back,
    restored from a backup, or simply newer would re-execute a claimed row, and
    the operator's decision that clawgate presses Enter makes that a second
    command execution.
    """
    server.claim_batches = [[write()], [write()]]   # the SAME id, twice
    run_agent(server, tmux_stub, tmp_path, expect_requests=4)
    text_sends = [s for s in tmux_stub.send_keys_calls() if "-l" in s]
    assert len(text_sends) == 1, (
        f"the agent executed the same write id {len(text_sends)} times: {tmux_stub.send_keys_calls()}")
    states = [json.loads(r["body"])["state"]
              for r in server.requests if r["path"].endswith("/result")]
    assert states[0] == "delivered", states
    assert "refused" in states[1:], (
        f"the duplicate was not reported as refused ({states}); a silently dropped duplicate is "
        "indistinguishable in the audit log from one that never arrived")


def test_the_dedupe_memory_is_bounded():
    """An unbounded set in a process that runs for weeks is a slow leak."""
    AGENT._SEEN.clear()
    for i in range(AGENT.SEEN_LIMIT + 50):
        assert not AGENT.already_executed(f"id{i}")
    assert len(AGENT._SEEN) <= AGENT.SEEN_LIMIT
    # Oldest-first eviction: the earliest ids are the ones forgotten.
    assert "id0" not in AGENT._SEEN
    assert f"id{AGENT.SEEN_LIMIT + 49}" in AGENT._SEEN
    AGENT._SEEN.clear()


@pytest.mark.parametrize("wid", ["", "../other", "a/b", "x" * 65, "a b", "a\nb", None, 7])
def test_an_unusable_write_id_is_never_put_in_a_url(server, tmux_stub, tmp_path, wid):
    """🔴 THE ID IS INTERPOLATED INTO A URL THIS AGENT CONSTRUCTS. A server-supplied
    id it cannot vouch for could steer the request somewhere else, and it would
    otherwise be unbounded text in a log line too."""
    server.claim_batches = [[write(id=wid)]]
    run_agent(server, tmux_stub, tmp_path)
    assert tmux_stub.send_keys_calls() == [], "a write with an unusable id was executed"
    for req in server.requests:
        assert "/result" not in req["path"], f"a request was aimed at {req['path']!r}"


def test_a_new_session_ALWAYS_submits_whatever_the_row_says(server, tmux_stub, tmp_path):
    """🔴 BOTH DIRECTIONS, because neither was pinned before.

    A new-session exists to produce a window that is DOING something; typing a
    command into a fresh shell and not running it leaves the operator a window to
    go and finish by hand. So `submit` on the row describes the send-keys case
    and is not consulted here — and that has to be asserted against a row saying
    `false`, or a mutant that starts honouring the field survives.
    """
    for submit in (True, False):
        server.requests.clear()
        tmux_stub.reset()
        AGENT._SEEN.clear()
        server.claim_batches = [[write(id=f"w-ns{int(submit)}", kind="new-session", pane="",
                                       cwd=real_dir(tmp_path), text="echo hi", submit=submit)]]
        run_agent(server, tmux_stub, tmp_path)
        sends = tmux_stub.send_keys_calls()
        assert len(sends) == 2, (
            f"submit={submit}: expected the text send AND the Enter send, got {sends}")
        assert sends[1][-1] == "Enter", sends[1]
    assert AGENT.NEW_SESSION_ALWAYS_SUBMITS is True


def test_a_server_supplied_pane_cannot_forge_a_log_line(server, tmux_stub, tmp_path):
    """Every field in a claim is server-controlled. A pane that failed the id
    check must not be echoed verbatim into the journal — a newline in it would
    forge a second, well-formed-looking log line."""
    server.claim_batches = [[write(pane="%1\ntmux-reply-agent: delivered everything")]]
    _rc, out = run_agent(server, tmux_stub, tmp_path)
    assert "delivered everything" not in out, out
    assert tmux_stub.send_keys_calls() == []


# --------------------------------------------------------------------------- #
# 12. The launched window goes where the CALLER said, not where tmux felt like.
# --------------------------------------------------------------------------- #
def test_a_named_tmux_session_is_passed_to_tmux_as_a_target(server, tmux_stub, tmp_path):
    """🔴 WITHOUT `-t` THE TARGET IS DECIDED BY tmux, NOT BY THE CALLER.

    `new-window` with no target joins whatever session this server considers
    CURRENT — which depends on what the operator last looked at — so a launched
    window lands somewhere nobody chose. That is the same wrong-place class the
    pane rule exists for, one level up: a whole WINDOW rather than a keystroke.

    The trailing colon is part of the contract: `-t "name:"` means "this session,
    next free index", and a session that does not exist makes tmux FAIL rather
    than silently fall back to the current one.
    """
    server.claim_batches = [[write(kind="new-session", pane="", cwd=real_dir(tmp_path),
                                   tmuxSessionName="scratch2", text="echo hi")]]
    run_agent(server, tmux_stub, tmp_path)
    new_windows = [c for c in tmux_stub.calls() if c and c[0] == "new-window"]
    assert len(new_windows) == 1, tmux_stub.calls()
    # 🔴 `=` FORCES AN EXACT SESSION MATCH. Without it tmux PREFIX-MATCHES:
    # `-t scratch2:` with `scratch2` absent and `scratch20` live returns rc 0 and
    # opens the window in `scratch20` — measured on 3.7c against the operator's
    # own server, which holds exactly that pair.
    assert new_windows[0] == ["new-window", "-P", "-F", AGENT.NEW_WINDOW_FORMAT,
                              "-c", real_dir(tmp_path), "-t", "=scratch2:"], new_windows[0]


def test_an_absent_tmux_session_leaves_the_target_to_tmux(server, tmux_stub, tmp_path):
    """The control, and the back-compatible half: absent means "wherever this
    server considers current", which is what happened before the field existed
    and the only honest answer when nobody expressed a preference. A `-t` with an
    empty value would be a target of its own."""
    server.claim_batches = [[write(kind="new-session", pane="", cwd=real_dir(tmp_path), text="echo hi")]]
    run_agent(server, tmux_stub, tmp_path)
    new_windows = [c for c in tmux_stub.calls() if c and c[0] == "new-window"]
    assert len(new_windows) == 1
    assert "-t" not in new_windows[0], new_windows[0]


@pytest.mark.parametrize("name", [
    "scratch:2",            # a window index inside another session
    "scratch:2.0",          # ...and a pane inside it
    "main.0",               # a pane address
    "-t",                   # option-shaped
    "has space",
    "weirdname",      # a C1 control the old denylist would have passed
    "a" * 65,
])
def test_the_host_refuses_a_compound_tmux_target(server, tmux_stub, tmp_path, name):
    """🔴 RE-VALIDATED ON THE HOST, NOT TRUSTED. The server checks this too; this
    process is the one that hands the value to tmux, and the two sides of the
    boundary fail independently."""
    server.claim_batches = [[write(kind="new-session", pane="", cwd=real_dir(tmp_path),
                                   tmuxSessionName=name, text="echo hi")]]
    run_agent(server, tmux_stub, tmp_path)
    assert [c for c in tmux_stub.calls() if c and c[0] in ("new-window", "send-keys")] == [], (
        f"the agent opened a window targeting {name!r}")
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "refused", body


def test_the_host_refuses_a_tab_even_though_the_shared_policy_allows_one():
    """🔴 A DELIBERATE DIVERGENCE FROM THE SHARED POLICY, IN THE SAFE DIRECTION.

    `send-keys -l` delivers a tab literally and a bash/zsh pane runs COMPLETION on
    it: `rm -rf ~/pro` + TAB becomes `rm -rf ~/projects/`, and the Enter this path
    presses submits THAT — while clawgate's audit row still says `rm -rf ~/pro`.
    That is the identical property the trailing-`;` refusal exists for.

    `session-write` keeps the tab because it is invoked by the operator at their
    own keyboard, where the buffer is visible before anything runs. Here the row
    IS the compensating control, so a character that makes it lie costs more than
    the typography is worth.

    Both halves are asserted: the shared policy still permits it (so this is a
    divergence, not a policy change that would surprise session-write), and this
    surface refuses it anyway.
    """
    assert AGENT.TEXT_POLICY.TEXT_IS_ALLOWED("\t"), (
        "the shared policy no longer allows a tab — this test is now asserting a divergence "
        "that does not exist, and session-write's own guards would have moved too")
    assert AGENT.validate({"id": "w-abc123", "kind": "send-keys", "pane": "%1",
                           "text": "rm -rf ~/pro\t"}) != ""
    # The control: the same text without the tab is ordinary and passes.
    assert AGENT.validate({"id": "w-abc123", "kind": "send-keys", "pane": "%1",
                           "text": "rm -rf ~/projects/"}) == ""


# --------------------------------------------------------------------------- #
# 13. REAL tmux. The stub cannot express either of these.
# --------------------------------------------------------------------------- #
#
# 🔴 THE STUB ALWAYS EXITS 0, so it models neither of the two resolution rules
# that shipped as defects: tmux PREFIX-MATCHES a session name, and `-c <missing
# path>` falls back to the home directory. Both were measured live, both reported
# `delivered`, and both were invisible to every stubbed test in this file. These
# drive a REAL tmux on a private `-L` socket — never the operator's server — and
# `tmux` is in REQUIRED_TOOLS and flake.nix's gateTools so they cannot silently
# skip in the tier that gates.

@pytest.fixture
def real_tmux(tmp_path):
    """A private tmux server with known sessions, torn down afterwards.

    🔴 `-L <unique>` AND A PRIVATE TMUX_TMPDIR. This must never touch the
    operator's live server: the thing under test CREATES WINDOWS.
    """
    sock = "rank29-" + os.path.basename(str(tmp_path))
    sockdir = tempfile.mkdtemp(prefix="tmuxr29.")
    env = dict(os.environ, TMUX_TMPDIR=sockdir)

    def tmux(*args, check=True):
        p = subprocess.run(["tmux", "-L", sock, *args], capture_output=True,
                           text=True, env=env, timeout=30)
        if check and p.returncode != 0:
            raise AssertionError(f"tmux {args} failed: {p.stderr}")
        return p

    # The operator's real server holds `scratch`, `scratch2` … `scratch20` and
    # `datapacket-talos`, `datapacket-talos-2`. This reproduces the SHAPE that
    # makes a prefix match land somewhere else: a longer name present, the
    # shorter one absent.
    tmux("new-session", "-d", "-s", "scratch20")
    tmux("new-session", "-d", "-s", "keep")
    try:
        yield {"sock": sock, "env": env, "tmux": tmux}
    finally:
        subprocess.run(["tmux", "-L", sock, "kill-server"], capture_output=True,
                       env=env, timeout=30)
        shutil.rmtree(sockdir, ignore_errors=True)


def _agent_with_real_tmux(monkeypatch, real_tmux):
    """Point the agent's tmux seam at the private server."""
    sock, base_env = real_tmux["sock"], real_tmux["env"]

    def run(args, env=None):
        # `env_override` models the real seam: open_window hands tmux a corrected
        # PATH so a launched pane can find `claude` (task 524). Keep the private
        # socket pinned whichever environment is used, or the call escapes to the
        # operator's server — which this fixture exists to prevent.
        e = dict(env if env is not None else base_env)
        e["TMUX_TMPDIR"] = base_env["TMUX_TMPDIR"]
        p = subprocess.run(["tmux", "-L", sock, *args], capture_output=True,
                           text=True, env=e, timeout=30)
        return p.returncode, p.stdout, p.stderr

    monkeypatch.setattr(AGENT, "run_tmux", run)


def test_a_MISSING_session_is_refused_not_prefix_matched(monkeypatch, real_tmux):
    """🔴 THE FIRST SHIPPED DEFECT, against real tmux.

    tmux prefix-matches a session name. `-t scratch2:` with `scratch2` absent and
    `scratch20` live returns rc 0 and opens the window in `scratch20` — measured
    on 3.7c against the operator's own server, where `scratch2` … `scratch20`
    both exist. End to end the agent typed the command, pressed Enter and
    reported `delivered`.

    `scratch2` is the literal this file's own fixtures use.
    """
    _agent_with_real_tmux(monkeypatch, real_tmux)
    before = real_tmux["tmux"]("list-windows", "-a", "-F", "#{session_name}").stdout

    pane, err = AGENT.open_window(str(pathlib.Path.home()), "scratch2")
    assert err, ("open_window SUCCEEDED for a session that does not exist. tmux prefix-matched "
                 "it to `scratch20` and the next step presses Enter.")
    assert pane == ""
    after = real_tmux["tmux"]("list-windows", "-a", "-F", "#{session_name}").stdout
    assert after == before, f"a window was created anyway:\nbefore={before!r}\nafter={after!r}"


def test_the_EXACT_session_still_works(monkeypatch, real_tmux):
    """The control for the refusal above: the real name opens a real window, so
    the refusal is the `=` prefix and not a gate that refuses everything."""
    _agent_with_real_tmux(monkeypatch, real_tmux)
    pane, err = AGENT.open_window(str(pathlib.Path.home()), "scratch20")
    assert not err, err
    assert pane.startswith("%")
    landed = real_tmux["tmux"](
        "display-message", "-p", "-t", pane, "#{session_name}").stdout.strip()
    assert landed == "scratch20", landed


def test_a_cwd_that_does_not_exist_is_REFUSED_not_opened_in_HOME(monkeypatch, real_tmux, tmp_path):
    """🔴 THE SECOND SHIPPED DEFECT, against real tmux.

    `new-window -c <absolute path that does not exist>` returns rc 0 and opens
    the window in the home directory — measured. End to end the agent reported
    `state='delivered' detail='opened %1'` while the command ran in $HOME, so a
    build or a recursive delete would have run there.
    """
    _agent_with_real_tmux(monkeypatch, real_tmux)
    missing = str(tmp_path / "definitely" / "not" / "here")
    pane, err = AGENT.open_window(missing, "scratch20")
    assert err, ("open_window SUCCEEDED for a directory that does not exist; tmux fell back to "
                 "the home directory and the command would have run there")
    assert pane == ""

    # The control: a directory that DOES exist opens there, and the read-back
    # agrees — so the refusal is the fallback and not an unconditional no.
    real = tmp_path / "real"
    real.mkdir()
    pane, err = AGENT.open_window(str(real), "scratch20")
    assert not err, err
    landed = real_tmux["tmux"](
        "display-message", "-p", "-t", pane, "#{pane_current_path}").stdout.strip()
    assert AGENT.same_directory(landed, str(real)), f"landed={landed!r} want={real}"


def test_a_trailing_separator_in_the_cwd_is_refused(tmp_path):
    """tmux eats a trailing separator off the `-c` word exactly as it does off a
    send-keys token, so the path becomes a DIFFERENT path — and if that does not
    exist, the window opens in the home directory. The text path already refused
    this character; the cwd path did not."""
    real = tmp_path / "build"
    real.mkdir()
    ok = {"id": "w-abc123", "kind": "new-session", "pane": "", "text": "make"}
    assert AGENT.validate({**ok, "cwd": str(real)}) == ""
    for bad in (str(real) + ";", str(real) + " ", " " + str(real)):
        assert AGENT.validate({**ok, "cwd": bad}) != "", bad


def test_a_nonexistent_cwd_is_refused_before_tmux_is_reached(tmp_path):
    """The early, legible refusal. open_window re-checks where tmux ACTUALLY
    landed; this makes the common case a clear message rather than a fallback."""
    ok = {"id": "w-abc123", "kind": "new-session", "pane": "", "text": "make"}
    assert "does not exist" in AGENT.validate({**ok, "cwd": str(tmp_path / "nope")})
    assert AGENT.validate({**ok, "cwd": str(tmp_path)}) == ""


# --------------------------------------------------------------------------- #
# 14. The guards that had no coverage.
# --------------------------------------------------------------------------- #
def test_the_shared_scan_sees_a_bad_character_at_OFFSET_ZERO():
    """🟡 THE EXACT TRAP THE POLICY'S OWN DOCSTRING RECORDS, in the one caller.

    `enumerate(text)` → `enumerate(text[1:], 1)` SURVIVED all 85 tests: every
    fixture in this file puts the bad character LATER (`"echo hi" + ch`,
    `"a" + ch + "b"`), so a scan blind at offset 0 refuses the same character at
    the end and DELIVERS it at the start. session-write's own offset-zero test
    cannot cover this — that file open-codes its own scan, and `first_disallowed`
    has exactly one caller: this agent, the network-facing one.
    """
    policy = AGENT.TEXT_POLICY
    for ch in ("\x0f", "\n", "\x1b", "\x00", "​", "\x85"):
        got = policy.first_disallowed(ch + "echo hi")
        assert got is not None, f"{ch!r} at offset 0 was not seen by the shared scan"
        assert got[0] == 0, f"{ch!r} reported at offset {got[0]}, want 0"
        # …and through the agent's own gate, which is what actually ships.
        assert AGENT.validate({"id": "w-abc123", "kind": "send-keys", "pane": "%1",
                               "text": ch + "echo hi"}) != "", ch
    # The control: an allowed character at offset 0 is still allowed, so this is
    # not a scan that refuses everything at the start.
    assert policy.first_disallowed("echo hi") is None


def test_the_response_read_is_BOUNDED(monkeypatch):
    """🟡 `resp.read(MAX)` → `resp.read()` survived all 85 tests. A bare read
    takes whatever the peer sends into a process on the operator's workstation."""
    captured = {}

    class FakeResp:
        def read(self, *args):
            captured["args"] = args
            return b'{"writes":[]}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(AGENT.urllib.request, "urlopen", lambda *a, **k: FakeResp())
    AGENT.post_json("http://127.0.0.1:1/x", "tok", {})
    assert captured["args"], "the response was read with NO limit argument"
    assert captured["args"][0] == AGENT.MAX_RESPONSE_BYTES


def test_a_write_that_makes_the_validator_RAISE_is_still_reported(server, tmux_stub, tmp_path):
    """🟡 A non-string `kind` raised out of the whole round: no result was POSTed
    for that row OR ANY LATER ROW in the batch, so they landed `abandoned` —
    "delivery UNKNOWN" — when the truthful answer is `refused`, nothing ran.

    An audit log that says UNKNOWN where it could say NO is exactly the failure
    its design is trying to avoid.
    """
    AGENT._SEEN.clear()
    server.claim_batches = [[write(id="w-bad", kind=123), write(id="w-later")]]
    run_agent(server, tmux_stub, tmp_path, expect_requests=4)
    results = {json.loads(r["body"])["state"]
               for r in server.requests if r["path"].endswith("/result")}
    posted = [r["path"] for r in server.requests if r["path"].endswith("/result")]
    assert any("w-bad" in p for p in posted), (
        f"the raising write was never reported, so it becomes `abandoned` — delivery UNKNOWN — "
        f"when nothing ran. Reported: {posted}")
    assert any("w-later" in p for p in posted), (
        f"a LATER write in the same batch was never reported either: {posted}")
    assert "refused" in results, results


@pytest.mark.parametrize("field,value", [
    ("kind", 123), ("kind", None), ("expectTmuxServerId", []),
    ("expectTmuxServerId", 7), ("pane", 12), ("cwd", None), ("text", 5), ("id", 9),
])
def test_the_validator_never_raises_on_a_non_string_field(field, value):
    """The sentence this test's predecessor made was wider than the test: it
    claimed "non-string fields must not raise" and exercised only `pane` and
    `cwd`. Every field a server can send is a string in the contract and none of
    them is guaranteed to be one on the wire."""
    w = {"id": "w-abc123", "kind": "send-keys", "pane": "%1", "text": "hi", field: value}
    assert isinstance(AGENT.validate(w), str)


def test_a_refusal_detail_is_CLAMPED_like_every_other(server, tmux_stub, tmp_path):
    """🟡 A validate() refusal bypassed redact() entirely: an unbounded
    server-supplied `kind` interpolated into the message produced a
    100,021-character detail, into a journal line and into the audit-log column
    — while the comment beside it asserted every interpolated field was bounded.
    """
    AGENT._SEEN.clear()
    server.claim_batches = [[write(kind="x" * 100000)]]
    _rc, out = run_agent(server, tmux_stub, tmp_path)
    body = json.loads(
        [r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert len(body["detail"]) <= 400, (
        f"the reported detail is {len(body['detail'])} characters; every other path is clamped "
        "to 400 and this one is stored in a retained column")
    for line in out.splitlines():
        assert len(line) < 1000, f"a journal line is {len(line)} characters"


def test_a_window_that_LANDED_ELSEWHERE_is_refused_even_when_tmux_said_ok(
        server, tmux_stub, tmp_path):
    """🔴 THE READ-BACK, PINNED INDEPENDENTLY OF THE CHECKS THAT USUALLY FIRE FIRST.

    `=` makes tmux fail for a session that does not exist, and `isdir` refuses a
    cwd that does not exist — so in the ordinary case the read-back never
    decides anything, and removing it left every test green (measured, as two
    surviving mutants). It exists for the case those two cannot see: tmux
    resolving the target to something else for a reason nobody here has thought
    of. Only a stub that LIES about where the window landed can express that.

    The assertion is that NO keys are sent, not merely that the outcome says
    `refused`: the next step presses Enter, so a check that runs after the typing
    is not a check.
    """
    AGENT._SEEN.clear()
    tmux_stub.lie_about_landing(session="somewhere-else")
    server.claim_batches = [[write(id="w-sess", kind="new-session", pane="",
                                   cwd=real_dir(tmp_path), tmuxSessionName="scratch2",
                                   text="echo hi")]]
    run_agent(server, tmux_stub, tmp_path)
    assert tmux_stub.send_keys_calls() == [], (
        "the agent typed into a window tmux placed in a DIFFERENT session than the one "
        "requested, and the next step presses Enter")
    body = json.loads(
        [r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "failed", body
    assert "not the requested" in body["detail"], body


def test_a_window_that_landed_in_the_WRONG_DIRECTORY_is_refused(
        server, tmux_stub, tmp_path):
    """The same, for the working directory.

    `isdir` refuses the common case up front; this is the one where the path
    exists and tmux still put the window somewhere else. A build or a recursive
    delete running one directory over is the whole reason the read-back is there.
    """
    AGENT._SEEN.clear()
    tmux_stub.lie_about_landing(path=str(tmp_path / "elsewhere"))
    (tmp_path / "elsewhere").mkdir(exist_ok=True)
    server.claim_batches = [[write(id="w-path", kind="new-session", pane="",
                                   cwd=real_dir(tmp_path), text="make")]]
    run_agent(server, tmux_stub, tmp_path)
    assert tmux_stub.send_keys_calls() == [], (
        "the agent typed a command into a window tmux opened in a directory OTHER than the "
        "one requested")
    body = json.loads(
        [r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "failed", body
    assert "not the requested" in body["detail"], body


def test_the_agent_SHELLS_OUT_TO_TMUX_AND_NOTHING_ELSE():
    """🔴 THE PIN `test_no_real_launchers.py`'s ACKNOWLEDGEMENT PROMISES, and it
    did not exist until an audit went looking for it.

    That ledger acknowledges this file as reaching `systemctl` in PROSE — one
    line of module docstring giving the operator the second half of the disarm
    procedure. The acknowledgement is only honest while the prose stays prose,
    and its justification asserted that "test_tmux_reply_agent.py pins that the
    agent shells out to nothing else". No such test existed; the sentence named
    a compensating control that was never written.

    🔴 THAT GAP WAS MEASURED, NOT INFERRED. `launcher_scan.hazard_hits` returns a
    FILE SET, and the ledger asserts set equality — so once this file is IN the
    set for `systemctl`, a genuine call site added to it changes the set by
    nothing and the ledger stays green. Injecting

        subprocess.run(["systemctl", "--user", "restart", "some-unit"], check=False)

    into this agent left `test_no_real_launchers.py` at **77 passed**. The
    acknowledgement had blinded the very guard it was filed under.

    So the pin is written here, where it can see argv rather than file names:
    a DIRECT spawn in the agent must take its argv from `tmux_bin()`. AST, not
    grep — this file and the agent both NAME `systemctl` in prose, and a text
    scan cannot tell a docstring from a call.

    🔴 WHAT IT DOES NOT SEE, STATED RATHER THAN IMPLIED — an unqualified "every
    spawn" is what made the two previous versions of this docstring false, and a
    guard whose sentence is wider than its body reads as coverage while providing
    none. MEASURED SURVIVORS at this revision: reflective lookup
    (`getattr(subprocess, "run")`, `importlib.import_module`, `__import__`), a
    star-import (`from subprocess import *`), an indirect binding
    (`_m = subprocess` / `f = run`), and spawners in OTHER modules (`pty.spawn`,
    `asyncio.create_subprocess_exec`). Those are residuals, not oversights: each
    needs a deliberate indirection, whereas the verbs above are what ordinary
    code reaches for. If this file ever grows one of them, this guard will not
    say so — and neither will the launcher ledger.

    🔴 IT RESOLVES IMPORT BINDINGS, BECAUSE ITS FIRST VERSION DID NOT AND WAS
    NARROWER THAN THIS DOCSTRING. That draft matched only a literal
    `subprocess.<verb>(...)` / `os.<verb>(...)` attribute call, so an ALIAS
    walked straight past a guard whose sentence said "nothing else". MEASURED,
    with every `__pycache__` purged and PYTHONDONTWRITEBYTECODE=1 (a `cp -a`
    battery preserves pytest-rewritten bytecode and scores phantom survivors):

        subprocess.run(["systemctl", ...])          KILLED
        os.execv("/bin/systemctl", [...])           KILLED
        subprocess.run(cmd)  # variable argv        KILLED
        from subprocess import run as _r; _r([...]) SURVIVED
        from subprocess import run;      run([...]) SURVIVED
        import subprocess as _sp; _sp.run([...])    SURVIVED
        os.posix_spawn("/.../systemctl", [...], {}) SURVIVED

    Four survivors, and with one of them live the two files were 183 passed —
    the round-2 defect exactly, one import spelling narrower. `claude/RULES.md`:
    "a guard's DESCRIPTION claims COVERAGE — check the implementation is as wide
    as the sentence."

    So the binding map below is the guard, not a nicety: `import subprocess as X`
    and `from subprocess import run as Y` are resolved to their real targets, and
    `posix_spawn` is named explicitly rather than caught by a prefix.
    """
    import ast

    src = SCRIPT.read_text()
    tree = ast.parse(src)

    # 🔴 `getoutput`/`getstatusoutput` ARE IN THIS SET BECAUSE THEY RUN A SHELL
    # and were missing from the first five. Same module, same `subprocess.<verb>()`
    # shape this walker already sees — no aliasing, no reflection — so
    # `subprocess.getoutput(f"systemctl --user is-active {unit}")` SURVIVED a guard
    # whose sentence said "nothing else", while the launcher ledger (which asserts
    # a FILE set, and this file is already in it) is structurally blind to it too.
    SUBPROCESS_VERBS = {"run", "Popen", "call", "check_call", "check_output",
                        "getoutput", "getstatusoutput"}
    # 🔴 NOT every `os.*` — an early draft took the whole module and matched
    # `os.getpid()`, failing on a CLEAN tree. A negative control that goes red is
    # a broken instrument, not a finding. Only verbs that can START a process.
    def _os_spawns(a):
        return a.startswith("exec") or a.startswith("spawn") or a in (
            "system", "popen", "posix_spawn", "posix_spawnp", "forkpty", "fork")

    # name -> "subprocess" | "os", following `as` aliases.
    mod_alias = {}
    # bare name -> the module it was imported OUT of, following `as` aliases.
    # ⚠ SCOPE-BLIND, BOTH WAYS, AND LATENT TODAY. This walks the whole module, so
    # a `from subprocess import ...` inside a function body registers globally
    # (over-strict — fails loud, safe direction), and a LOCAL rebinding of the
    # same name is read as the import (a FALSE POSITIVE: a parameter named `run`
    # in `def _use(run, argv): return run(argv)` is flagged as a spawn). Latent
    # because `scripts/tmux-reply-agent` imports `os` and `subprocess` as modules
    # only, with no from-import, so this dict is EMPTY on the real tree. It goes
    # live the first time anyone adds one; prefer renaming the local over
    # loosening this map.
    fn_alias = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name in ("subprocess", "os"):
                    mod_alias[a.asname or a.name] = a.name
        elif isinstance(node, ast.ImportFrom):
            if node.module in ("subprocess", "os"):
                for a in node.names:
                    fn_alias[a.asname or a.name] = (node.module, a.name)

    argv0 = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        mod = attr = None
        if isinstance(fn, ast.Attribute):
            base = getattr(getattr(fn, "value", None), "id", None)
            mod, attr = mod_alias.get(base), fn.attr
        elif isinstance(fn, ast.Name) and fn.id in fn_alias:
            mod, attr = fn_alias[fn.id]

        if mod is None:
            continue
        spawns = (mod == "subprocess" and attr in SUBPROCESS_VERBS) or \
                 (mod == "os" and _os_spawns(attr))
        if not spawns:
            continue
        if not node.args:
            argv0.append(f"<no-argv:{mod}.{attr}>")
            continue
        first = node.args[0]
        if isinstance(first, ast.BinOp) and isinstance(first.left, ast.List):
            first = first.left
        if isinstance(first, ast.List) and first.elts:
            head = first.elts[0]
        else:
            head = first
        if isinstance(head, ast.Call) and isinstance(head.func, ast.Name):
            argv0.append(head.func.id)
        elif isinstance(head, ast.Constant):
            argv0.append(repr(head.value))
        else:
            argv0.append(ast.dump(head)[:60])

    assert set(argv0) == {"tmux_bin"}, (
        f"the agent's subprocess argv[0] set is {sorted(set(argv0))}, not {{'tmux_bin'}}. "
        "This agent must shell out to tmux and nothing else (this check sees DIRECT spawns; "
        "see the docstring for the reflective/star-import residuals it does not) — it runs "
        "as the operator, is "
        "driven by a network route, and `test_no_real_launchers.py` ACKNOWLEDGES its prose "
        "mention of `systemctl` on exactly this basis. A new argv[0] here is invisible to "
        "that ledger (it asserts a FILE set, and this file is already in it), so this is the "
        "only place the addition can be seen. Do not relax it to make a call site pass.")


def test_an_EMPTY_pane_current_path_does_not_collapse_the_readback(monkeypatch):
    """🔴 A WINDOW TMUX ALREADY CREATED WAS BEING REJECTED BY A `.strip()`.

    `NEW_WINDOW_FORMAT` is `#{pane_id}\\t#{session_name}\\t#{pane_current_path}`.
    The read-back did `out.strip().splitlines()[0].split("\\t")` — and `.strip()`
    removes a TRAILING TAB, so a well-formed three-field line whose last field is
    empty arrives as TWO fields and fails the length check. The window exists by
    then: the caller gets an error, a stray window is left behind, and a retry
    would make a second one.

    MEASURED as an intermittent real-tmux failure, 3 occurrences across ~12 runs
    of `test_the_EXACT_session_still_works`, one on a completely unmutated tree,
    with wall times within 0.5 s of the control — so not a load flake. The
    observed string `'%2\\tscratch20'` is byte-identical to `'%2\\tscratch20\\t'`
    after `.strip()`, which is what identified the mechanism.

    🔴 AND THE TRIGGER HAS SINCE BEEN FORCED DIRECTLY, so this no longer rests on
    byte-identity. On a private `-L` socket (tmux 3.7c), `new-window -P -F` with
    this format produced an EMPTY third field **11 times in ~310 warm-server
    creations (~3.6%)**, and **0 times in 60 cold-server creations** — so the race
    is not server startup. In **11 of 11** the immediate
    `display-message -p -t <pane>` re-read returned the correct populated path,
    which is end-to-end evidence for the remedy below against REAL tmux rather
    than against a stub. That also retires the rival mechanism byte-identity alone
    could not exclude: tmux genuinely emitting two fields.

    🔴 AND THE PARSE IS ONLY HALF OF IT. With the fields split correctly,
    `landed_path` is `""`, and `same_directory("", cwd)` does NOT compare empty
    against cwd — `os.path.realpath("")` returns the AGENT'S OWN cwd, so the
    check would silently compare the wrong directory and usually refuse. An
    unreadable path is "not readable yet", NOT "tmux fell back to the wrong
    directory", and the two must not share a code path.

    This drives `run_tmux` directly rather than through the tmux stub, because
    the stub composes its own third field and cannot express an EMPTY one — the
    exact value under test.
    """
    calls = []

    def fake_run_tmux(args, env=None):
        calls.append(args)
        # task 524: open_window asks the SERVER for the PATH a launched pane
        # should get. Answering "unset" here (tmux's `-PATH`) keeps this test
        # about the READ-BACK parse it was written for — open_window then passes
        # no env, exactly as it did before that change.
        if args[0] == "show-environment":
            return 0, "-PATH\n", ""
        if args[0] == "new-window":
            # Well-formed, three fields, last one EMPTY. Note the trailing tab.
            return 0, "%2\tscratch20\t\n", ""
        if args[0] == "display-message":
            return 0, str(pathlib.Path.home()) + "\n", ""
        raise AssertionError(f"unexpected tmux call: {args}")

    monkeypatch.setattr(AGENT, "run_tmux", fake_run_tmux)
    pane, err = AGENT.open_window(str(pathlib.Path.home()), "scratch20")

    assert not err, (
        f"a three-field read-back whose last field is empty was rejected: {err!r}. "
        "The window already exists at this point, so this is a lost window plus a "
        "misleading error, not a refusal.")
    assert pane == "%2", pane
    assert any(a[0] == "display-message" for a in calls), (
        "an empty pane_current_path must be RE-READ for that pane, not treated as a "
        "directory mismatch — `os.path.realpath('')` is the agent's own cwd, so the "
        "same_directory check would compare something nobody asked about.")


def test_an_UNREADABLE_pane_current_path_still_REFUSES(monkeypatch):
    """The other half, so the fix above cannot become "accept anything".

    If the re-read ALSO comes back empty the directory is genuinely unverifiable,
    and this agent presses Enter — so it must refuse rather than type into a pane
    whose location nothing confirmed.

    🔴 IT PINS THE WHOLE NORMALISED STRING, BECAUSE THE FIRST VERSION OF THIS
    TEST PINNED THE WORD "directory" AND WAS WALKABLE BY ITS OWN NEIGHBOUR.
    That word appears in the unverifiable-path refusal AND in the `same_directory`
    MISMATCH message four lines below it, so deleting the refusal outright left
    this test GREEN — measured as a surviving mutant. The fallthrough then
    produced:

        tmux opened the window in '', not the requested '/home/zach' — the
        directory does not exist on this host, so tmux fell back

    which satisfied both of the old assertions while asserting the exact
    confusion the fix exists to prevent, since `os.path.realpath("")` is the
    AGENT'S OWN cwd (verified) and the unit runs with `WorkingDirectory=~`.
    ⚠ Worse, the old test's verdict depended on an UNPINNED dimension: with
    pytest's cwd at `/home/zach` the mutant died, from `/tmp` or the repo root it
    survived — and the gate runs from the repo root.

    `claude/RULES.md`: assert the STATE, never a word another branch can spell.
    """
    def fake_run_tmux(args, env=None):
        # task 524: open_window now asks the SERVER what PATH a launched pane
        # should get. "-PATH" is tmux's spelling for an unset variable, so
        # open_window passes no environment and this test stays about the
        # unreadable-path refusal it was written for.
        if args[0] == "show-environment":
            return 0, "-PATH\n", ""
        if args[0] == "new-window":
            return 0, "%2\tscratch20\t\n", ""
        if args[0] == "display-message":
            return 0, "\n", ""
        raise AssertionError(f"unexpected tmux call: {args}")

    monkeypatch.setattr(AGENT, "run_tmux", fake_run_tmux)
    pane, err = AGENT.open_window(str(pathlib.Path.home()), "scratch20")
    assert pane == "", pane
    assert " ".join(err.split()) == (
        "tmux did not report the new window's directory, so where it landed "
        "could not be verified"), (
        f"the refusal message is {err!r}. This asserts the WHOLE normalised string "
        "on purpose: pinning a word lets the `same_directory` mismatch branch below "
        "satisfy this test while the refusal is gone.")
    assert "fell back" not in err, (
        f"the unverifiable-path refusal must not be the FALLTHROUGH mismatch: {err!r}. "
        "'could not read where it landed' and 'tmux landed somewhere else' are "
        "different facts and must not share a code path.")


# ---------------------------------------------------------------------------
# task 524: a LAUNCHED PANE must get a PATH that can find `claude`.
#
# 🔴 THE EXISTING real_tmux TESTS ARE STRUCTURALLY BLIND TO THIS.
# `_agent_with_real_tmux` runs tmux with `dict(os.environ, …)` — a FULL
# developer PATH — so the pane it creates inherits a working PATH by accident of
# the harness. Under systemd the agent's own PATH is the unit's deliberately
# minimal `Environment=PATH=` (coreutils + python3 + tmux, 3 entries), and
# `new-window` with no `-e` hands THAT to the new pane. Measured on workbench
# 2026-09-07: the launched pane ran `claude` and got `command not found`, while
# the tmux SERVER's own global PATH was complete — i.e. tmux was never the
# source, the caller's environment was.
#
# So this test supplies the stripped parent environment the unit really has.
# Without it the assertion cannot fail, which is the whole point.
# ---------------------------------------------------------------------------

def _pane_environ_path(real_tmux, pane: str) -> str:
    """The PATH of the pane's own process, read from /proc — not from a shell.

    Reading `/proc/<pid>/environ` is deterministic: it needs no prompt to be
    ready, no command to be typed and no capture-pane timing. A shell-based read
    would be racing the pane's own startup.
    """
    p = real_tmux["tmux"]("display-message", "-p", "-t", pane, "#{pane_pid}")
    pid = p.stdout.strip()
    assert pid.isdigit(), f"no pane pid for {pane!r}: {p.stdout!r}"
    raw = pathlib.Path(f"/proc/{pid}/environ").read_bytes().decode("utf-8", "replace")
    for entry in raw.split("\0"):
        if entry.startswith("PATH="):
            return entry[5:]
    return ""


def test_a_launched_pane_gets_a_PATH_THAT_CAN_FIND_claude(monkeypatch, tmp_path):
    """task 524, criterion 3 — RED before the fix, GREEN after.

    🔴 HERMETIC ON PURPOSE: its own tmux server, its own socket dir, and an
    environment built from scratch rather than from os.environ. It does NOT use
    the shared `real_tmux` fixture. Measured while writing it: run inside a
    four-suite sweep it failed while passing alone, because `pane_path_env`
    came back with another test's tmp_path — some session-scoped fixture in that
    sweep mutates the ambient environment these helpers capture. A test that
    depends on ambient state reports on the run order, not on the code.

    The fake `claude` sits in a directory ABSENT from the agent's own PATH and
    PRESENT in the tmux server's global PATH. That is the live shape, and it is
    what makes the two outcomes distinguishable: a pane built from the caller's
    environment cannot see it, one built from the server's can.
    """
    tmux_exe = _TMUX_EXE_AT_IMPORT
    assert tmux_exe, "tmux is in REQUIRED_TOOLS; it must be on PATH here"

    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    # POSIX-sh body, no shebang — write_exec owns that, and
    # test_runtime_shebangs.py fails any test that writes its own.
    write_exec(bindir / "claude", "exit 0\n")

    sockdir = tempfile.mkdtemp(prefix="t524.")
    sock = "t524-" + os.path.basename(str(tmp_path))
    # A complete environment we OWN, so nothing ambient can reach this test.
    server_env = {
        "PATH": f"{bindir}:{os.path.dirname(tmux_exe)}:/usr/bin:/bin",
        "TMUX_TMPDIR": sockdir,
        "HOME": os.environ.get("HOME", "/tmp"),
        "TERM": "xterm",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }

    def tmux(*args, env=None):
        return subprocess.run([tmux_exe, "-L", sock, *args], capture_output=True,
                              text=True, env=(env or server_env), timeout=30)

    try:
        tmux("new-session", "-d", "-s", "scratch20")
        # POSITIVE CONTROL on the fixture: prove the server really holds a PATH
        # carrying `claude` BEFORE asserting anything about a pane, so a failure
        # below is about the pane and never about the setup.
        seen = tmux("show-environment", "-g", "PATH").stdout.strip()
        assert str(bindir) in seen, f"fixture did not take; server PATH = {seen!r}"

        # 🔴 THE AGENT'S OWN ENVIRONMENT, as systemd really gives it: PATH
        # OVERRIDDEN and nothing else, which is exactly what `Environment=PATH=`
        # does. No `claude` on it.
        agent_env = dict(server_env, PATH=os.path.dirname(tmux_exe))

        def run(args, env=None):
            # env=None means "the agent passed nothing" — the defect. A stub that
            # ignored the argument would pass with or without the fix.
            e = dict(env if env is not None else agent_env)
            e["TMUX_TMPDIR"] = sockdir          # pin the private socket only
            return (lambda p: (p.returncode, p.stdout, p.stderr))(
                subprocess.run([tmux_exe, "-L", sock, *args], capture_output=True,
                               text=True, env=e, timeout=30))

        monkeypatch.setattr(AGENT, "run_tmux", run)

        pane, err = AGENT.open_window(str(tmp_path), "scratch20")
        assert not err, f"open_window failed: {err}"
        assert pane.startswith("%"), f"no pane id: {pane!r}"

        pid = tmux("display-message", "-p", "-t", pane, "#{pane_pid}").stdout.strip()
        assert pid.isdigit(), f"no pane pid for {pane!r}"
        raw = pathlib.Path(f"/proc/{pid}/environ").read_bytes().decode("utf-8", "replace")
        pane_path = ""
        for entry in raw.split("\0"):
            if entry.startswith("PATH="):
                pane_path = entry[5:]
                break

        assert pane_path, f"could not read the pane's PATH at all (pane {pane})"
        assert str(bindir) in pane_path.split(":"), (
            "the launched pane cannot find `claude`: its PATH is "
            f"{pane_path!r}, which does not contain {str(bindir)!r}. The pane "
            "inherited the AGENT's environment instead of the tmux server's. "
            "This is task 524: on workbench the launched window sat on "
            "`claude: command not found`."
        )
    finally:
        subprocess.run([tmux_exe, "-L", sock, "kill-server"], capture_output=True,
                       env=server_env, timeout=30)
        shutil.rmtree(sockdir, ignore_errors=True)


def test_the_launch_env_KEEPS_tmux_reachable_when_the_server_PATH_lacks_it(monkeypatch, tmp_path):
    """The corrected PATH must PREPEND, never REPLACE — task 524.

    🔴 THE MUTANT THIS EXISTS FOR. `launch_env = dict(os.environ, PATH=pane_path)`
    reads as the obvious implementation and passes the sibling test above, because
    there the server's PATH happens to contain tmux. It is wrong: this process
    re-execs tmux WITH that environment, so an operator PATH that does not carry
    tmux would leave the agent unable to run tmux at all — the launch fails
    outright instead of landing a diagnosable window. Measured: without this case
    that mutant SURVIVES.

    So the server PATH here deliberately does NOT contain tmux. Only the
    append-our-own behaviour can make this pass.
    """
    tmux_exe = _TMUX_EXE_AT_IMPORT
    assert tmux_exe, "tmux is in REQUIRED_TOOLS; it must be on PATH here"

    bindir = tmp_path / "onlybin"
    bindir.mkdir()

    sockdir = tempfile.mkdtemp(prefix="t524b.")
    sock = "t524b-" + os.path.basename(str(tmp_path))
    server_env = {
        "PATH": f"{os.path.dirname(tmux_exe)}:/usr/bin:/bin",
        "TMUX_TMPDIR": sockdir,
        "HOME": os.environ.get("HOME", "/tmp"),
        "TERM": "xterm",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }

    def tmux(*args):
        return subprocess.run([tmux_exe, "-L", sock, *args], capture_output=True,
                              text=True, env=server_env, timeout=30)

    try:
        tmux("new-session", "-d", "-s", "scratch20")
        # 🔴 A server PATH WITHOUT tmux on it. This is the whole fixture.
        tmux("set-environment", "-g", "PATH", str(bindir))
        seen = tmux("show-environment", "-g", "PATH").stdout.strip()
        assert seen == f"PATH={bindir}", f"fixture did not take: {seen!r}"

        agent_env = dict(server_env, PATH=os.path.dirname(tmux_exe))

        def run(args, env=None):
            e = dict(env if env is not None else agent_env)
            e["TMUX_TMPDIR"] = sockdir
            # 🔴 BY NAME, NOT BY ABSOLUTE PATH — the real agent runs
            # `tmux_bin()`, which defaults to the bare name "tmux", so PATH is
            # what decides whether tmux can be executed at all. A stub using an
            # absolute path makes this test unable to fail: measured, the
            # replace-PATH mutant SURVIVED until this line changed.
            return (lambda p: (p.returncode, p.stdout, p.stderr))(
                subprocess.run(["tmux", "-L", sock, *args], capture_output=True,
                               text=True, env=e, timeout=30))

        monkeypatch.setattr(AGENT, "run_tmux", run)

        pane, err = AGENT.open_window(str(tmp_path), "scratch20")
        assert not err, (
            "open_window failed with a server PATH that does not carry tmux: "
            f"{err!r}. The launch environment REPLACED this unit's PATH instead "
            "of prepending to it, so tmux itself became unreachable."
        )
        assert pane.startswith("%"), f"no pane id: {pane!r}"
    finally:
        subprocess.run([tmux_exe, "-L", sock, "kill-server"], capture_output=True,
                       env=server_env, timeout=30)
        shutil.rmtree(sockdir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# The transcript DELTA STREAM seam.
#
# 🔴 THE MODULE AND THE AGENT ARE EACH TESTED IN ISOLATION ELSEWHERE, AND THAT IS
# EXACTLY WHY THESE EXIST. `test_transcript_stream.py` drives the protocol against
# a simulated server; the tests above drive the write path against a stub. Neither
# ever builds the combined state, and the defect this feature can produce lives in
# the seam nobody owns: a stream failure reaching the WRITE loop's handler, which
# would put the agent into its 60-second backoff and delay somebody's typed reply
# by a minute.
# --------------------------------------------------------------------------- #


def _projects_tree(tmp_path, session_id="stream-sess", body=None):
    """A synthetic ~/.claude/projects tree with one session."""
    root = tmp_path / "projects" / "-home-zach-workspace-devrc"
    root.mkdir(parents=True, exist_ok=True)
    f = root / f"{session_id}.jsonl"
    f.write_text(body or (
        '{"type":"user","sessionId":"%s","message":{"role":"user","content":"hello"}}\n'
        % session_id), encoding="utf-8")
    return tmp_path / "projects", f


def _stream_posts(server):
    return [r for r in server.requests if r["path"] == "/api/transcripts/stream"]


def test_the_agent_streams_transcript_deltas_on_the_SAME_poll(server, tmux_stub, tmp_path):
    """🔴 THE WHOLE CLAIM OF THIS FEATURE'S TRANSPORT: no new port, no new
    connection, no new unit — the deltas ride the poll the write agent already
    holds. Asserted by seeing BOTH surfaces' traffic from ONE process."""
    projects, _ = _projects_tree(tmp_path)
    server.claim_batches = [[write()]]
    rc, out = run_agent(server, tmux_stub, tmp_path, expect_requests=4, env_extra={
        "CLAWGATE_HOOK_TOKEN": "hook-token-for-the-stream",
        "CLAUDE_PROJECTS_DIR": str(projects),
    })

    posts = _stream_posts(server)
    assert posts, f"the agent never streamed a delta frame; requests={[r['path'] for r in server.requests]}\n{out}"
    frame = json.loads(posts[0]["body"])
    assert frame["host"] == "workbench", frame
    assert [s["sessionId"] for s in frame["sessions"]] == ["stream-sess"], frame
    assert frame["sessions"][0]["reset"] is True, "the first frame for an unseen file must reseed"

    # 🔴 THE HOOK TOKEN, NOT THE TERMINAL ONE. Fusing the tiers would mean
    # disarming the write surface silently takes the operator's session view with
    # it — see transcript_round's docstring.
    assert posts[0]["auth"] == "Bearer hook-token-for-the-stream", posts[0]["auth"]
    claims = [r for r in server.requests if r["path"].endswith("/claim")]
    assert claims, "the write path stopped working once streaming was added"
    assert claims[0]["auth"] == "Bearer not-a-real-terminal-token-not-a-real-token"

    # And the write itself still happened.
    assert len(tmux_stub.send_keys_calls()) == 2, tmux_stub.send_keys_calls()


def test_a_FAILING_transcript_stream_does_not_disturb_the_write_path(server, tmux_stub, tmp_path):
    """🔴 THE SEAM DEFECT THIS GUARDS. Everything the stream can raise is caught
    at the stream's own call site, NOT by the loop's handler — that handler puts
    the agent into a 60-second backoff, so a transcript hiccup would delay a
    typed reply by a minute. The write surface is the capability with a person
    waiting on it."""
    projects, _ = _projects_tree(tmp_path)
    server.stream_status = 500
    server.claim_batches = [[write()]]
    rc, out = run_agent(server, tmux_stub, tmp_path, expect_requests=4, env_extra={
        "CLAWGATE_HOOK_TOKEN": "hook-token-for-the-stream",
        "CLAUDE_PROJECTS_DIR": str(projects),
    })

    assert _stream_posts(server), "the stream never even tried — this test is measuring nothing"
    sends = tmux_stub.send_keys_calls()
    assert len(sends) == 2, (
        f"a FAILING transcript stream stopped the write from being delivered: {sends}\n{out}")
    results = [r for r in server.requests if r["path"].endswith("/result")]
    assert results and json.loads(results[0]["body"])["state"] == "delivered", out
    assert "transcript streaming failed this poll" in out, (
        f"the stream failure was swallowed silently: {out}")


def test_no_hook_token_turns_streaming_OFF_and_says_so_without_touching_the_write_path(
    server, tmux_stub, tmp_path
):
    """🔴 NOT FATAL, AND NOT SILENT. The transcript routes are on the hook tier;
    a host with a terminal token but no hook token must still deliver writes, and
    the 5-minute bulk push keeps feeding the read model. Exiting here would take
    the write surface down over a read model's missing key."""
    projects, _ = _projects_tree(tmp_path)
    server.claim_batches = [[write()]]
    rc, out = run_agent(server, tmux_stub, tmp_path, env_extra={
        "CLAUDE_PROJECTS_DIR": str(projects),
    })

    assert not _stream_posts(server), "a frame was streamed with no hook token"
    assert "transcript streaming is OFF" in out, out
    assert "bulk push" in out, "the log does not say what still covers the feed: " + out
    assert len(tmux_stub.send_keys_calls()) == 2, (
        f"the write path broke when streaming was unconfigured\n{out}")


def test_a_transcript_stream_failure_forces_a_RE_HYDRATION_on_the_next_poll(
    server, tmux_stub, tmp_path
):
    """🔴 THE MOST LIKELY CAUSE OF A STREAM FAILURE IS A REDEPLOY, which is
    exactly when this host's cursors may no longer describe what the server
    holds. Adopting them again is what makes the gap bounded rather than
    permanent — so a failure must cost a cursor read, not just a retry."""
    projects, _ = _projects_tree(tmp_path)
    server.stream_status = 500
    server.claim_batches = [[write()], []]
    rc, out = run_agent(server, tmux_stub, tmp_path, expect_requests=8, env_extra={
        "CLAWGATE_HOOK_TOKEN": "hook-token-for-the-stream",
        "CLAUDE_PROJECTS_DIR": str(projects),
    })

    cursor_reads = [r for r in server.requests if r["path"] == "/api/transcripts/stream/cursors"]
    assert len(cursor_reads) >= 2, (
        f"the cursors were read {len(cursor_reads)} time(s) across repeated stream failures — "
        f"hydration happens once per agent lifetime, so a failure that does not clear it leaves "
        f"the host resending against cursors the server may no longer hold\n{out}")


def test_TWO_skip_reasons_are_each_logged_ONCE_through_the_REAL_loop(server, tmux_stub, tmp_path):
    """🔴 THE STICKY MEMO'S UNION IS ONLY OBSERVABLE WITH **TWO** REASONS, AND
    EVERY EARLIER TEST HAD ONE.

    `stream_skips |= fresh` accumulates; mutating it to a REBIND
    (`stream_skips = fresh`) passed all 208 tests — and it restores exactly the
    flood round 7 removed. With `= fresh`, `seen` is REPLACED by whatever was
    fresh, so two simultaneously-present reasons ping-pong forever:

        {A,B} - {A} = {B}   -> seen={B}
        {A,B} - {B} = {A}   -> seen={A}   -> one line per poll, for ever

    Measured against the real agent as a subprocess: shipped = 2 skip lines,
    the rebind mutant = 69.

    A single-reason flap does NOT distinguish them (both score 1), and the unit
    test for `skip_reasons_to_report` cannot either — it does its own `seen |=
    fresh`, so it is structurally blind to a mutation of the LOOP. This drives
    the loop, with two reasons whose onsets are STAGGERED so the union is what
    has to hold.
    """
    projects, _first = _projects_tree(tmp_path, "skip-a")
    d = projects / "-home-zach-workspace-devrc"
    # Reason 1, present from the first poll: a line still being written.
    (d / "skip-a.jsonl").write_text('{"type":"user","partial":tr', encoding="utf-8")

    # 🔴 REASON 2 ARRIVES **LATER**, AND THE STAGGER IS THE WHOLE FIXTURE. With
    # both files present from poll 1 the rebind mutant SURVIVES: the first poll
    # reports {A,B}, every later poll has fresh=∅, so `stream_skips = fresh` never
    # executes again and the two formulations are indistinguishable.
    #
    # 🔴 SO THE STAGGER MUST BE DETERMINISTIC, AND A `threading.Timer` IS NOT.
    # Measured with the timer: under this box's normal load it fired before the
    # agent's first poll in 3 of 12 runs — the degenerate configuration — and the
    # test PASSED anyway, so the mutant survived 4 of 14 loaded runs while the
    # battery reported 6/6 at ambient load. The file is now written from the stub
    # server's own handler on the agent's THIRD POLL, so "after A has been
    # reported" is a fact about the traffic rather than about the clock.
    def _second_reason_after_third_poll(n):
        if n == 3:
            (d / "skip-b.jsonl").write_bytes(b'{"type":"user","t":"\xff\xfe"}\n')

    server.on_poll = _second_reason_after_third_poll
    server.claim_batches = [[], [], [], []]
    rc, out = run_agent(server, tmux_stub, tmp_path, expect_requests=30, env_extra={
        "CLAWGATE_HOOK_TOKEN": "hook-token-for-the-stream",
        "CLAUDE_PROJECTS_DIR": str(projects),
    })

    lines = [l for l in out.splitlines() if "skipped some sessions" in l]
    # 🔴 EXACTLY TWO, NOT "AT MOST TWO", NOW THAT THE STAGGER IS DETERMINISTIC.
    # `1 <= len(lines)` was the arm that admitted the degenerate run: one line
    # means both reasons arrived together, which is precisely the configuration
    # the mutant survives, so a pass there proved nothing.
    assert len(lines) == 2, (
        f"two skip reasons with STAGGERED onsets produced {len(lines)} log lines, want exactly "
        f"2. 1 means the stagger did not happen and this run proved NOTHING; more than 2 means "
        f"the memo is not ACCUMULATING — the two reasons are ping-ponging and this loop polls "
        f"17,280 times a day:\n"
        + "\n".join(lines[:6]) + "\n---\n" + out[-1500:])
    reported = " ".join(lines)
    assert "no-record-boundary" in reported, reported
    assert "undecodable" in reported, (
        "the SECOND reason was never reported — with a rebinding memo the two reasons "
        f"ping-pong and neither ever settles:\n{reported}")


def test_a_REPEATING_skip_condition_is_logged_ONCE_not_on_every_poll(server, tmux_stub, tmp_path):
    """🔴 THE ONE-REASON CASE, WHERE THE COUNT IS DETERMINISTIC.

    One session, one reason, present on every poll: a correct memo logs it
    exactly once. An upper bound would not do here — `<= 1` is satisfied by ZERO,
    i.e. by the skip line never being emitted at all, which is the regression
    this whole arc is about. Measured: with the emit forced off, the `<= 1`
    version SURVIVED.

    ⚠ THE FIXTURE IS ONE SESSION, AND AN EARLIER COMMENT HERE CLAIMED TWO. It
    wrote `skip-a.jsonl` — the same path `_projects_tree` had just created — so
    "the count goes 1 -> 2" never happened and the unused `first` was the tell.
    The two-reason case it was reaching for is the test above.
    """
    projects, _first = _projects_tree(tmp_path, "skip-a")
    (projects / "-home-zach-workspace-devrc" / "skip-a.jsonl").write_text(
        '{"type":"user","partial":tr', encoding="utf-8")

    server.claim_batches = [[], [], [], []]
    rc, out = run_agent(server, tmux_stub, tmp_path, expect_requests=10, env_extra={
        "CLAWGATE_HOOK_TOKEN": "hook-token-for-the-stream",
        "CLAUDE_PROJECTS_DIR": str(projects),
    })

    lines = [l for l in out.splitlines() if "skipped some sessions" in l]
    assert len(lines) == 1, (
        f"the skip condition was logged {len(lines)} times across one run, want exactly 1 — "
        f"0 means the signal is gone, >1 means the memo is not holding and this loop polls "
        f"17,280 times a day:\n" + "\n".join(lines[:5]) + "\n---\n" + out[-1500:])
    assert "no-record-boundary" in lines[0], lines[0]


def test_a_FLAPPING_skip_reason_is_reported_ONCE_not_on_every_appearance():
    """🔴 THE SET-COMPARISON MEMO STILL FLOODED, AND ITS OWN DOCSTRING CLAIMED IT
    DID NOT. Comparing the current reason set against the previous one logs on
    every change in EITHER direction, so a reason that appears and disappears
    costs TWO lines per cycle — and `no-record-boundary` is exactly that shape: a
    session is mid-record on one poll and complete on the next, which the tailer's
    own docstring calls the ordinary steady state.

    Measured against a transcription of the loop before this fix: 39 log lines
    over 40 polls when the reason flaps; 8 when it appears 1 poll in 10, which is
    3,456 a day at the real cadence.

    Driven against the REAL decision function, because a rule inside main()'s loop
    cannot be tested where it can be wrong — the lesson the previous fix in this
    same arc had to learn.
    """
    report = AGENT.skip_reasons_to_report
    seen = frozenset()
    lines = 0
    # 40 polls, the reason flapping on every other one.
    for i in range(40):
        skipped = {"no-record-boundary": 1} if i % 2 == 0 else {"unchanged": 3}
        fresh = report(seen, skipped)
        if fresh:
            lines += 1
            seen |= fresh
    assert lines == 1, (
        f"a FLAPPING reason produced {lines} log lines over 40 polls. At 17,280 polls a day "
        f"that is a channel nobody reads.")

    # 🔴 THE POSITIVE CONTROL: a genuinely NEW reason must still be reported, or
    # the bound above is achieved by saying nothing.
    fresh = report(seen, {"undecodable": 1})
    assert fresh == frozenset({"undecodable"}), fresh


def test_the_skip_memo_key_ignores_COUNTS_and_tracks_REASONS():
    """🔴 THE SUBPROCESS TEST ABOVE COULD NOT SEE THIS, AND THE MUTANT SURVIVED
    IT. Its fixture has one skipping session throughout, so no count ever moves —
    the count-keyed and set-keyed memos behave identically under it. The rule has
    to be asserted at the point it can be wrong.

    A count moves whenever a different NUMBER of sessions is mid-record this poll
    than last, and `no-record-boundary` is the ordinary steady state — so a
    count-keyed memo re-logs the same condition on a loop that runs 17,280 times
    a day.
    """
    k = AGENT.skip_memo_key

    # The COUNT moving must NOT move the key.
    assert k({"no-record-boundary": 1}) == k({"no-record-boundary": 2}) == k({"no-record-boundary": 97})

    # A new REASON must.
    assert k({"no-record-boundary": 1}) != k({"no-record-boundary": 1, "undecodable": 1})

    # `unchanged` is the steady state and must not register at all — otherwise
    # every poll of a quiet fleet logs.
    assert k({"unchanged": 3}) == k({}) == k(None) == frozenset()
    assert k({"unchanged": 3, "undecodable": 1}) == k({"undecodable": 9}) == frozenset({"undecodable"})


def test_the_reported_reason_bound_matches_the_TAILERS_OWN_set():
    """🔴 A LEDGER, NOT A RECOMPUTATION. `skip_reasons_to_report` logs at most one
    line per distinct reason per agent lifetime, so the bound IS the tailer's
    reason vocabulary — and the prose used to write it out as "four, since there
    are four reasons" when there were five.

    🔴 THE FIRST VERSION OF THIS TEST WAS A TAUTOLOGY. It built `reportable` as
    `{r for r in reasons if r != "unchanged"}` and compared it against
    `skip_memo_key`, whose body is the identical predicate — so both sides
    recomputed from the same input and the equality held for ANY constant set.
    Measured: adding a sixth reason to the tailer PASSED, while this test's own
    docstring claimed such a change "moves this test".

    So the expected set is written out ONCE, by hand, and checked BOTH ways
    against the module: a reason added upstream fails here (GROWTH), and a reason
    removed fails here too (SHRINK). That is a ledger — the only kind worth
    having, and the same shape this repo's other two-way ledgers use.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "ts_for_bound", REPO_ROOT / "scripts" / "lib" / "transcript_stream.py")
    ts = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ts)

    # The ledger. Every entry is a reason a skip line may name; `unchanged` is
    # deliberately absent because it IS the steady state and must never be logged.
    WANT_REPORTABLE = {
        "no-record-boundary",
        "undecodable",
        "unreadable",
        "duplicate-session-id",
        "no-session-id",
    }

    declared = {v for k, v in vars(ts).items() if k.startswith("SKIP_") and isinstance(v, str)}
    assert declared, "the SKIP_* scan found nothing — it is measuring nothing"
    assert "unchanged" in declared, (
        f"the steady-state reason is not among the tailer's SKIP_* values: {sorted(declared)}")

    assert declared - {"unchanged"} == WANT_REPORTABLE, (
        f"the tailer's reportable reasons are {sorted(declared - {'unchanged'})} but this "
        f"ledger says {sorted(WANT_REPORTABLE)}. A reason added upstream widens the "
        f"per-lifetime line bound the sticky memo promises; one removed narrows it. Update "
        f"the ledger deliberately — that IS the accounting.")

    # And the memo key must admit exactly the ledger, no more and no less.
    assert AGENT.skip_memo_key({r: 1 for r in declared}) == frozenset(WANT_REPORTABLE), (
        "the memo key does not admit exactly the tailer's reportable reasons, so the bound "
        "the docstring describes is not the one the code enforces")


# --------------------------------------------------------------------------- #
# 16. THE ASKUSERQUESTION MENU — every option the operator tapped delivered
#     OPTION 1, and the card said `Delivered`.
# --------------------------------------------------------------------------- #
#
# 🔴 THE DEFECT, MEASURED LIVE (Claude Code 2.1.232). A menu DISCARDS typed
# characters — no filter, no selection, the cursor unmoved — so
#
#     tmux send-keys -t %176 -l -- 'SQLite'
#     tmux send-keys -t %176 Enter
#
# typed nothing and then selected whatever row was HIGHLIGHTED, which on a fresh
# render is option 1. The pair is CORRECT against a text prompt, which is why it
# survived: every stubbed test in this file described a pane that does not exist
# in the failing case, because the stub had no pane content at all.
#
# So the load-bearing test here is `test_tapping_the_SECOND_option_delivers_the_
# SECOND_option`: it is red at the pre-change code, with the agent sending the
# label and an Enter, and green at HEAD with the row's DIGIT. Every refusal below
# is a claim about that same instrument.


def test_tapping_the_SECOND_option_delivers_the_SECOND_option(server, tmux_stub, tmp_path):
    """🔴 THE REGRESSION TEST FOR THE WRONG-ANSWER BUG, AND THE DELIVERABLE.

    Red at the pre-change code — which sends `-l -- 'Flatfile'` and an `Enter`,
    i.e. nothing typed and row 1 selected — and green here, where the agent reads
    the pane, finds the label on row 2, and presses `2`.

    🔴 IT ASSERTS THE WHOLE argv LIST, not merely that a `2` appears somewhere.
    The old pair still "contains" the right answer in the sense that the label is
    in it; what makes the delivery correct is that the label is NOT typed and the
    Enter is NOT pressed, so the absence is as load-bearing as the presence.
    """
    tmux_stub.set_captures(SINGLE_MENU_CAPTURE, ANSWERED_CAPTURE)
    server.claim_batches = [[write(text="Flatfile")]]
    rc, out = run_agent(server, tmux_stub, tmp_path)

    assert tmux_stub.send_keys_calls() == [
        ["send-keys", "-t", "%12", "-l", "--", "2"]], (
        "the agent did not answer the menu by its row's DIGIT. Typing into an "
        "AskUserQuestion menu is discarded and the Enter selects whatever row is "
        f"highlighted — option 1 — whatever the operator tapped.\n{out}")

    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "delivered", body
    assert "2" in body["detail"], body


def test_tapping_the_FIRST_option_uses_its_own_digit_not_a_constant(server, tmux_stub, tmp_path):
    """The pair to the test above, and it is not redundant with it.

    A mutant that hardcodes `"1"` delivers the right answer for option 1 and the
    wrong one for every other option; a mutant that hardcodes `"2"` does the
    reverse. Only both tests together pin the digit to the row.
    """
    tmux_stub.set_captures(SINGLE_MENU_CAPTURE, ANSWERED_CAPTURE)
    server.claim_batches = [[write(text="Ledger")]]
    run_agent(server, tmux_stub, tmp_path)
    assert tmux_stub.send_keys_calls() == [
        ["send-keys", "-t", "%12", "-l", "--", "1"]], tmux_stub.send_keys_calls()


def test_a_REORDERED_menu_is_answered_by_the_row_the_PANE_shows(server, tmux_stub, tmp_path):
    """🔴 THE DIGIT COMES OFF THE PANE, NEVER OFF THE ORDER CLAWGATE SENT.

    clawgate renders its own option list and carries the index as
    `data-reply-option` — a render-time data attribute the write route never
    reads — so the only thing on the wire is the LABEL. A screen whose rows are
    in a different order is exactly the case clawgate's own comment refuses to
    send an index for, and it is the one where an index taken from anywhere but
    this pane answers the wrong question.

    Same two options as the test above, swapped: `Ledger` is now row 2.
    """
    tmux_stub.set_captures(REORDERED_MENU_CAPTURE, ANSWERED_CAPTURE)
    server.claim_batches = [[write(text="Ledger")]]
    run_agent(server, tmux_stub, tmp_path)
    assert tmux_stub.send_keys_calls() == [
        ["send-keys", "-t", "%12", "-l", "--", "2"]], (
        "the agent pressed the digit the option had in clawgate's list rather than the "
        "one the pane is showing")


def test_the_pane_is_READ_before_anything_is_typed(server, tmux_stub, tmp_path):
    """A read that happens AFTER the keys is not a guard.

    The positive control for the whole section: the capture must actually be
    made, against the write's own pane, with the arguments real tmux accepts.
    """
    tmux_stub.set_captures(SINGLE_MENU_CAPTURE, ANSWERED_CAPTURE)
    server.claim_batches = [[write(text="Flatfile")]]
    run_agent(server, tmux_stub, tmp_path)

    calls = tmux_stub.calls()
    caps = [i for i, c in enumerate(calls) if c and c[0] == "capture-pane"]
    sends = [i for i, c in enumerate(calls) if c and c[0] == "send-keys"]
    assert caps, f"the agent never read the pane: {calls}"
    assert sends, f"the agent never sent anything: {calls}"
    assert caps[0] < sends[0], (
        f"the pane was read only AFTER keys had already gone into it: {calls}")
    assert calls[caps[0]] == ["capture-pane", "-p", "-t", "%12"], calls[caps[0]]


# --------------------------------------------------------------------------- #
# 16b. The ordinary path is UNTOUCHED.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("capture,what", [
    (DEFAULT_PROMPT_CAPTURE, "a shell prompt"),
    (CLAUDE_TEXT_PROMPT_CAPTURE, "a Claude Code text prompt"),
    (ANSWERED_CAPTURE, "a transcript with no prompt of its own"),
    ("", "a pane that reads back blank"),
])
def test_a_text_prompt_is_delivered_EXACTLY_as_before(server, tmux_stub, tmp_path,
                                                      capture, what):
    """🔴 THE COMMON CASE, AND THE ONE THIS CHANGE MUST NOT TOUCH.

    Typing works against a prompt, so the delivery there is the literal text and
    then Enter — the exact argv the positive control at the top of this file
    pins. A detector that answered "menu" for an ordinary prompt would send a
    DIGIT into a shell, so the false-positive direction is pinned here rather
    than assumed from the menu tests passing.

    ⚠ A pane that reads back BLANK is in this list on purpose: it is not the same
    fact as a pane that could not be READ (the next test), and it provably cannot
    be a menu, because a menu has visible rows.
    """
    tmux_stub.set_captures(capture)
    server.claim_batches = [[write(text="yes, go ahead")]]
    run_agent(server, tmux_stub, tmp_path)
    assert tmux_stub.send_keys_calls() == [
        ["send-keys", "-t", "%12", "-l", "--", "yes, go ahead"],
        ["send-keys", "-t", "%12", "Enter"],
    ], f"the delivery to {what} changed shape: {tmux_stub.send_keys_calls()}"
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "delivered", body


def test_a_capture_that_FAILS_refuses_rather_than_typing_BLIND(server, tmux_stub, tmp_path):
    """🔴 AN UNREADABLE PANE CANNOT RULE OUT A MENU.

    The two possible guesses are not symmetric: typing into a menu is a WRONG
    answer reported as delivered, and refusing is no answer, recorded as one. So
    a capture this agent cannot make is a refusal with NO send at all.
    """
    tmux_stub.fail_capture(rc=1, stderr="can't find pane: %12")
    server.claim_batches = [[write(text="yes, go ahead")]]
    run_agent(server, tmux_stub, tmp_path)

    assert tmux_stub.send_keys_calls() == [], (
        "the agent typed into a pane it could not read, so whether the characters were "
        "discarded by a menu is unknown — and the Enter would then have answered "
        "whichever row was highlighted")
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "refused", body
    assert "could not be read" in body["detail"], body


# --------------------------------------------------------------------------- #
# 16c. What this change deliberately REFUSES.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("capture,needle,what", [
    (MULTI_QUESTION_MENU_CAPTURE, "MULTI-question", "a multi-question tab strip"),
    (MULTI_QUESTION_REVIEW_CAPTURE, "MULTI-question", "its review screen"),
    (MULTI_SELECT_MENU_CAPTURE, "multi-select", "a multiSelect checkbox list"),
    (TWO_CURSOR_MENU_CAPTURE, "exactly one cursor row",
     "numbered rows with two cursor glyphs"),
])
def test_a_menu_shape_this_agent_cannot_drive_is_REFUSED_with_no_send(
        server, tmux_stub, tmp_path, capture, needle, what):
    """🔴 REFUSE, DO NOT HALF-ANSWER.

    Each shape is out of scope for a DIFFERENT reason, and each reason is a wrong
    answer rather than a missing feature:

      * the multi-question chain — answering question 1 leaves the rest
        unanswered, and the operator's card says it was delivered;
      * its review screen — `❯ 1. Submit answers` / `2. Cancel`, where a label
        match on a real option would press Submit or Cancel;
      * multiSelect — a digit TOGGLES in place and the completing `Submit` row is
        UNNUMBERED, reachable only by arrow keys, which `termwrite.textIsAllowed`
        refuses to carry by design;
      * numbered rows carrying TWO cursor glyphs — whether a live menu is up at
        all cannot be decided, so neither a digit nor a typed reply is safe.

    ⚠ A NUMBERED MENU WITH NO FREE-TEXT ROW IS NO LONGER IN THIS LIST, and that
    is `NO_FREE_TEXT_MENU_CAPTURE`'s own two tests below: an exactly-matching reply
    is DELIVERED by its row's digit, and only a reply matching nothing is refused —
    by the delivery, after the match has been tried.

    The assertion is NO send-keys, not merely that the state says `refused`: a
    refusal reported after the keys have gone is not a refusal.
    """
    tmux_stub.set_captures(capture)
    server.claim_batches = [[write(text="Flatfile")]]
    run_agent(server, tmux_stub, tmp_path)

    assert tmux_stub.send_keys_calls() == [], (
        f"the agent sent keys into {what}: {tmux_stub.send_keys_calls()}")
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "refused", body
    assert needle in body["detail"], body


def test_an_AMBIGUOUS_match_is_refused_rather_than_guessed(server, tmux_stub, tmp_path):
    """Two rows carrying the same label cannot both be what the operator tapped.

    Picking the first would be a coin flip recorded as a delivery. The pane is
    the only evidence there is, and it does not answer the question.
    """
    twin = SINGLE_MENU_CAPTURE.replace("    2. Flatfile\n", "    2. Ledger\n")
    tmux_stub.set_captures(twin)
    server.claim_batches = [[write(text="Ledger")]]
    run_agent(server, tmux_stub, tmp_path)
    assert tmux_stub.send_keys_calls() == [], tmux_stub.send_keys_calls()
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "refused", body
    assert "matches 2 of the menu's 4 option rows" in body["detail"], body


def test_a_TYPE_ONLY_write_to_a_menu_is_refused(server, tmux_stub, tmp_path):
    """`submit: false` means "put it in the pane and leave it there".

    A menu has no such state: it discards the characters, and the digit that
    would select the row also ADVANCES. So there is nothing to honour, and
    pressing the digit anyway would answer a question the write said not to
    answer. The field is still honoured exactly as before on a text prompt —
    `test_type_only_sends_no_enter` pins that.
    """
    tmux_stub.set_captures(SINGLE_MENU_CAPTURE)
    server.claim_batches = [[write(text="Flatfile", submit=False)]]
    run_agent(server, tmux_stub, tmp_path)
    assert tmux_stub.send_keys_calls() == [], tmux_stub.send_keys_calls()
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "refused", body
    assert "type-only" in body["detail"], body


# --------------------------------------------------------------------------- #
# 16d. A reply that names no option is a genuine free-text answer.
# --------------------------------------------------------------------------- #
def test_a_reply_that_matches_NO_row_goes_through_the_free_text_row(server, tmux_stub, tmp_path):
    """🔴 THE ONE ROUTE THAT DOES NOT PUT WORDS NOBODY CHOSE INTO A SELECTION.

    Three keypresses in order: the free-text row's digit, the reply LITERALLY,
    then Enter. The order is the whole thing — the digit must precede the text,
    because before it the row is not focused and the characters are discarded.
    """
    tmux_stub.set_captures(SINGLE_MENU_CAPTURE,
                           SINGLE_MENU_FREE_TEXT_FOCUSED_CAPTURE,
                           ANSWERED_CAPTURE)
    server.claim_batches = [[write(text="neither, use the ledger for now")]]
    run_agent(server, tmux_stub, tmp_path)

    assert tmux_stub.send_keys_calls() == [
        ["send-keys", "-t", "%12", "-l", "--", "3"],
        ["send-keys", "-t", "%12", "-l", "--", "neither, use the ledger for now"],
        ["send-keys", "-t", "%12", "Enter"],
    ], tmux_stub.send_keys_calls()
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "delivered", body


def test_a_free_text_row_that_never_TAKES_FOCUS_is_not_typed_into(server, tmux_stub, tmp_path):
    """🔴 WITHOUT THIS THE FREE-TEXT PATH REBUILDS THE ORIGINAL DEFECT.

    If the digit did not focus the row, the characters that follow are discarded
    exactly as they were before this change and the Enter answers whichever row is
    highlighted. So the reply is NOT typed and Enter is NEVER pressed until focus
    is observed — the stub shows a menu that stays unfocused, and the outcome is
    `failed`, not `delivered`.
    """
    tmux_stub.set_captures(SINGLE_MENU_CAPTURE)   # never focuses, repeats for ever
    server.claim_batches = [[write(text="neither, use the ledger for now")]]
    run_agent(server, tmux_stub, tmp_path)

    assert tmux_stub.send_keys_calls() == [
        ["send-keys", "-t", "%12", "-l", "--", "3"]], (
        "the agent typed into a free-text row that had not taken focus, so the Enter "
        f"would have answered a highlighted row: {tmux_stub.send_keys_calls()}")
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "failed", body
    assert "did not take focus" in body["detail"], body
    assert "Enter was NOT pressed" in body["detail"], body


def test_a_free_text_reply_TYPED_but_never_COMMITTED_is_not_reported_delivered(
        server, tmux_stub, tmp_path):
    """🔴 THE ONE STATE THE OBVIOUS READ-BACK CANNOT SEE, AND IT WAS MEASURED HERE.

    Typing REPLACES the focused row's label, so a reply that was typed and never
    committed changes the label set — and it also stops the pane classifying as a
    menu at all, because the `Type something.` row that made it recognisable IS
    now the reply. The first draft asked `menu_settled`'s question ("did the menu
    move on") and BOTH of those satisfied it, so this exact fixture reported
    `delivered` for a reply sitting uncommitted in a row.

    So the free-text path asks a different question — is the reply still in a menu
    row — and this is the fixture that distinguishes the two.
    """
    typed = SINGLE_MENU_FREE_TEXT_FOCUSED_CAPTURE.replace(
        "  ❯ 3. Type something.\n", "  ❯ 3. neither, use the ledger for now\n")
    tmux_stub.set_captures(SINGLE_MENU_CAPTURE,
                           SINGLE_MENU_FREE_TEXT_FOCUSED_CAPTURE,
                           typed)   # repeats for ever: Enter never took
    server.claim_batches = [[write(text="neither, use the ledger for now")]]
    run_agent(server, tmux_stub, tmp_path)

    assert tmux_stub.send_keys_calls() == [
        ["send-keys", "-t", "%12", "-l", "--", "3"],
        ["send-keys", "-t", "%12", "-l", "--", "neither, use the ledger for now"],
        ["send-keys", "-t", "%12", "Enter"],
    ], tmux_stub.send_keys_calls()
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "failed", body
    assert "UNKNOWN" in body["detail"], body
    assert "still sitting in a menu row" in body["detail"], body


# --------------------------------------------------------------------------- #
# 16e. `delivered` is a MEASUREMENT, not an assumption.
# --------------------------------------------------------------------------- #
def test_a_menu_that_does_NOT_advance_is_reported_failed_not_delivered(
        server, tmux_stub, tmp_path):
    """🔴 THE WHOLE FAILURE BEING FIXED IS A WRONG ANSWER REPORTED AS SUCCESS.

    So the digit is followed by a read-back, and a menu still showing the same
    options is `failed` — which the audit log reads as delivery UNKNOWN. And it
    must not RETRY: at-most-once is the one guarantee this agent cannot trade,
    so exactly one keypress may appear.
    """
    tmux_stub.set_captures(SINGLE_MENU_CAPTURE)   # never moves, repeats for ever
    server.claim_batches = [[write(text="Flatfile")]]
    run_agent(server, tmux_stub, tmp_path)

    assert tmux_stub.send_keys_calls() == [
        ["send-keys", "-t", "%12", "-l", "--", "2"]], (
        f"the agent re-sent the answer after the menu did not move: "
        f"{tmux_stub.send_keys_calls()}")
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "failed", body
    assert "UNKNOWN" in body["detail"], body
    assert "nothing was re-sent" in body["detail"], body


def test_a_NEW_menu_after_the_answer_counts_as_an_advance_ONLY_WITH_A_RECORD(
        server, tmux_stub, tmp_path):
    """🔴 THE ONE STATE THAT IS SHARED BY A GOOD DELIVERY AND A HALF-ANSWERED CHAIN.

    Answering may be followed immediately by another ask, so "there is still a menu
    on screen" is not the question. But neither is "is it the SAME menu": answering
    the FIRST question of a CHAIN also renders a different menu, and the shipped
    code reported that `delivered` while the tool was still blocked. Two very
    different outcomes, one observation — RULES.md's "an empty result cannot
    distinguish two mechanisms", in the read-back.

    What distinguishes them is POSITIVE evidence, and it is in the pane: a genuine
    answer prints `● User answered Claude's questions:` with the chosen label,
    while a chain prints nothing at all until Submit. So a different menu settles
    only when the pane RECORDS this reply as an answer.

    ⚠ IT ASSERTS THE DIGIT TOO, and that clause is what stops the first half being
    VACUOUS. A `delivered` on its own is what the PRE-CHANGE agent reported for
    this fixture — it typed the label, pressed Enter and called it delivered — so
    the state alone was green at the base sha and proved nothing.
    """
    second = SINGLE_MENU_CAPTURE.replace("Ledger", "Postgres").replace("Flatfile", "SQLite")

    # A REAL answer followed by a new ask: the record is there, above the new modal.
    tmux_stub.set_captures(SINGLE_MENU_CAPTURE, ANSWERED_CAPTURE + second)
    server.claim_batches = [[write(text="Flatfile")]]
    run_agent(server, tmux_stub, tmp_path)
    assert tmux_stub.send_keys_calls() == [
        ["send-keys", "-t", "%12", "-l", "--", "2"]], tmux_stub.send_keys_calls()
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "delivered", body
    assert "records this option as the answer" in body["detail"], body

    # The SAME observable with no record — which is what a half-answered chain
    # looks like — is UNKNOWN, and nothing is re-sent.
    tmux_stub.reset()
    server.requests.clear()
    tmux_stub.set_captures(SINGLE_MENU_CAPTURE, second)
    server.claim_batches = [[write(text="Flatfile")]]
    run_agent(server, tmux_stub, tmp_path)
    assert tmux_stub.send_keys_calls() == [
        ["send-keys", "-t", "%12", "-l", "--", "2"]], tmux_stub.send_keys_calls()
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "failed", body
    assert "records no answer" in body["detail"], body


def test_the_menu_detail_never_carries_the_reply_text(server, tmux_stub, tmp_path):
    """🔴 A MATCHED LABEL *IS* THE REPLY TEXT, WHICH IS CREDENTIAL-GRADE.

    Every diagnostic on the menu path names a digit, a count or a shape — never a
    label — because the label that matched is by definition the text the operator
    sent, and that text never leaves this process. Driven with the marker AS the
    option label so a leak has somewhere to show up.
    """
    marked = SINGLE_MENU_CAPTURE.replace("    2. Flatfile\n", f"    2. {MARKER}\n")
    tmux_stub.set_captures(marked)   # never advances -> the wordiest detail there is
    server.claim_batches = [[write(text=MARKER)]]
    rc, out = run_agent(server, tmux_stub, tmp_path)

    assert MARKER not in out, f"the reply text reached the journal:\n{out}"
    results = [r for r in server.requests if r["path"].endswith("/result")]
    body = json.loads(results[0]["body"])
    assert body["state"] == "failed", body
    assert MARKER not in body["detail"], body


# --------------------------------------------------------------------------- #
# 16f. The predicates, driven directly.
# --------------------------------------------------------------------------- #
def test_the_pane_classifier_is_exercised_directly():
    """Every kind, including the pairs no single agent run can reach at once."""
    assert AGENT.classify_pane(DEFAULT_PROMPT_CAPTURE)[0] == AGENT.PANE_TEXT
    assert AGENT.classify_pane(CLAUDE_TEXT_PROMPT_CAPTURE)[0] == AGENT.PANE_TEXT
    assert AGENT.classify_pane("")[0] == AGENT.PANE_TEXT
    assert AGENT.classify_pane(ANSWERED_CAPTURE)[0] == AGENT.PANE_TEXT

    kind, labels, why = AGENT.classify_pane(SINGLE_MENU_CAPTURE)
    assert kind == AGENT.PANE_MENU and why == ""
    assert labels == ["Ledger", "Flatfile", "Type something.", "Chat about this"], labels

    for capture, want in (
            (MULTI_QUESTION_MENU_CAPTURE, AGENT.PANE_MULTI_QUESTION),
            (MULTI_QUESTION_REVIEW_CAPTURE, AGENT.PANE_MULTI_QUESTION),
            (MULTI_SELECT_MENU_CAPTURE, AGENT.PANE_MULTI_SELECT),
            (TWO_CURSOR_MENU_CAPTURE, AGENT.PANE_UNKNOWN_MENU)):
        kind, labels, why = AGENT.classify_pane(capture)
        assert kind == want, f"{want}: got {kind}"
        assert labels == [] and why, (kind, labels, why)

    # A cursored numbered menu with NO free-text row is a MENU, with its labels —
    # the free-text row decides whether an UNMATCHED reply has a route, which is
    # `deliver_to_menu`'s question and not this one.
    kind, labels, why = AGENT.classify_pane(NO_FREE_TEXT_MENU_CAPTURE)
    assert (kind, labels, why) == (AGENT.PANE_MENU, ["Ledger", "Flatfile"], ""), (
        kind, labels, why)

    # A numbered block and a question marker with NO cursor row is refused: that is
    # also what a menu caught half-drawn looks like. It needs BOTH signals — the
    # same block with the marker stripped is an ordinary numbered list.
    nocursor = SINGLE_MENU_CAPTURE.replace("  ❯ 1.", "    1.")
    assert AGENT.classify_pane(nocursor)[0] == AGENT.PANE_UNKNOWN_MENU
    assert "half-drawn" in AGENT.classify_pane(nocursor)[2], AGENT.classify_pane(nocursor)[2]

    # 🔴 AND STRIPPING THE MARKER IS NO LONGER ENOUGH TO MAKE IT ORDINARY TEXT,
    # which is a DELIBERATE change to this assertion rather than an accident of
    # one. It used to read "the same block with the marker stripped is an ordinary
    # numbered list" — true when the `☐` was the only evidence a modal was up, and
    # false now that the FOOTER is, because the footer survives the clipping that
    # takes the marker. Both signals have to go before this is text.
    marker_stripped = nocursor.replace("  ☐ Store\n", "")
    assert AGENT.classify_pane(marker_stripped)[0] == AGENT.PANE_UNKNOWN_MENU, (
        "the ask's own footer stopped being read as evidence a modal is up")
    assert AGENT.classify_pane(
        marker_stripped.replace(MENU_FOOTER_SINGLE, ""))[0] == AGENT.PANE_TEXT

    # And numbered rows carrying TWO cursor glyphs are undecidable, not a menu.
    assert AGENT.classify_pane(
        SINGLE_MENU_CAPTURE.replace("    2. Flatfile", "  ❯ 2. Flatfile")
    )[0] == AGENT.PANE_UNKNOWN_MENU


def test_the_classifier_does_not_call_ANY_numbered_text_a_menu():
    """🔴 THE FALSE-POSITIVE DIRECTION, WHICH SENDS A DIGIT INTO A SHELL.

    Every case here is a real pane a reply is legitimately delivered to, and each
    was chosen because it defeats one signal on its own. The signature that makes
    a menu is narrow on purpose: contiguous rows from 1, on adjacent lines, with
    the cursor glyph, and a free-text row.

    🔴 THE FIRST CASE IS NOT HYPOTHETICAL — it was MEASURED as a false positive
    while this was being written. A numbered block ALONE used to be enough of a
    signal, so a pane whose ordinary output contained `1. … 2. … 3. …` classified
    as a menu-shaped thing and every reply into it was refused. A detector whose
    failure direction is refusing legitimate replies is the one an operator turns
    off, so the numbering had to stop counting by itself.
    """
    for capture, defeats in (
            ("1. install the thing\n2. run the thing\n3. profit\n",
             "numbered rows, no cursor glyph"),
            ("devrc $ grep -c '\\[ \\]' TODO.md\n7\ndevrc $ \n",
             "a checkbox in shell output"),
            ("  ❯ 1. Ledger\n", "a cursored row, but only one of them"),
            ("  ❯ 1. Ledger\n\n\n\n\n\n    2. Flatfile\n",
             "two numbered rows too far apart to be one block"),
            ("● Reading the file now.\n\n  1. Ledger\n  ❯ tail -f log\n",
             "a cursor glyph that is not on a numbered row"),
    ):
        assert AGENT.classify_pane(capture)[0] == AGENT.PANE_TEXT, (capture, defeats)


def test_the_classifier_reads_the_BOTTOM_MOST_menu():
    """A live menu is the thing at the bottom; anything above it is transcript.

    🔴 TWO COMPLETE MENUS, DELIBERATELY. An earlier version stacked a transcript
    above ONE menu, which cannot see the defect at all: with only one block there
    is nothing to choose between, so a mutant that took the FIRST block died on
    the `kind` assertion — for the wrong reason — rather than on the labels. Both
    blocks are full four-row menus here, so the only thing that differs between
    the two readings is WHICH labels come back.
    """
    stacked = SINGLE_MENU_CAPTURE + "\n" + SINGLE_MENU_CAPTURE.replace(
        "Ledger", "Second").replace("Flatfile", "Third")
    kind, labels, _ = AGENT.classify_pane(stacked)
    assert kind == AGENT.PANE_MENU, stacked
    assert labels[:2] == ["Second", "Third"], labels


def test_label_matching_is_exact_case_insensitive_and_truncation_aware():
    """What counts as "this row is the option the reply names"."""
    assert AGENT.label_matches("Flatfile", "Flatfile")
    assert AGENT.label_matches("Flatfile", "  Flatfile  ")
    assert AGENT.label_matches("Flat  file", "Flat file")
    assert AGENT.label_matches("flatfile", "Flatfile")
    # A label too long for the pane renders truncated, and the mark is REQUIRED —
    # so a short label can never prefix-match a longer, different reply.
    assert AGENT.label_matches("Use the flatfile stor…", "Use the flatfile store for now")
    assert AGENT.label_matches("Use the flatfile stor...", "Use the flatfile store for now")
    # 🔴 THE MARK IS WHAT LICENSES THE PREFIX MATCH, and this is the case that
    # says so. MEASURED: a mutant dropping the `endswith` check SURVIVED against
    # the short `"Use"` line below, because the minimum-head length rejected it
    # for an unrelated reason — so the fixture has to be long enough that only
    # the missing mark can refuse it.
    assert not AGENT.label_matches("Use the flatfile", "Use the flatfile store for now")
    assert not AGENT.label_matches("Use", "Use the flatfile store for now")
    assert not AGENT.label_matches("Short…", "Shorter thing entirely")
    assert not AGENT.label_matches("Flatfile", "Ledger")
    assert not AGENT.label_matches("Flatfile", "Flatfiles")


def test_the_free_text_row_is_recognised_by_an_ENUMERATED_label():
    """The row a genuine free-text reply is routed through.

    An enumeration rather than a pattern: it decides whether such a reply has a
    route at all, and a pattern loose enough to match a real option label would
    type the reply into the wrong row.
    """
    assert AGENT.free_text_key("Type something.") in AGENT.MENU_FREE_TEXT_LABELS
    assert AGENT.free_text_key("  type SOMETHING  ") in AGENT.MENU_FREE_TEXT_LABELS
    assert AGENT.free_text_key("Type your own…") in AGENT.MENU_FREE_TEXT_LABELS
    assert AGENT.free_text_key("Chat about this") not in AGENT.MENU_FREE_TEXT_LABELS
    assert AGENT.free_text_key("Flatfile") not in AGENT.MENU_FREE_TEXT_LABELS


def test_a_row_past_the_ninth_is_refused_rather_than_approximated():
    """A digit key selects; row 10 has no digit, and arrows are not sent here."""
    assert AGENT.menu_digit(0) == ("1", "")
    assert AGENT.menu_digit(8) == ("9", "")
    digit, why = AGENT.menu_digit(9)
    assert digit == "" and "row 10" in why, (digit, why)
    assert str(AGENT.MENU_MAX_DIGIT) in why, why


def test_the_menu_path_refuses_an_empty_text_rather_than_ABORTING_the_tool():
    """🔴 ENTER ON AN EMPTY FREE-TEXT ROW ABORTS THE WHOLE ASK — measured: `●
    User declined to answer questions`. That is destructive, not a no-op, so the
    emptiness is re-checked on this path even though `validate` already refuses
    it for a send-keys write."""
    state, detail = AGENT.deliver_to_menu("%12", ["Ledger", "Type something."], "", True)
    assert state == "refused", (state, detail)
    assert "aborts the menu" in detail, detail


# --------------------------------------------------------------------------- #
# 16h. 🔴 THE FALSE-POSITIVE DIRECTION, MEASURED ON THE LIVE FLEET.
#
# The shipped classifier entered its refusing branches on a question marker ALONE,
# with no cursor row and no numbered option block required. Swept over all 53 live
# tmux panes on this host it produced `text 49 · multi-question 2 · unknown-menu 2`
# — 4 refused (7.5%), every one of them `pane_current_command=claude`, and ZERO
# panes classified as a menu this agent can drive. All four carried a marker or a
# `✔ Submit` strip quoted in a transcript, with zero numbered rows and zero cursor
# rows. So the refusals were not conservatism: they were the detector reading
# scrollback.
#
# The precondition is now `a numbered block carrying the cursor, or a cursored run
# of checkbox rows` — for EVERY branch, not just the numbered one.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("capture,what", QUOTED_MENU_SHAPES)
def test_a_QUOTED_menu_marker_is_a_TEXT_pane_and_is_DELIVERED_to(
        server, tmux_stub, tmp_path, capture, what):
    """🔴 EACH OF THESE WAS A LIVE REFUSAL, and each is a pane a reply belongs in.

    The assertion is the full argv pair, not merely that the state says
    `delivered`: what makes this pane ordinary is that it gets the LITERAL text and
    an Enter, exactly as it did before the menu surface existed.

    ⚠ TWO OF THE FOUR ARE REGRESSION GUARDS AND TWO ARE INVARIANT GUARDS — measured
    at `18cfe7df`, not assumed. The `☐` marker and the `✔ Submit` tab strip are RED
    at the shipped code (a marker alone opened the refusing branches). The prose
    naming `Review your answers` and the bare markdown task list were already GREEN
    there, because neither carries a `☐`/`☒` and the shipped precondition keyed on
    that count alone — they pin the live SHAPES against a future widening, and are
    not evidence about this fix. `test_a_TASK_LIST_above_a_REAL_menu_…` is the one
    that catches the checkbox signal, and it is red at base.
    """
    assert AGENT.classify_pane(capture)[0] == AGENT.PANE_TEXT, (
        f"{what} classified as {AGENT.classify_pane(capture)[0]}; it carries no numbered "
        f"option row and no cursor row, so it is scrollback, not a live modal")

    tmux_stub.set_captures(capture)
    server.claim_batches = [[write(text="yes, go ahead")]]
    run_agent(server, tmux_stub, tmp_path)
    assert tmux_stub.send_keys_calls() == [
        ["send-keys", "-t", "%12", "-l", "--", "yes, go ahead"],
        ["send-keys", "-t", "%12", "Enter"],
    ], f"the reply to a pane showing {what} was not delivered: {tmux_stub.send_keys_calls()}"
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "delivered", body


def test_a_menu_whose_options_carry_DESCRIPTIONS_is_a_MENU_and_is_ANSWERED(
        server, tmux_stub, tmp_path):
    """🔴 THE SHAPE EVERY REAL ASK ON THIS HOST HAS, AND THE DETECTOR WAS BLIND TO IT.

    `MENU_ROW_GAP = 4` could not span an option's own description, so `menu_block`
    returned ZERO rows for all three live modals, every one of them classified
    `text`, and the fleet sweep found not one drivable menu — a zero that read like
    "no menus were up" and was actually "the detector cannot see one".

    🔴 AND THE FIRST ATTEMPT AT THE AUDIT'S OWN FIX WOULD HAVE TYPED INTO THEM.
    Requiring a cursored block before the refusing branches is right, but with a
    block nothing could satisfy it turned three LIVE menus from `refused` into
    `text` — the original defect, restored, on the exact panes the sweep was
    supposed to be evidence about. The rule is now "no BLANK line between two
    option rows", which is what the measurement says separates a menu from an
    unrelated numbered list.
    """
    # The fixture must OVERSHOOT the old bound, or a mutant restoring it survives
    # this test without the widened rule ever executing.
    spans = [b["line"] - a["line"] for a, b in
             zip(AGENT.menu_option_rows(DESCRIBED_MENU_CAPTURE),
                 AGENT.menu_option_rows(DESCRIBED_MENU_CAPTURE)[1:])]
    assert max(spans) >= 7, (
        f"the fixture's widest inter-row span is {max(spans)}; the measured render's is 7 "
        f"and the bound this test exists to move was 4 — at {max(spans)} it proves nothing")

    kind, labels, why = AGENT.classify_pane(DESCRIBED_MENU_CAPTURE)
    assert kind == AGENT.PANE_MENU, (
        f"a real ask with described options classified as {kind} ({why}) — its option rows "
        f"are {len(AGENT.menu_block(DESCRIBED_MENU_CAPTURE))} apart-by-description rows")
    assert labels == ["Ledger", "Flatfile", "Type something.", "Chat about this"], labels

    tmux_stub.set_captures(DESCRIBED_MENU_CAPTURE, ANSWERED_CAPTURE)
    server.claim_batches = [[write(text="Flatfile")]]
    run_agent(server, tmux_stub, tmp_path)
    assert tmux_stub.send_keys_calls() == [
        ["send-keys", "-t", "%12", "-l", "--", "2"]], (
        f"a described menu was not answered by its row's digit: "
        f"{tmux_stub.send_keys_calls()}")


def test_a_DESCRIBED_multi_question_chain_is_still_REFUSED(server, tmux_stub, tmp_path):
    """The other direction of the same widening, and the one that could go wrong.

    Making the rows visible must not make a multi-question chain answerable: two of
    the three live modals measured carried a `✔ Submit` tab strip, so this is the
    majority case, and half-answering it is its own wrong answer.
    """
    assert (AGENT.classify_pane(DESCRIBED_MULTI_QUESTION_CAPTURE)[0]
            == AGENT.PANE_MULTI_QUESTION), AGENT.classify_pane(DESCRIBED_MULTI_QUESTION_CAPTURE)

    tmux_stub.set_captures(DESCRIBED_MULTI_QUESTION_CAPTURE)
    server.claim_batches = [[write(text="Flatfile")]]
    run_agent(server, tmux_stub, tmp_path)
    assert tmux_stub.send_keys_calls() == [], tmux_stub.send_keys_calls()
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "refused", body
    assert "MULTI-question" in body["detail"], body


def test_a_BLANK_line_between_two_numbered_rows_breaks_the_block():
    """🔴 WHAT THE WIDENING MUST NOT COST, pinned in both directions.

    The blank line is the signal, not the distance: an option's description never
    contains one (measured on every live modal), and two unrelated numbers on a
    pane are separated by one. So a pair six lines apart with PROSE between them is
    one block and a pair one line apart with a BLANK between them is not.
    """
    prose = ("  ❯ 1. Ledger\n"
             "     it keeps a journal\n"
             "     and replays it\n"
             "     and costs an fsync\n"
             "    2. Flatfile\n")
    assert len(AGENT.menu_block(prose)) == 2, AGENT.menu_block(prose)

    blanked = "  ❯ 1. Ledger\n\n    2. Flatfile\n"
    assert AGENT.menu_block(blanked) == [], AGENT.menu_block(blanked)

    # And MENU_ROW_GAP is still a live bound: prose longer than it does NOT join.
    far = ("  ❯ 1. Ledger\n"
           + "     more description\n" * (AGENT.MENU_ROW_GAP + 1)
           + "    2. Flatfile\n")
    assert AGENT.menu_block(far) == [], (
        f"MENU_ROW_GAP={AGENT.MENU_ROW_GAP} is not enforced, so an arbitrarily long span of "
        f"prose joins two unrelated numbers: {AGENT.menu_block(far)}")


def test_a_TASK_LIST_above_a_REAL_menu_does_not_make_it_a_multiSelect(
        server, tmux_stub, tmp_path):
    """🔴 THE CHECKBOX SIGNAL USED TO MATCH ANYWHERE IN THE CAPTURE.

    A markdown task list in the transcript above a genuine single-question ask made
    the whole pane read as a multiSelect list, so every reply to that ask was
    refused. The task list has no cursor glyph and stands nowhere near the option
    rows; the multiSelect signal is a CURSORED run of checkbox rows.
    """
    mixed = ("● Remaining:\n  - [ ] wire the store\n  - [x] read the pane\n\n"
             + SINGLE_MENU_CAPTURE)
    assert AGENT.classify_pane(mixed)[0] == AGENT.PANE_MENU, AGENT.classify_pane(mixed)

    tmux_stub.set_captures(mixed, ANSWERED_CAPTURE)
    server.claim_batches = [[write(text="Flatfile")]]
    run_agent(server, tmux_stub, tmp_path)
    assert tmux_stub.send_keys_calls() == [
        ["send-keys", "-t", "%12", "-l", "--", "2"]], (
        "a markdown task list above the menu made it unanswerable: "
        f"{tmux_stub.send_keys_calls()}")


def test_the_checkbox_ROW_pattern_requires_the_box_where_the_LABEL_would_stand():
    """The pattern's contract — and the COMBINED anchoring mutant, now killed.

    🔴 THE PREVIOUS VERSION OF THIS DOCSTRING ASSERTED A FALSEHOOD AND USED IT TO
    EXCUSE A GAP. It said the combined mutant (no `^`, `.match` -> `.search`) was
    undistinguishable by realistic data because "a mid-line `[x]` yields
    `cursor=False`", and that constructing a pane that did otherwise — it even
    named `1. Ledger ❯ [x] more` — "would assert a fiction". Measured: on the
    equivalent line for the REAL render the widened pattern matches the cursored
    group mid-line, `group(1)` is the cursor, and `cursor` is **True**. The
    counter-example it dismissed is the kill.

    🔴 AND THE FIXTURES THIS TEST PINNED WERE THEMSELVES A FICTION — the defect one
    level up. Every assertion here asserted `❯ [ ] 1. Ledger`, box before number,
    on the strength of a comment claiming that was "the measured render". It is not
    what Claude Code 2.1.232 emits: the multiSelect renderer composes
    `<cursor-or-space> {`${index}.`.padEnd(w)} {["[", tick|" ", "]"]} {label}`, i.e.
    `❯ 1. [ ] Ledger`. So the pattern matched nothing a real pane contains,
    `menu_checkbox_block` returned `[]` for every live multiSelect, and
    `PANE_MULTI_SELECT` was unreachable code with a green test over it. The name of
    this test changed with the fix, because the OLD name stated the falsehood.

    The `^`/`.match` pair really are EQUIVALENT mutants individually (with `^` and
    no MULTILINE, `.search` anchors exactly as `.match` does), and that stands. It
    is the pair dropped TOGETHER that is killable, and the assertions below kill it
    on a realistic shape: a transcript quoting a cursored checkbox row inside
    prose, which `claude/skills/session-manager/reference/waiting-signal.md`
    records as routine ("agents quote menus back at the operator all day").

    ⚠ STATED PRECISELY, BECAUSE THE SCOPE IS WHAT THE OLD VERSION GOT WRONG, AND THE
    FIGURE IS RE-MEASURED FOR THE CORRECTED ROW SPELLING RATHER THAN CARRIED FORWARD:
    a cursored numbered CHECKBOX row appearing mid-line, with text before it, occurs
    on 0 of the 58 live panes on this host — 0 in the viewport and 0 in 2000 lines of
    scrollback. So this is a realistic SHAPE, not a live capture — which is the
    honest claim, and a stronger basis than "nobody has measured it" was for
    declining to test it at all. ⚠ The same sweep found ZERO genuine multiSelect
    renders either, so the corrected row spelling rests on the RENDERER and on one
    cited capture of pane %170, not on a pane this round watched.
    """
    m = AGENT.MENU_CHECKBOX_ROW_RE
    # 🔴 THE COMBINED MUTANT, KILLED. Built here rather than described: the widened
    # pattern must NOT see a cursor where the real one sees no row at all.
    widened = re.compile(m.pattern.lstrip("^"))
    quoted_row = "● The list rendered 2. Flatfile ❯ 9. [x] Ledger"
    assert not m.match(quoted_row), (
        "the row pattern stopped requiring the row to START at the line's beginning, so a "
        "quoted cursored checkbox row inside prose now reads as a multiSelect option")
    assert widened.search(quoted_row).group(1) == AGENT.MENU_CURSOR, (
        "the widened pattern no longer finds a mid-line cursor, so this kill is stale and "
        "the docstring's claim needs re-measuring rather than trusting")
    # And end to end: the quoted pair must stay TEXT, which is what makes the kill
    # a behavioural one rather than a fact about a regex.
    quoted_pair = (quoted_row + "\n"
                   + "  and the next one as   3. [ ] Flatfile\n"
                   + "\n● Carrying on.\n")
    assert AGENT.classify_pane(quoted_pair)[0] == AGENT.PANE_TEXT, (
        "a transcript quoting a cursored checkbox row now refuses a legitimate reply: "
        f"{AGENT.classify_pane(quoted_pair)}")
    # 🔴 THE MEASURED multiSelect RENDER — NUMBER, THEN BOX. This is the assertion
    # block that was pinned to a render Claude Code does not produce: it read
    # `m.match("  ❯ [ ] 1. Ledger")`, and the shipped renderer emits
    # `❯ 1. [ ] Ledger`. Cursored and not, and both tick spellings.
    assert m.match("  ❯ 1. [ ] Ledger")
    assert m.match("    2. [x] Flatfile")
    assert m.match("  ❯ 3. [✔] Archive")
    # 🔴 AND THE FALSIFIED RENDER MUST NOT MATCH ANY MORE. Keeping it would leave the
    # pattern accepting a shape no renderer emits, which is where the mid-line
    # `❯ [x]` false positive came from.
    assert not m.match("  ❯ [ ] 1. Ledger"), (
        "the box-BEFORE-number spelling is back; no renderer in Claude Code 2.1.232 "
        "emits it and accepting it re-opens the quoted-row false positive")
    # A markdown task list is NOT row-shaped under this pattern — it has no number —
    # which is a second, independent reason it cannot be a multiSelect. The cursor
    # requirement in `menu_checkbox_block` is the first.
    assert not m.match("  - [ ] wire the store")
    # An ORDERED markdown checklist IS row-shaped; the cursor is what excludes it,
    # and `menu_checkbox_block` is where that lives, not here.
    assert m.match("  1. [ ] wire the store")
    # A checkbox that is NOT where the label would stand.
    assert not m.match("    2. Flatfile [x] (current)")
    assert not m.match("devrc $ grep -c '[ ]' TODO.md")
    assert not m.match("  ❯ 1. Ledger [x]")
    # And the cursor group is what `menu_checkbox_block` reads.
    assert m.match("  ❯ 1. [ ] Ledger").group(1) == AGENT.MENU_CURSOR
    assert m.match("    2. [ ] Flatfile").group(1) is None


def test_an_option_LABEL_containing_a_checkbox_does_not_refuse_the_menu():
    """The same signal, reached the other way: `[x]` inside a row's own label."""
    labelled = SINGLE_MENU_CAPTURE.replace(
        "    2. Flatfile\n", "    2. Flatfile [x] (current)\n")
    kind, labels, why = AGENT.classify_pane(labelled)
    assert kind == AGENT.PANE_MENU, (kind, why)
    assert labels[1] == "Flatfile [x] (current)", labels


def test_a_REAL_multiSelect_list_is_STILL_refused_and_the_CURSOR_is_why():
    """🔴 THE OTHER DIRECTION OF THE SAME CHANGE, PINNED SO IT CANNOT DRIFT.

    Narrowing the checkbox signal must not reopen the hole it closes: a live
    multiSelect list is still refused.

    🔴 AND THE REASON THIS TEST USED TO GIVE WAS FALSE, WHICH IS WHY IT PASSED OVER
    A DEAD GUARD. It asserted `menu_block(...) == []` "because the checkbox stands
    before the number", making the checkbox signal "the ONLY thing standing between
    a multiSelect ask and a blind type-and-Enter". The real row is `❯ 1. [ ] Ledger`,
    which `MENU_OPTION_RE` matches with `label="[ ] Ledger"` — so `menu_block` sees
    a multiSelect perfectly well, and the old assertion was green only because the
    fixture was an invented render. RED at `a324b00e` with the fixture corrected.

    ⚠ SO WHAT ACTUALLY KEPT A MULTISELECT SAFE BEFORE THIS FIX WAS AN ACCIDENT, NOT
    A GUARD: the parsed labels carry the box (`[ ] Ledger`), so an operator's reply
    of `Ledger` matched no row, and `[ ] Type something` is not a recognised
    free-text row either — the pane was refused by `deliver_to_menu` with "the reply
    matches none of the menu's N option rows", a true outcome with a wrong reason.
    The refusal is now on purpose and says multi-select.

    ⚠ THE RESIDUAL THIS TEST ALSO STATES: the cursor glyph is load-bearing for the
    CHECKBOX block. Strip it and the pane falls back to the numbered block, which
    does see these rows — so it reads as an unknown/quoted menu rather than a
    multiSelect. A live Claude Code modal always draws the cursor (`waiting-signal.md`
    is the repo's measured live-modal signal), so what is lost is a precise REASON on
    an UNFOCUSED checkbox list, not the refusal itself.
    """
    assert AGENT.classify_pane(MULTI_SELECT_MENU_CAPTURE)[0] == AGENT.PANE_MULTI_SELECT
    # 🔴 THE CORRECTED FACT, ASSERTED SO THE OLD ONE CANNOT COME BACK: the numbered
    # block DOES see a multiSelect, and its labels carry the box.
    numbered = AGENT.menu_block(MULTI_SELECT_MENU_CAPTURE)
    assert [r["label"] for r in numbered] == [
        "[ ] Ledger", "[ ] Flatfile", "[✔] Archive"], numbered
    # …which is exactly why the checkbox block has to win. Without it this pane is a
    # cursored numbered menu under a single-question ask footer, i.e. `menu`.
    assert AGENT.menu_footer(MULTI_SELECT_MENU_CAPTURE) == AGENT.FOOTER_SINGLE
    assert AGENT.MENU_SUBMIT_TAB not in MULTI_SELECT_MENU_CAPTURE, (
        "the fixture grew a `✔ Submit`, so `multi` would refuse this pane and the "
        "checkbox block would stop being what this test measures")

    # 🔴 THE INDEPENDENT CLIPPING CASE, which is what the checkbox block reaches and
    # the numbered one cannot: row 1 has scrolled out, so no run starts at 1. The
    # cursor moves onto a VISIBLE row, which is what a real scrolled render shows —
    # and the checkbox block needs it, so constructing this by deleting row 1 alone
    # would have measured the cursor requirement instead of the clipping.
    clipped = MULTI_SELECT_MENU_CAPTURE.replace(
        "  ❯ 1. [ ] Ledger\n", "").replace("    2. [x]", "  ❯ 2. [x]").replace(
        "    2. [ ]", "  ❯ 2. [ ]")
    assert AGENT.menu_block(clipped) == [], AGENT.menu_block(clipped)
    assert AGENT.classify_pane(clipped)[0] == AGENT.PANE_MULTI_SELECT, (
        "a multiSelect whose first row is clipped is no longer refused as one: "
        f"{AGENT.classify_pane(clipped)}")

    uncursored = MULTI_SELECT_MENU_CAPTURE.replace("  ❯ 1. [ ]", "    1. [ ]")
    assert AGENT.classify_pane(uncursored)[0] != AGENT.PANE_MENU, (
        "the stated residual has changed — an uncursored checkbox list is being "
        f"driven by digit: {AGENT.classify_pane(uncursored)}")

    # And a single cursored checkbox row is not a list: MENU_MIN_OPTIONS applies
    # here exactly as it does to the numbered block.
    lone = "● Noting one item:\n  ❯ 1. [ ] wire the store\n\ndevrc git:(main) $ \n"
    assert AGENT.classify_pane(lone)[0] == AGENT.PANE_TEXT, AGENT.classify_pane(lone)


# --------------------------------------------------------------------------- #
# 16i. A reply that names a row EXACTLY has a route even with no free-text row.
# --------------------------------------------------------------------------- #
def test_a_reply_naming_a_row_EXACTLY_is_delivered_with_NO_free_text_row(
        server, tmux_stub, tmp_path):
    """🔴 THE PRECONDITION THAT REFUSED THIS WAS BOTH REDUNDANT AND WRONG.

    `classify_pane` used to refuse a cursored numbered menu carrying no recognised
    free-text row BEFORE any label matching happened, with the reason "a reply that
    matches no option has no route" — a statement about a reply it had not looked
    at. This reply names row 2 exactly. The row is on screen; its digit answers it.
    """
    tmux_stub.set_captures(NO_FREE_TEXT_MENU_CAPTURE, ANSWERED_CAPTURE)
    server.claim_batches = [[write(text="Flatfile")]]
    run_agent(server, tmux_stub, tmp_path)
    assert tmux_stub.send_keys_calls() == [
        ["send-keys", "-t", "%12", "-l", "--", "2"]], (
        "an exactly-matching reply was refused because the menu offers no free-text row: "
        f"{tmux_stub.send_keys_calls()}")
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "delivered", body


def test_a_reply_matching_NOTHING_with_no_free_text_row_is_refused_BY_THE_DELIVERY(
        server, tmux_stub, tmp_path):
    """The pair to the test above, and what makes deleting the precondition safe.

    The refusal still happens — it just happens at the point where it is TRUE, and
    it names the count it measured. `0 free-text rows` is reachable only because the
    precondition is gone.
    """
    tmux_stub.set_captures(NO_FREE_TEXT_MENU_CAPTURE)
    server.claim_batches = [[write(text="neither, use the ledger for now")]]
    run_agent(server, tmux_stub, tmp_path)
    assert tmux_stub.send_keys_calls() == [], tmux_stub.send_keys_calls()
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "refused", body
    assert "offers 0 free-text rows" in body["detail"], body


# --------------------------------------------------------------------------- #
# 16j. 🔴 `ctrl+g` ANYWHERE IS NOT FOCUS — the FOURTH instance of this PR's own
#      defect, and the two clauses that shielded each other from the mutants.
# --------------------------------------------------------------------------- #
#: The free-text row UNFOCUSED (cursor on row 1) while `ctrl+g` is on screen for an
#: unrelated reason. The shipped guard substring-matched the whole capture, so it
#: called this focused, typed the reply into an unfocused menu, pressed Enter, and
#: reported `delivered` — "typed the reply into menu row 3 and committed it".
CTRL_G_BUT_UNFOCUSED_CAPTURE = SINGLE_MENU_FREE_TEXT_FOCUSED_CAPTURE.replace(
    "    1. Ledger\n", "  ❯ 1. Ledger\n").replace(
    "  ❯ 3. Type something.\n", "    3. Type something.\n")


def test_ctrl_g_ANYWHERE_is_not_the_free_text_row_TAKING_FOCUS(
        server, tmux_stub, tmp_path):
    """🔴 RED AT 18cfe7df: the reply was typed and Enter pressed into an UNFOCUSED
    menu, and the agent reported `delivered`.

    The capture carries `ctrl+g to edit in Nvim` in its footer and the cursor on
    ROW 1 — the state that exists whenever the hint is drawn for its own reasons.
    The only correct outcome is the digit alone, no text, no Enter, and `failed`.
    """
    assert "ctrl+g" in CTRL_G_BUT_UNFOCUSED_CAPTURE, "the fixture lost its hint"
    rows = AGENT.menu_block(CTRL_G_BUT_UNFOCUSED_CAPTURE)
    assert [r["cursor"] for r in rows] == [True, False, False, False], rows

    tmux_stub.set_captures(CTRL_G_BUT_UNFOCUSED_CAPTURE)   # repeats for ever
    server.claim_batches = [[write(text="neither, use the ledger for now")]]
    run_agent(server, tmux_stub, tmp_path)

    assert tmux_stub.send_keys_calls() == [
        ["send-keys", "-t", "%12", "-l", "--", "3"]], (
        "`ctrl+g` somewhere in the pane was read as the free-text row taking focus, so "
        "the reply was typed into an unfocused menu and the Enter answered whichever row "
        f"is highlighted: {tmux_stub.send_keys_calls()}")
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "failed", body
    assert "did not take focus" in body["detail"], body
    assert "Enter was NOT pressed" in body["detail"], body


def test_the_focus_predicate_reads_the_CURSOR_ON_THAT_ROW_and_nothing_else(monkeypatch):
    """The predicate driven directly, both directions, with the hint present in BOTH.

    🔴 WHY THIS EXISTS AS WELL AS THE AGENT-RUN TEST ABOVE. The two clauses of the
    old `or` shielded each other from mutation: deleting either one left the whole
    suite green at `149 passed`, because the measured focused render satisfies both.
    With the hint clause gone the cursor clause is the only one there is, and this
    pins it against a capture where the hint would answer differently.
    """
    calls = {"n": 0}

    def capture_of(text):
        def fake(pane):
            calls["n"] += 1
            return text, ""
        return fake

    monkeypatch.setattr(AGENT, "MENU_SETTLE_SECONDS", 0.0)
    monkeypatch.setattr(AGENT, "MENU_VERIFY_ATTEMPTS", 2)

    monkeypatch.setattr(AGENT, "capture_pane",
                        capture_of(SINGLE_MENU_FREE_TEXT_FOCUSED_CAPTURE))
    assert AGENT.menu_free_text_focused("%12", 2) is True, (
        "the cursor on row 3 is the focus signal and it was not read")

    monkeypatch.setattr(AGENT, "capture_pane",
                        capture_of(CTRL_G_BUT_UNFOCUSED_CAPTURE))
    assert AGENT.menu_free_text_focused("%12", 2) is False, (
        "a capture carrying `ctrl+g` with the cursor on row 1 was called focused")
    assert calls["n"] >= 3, f"the predicate never read the pane: {calls}"


# --------------------------------------------------------------------------- #
# 16k. 🔴 A WRAPPED ROW — the third instance, inside the guard written for the
#      second. A reply too long for the row renders with NO truncation mark.
# --------------------------------------------------------------------------- #
def test_a_reply_WRAPPED_in_its_row_is_not_reported_delivered(server, tmux_stub, tmp_path):
    """🔴 RED AT 18cfe7df, reporting `delivered` for an uncommitted reply.

    `menu_committed` asked `label_matches`, which REQUIRES a truncation mark before
    it will prefix-match — correct for deciding which row to press, and wrong for
    deciding whether the reply is still sitting in one. A row too narrow for the
    reply renders as `N. <head>` with no mark at all, so nothing matched, the
    function concluded the reply had left the menu, and the card said `Delivered`.
    """
    long_reply = "neither of those, keep the ledger until the migration lands"
    wrapped = SINGLE_MENU_FREE_TEXT_FOCUSED_CAPTURE.replace(
        "  ❯ 3. Type something.\n", "  ❯ 3. neither of those, keep the led\n")
    assert "…" not in wrapped and "..." not in wrapped, "the fixture is the MARKED case"

    tmux_stub.set_captures(SINGLE_MENU_CAPTURE,
                           SINGLE_MENU_FREE_TEXT_FOCUSED_CAPTURE,
                           wrapped)   # repeats for ever: Enter never took
    server.claim_batches = [[write(text=long_reply)]]
    run_agent(server, tmux_stub, tmp_path)

    assert tmux_stub.send_keys_calls() == [
        ["send-keys", "-t", "%12", "-l", "--", "3"],
        ["send-keys", "-t", "%12", "-l", "--", long_reply],
        ["send-keys", "-t", "%12", "Enter"],
    ], tmux_stub.send_keys_calls()
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "failed", (
        "a reply wrapped into its row read as committed, so an uncommitted answer was "
        f"reported as delivered: {body}")
    assert "still sitting in a menu row" in body["detail"], body


def test_the_readback_predicate_is_WIDER_than_the_row_MATCHING_one():
    """The two predicates, side by side, because folding them would be the bug.

    `label_matches` decides which row to PRESS — an unmarked prefix match there
    would select a short row for a longer, different reply. `menu_row_holds_reply`
    decides whether a reply is still SITTING in a row, where the same looseness is
    the safe direction: a false positive costs a `failed` on a good delivery.
    """
    reply = "neither of those, keep the ledger until the migration lands"
    head = "neither of those, keep the led"

    assert not AGENT.label_matches(head, reply), (
        "label_matches now prefix-matches WITHOUT a truncation mark, so a short option "
        "row can be selected for a longer, different reply")
    assert AGENT.menu_row_holds_reply(head, reply)
    assert AGENT.menu_row_holds_reply(reply, reply)
    assert AGENT.menu_row_holds_reply("neither of those, keep the led…", reply)

    # Too short to be evidence of anything, and an unrelated row.
    assert not AGENT.menu_row_holds_reply("neither", reply)
    assert not AGENT.menu_row_holds_reply("Ledger", reply)
    # A row LONGER than the reply is not a rendering of it — the reply cannot be a
    # wrap of something longer than itself.
    assert not AGENT.menu_row_holds_reply(reply + " and more", reply)


def test_a_committed_reply_is_not_read_off_a_pane_that_KEEPS_CHANGING(
        server, tmux_stub, tmp_path):
    """🔴 `menu_committed` CONCLUDES FROM AN ABSENCE TOO, so it reads it twice.

    Its verdict is "no row holds the reply", and a half-drawn frame shows no rows
    at all — so one read of a pane mid-redraw reported `delivered` for a reply that
    was still sitting in its row on the very next frame. The fixture alternates
    between the reply sitting in row 3 and a pane with no rows, so no consecutive
    pair of reads ever agrees that the reply has gone.
    """
    reply = "neither, use the ledger for now"
    typed = SINGLE_MENU_FREE_TEXT_FOCUSED_CAPTURE.replace(
        "  ❯ 3. Type something.\n", f"  ❯ 3. {reply}\n")
    tmux_stub.set_captures(SINGLE_MENU_CAPTURE,
                           SINGLE_MENU_FREE_TEXT_FOCUSED_CAPTURE,
                           *([ANSWERED_CAPTURE, typed] * 6))
    server.claim_batches = [[write(text=reply)]]
    run_agent(server, tmux_stub, tmp_path)

    assert tmux_stub.send_keys_calls() == [
        ["send-keys", "-t", "%12", "-l", "--", "3"],
        ["send-keys", "-t", "%12", "-l", "--", reply],
        ["send-keys", "-t", "%12", "Enter"],
    ], tmux_stub.send_keys_calls()
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "failed", (
        "one frame showing no menu rows was read as the reply having been committed: "
        f"{body}")
    assert "still sitting in a menu row" in body["detail"], body


# --------------------------------------------------------------------------- #
# 16l. 🔴 `delivered` MUST NOT BE READ OFF A HALF-DRAWN FRAME, and the detail must
#      not claim a selection nothing measured.
# --------------------------------------------------------------------------- #
def test_a_pane_that_never_HOLDS_STILL_is_reported_UNKNOWN_not_delivered(
        server, tmux_stub, tmp_path):
    """🔴 THE READ-BACK USED TO RETURN TRUE ON THE FIRST NON-MATCHING READ.

    Its verdict rests on NOT seeing the menu, and that is also what a half-drawn
    frame looks like — so a pane whose state never settles reported `delivered`,
    while the SAME frame at classify time is one this agent would type into. Two
    ends of one delivery, two answers.

    The fixture alternates between two states that are each not the original menu
    and never equal to each other, so no pair of consecutive reads ever agrees.
    """
    other = SINGLE_MENU_CAPTURE.replace("Ledger", "Postgres").replace("Flatfile", "SQLite")
    flicker = [ANSWERED_CAPTURE, other] * 5
    tmux_stub.set_captures(SINGLE_MENU_CAPTURE, *flicker)
    server.claim_batches = [[write(text="Flatfile")]]
    run_agent(server, tmux_stub, tmp_path)

    assert tmux_stub.send_keys_calls() == [
        ["send-keys", "-t", "%12", "-l", "--", "2"]], (
        f"the agent re-sent the answer: {tmux_stub.send_keys_calls()}")
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "failed", (
        "a pane that never held one state long enough to be read twice was reported as a "
        f"delivery: {body}")
    assert "never held one state" in body["detail"], body
    assert "UNKNOWN" in body["detail"], body
    assert "nothing was re-sent" in body["detail"], body


def test_the_delivered_detail_says_what_was_OBSERVED_not_that_a_row_was_SELECTED(
        server, tmux_stub, tmp_path):
    """🔴 EVERY SENTENCE IN AN AUDIT ROW IS A CLAIM.

    The old detail read `selected menu row 2 by its digit` — a selection nothing on
    this path measures. What the read-back can vouch for is what it SAW, and the
    strongest thing it can see is the pane's own record of the answer.
    """
    tmux_stub.set_captures(SINGLE_MENU_CAPTURE, ANSWERED_CAPTURE)
    server.claim_batches = [[write(text="Flatfile")]]
    run_agent(server, tmux_stub, tmp_path)
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "delivered", body
    assert body["detail"] == (
        "menu row 2 was sent and the pane records this option as the answer"), body

    # The same delivery with the record out of the viewport: the weaker claim, and
    # it says exactly what it saw rather than borrowing the stronger one.
    tmux_stub.reset()
    server.requests.clear()
    tmux_stub.set_captures(SINGLE_MENU_CAPTURE, DEFAULT_PROMPT_CAPTURE)
    server.claim_batches = [[write(text="Flatfile")]]
    run_agent(server, tmux_stub, tmp_path)
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "delivered", body
    assert body["detail"] == "menu row 2 was sent and the menu is no longer on screen", body

    # And a record naming a DIFFERENT option is a wrong answer caught red-handed.
    tmux_stub.reset()
    server.requests.clear()
    wrong = ANSWERED_CAPTURE.replace("→ Flatfile", "→ Ledger")
    tmux_stub.set_captures(SINGLE_MENU_CAPTURE, wrong)
    server.claim_batches = [[write(text="Flatfile")]]
    run_agent(server, tmux_stub, tmp_path)
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "failed", body
    assert "records a DIFFERENT option" in body["detail"], body


def test_a_record_that_APPEARS_while_THIS_menu_is_still_up_is_not_this_asks(monkeypatch):
    """🔴 F4a: THE ORDERING `verdict = … if seen != want` IS LOAD-BEARING, AND NOTHING
    PINNED IT. An independent mutation sweep dropped the `seen != want` clause and the
    mutant SURVIVED the whole suite, while the comment beside it called the ordering
    "load-bearing rather than tidy". A comment is a claim; this is the measurement.

    THE FRAME: our menu is still demonstrably on screen, and the bottom-most answered
    record has CHANGED since the pre-keypress read — which the baseline alone cannot
    rule out, because the transcript ABOVE a live modal keeps rendering and an earlier
    ask's record can finish drawing there. Without the ordering clause that record is
    attributed to THIS ask and named as a DIFFERENT option, so a delivery that has not
    happened yet is reported `failed` with a false explanation.

    ⚠ THE MUTANT THIS KILLS, EXACTLY: `verdict = menu_answer_verdict(captured, text,
    before) if text else ""` — i.e. the same line with `and seen != want` removed. It
    dies on this test's own assertion message, not on a neighbouring guard's.
    """
    monkeypatch.setattr(AGENT, "MENU_SETTLE_SECONDS", 0.0)
    monkeypatch.setattr(AGENT, "MENU_VERIFY_ATTEMPTS", 4)
    labels = ["Ledger", "Flatfile", "Type something.", "Chat about this"]

    # An earlier ask's record finishes drawing ABOVE the live menu. `menu_block` is
    # bottom-most, so the pane still classifies as the SAME menu with the SAME labels.
    someone_elses = ANSWERED_CAPTURE.replace("→ Flatfile", "→ Postgres")
    frame = someone_elses + SINGLE_MENU_CAPTURE

    kind, seen_labels, _ = AGENT.classify_pane(frame)
    assert (kind, seen_labels) == (AGENT.PANE_MENU, labels), (
        "the fixture no longer shows THIS menu, so it cannot measure the ordering: "
        f"{(kind, seen_labels)}")
    assert AGENT.menu_answer_records(frame) == ["Postgres"], (
        "the fixture no longer carries a record that differs from the baseline, so the "
        "`before` difference would not fire and this test would pass vacuously: "
        f"{AGENT.menu_answer_records(frame)}")

    def fake(pane):
        return frame, ""
    monkeypatch.setattr(AGENT, "capture_pane", fake)

    # The baseline is EMPTY, so `Postgres` genuinely is new -- and must still not be
    # read as the answer to an ask whose menu is right there on screen.
    assert AGENT.menu_settled("%12", labels, "Flatfile", []) == (
        False, "the menu did not move"), (
        "a record that appeared while THIS menu is still on screen is being attributed "
        "to this ask, so a delivery that has not happened yet reports a DIFFERENT "
        f"option: {AGENT.menu_settled('%12', labels, 'Flatfile', [])}")


def test_a_record_seen_while_our_menu_was_UP_is_not_half_of_an_AGREEING_PAIR(monkeypatch):
    """🔴 F4, THE SECOND HALF: THE `if seen != want else None` ON THE **`new`** LINE.

    The comment beside those two lines claimed the ordering was "load-bearing rather
    than tidy" and named ONE test as pinning it — and that test pins the `verdict`
    line's `and seen != want` only. An independent mutation sweep dropped the clause
    from the `new` line instead and SURVIVED the whole 195-test suite. Reproduced here
    before writing this: the mutant
    `new = menu_answer_records_for_this_ask(captured, before)` scored 195 passed with a
    sentinel mutant in the same batch KILLED (32 failed), so the sweep could see a
    difference and there was none to see.

    ⚠ THE COMMENT WAS A COVERAGE CLAIM WIDER THAN ITS IMPLEMENTATION — the exact shape
    RULES.md names. Two lines, one pin, a docstring naming both.

    THE CONSEQUENCE, WHICH IS WHY THIS IS A BEHAVIOURAL TEST AND NOT A STRUCTURAL ONE:
    without the clause, a record that appeared while OUR menu was still up is carried
    into `prev_new`, and the very next read — our menu gone, our OWN record not drawn
    yet, the stale one still bottom-most — finds `new == prev_new` and fires the
    MISMATCH arm. A correct delivery is then reported `failed` with
    "the pane records a DIFFERENT option as the answer to this ask". MEASURED: the
    mutant returns exactly `(False, 'the pane records a DIFFERENT option as the answer
    to this ask')` here, against `(True, 'the menu is no longer on screen')` at HEAD.

    ⚠ AND THE DELIVERED DETAIL HERE IS THE WEAKER ONE ON PURPOSE. The record that
    finally appears is ours, but it has not been read twice by the time the pane has
    been, so the absence rule is what answers — see
    `test_the_UPGRADE_needs_the_RECORD_read_twice_not_only_the_PANE`.
    """
    monkeypatch.setattr(AGENT, "MENU_SETTLE_SECONDS", 0.0)
    monkeypatch.setattr(AGENT, "MENU_VERIFY_ATTEMPTS", 6)
    labels = ["Ledger", "Flatfile", "Type something.", "Chat about this"]

    # An EARLIER ask's record, finishing after the baseline was taken.
    stale = ANSWERED_CAPTURE.replace("→ Flatfile", "→ Postgres")
    frames = [
        stale + SINGLE_MENU_CAPTURE,   # our menu still up, the stale record present
        stale,                         # our menu gone, our own record not drawn yet
        stale + ANSWERED_CAPTURE,      # our record finally arrives, bottom-most
    ]
    # Fixture controls: if any of these three stops holding, the sequence no longer
    # exercises the ordering and the test would pass for the wrong reason.
    assert AGENT.classify_pane(frames[0])[:2] == (AGENT.PANE_MENU, labels), (
        "frame 1 no longer shows THIS menu, so `seen == want` never holds and the "
        f"ordering clause is never consulted: {AGENT.classify_pane(frames[0])[:2]}")
    assert AGENT.menu_answer_records(frames[0]) == ["Postgres"], (
        "frame 1 no longer carries a record differing from the baseline, so the "
        f"`before` difference would not fire: {AGENT.menu_answer_records(frames[0])}")
    assert AGENT.menu_answer_records(frames[1]) == ["Postgres"], (
        "frame 2 must carry the SAME stale record as frame 1 — that identity is what "
        f"the mutant reads as an agreeing pair: {AGENT.menu_answer_records(frames[1])}")
    assert AGENT.menu_answer_records(frames[2]) == ["Flatfile"], (
        f"frame 3 no longer records OUR answer: {AGENT.menu_answer_records(frames[2])}")

    seq = list(frames)

    def fake(pane):
        return (seq.pop(0) if len(seq) > 1 else seq[0]), ""
    monkeypatch.setattr(AGENT, "capture_pane", fake)

    # ⚠ CAPTURED ONCE, NOT RE-CALLED IN THE MESSAGE. `fake` CONSUMES the frame list, so
    # a second call inside the f-string reads a different sequence and prints a value
    # the assertion never saw — which is how a failure message comes to disagree with
    # its own assertion. (Measured while writing this: the re-call printed
    # `(True, 'the pane records this option as the answer')` for a mutant that had
    # actually returned the accusation.)
    got = AGENT.menu_settled("%12", labels, "Flatfile", [])
    assert got == (True, "the menu is no longer on screen"), (
        "a record read while OUR menu was still on screen is being used as half of the "
        "agreeing pair the mismatch arm requires, so a correct delivery is reported as "
        f"answering a DIFFERENT option: {got}")


def test_the_UPGRADE_needs_the_RECORD_read_twice_not_only_the_PANE(monkeypatch):
    """🔴 F2/F3: THE MATCH ARM RESTED ON **PANE** STABILITY, WHICH IS THE WRONG ONE.

    `menu_settled`'s own docstring says so, about the arm beside it: "the classification
    is identical across both halves of a half-drawn record, so the stability that
    matters is the RECORD's, not the pane kind's". The MISMATCH arm required
    `new == prev_new`; the MATCH arm required only `seen == prev` — so the strongest
    claim this function emits, `the pane records this option as the answer`, could be
    made off ONE read of the record while the accusation beside it needed two.

    THE FRAME: the pane is stable across both reads (same kind, same labels) and the
    record BLOCK is still filling in — a chain's second answer arriving. Both reads say
    `match`, so no accusation is at stake; what is at stake is whether the strong
    detail is emitted off a record nobody watched hold still.

    ⚠ BOTH OUTCOMES ARE `delivered`, AND THAT IS THE POINT RATHER THAN A WEAKNESS. The
    fix costs no delivery — it costs the CLAIM. A record that fails the second read
    falls through to the absence rule, which says what it saw ("the menu is no longer
    on screen") instead of asserting a measurement it made once.
    """
    monkeypatch.setattr(AGENT, "MENU_SETTLE_SECONDS", 0.0)
    monkeypatch.setattr(AGENT, "MENU_VERIFY_ATTEMPTS", 6)
    labels = ["Ledger", "Flatfile", "Type something.", "Chat about this"]

    one = ANSWERED_CAPTURE
    two = ANSWERED_CAPTURE.replace(
        "  ⎿  · Which store should the queue use? → Flatfile\n",
        "  ⎿  · Which store should the queue use? → Flatfile\n"
        "     · Which region? → us-east\n")
    # Fixture controls: the pane must be STABLE while the record CHANGES, and both
    # reads must say `match` — otherwise this measures the mismatch arm instead.
    assert AGENT.classify_pane(one)[:2] == AGENT.classify_pane(two)[:2], (
        "the two frames classify differently, so `seen == prev` never holds and the "
        "match arm is never reached")
    assert AGENT.menu_answer_records(one) == ["Flatfile"], AGENT.menu_answer_records(one)
    assert AGENT.menu_answer_records(two) == ["Flatfile", "us-east"], (
        AGENT.menu_answer_records(two))
    assert (AGENT.menu_answer_verdict(one, "Flatfile", [])
            == AGENT.menu_answer_verdict(two, "Flatfile", []) == "match"), (
        "both frames must hold the reply, or this test is measuring the mismatch arm")

    seq = [one, two]

    def fake(pane):
        return (seq.pop(0) if len(seq) > 1 else seq[0]), ""
    monkeypatch.setattr(AGENT, "capture_pane", fake)

    # ⚠ CAPTURED ONCE — `fake` consumes `seq`, so re-calling in the message would print
    # a value the assertion never saw.
    got = AGENT.menu_settled("%12", labels, "Flatfile", [])
    assert got == (True, "the menu is no longer on screen"), (
        "the strongest claim this function makes — `the pane records this option as "
        "the answer` — is being emitted off a record that was read ONCE, while the "
        f"accusation arm beside it requires two agreeing reads: {got}")

    # 🔴 AND THE POSITIVE CONTROL: with the record STABLE across both reads the upgrade
    # must still be reached, or the fix has simply deleted the strong claim.
    seq[:] = [two]
    got = AGENT.menu_settled("%12", labels, "Flatfile", [])
    assert got == (True, "the pane records this option as the answer"), (
        "a record that IS stable across two reads no longer reaches the upgrade, so "
        f"the strong claim is now unreachable rather than better-founded: {got}")


def test_the_settle_verdict_is_driven_directly_through_EVERY_outcome(monkeypatch):
    """Each verdict with its own observed string, so none can be reached by accident.

    🔴 THE TWO FALSE ONES ARE DIFFERENT FACTS AND MUST NOT SHARE A SENTENCE. "the
    menu did not move" says the ask is still up; "never held one state" says the
    pane could not be read consistently at all. An operator acts differently on
    each, and a single wording would have hidden the second behind the first — which
    is how the half-drawn frame went unnoticed.

    ⚠ AND THE `text` ARGUMENT IS WHAT SEPARATES THE ABSENCE RULE FROM THE RECORD
    RULE, so it is driven both ways here: omitted (the old behaviour, absence only)
    and supplied (the record is consulted). A call with no reply to compare against
    must not gain a refusal it cannot justify.

    🔴 AND SO IS `before`, THE PRE-KEYPRESS RECORD SET — the arms that read the
    answered render are claims about THIS ask, and a bottom-most block is not
    evidence of that. Without a baseline they fired on STALE rows in BOTH directions,
    and both are driven below as regressions rather than described.
    """
    monkeypatch.setattr(AGENT, "MENU_SETTLE_SECONDS", 0.0)
    monkeypatch.setattr(AGENT, "MENU_VERIFY_ATTEMPTS", 4)
    labels = ["Ledger", "Flatfile", "Type something.", "Chat about this"]
    other = SINGLE_MENU_CAPTURE.replace("Ledger", "Postgres").replace("Flatfile", "SQLite")

    def feed(*records):
        seq = list(records)

        def fake(pane):
            return (seq.pop(0) if len(seq) > 1 else seq[0]), ""
        monkeypatch.setattr(AGENT, "capture_pane", fake)

    feed(ANSWERED_CAPTURE)
    assert AGENT.menu_settled("%12", labels) == (
        True, "the menu is no longer on screen")
    # With the reply AND a baseline supplied, the SAME frame yields the stronger
    # claim. `[]` is a real baseline: the pane carried no record before the keypress.
    assert AGENT.menu_settled("%12", labels, "Flatfile", []) == (
        True, "the pane records this option as the answer")
    # 🔴 F3 REGRESSION — A STALE RECORD IS NOT THIS ASK'S ANSWER. Same frame, but the
    # record was ALREADY on screen before the keypress, so this ask produced none.
    # The previous round returned `delivered` + "the pane records this option as the
    # answer" here, which is a false measurement claim about a row it did not watch
    # appear. The honest verdict is the weaker one.
    assert AGENT.menu_settled("%12", labels, "Flatfile", ["Flatfile"]) == (
        True, "the menu is no longer on screen")
    # 🔴 AND WITH NO BASELINE AT ALL THERE IS NO UPGRADE EITHER — `None` means
    # attribution is impossible, which is not the same fact as an empty record set.
    assert AGENT.menu_settled("%12", labels, "Flatfile") == (
        True, "the menu is no longer on screen")

    # A record naming another option is a mismatch — now on TWO AGREEING READS of the
    # same attributable record, not one.
    feed(ANSWERED_CAPTURE.replace("→ Flatfile", "→ Ledger"))
    assert AGENT.menu_settled("%12", labels, "Flatfile", []) == (
        False, "the pane records a DIFFERENT option as the answer to this ask")
    # 🔴 F2 REGRESSION — THE FALSE ACCUSATION. The ask has closed and its own record
    # has not been drawn yet, while a record from an EARLIER ask in the same pane is
    # still bottom-most. The previous round read that one snapshot and reported
    # `failed` with "the pane records a DIFFERENT option as the answer to this ask" —
    # a correct delivery accused of answering the wrong thing. Nothing about this
    # frame is attributable, so nothing may be claimed about it.
    stale = ANSWERED_CAPTURE.replace("→ Flatfile", "→ Ledger")
    assert AGENT.menu_settled("%12", labels, "Flatfile", ["Ledger"]) == (
        True, "the menu is no longer on screen"), (
        "a stale record from an earlier ask is being read as THIS ask's answer")
    assert AGENT.menu_answer_records(stale) == ["Ledger"], (
        "the F2 fixture no longer carries the stale record it is built around")
    # 🔴 F4b REGRESSION — ONE READ IS NOT ENOUGH, BECAUSE THE RECORD RENDERS
    # PROGRESSIVELY. A wrapped answer caught half-drawn reads SHORT, does not hold the
    # reply, and is indistinguishable from a wrong answer on a single frame. The
    # verdict must be a DELIVERY, not the accusation the first frame alone would have
    # produced.
    # ⚠ AND THE DETAIL IS THE WEAKER ONE, WHICH IS A CHANGE FROM THE ROUND THAT ADDED
    # THIS LINE. It used to assert `the pane records this option as the answer` here.
    # The match arm now needs the RECORD read twice as well (the mismatch arm always
    # did), and in this sequence the record differs between the only two reads the pane
    # holds still for — so control falls through to the absence rule, which says what
    # it saw. Both outcomes are `delivered`; what moved is the strength of the claim,
    # and the claim that was being made here was not measured. See
    # test_the_UPGRADE_needs_the_RECORD_read_twice_not_only_the_PANE.
    half = ANSWERED_CAPTURE.replace("→ Flatfile", "→ Flat")
    full = ANSWERED_CAPTURE.replace("→ Flatfile", "→ Flatfile with the journal")
    assert AGENT.menu_answer_verdict(half, "Flatfile with the journal", []) == "mismatch", (
        "the half-drawn fixture no longer looks like a wrong answer, so this "
        "regression is measuring nothing")
    feed(half, full)
    assert AGENT.menu_settled(
        "%12", labels, "Flatfile with the journal", []) == (
        True, "the menu is no longer on screen"), (
        "a half-drawn answered record is being reported as a DIFFERENT option")

    feed(other)
    assert AGENT.menu_settled("%12", labels) == (
        False, "a different menu is now on screen and the pane records no answer to this "
               "one, which is also what answering the first question of a CHAIN looks like")
    # The record turns the same frame into a delivery.
    feed(ANSWERED_CAPTURE + other)
    assert AGENT.menu_settled("%12", labels, "Flatfile", []) == (
        True, "the pane records this option as the answer")

    feed(SINGLE_MENU_CAPTURE)
    assert AGENT.menu_settled("%12", labels) == (False, "the menu did not move")

    feed(ANSWERED_CAPTURE, other, ANSWERED_CAPTURE, other)
    assert AGENT.menu_settled("%12", labels) == (
        False, "the pane never held one state long enough to be read twice")

    # An UNREADABLE read is not an observation, so it cannot be half of an
    # agreeing pair — two `gone` reads separated by one error must not settle on
    # the strength of the pair straddling it.
    reads = {"n": 0}

    def flaky(pane):
        reads["n"] += 1
        return ("", "tmux exited 1") if reads["n"] == 2 else (ANSWERED_CAPTURE, "")
    monkeypatch.setattr(AGENT, "capture_pane", flaky)
    monkeypatch.setattr(AGENT, "MENU_VERIFY_ATTEMPTS", 3)
    assert AGENT.menu_settled("%12", labels) == (
        False, "the pane never held one state long enough to be read twice")


# --------------------------------------------------------------------------- #
# 16g. REAL tmux. The stub cannot prove `capture-pane -p -t %N` is a call real
#      tmux accepts, nor that the detector works on bytes a terminal produced.
# --------------------------------------------------------------------------- #
def _real_pane_showing(real_tmux, body: str, tmp_path, name: str) -> str:
    """A pane on the private server whose VISIBLE content is `body`.

    🔴 `sys.executable <script>` RATHER THAN `sh -c "cat …; sleep …"`, and both
    halves of that are deliberate. The gate has TWO tiers and the `nix build`
    sandbox is the one CI runs: a pane command that needs `sh`, `cat` and `sleep`
    to be resolvable there is a dependency this file did not declare, and its
    failure would present as "the detector is broken" rather than as a missing
    tool. `sys.executable` is an absolute path that is present by construction.
    TWO arguments, so it also cannot be mangled if tmux joins them and hands the
    result to a shell — a `-c` program string could be.
    """
    src = tmp_path / name
    src.write_text(body, encoding="utf-8")
    holder = tmp_path / (name + ".show.py")
    holder.write_text(
        "import sys, time\n"
        f"sys.stdout.write(open({str(src)!r}, encoding='utf-8').read())\n"
        "sys.stdout.flush()\n"
        "time.sleep(60)\n",
        encoding="utf-8")
    out = real_tmux["tmux"](
        "new-window", "-d", "-t", "=keep:", "-P", "-F", "#{pane_id}",
        sys.executable, str(holder)).stdout
    return out.splitlines()[0].strip()


def test_capture_pane_really_READS_a_menu_off_a_REAL_pane(monkeypatch, real_tmux, tmp_path):
    """🔴 THE SEAM, AGAINST REAL tmux, WITH BOTH CONTROLS.

    The stub answers whatever the fixture wrote, so it cannot tell a valid
    `capture-pane` invocation from an invalid one — and an invalid one would make
    every menu look like a text prompt and restore the wrong-answer bug in full,
    with the whole stubbed section above still green.

    So: a real pane rendering a menu must classify as a MENU with the labels the
    terminal actually shows, and a real pane running a SHELL must classify as
    TEXT. Both directions, because a detector that answered "menu" for everything
    would satisfy the first alone.
    """
    _agent_with_real_tmux(monkeypatch, real_tmux)

    pane = _real_pane_showing(real_tmux, SINGLE_MENU_CAPTURE, tmp_path, "menu.txt")
    captured = ""
    for _ in range(60):
        captured, err = AGENT.capture_pane(pane)
        assert not err, f"real tmux refused the capture: {err}"
        if AGENT.classify_pane(captured)[0] == AGENT.PANE_MENU:
            break
        time.sleep(0.05)
    kind, labels, why = AGENT.classify_pane(captured)
    assert kind == AGENT.PANE_MENU, f"real capture did not read as a menu: {captured!r}"
    assert labels == ["Ledger", "Flatfile", "Type something.", "Chat about this"], labels
    assert why == ""

    # THE CONTROL: a pane running an ordinary shell is a TEXT prompt.
    shell_pane = real_tmux["tmux"](
        "list-panes", "-t", "=keep:", "-F", "#{pane_id}").stdout.splitlines()[0].strip()
    shell_capture, err = AGENT.capture_pane(shell_pane)
    assert not err, err
    assert AGENT.classify_pane(shell_capture)[0] == AGENT.PANE_TEXT, (
        f"a real shell pane read as a menu, so a digit would be sent into a shell: "
        f"{shell_capture!r}")


def test_a_capture_of_a_pane_that_does_NOT_EXIST_is_an_ERROR_not_an_empty_read(
        monkeypatch, real_tmux):
    """🔴 THE TWO STATES THIS CODE MUST NOT CONFLATE, measured against real tmux.

    "the pane could not be read" is a refusal and "the pane is blank" is a text
    delivery, so a `capture_pane` that returned `("", "")` for a missing pane
    would silently turn every unreadable pane into a blind type-and-Enter. Real
    tmux is the only thing that can say which it does.
    """
    _agent_with_real_tmux(monkeypatch, real_tmux)
    captured, err = AGENT.capture_pane("%9301")
    assert captured == "", captured
    assert err, "a capture of a non-existent pane reported no error"


# --------------------------------------------------------------------------- #
# 16m. 🔴 THE SEAM WITH `session-manager`'S OWN MENU PREDICATE.
#
# `scripts/session-manager` decides "is a modal up" for its `waiting` signal, from
# `_MENU_SELECTED_RE` + `_MENU_OPTION_RE`, with the measured render facts in
# `claude/skills/session-manager/reference/waiting-signal.md`. This agent decides
# "which row carries which label, and is this a shape I can drive". Two predicates
# over the same bytes, worded independently — the shape this repo already
# consolidated for the TEXT policy (`scripts/lib/tmux_text_policy.py`, pinned two
# ways) and has not for the MENU one.
#
# 🔴 THEY ARE NOT MERGED, AND THAT IS A DECISION WITH A REASON. session-manager's
# predicate feeds a signal with a measured 11/11 precision; changing it is a change
# to that detector and belongs in its own PR with its own dogfood. What is pinned
# here instead is the RELATIONSHIP, because that is what found the blocker: on the
# live fleet session-manager saw a modal on the three panes this agent's
# `menu_block` could not see, and that disagreement is how `MENU_ROW_GAP = 4` was
# caught. A ledger that fails when either side drifts is the cheap half of
# consolidation and the half that does the finding.
# --------------------------------------------------------------------------- #
#: Spelled out so the docstring assertion below has one canonical phrase to look
#: for. It exists because the NUMBER is the thing that drifts, not the prose.
_COUNT_WORDS = {0: "ZERO", 1: "ONE", 2: "TWO", 3: "THREE", 4: "FOUR", 5: "FIVE",
                6: "SIX", 7: "SEVEN", 8: "EIGHT", 9: "NINE", 10: "TEN"}


def divergence_phrase(n: int) -> str:
    """The exact phrase a docstring must carry to claim `n` enumerated divergences."""
    return f"the {_COUNT_WORDS[n]} divergence" + ("" if n == 1 else "s")


def test_the_menu_predicate_AGREES_with_session_managers_over_a_shared_corpus():
    """🔴 A RELATIONSHIP, NOT A COMPONENT — and the ONE divergence is enumerated.

    Every capture in the corpus is labelled with whether a live modal is up, and
    BOTH predicates are checked against that label. So the ledger fails when this
    agent stops seeing a modal (the blocker: `MENU_ROW_GAP = 4` blinded it to every
    real render), when it starts seeing one that is not there (the live
    false-positive direction), and when session-manager drifts either way.

    🔴 THERE USED TO BE **TWO** ENUMERATED DIVERGENCES AND THE FIRST ONE WAS NOT
    REAL. It read: "session-manager's `_MENU_OPTION_RE` requires the number first, so
    `❯ [ ] 1. Ledger` matches nothing and it does not see a multiSelect modal at all —
    a gap in the `waiting` detector". There is no such gap. `❯ [ ] 1. Ledger` is not a
    render Claude Code produces; the real multiSelect row is `❯ 1. [ ] Ledger`, which
    `_MENU_SELECTED_RE` (`^\\s*❯\\s*\\d+\\.\\s+\\S`) matches like any other numbered row.
    So a "divergence" was reported, written into a ledger, and passed for a round —
    an invented render can manufacture a finding about a NEIGHBOURING subsystem, and
    this is the third claim in this file descended from that one fiction.

    ⚠ THE ONE divergence THAT REMAINS IS A FINDING, NOT A CARVE-OUT, and it was
    measured by this guard rather than assumed: session-manager has no adjacency rule, so two
    numbered rows separated by a BLANK line read as a modal to it and not to this
    agent. Its predicate is the broader of the two; for a `waiting` signal that is
    checked by its own measured precision, and for a surface that PRESSES KEYS it
    would be unsafe.

    It is not fixed here — changing that detector belongs in its own PR. It is one
    literal row below rather than a silent mismatch, so closing it moves this test.
    Both sides' verdicts are written out, so the ledger fails when EITHER predicate
    drifts, in either direction.
    """
    sm = _load_collector()
    sm_selected = sm._MENU_SELECTED_RE
    sm_option = sm._MENU_OPTION_RE

    def session_manager_sees_a_modal(captured):
        lines = captured.splitlines()
        if not any(sm_selected.match(l) for l in lines):
            return False
        return sum(1 for l in lines if sm_option.match(l)) >= 2

    def agent_sees_a_modal(captured):
        return AGENT.classify_pane(captured)[0] != AGENT.PANE_TEXT

    # (capture, this agent's verdict, session-manager's verdict) — BOTH written
    # out, so a drift on either side fails rather than being absorbed.
    CORPUS = [
        (SINGLE_MENU_CAPTURE, True, True),
        (DESCRIBED_MENU_CAPTURE, True, True),
        (DESCRIBED_MULTI_QUESTION_CAPTURE, True, True),
        (MULTI_QUESTION_MENU_CAPTURE, True, True),
        (MULTI_QUESTION_REVIEW_CAPTURE, True, True),
        (NO_FREE_TEXT_MENU_CAPTURE, True, True),
        (TWO_CURSOR_MENU_CAPTURE, True, True),
        (SINGLE_MENU_FREE_TEXT_FOCUSED_CAPTURE, True, True),
        # 🔴 THIS ROW USED TO READ `True, False` UNDER A HEADING CLAIMING
        # session-manager "cannot see a multiSelect row at all". It can — see the
        # docstring. The AGREEMENT is load-bearing: session-manager seeing this pane
        # as `waiting` is what makes clawgate raise a reply card for it, which is the
        # reachability half of the multi-select refusal this file now emits.
        (MULTI_SELECT_MENU_CAPTURE, True, True),
        # 🔴 DIVERGENCE: session-manager has no adjacency rule, so a BLANK line
        # between two numbered rows still reads as a modal to it.
        ("  ❯ 1. Ledger\n\n    2. Flatfile\n", False, True),
        (DEFAULT_PROMPT_CAPTURE, False, False),
        (CLAUDE_TEXT_PROMPT_CAPTURE, False, False),
        (ANSWERED_CAPTURE, False, False),
        ("", False, False),
        ("1. install the thing\n2. run the thing\n3. profit\n", False, False),
        ("  ❯ 1. Ledger\n", False, False),
    ] + [(cap, False, False) for cap, _ in QUOTED_MENU_SHAPES]

    disagreements = []
    for capture, want_agent, want_sm in CORPUS:
        got_agent = agent_sees_a_modal(capture)
        got_sm = session_manager_sees_a_modal(capture)
        if got_agent is not want_agent:
            disagreements.append(("agent", want_agent, got_agent, capture[:60]))
        if got_sm is not want_sm:
            disagreements.append(("session-manager", want_sm, got_sm, capture[:60]))
    assert not disagreements, (
        "the two menu predicates no longer match the ledger. A row where the AGENT "
        "moved is a live defect — this is the guard the MENU_ROW_GAP blocker was "
        "found by. A row where SESSION-MANAGER moved means its `waiting` detector "
        f"changed and this ledger needs re-deriving:\n"
        + "\n".join(f"  {who}: wanted {want}, got {got}, capture={cap!r}"
                    for who, want, got, cap in disagreements))

    # 🔴 THE POSITIVE CONTROLS. A corpus of one kind proves nothing, and a ledger
    # whose two columns are identical everywhere is not measuring a relationship —
    # it is measuring one predicate twice.
    assert sum(1 for _, a, _ in CORPUS if a) >= 8, "the corpus has no modal rows"
    assert sum(1 for _, a, _ in CORPUS if not a) >= 8, "the corpus has no text rows"

    # 🔴 THE COUNT IS PINNED TO THE DOCSTRING RATHER THAN RESTATED IN IT, and that
    # is the mechanism fix for a defect that has now recurred three times in this
    # file's history: a prose number copied forward while the assertion beside it
    # moved. This docstring's opening line said "the ONE divergence is enumerated"
    # while the body enumerated two and this assertion read `== 2`. A number a
    # human types cannot be checked; a phrase a function builds can be.
    measured = sum(1 for _, a, s in CORPUS if a is not s)
    doc = test_the_menu_predicate_AGREES_with_session_managers_over_a_shared_corpus.__doc__
    assert divergence_phrase(measured) in doc, (
        f"the corpus now has {measured} divergence(s) between the two predicates, and this "
        f"test's own docstring does not say so. Write the phrase "
        f"{divergence_phrase(measured)!r} into it — and re-derive the enumeration beneath "
        f"it, because a divergence appearing or closing is a FINDING about one of the two "
        f"detectors, never a number to adjust.")


# --------------------------------------------------------------------------- #
# 16h. THE FOOTER: the only modal signal a SHORT PANE does not clip.
#
# 🔴 EVERY GUARD IN THIS SECTION EXISTS BECAUSE THE WHOLE SUITE WAS BLIND TO ONE
# DIMENSION. Every capture in this file, and every live measurement behind them,
# was taken at 104x32 or 118x38 — so the fixtures pinned ONE pane height and the
# classifier's height-dependent defects passed a 27-mutant sweep untouched.
# RULES.md names the class: "a suite whose CONFIG pins a dimension is
# STRUCTURALLY BLIND to that dimension's bugs".
#
# The shapes below were re-measured by raising REAL modals in a scratch tmux
# session and RESIZING THE WINDOW at 104 columns — not by truncating a capture,
# which is a simulation the real render disproves: the TUI re-renders to fit and
# scrolls its own option list rather than losing lines off the top. What came
# back (see the module under test for the full tables):
#
#     one question   menu at 40/32/28/24/20/16/15/14 · TEXT at 13/12/11/10
#     two questions  multi at 40/32/28/26/24/20/16   · MENU at 15/14
#                                                    · TEXT at 13/12/11/10
#
# `TEXT` there means the agent typed the reply and pressed Enter into a live
# modal — the original blocker. `MENU` on the chain means it answered question 1
# and reported `delivered` with the rest of the chain still open.
# --------------------------------------------------------------------------- #
def test_a_CLIPPED_question_CHAIN_is_refused_rather_than_ANSWERED_HALFWAY(
        server, tmux_stub, tmp_path):
    """🔴 A1: THE CHAIN SIGNALS ALL RENDER ABOVE THE ROWS AND A SHORT PANE EATS THEM.

    `multi` is computed from `✔ Submit`, `Review your answers` and the `☐` markers.
    Every one of those is at the TOP of the modal, and `capture_pane` reads the
    viewport only — so on a REAL 2-question chain resized to 104x14 all three were
    gone while the cursored rows and the footer remained. The shipped code read
    that `menu`, pressed a digit, answered question 1, saw question 2 render, and
    reported `delivered` because "a different menu is now on screen".

    RED at `c8cf7b15e`: there the fixture classifies `menu` and the run sends
    `send-keys … 2`. GREEN here: `multi-question`, and nothing is sent.
    """
    kind, labels, why = AGENT.classify_pane(CLIPPED_CHAIN_CAPTURE)
    assert kind == AGENT.PANE_MULTI_QUESTION, (
        "a live question CHAIN whose tab strip has been clipped out of the viewport is "
        f"being driven as a single menu: {(kind, labels, why)}")
    assert labels == [] and "MULTI-question" in why, (kind, labels, why)

    tmux_stub.set_captures(CLIPPED_CHAIN_CAPTURE)
    server.claim_batches = [[write(text="Flatfile")]]
    run_agent(server, tmux_stub, tmp_path)
    assert tmux_stub.send_keys_calls() == [], (
        "a key was sent to a half-visible question chain: "
        f"{tmux_stub.send_keys_calls()}")
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "refused", body


@pytest.mark.parametrize("capture,route", [
    pytest.param(SCROLLED_SINGLE_ASK_CAPTURE,
                 "row 1 clipped, and the scrolled edge row carries the modal's own `↓`",
                 id="clipped-and-scroll-marked"),
    pytest.param(
        ("  ❯ 1. Ledger\n"
         + "     more description\n" * (13)
         + "    2. Flatfile\n"
         + "\n" + MENU_FOOTER_SINGLE + "\n"),
        "option 1's description is taller than MENU_ROW_GAP",
        id="description-taller-than-the-gap"),
    pytest.param(
        ("  ❯ 1. Ledger\n\n    2. Flatfile\n\n" + MENU_FOOTER_SINGLE + "\n"),
        "a blank line between rows 1 and 2",
        id="blank-line-between-rows"),
    pytest.param(
        ("  ☐ Store\n\n    1. Ledger\n    2. Flatfile\n\n" + MENU_FOOTER_SINGLE + "\n"),
        "the cursor row has not been drawn yet",
        id="no-cursor-row"),
])
def test_a_live_ask_whose_ROW_GEOMETRY_is_GONE_is_REFUSED_not_TYPED_INTO(
        capture, route, server, tmux_stub, tmp_path):
    """🔴 A2: EVERY FAILURE OF THE ROW PARSER USED TO LAND ON THE TYPING PATH.

    The half-drawn refusal was gated on `block and question_marks`, so when the
    numbered block was DESTROYED there was nothing left to refuse on and control
    fell through to `return PANE_TEXT` — the agent typed the reply and pressed
    Enter into a live modal, which is the original defect of this whole file.

    Four routes to an empty or cursorless block on a live ask are parametrised
    here, and the FIRST is the one no earlier round named: the modal scrolls its
    own option list and marks the edge row with `↓`, which stands exactly where
    `MENU_OPTION_RE` expects whitespace or the cursor glyph. Measured on a real
    render at 104x13.

    RED at `c8cf7b15e` for the first three: all three classify `text` there and
    the run sends the reply plus Enter. The fourth (`no-cursor-row`) is an
    INVARIANT GUARD — it was already refused at base by the `block and
    question_marks` branch — and it is here to pin that the footer route reaches
    the same verdict, not as evidence about this fix.
    """
    kind, labels, why = AGENT.classify_pane(capture)
    assert kind == AGENT.PANE_UNKNOWN_MENU, (
        f"a live ask ({route}) classified {kind}, so the reply would be TYPED into a modal "
        f"that discards it and the Enter would answer whichever row is highlighted")
    assert labels == [] and why, (kind, labels, why)

    tmux_stub.set_captures(capture)
    server.claim_batches = [[write(text="Flatfile")]]
    run_agent(server, tmux_stub, tmp_path)
    assert tmux_stub.send_keys_calls() == [], (
        f"a key was sent to a live ask ({route}): {tmux_stub.send_keys_calls()}")
    body = json.loads([r for r in server.requests if r["path"].endswith("/result")][0]["body"])
    assert body["state"] == "refused", body


def test_the_menu_fixtures_span_MORE_THAN_ONE_PANE_HEIGHT():
    """🔴 THE STRUCTURAL GUARD ON THE BLIND DIMENSION ITSELF.

    A1 and A2 both survived a 27-mutant sweep for one reason: every fixture in
    this file was the same height, so no mutant could be distinguished by a height
    it was never shown. Pinning the individual defects does not stop the next
    height-dependent one — this does, by failing when the menu corpus collapses
    back onto a single height.

    ⚠ AN INVARIANT GUARD BY CONSTRUCTION, and labelled as one: it pins a property
    of the FIXTURE SET, not a behaviour of the agent, so it is not regression
    coverage for A1/A2. It is the thing that would have made them findable.
    """
    corpus = {
        "full single ask": SINGLE_MENU_CAPTURE,
        "described single ask": DESCRIBED_MENU_CAPTURE,
        "full chain": MULTI_QUESTION_MENU_CAPTURE,
        "clipped chain": CLIPPED_CHAIN_CAPTURE,
        "scrolled single ask": SCROLLED_SINGLE_ASK_CAPTURE,
        "approval prompt": PERMISSION_PROMPT_CAPTURE,
        "startup dialog": STARTUP_DIALOG_CAPTURE,
    }
    heights = {name: len(cap.splitlines()) for name, cap in corpus.items()}
    short = {n: h for n, h in heights.items() if h <= 10}
    tall = {n: h for n, h in heights.items() if h >= 14}
    assert short, (
        "no menu fixture is short enough to reproduce a CLIPPED render. 5 of the 57 live "
        f"panes on this host are <=28 lines and one is 21. heights={heights}")
    assert tall, f"no menu fixture reproduces a full-height render. heights={heights}"
    assert len(set(heights.values())) >= 4, (
        "the menu corpus has collapsed onto too few distinct heights, which is exactly the "
        f"condition that let A1 and A2 through a green sweep. heights={heights}")


# --------------------------------------------------------------------------- #
# 16i. A cursored numbered menu is NOT evidence of an `AskUserQuestion`.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("capture,what", [
    pytest.param(PERMISSION_PROMPT_CAPTURE, "a tool-approval prompt", id="approval"),
    pytest.param(STARTUP_DIALOG_CAPTURE, "the trust-this-folder dialog", id="trust"),
    pytest.param(INVERTED_STARTUP_DIALOG_CAPTURE,
                 "the bypass-permissions warning, whose rows are INVERTED",
                 id="bypass"),
    pytest.param(
        ("● Earlier I offered you:\n"
         "\n"
         "  ❯ 1. Ledger\n"
         "    2. Flatfile\n"
         "\n"
         "● and you picked the second one.\n"),
        "a transcript quoting a menu back at the operator",
        id="quoted-transcript"),
    pytest.param(
        ("devrc git:(main) $ cat plan.md\n"
         "  ❯ 1. Ledger\n"
         "    2. Flatfile\n"
         "devrc git:(main) $ \n"),
        "a numbered list left on screen by a shell command",
        id="shell-output"),
    # 🔴 THE TWO THAT THE `Enter to select` SUBSTRING COULD NOT REFUSE, and the
    # reason this test's own premise had to be rebuilt: it is not the ABSENCE of
    # the anchor that makes a chooser safe.
    pytest.param(SANDBOX_PICKER_CAPTURE,
                 "the /sandbox picker, which DOES carry `Enter to select`",
                 id="sandbox"),
    pytest.param(PERMISSIONS_RULE_LIST_CAPTURE,
                 "the /permissions rule list, which DOES carry `Enter to select`",
                 id="permissions"),
])
def test_a_cursored_numbered_menu_with_NO_ASK_FOOTER_is_REFUSED(
        capture, what, server, tmux_stub, tmp_path):
    """🔴 B1 + B2: THE CHOOSER ON SCREEN MAY NOT BE THE ONE THE REPLY ANSWERS.

    Deleting the free-text precondition left "a cursored numbered block" as the
    whole signal, and it is not enough. FIVE of these seven are LIVE CAPTURES of
    Claude Code choosers that are not asks at all, and the first is the one that
    matters: against the measured approval render (`❯ 1. Yes` / `2. Yes, allow all
    edits during this session (shift+tab)` / `3. No`) a reply of `Yes` pressed 1
    and APPROVED A TOOL CALL THE OPERATOR NEVER SAW.

    🔴 AND THE LAST TWO ARE WHY THIS TEST'S OWN PREMISE HAD TO BE REBUILT. The
    previous round's reasoning was "none of these carries `Enter to select`, that is
    the whole discriminator" — a guard on a WORD, dressed as a guard on state.
    `/sandbox` and `/permissions` both carry it, both carry the single-question nav
    hint beside it, and both were therefore DRIVEN BY DIGIT: a reply naming a
    `/sandbox` row changed the sandbox/permission mode, and `/permissions`' row 1
    ends in `…` so the truncation rule accepted any reply beginning `Add a new rule`.
    Neither was a regression — base classifies them the same way — but the PR
    asserted the opposite of what it shipped, and the test below pinned the
    incomplete footer set AS IF CLOSED. Both are fixed by reading the whole field
    list; these two rows are the behavioural half.

    🔴 AND BOTH DIRECTIONS WERE DELIVERABLE, contrary to an earlier round's
    reading: `No` is a BARE label in that render, so it matched row 3 exactly and
    denied the call. There is no asymmetry to lean on.

    🔴 THE MITIGATION THAT WAS OFFERED INSTEAD IS A CLAIM ABOUT CONFIGURATION, NOT
    CODE. "those go through clawgate's approval router, so this is not a path in
    use" is true of a settings file, which a `--settings` override, a fresh
    `CLAUDE_CONFIG_DIR` or an unset hook changes — all three were used to PRODUCE
    the capture this test is built from. The guard is deterministic instead.

    ⚠ AND IT IS A GUARD ON THE STATE, NOT ON A WORD. It reads the footer, never the
    labels, so it cannot be walked by relabelling a row — which matters because the
    approval prompt's rows have already been reworded once between renders, and
    because the trust dialog and the bypass warning put `Yes` on DIFFERENT rows.

    RED at `c8cf7b15e`: ALL FIVE, measured — each classifies `menu` there and the
    run sends `send-keys … 1`. ⚠ The fifth was drafted as an INVARIANT guard on the
    assumption that shell output could not carry a cursor on a contiguous numbered
    block; running it at base showed otherwise, so the label was corrected rather
    than the case dropped. That is the whole reason the matrix is measured and not
    reasoned about.
    """
    kind, labels, why = AGENT.classify_pane(capture)
    assert kind != AGENT.PANE_MENU, (
        f"{what} is being driven by digit, so a reply naming one of its rows would answer a "
        f"question the operator was never shown: {(kind, labels, why)}")
    assert labels == [], (kind, labels, why)

    tmux_stub.set_captures(capture)
    server.claim_batches = [[write(text="Yes")]]
    run_agent(server, tmux_stub, tmp_path)
    assert ["send-keys", "-t", "%12", "-l", "--", "1"] not in tmux_stub.send_keys_calls(), (
        f"a DIGIT was sent to {what}: {tmux_stub.send_keys_calls()}")


def test_a_reply_of_Yes_can_never_APPROVE_A_TOOL_CALL(server, tmux_stub, tmp_path):
    """🔴 B2, END TO END AND AS THE OUTCOME RATHER THAN THE CLASSIFICATION.

    The classification is the mechanism; this is the consequence. A single word in
    a reply must not be able to authorise a write the operator never saw — and
    against the live approval render at `c8cf7b15e` it could, because `Yes` matched
    row 1 exactly.

    ⚠ BOTH REPLIES ARE DRIVEN, because the approve direction is the dangerous one
    and the deny direction is the one an earlier round believed already safe. The
    guard must hold for both, since `No` matched row 3 exactly too.
    """
    for reply in ("Yes", "No"):
        tmux_stub.reset()
        server.requests.clear()
        tmux_stub.set_captures(PERMISSION_PROMPT_CAPTURE)
        server.claim_batches = [[write(text=reply)]]
        run_agent(server, tmux_stub, tmp_path)
        assert tmux_stub.send_keys_calls() == [], (
            f"a reply of {reply!r} reached a tool-approval prompt: "
            f"{tmux_stub.send_keys_calls()}")
        results = [r for r in server.requests if r["path"].endswith("/result")]
        body = json.loads(results[0]["body"])
        assert body["state"] == "refused", (reply, body)
        assert "tool-approval prompt" in body["detail"], (reply, body)


#: 🔴 FOOTERS THAT CAN RENDER `Enter to select` IN CLAUDE CODE 2.1.232, WITH THE
#: VERDICT THE READER GIVES EACH. It replaces a ledger that named FIVE footers and
#: pinned them "as if closed" while two greps over the shipped binary already found
#: SIXTEEN — of which seven satisfied the guard it was defending.
#:
#: 🔴 WHAT THIS LEDGER IS, SAID PRECISELY, BECAUSE THE PREVIOUS HEADER DID THE
#: ARITHMETIC WRONG. Each row is a footer STRING, not a renderer SITE, and the two
#: counts have no relationship to derive from each other. The old header said
#: "14 + 2 = 16, and every one is a row below"; that is false three ways:
#:
#:   * the TWO AskUserQuestion sites contribute THREE rows each — one per render
#:     state (plain / free-text row focused / question chain), so 2 sites -> 6 rows;
#:   * several non-ask sites render the SAME string and SHARE one row. `nt` sites 4,
#:     5 and 6 are all `↑/↓ to navigate · Enter to select · Esc to back`; sites 3
#:     and 8 are both `Enter to select · Esc to cancel`; site 1 renders the
#:     `Esc to cancel` and `Esc to go back` rows depending on its step index;
#:   * two rows were a SHORTENED form of their site's whole footer. `nt` site 12
#:     renders `←/→ to tabs · ↑/↓ to move · Enter to select · Esc to close` and the
#:     row dropped the first field; `nt` site 9's search mode renders
#:     `Type to Search · [Ctrl+A to show all projects] · Enter to select ·
#:     Esc to clear` and the row kept only two fields. Both are fixed below, and
#:     the whole-footer form is what is asserted.
#:
#: So `len(rows) == 16` was a coincidence. The relationship this file can actually
#: assert is checked in two places, deliberately split: every row's VERDICT here,
#: and the SITE COUNTS against the bundle in the DEV-HOST tier
#: (`scripts/devhost-tests/test_claude_footer_sites.py`) — see FOOTER_SITE_ALARM
#: below for why that half cannot live in this file.
#:
#: 🔴 AND THE SET OF PRODUCING SITES IS **NOT** CLOSED. `Enter to select` is not a
#: literal in the ask's renderer — the footer is assembled from a
#: `<chord>`/`<action>` component (`nt`) joined with ` · ` — so the bundle can be
#: asked which sites produce it, and it was, four ways:
#:
#:     B=…/claude-code-2.1.232/bin/.claude-wrapped
#:     grep -aoP 'chord:"enter",action:"select"' "$B"                 -> 14 literal
#:                                                                       chord/action pairs
#:     grep -aoP '.{0,120}Enter to select.{0,120}' "$B"               ->  2 whole literals
#:     grep -aoP 'lo,\{action:"[^"]*",context:"[^"]*",'
#:              'fallback:"Enter",description:"select"\}' "$B"        ->  6 `lo` sites
#:     the scrollable picker's DEFAULTED action
#:       (`qJl = hfw === void 0 ? "select" : hfw`, rendered
#:        `nt({chord:"enter", action: …})`)                           ->  1 site
#:
#: `lo({action,context,fallback,description})` renders
#: `nt({chord: WT(action,context,fallback), action: description})` and `WT` returns
#: the FALLBACK when that action has no configured keybinding — so those six sites
#: spell the anchor with a chord the first grep cannot see. At least 23 SITES
#: therefore reach it, not 16, and a third composition path would be invisible to
#: all four probes above. This is stated as unestablished: there is no completeness
#: argument here, and the previous one is retracted in `tmux-reply-agent`'s footer
#: section header.
#:
#: 🔴 WHAT CARRIES THE SAFETY IS THE GRAMMAR, MEASURED. Every one of the seven
#: additional producers is a row below and every one resolves FOOTER_UNKNOWN. WHICH
#: RULE refuses each was measured by walking the reader's own branches, not inferred:
#: all six `lo` footers on the LAST-field rule (`Esc to go back`, so the field run
#: never reaches a terminator and stops at the anchor), the picker on the FIRST-field
#: rule at >= 120 columns (its nav field renders BEFORE the anchor) and on the
#: NAV-COUNT rule below that width, where its nav field reads `↑/↓ to nav` and is not
#: a field this file names at all. The permitted field set is transcribed from the two
#: ask RENDERERS, so a field it does not name refuses whatever produced it.
#:
#: ⚠ NOT CLOSED ACROSS VERSIONS EITHER. A reworded render is a field this set does
#: not name, and there the reader FAILS SAFE: FOOTER_UNKNOWN, which refuses with a
#: named reason. So the failure mode of staleness is an inert feature, never a
#: driven chooser.
#: ⚠ THE VERDICT HALF BELOW IS TIER-INDEPENDENT AND THE BUNDLE HALF IS NOT. The
#: `nix build` tier CI runs has no `claude` in it, so the grep re-derivation lives in
#: a DEV-HOST-tier file, which that tier does not collect at all — not behind a skip,
#: because an unpinned skip reds `run-tests.sh`. The verdicts below run everywhere.
#: Splitting them is what gives CI the verdicts and the dev host a staleness alarm,
#: instead of one test that skips in the tier that matters.
ASK_FOOTER_LEDGER = [
    # (footer, verdict, what renders it)
    (MENU_FOOTER_SINGLE, AGENT.FOOTER_SINGLE,
     "ask renderer 1, one question"),
    (MENU_FOOTER_FREE_TEXT, AGENT.FOOTER_SINGLE,
     "ask renderer 1, one question, free-text row focused"),
    (MENU_FOOTER_CHAIN, AGENT.FOOTER_CHAIN,
     "ask renderer 1, a question CHAIN"),
    ("Enter to select · ↑/↓ to navigate · n to add notes · Esc to cancel",
     AGENT.FOOTER_UNKNOWN,
     "ask renderer 2 (options carry a preview), one question — a REAL ask, refused "
     "because a digit there only moves the highlight"),
    ("Enter to select · ↑/↓ to navigate · n to add notes · ctrl+g to edit in Nvim · "
     "Esc to cancel", AGENT.FOOTER_UNKNOWN,
     "ask renderer 2, free-text row focused"),
    ("Enter to select · ↑/↓ to navigate · n to add notes · Tab to switch questions · "
     "Esc to cancel", AGENT.FOOTER_CHAIN,
     "ask renderer 2, a question CHAIN — its chain marker is `switch questions`, "
     "which nothing in this file could see before"),
    (SANDBOX_PICKER_FOOTER, AGENT.FOOTER_UNKNOWN,
     "literal 1: the /sandbox picker — LIVE, and driven by digit at base"),
    (PERMISSIONS_RULE_LIST_FOOTER, AGENT.FOOTER_UNKNOWN,
     "literal 2: the /permissions rule list — LIVE, and driven by digit at base"),
    ("↑/↓ to navigate · Enter to select · Esc to back", AGENT.FOOTER_UNKNOWN,
     "`nt` sites 4, 5 and 6 — the MCP server-detail and tool-list choosers: nav "
     "first, and `Esc to back`. Three sites, one string"),
    ("↑/↓ to navigate · Enter to select · Esc to cancel", AGENT.FOOTER_UNKNOWN,
     "`nt` sites 1 (wizard, step 0) and 11: the ask's own fields in the WRONG "
     "ORDER — the row that makes `Enter to select` first a requirement rather "
     "than an observation. Also what the scrollable picker renders at >= 120 "
     "columns, so THREE producers share it"),
    ("Enter to select · Esc to continue", AGENT.FOOTER_UNKNOWN,
     "`nt` site 7"),
    ("Enter to select · Esc to cancel", AGENT.FOOTER_UNKNOWN,
     "`nt` sites 3 (the settings theme picker) and 8 (the remote-environment "
     "picker) — NO nav field, and the ask always has exactly one"),
    ("Enter to select · Esc to go back", AGENT.FOOTER_UNKNOWN,
     "`nt` site 10, `nt` site 1 past step 0, AND `lo` sites 1-4 (the plugin/"
     "marketplace settings choosers). SIX producers, one string — and the anchor "
     "is field 1 in all of them, so the first-field rule does not decide these; "
     "the LAST-field rule does, measured"),
    ("Enter to select · u to update · d to remove · Esc to go back",
     AGENT.FOOTER_UNKNOWN,
     "`lo` site 5, the marketplace pending-changes footer with no pending action: "
     "anchor first, two fields the ask never emits, and the wrong terminator"),
    ("↑ to navigate · Enter to select · Esc to go back", AGENT.FOOTER_UNKNOWN,
     "`lo` site 6, the failed-plugin detail view — its nav chord is the UP arrow "
     "alone, so the nav wording differs from the ask's as well"),
    ("Type to Search · Ctrl+A to show all projects · Enter to select · "
     "Esc to clear", AGENT.FOOTER_UNKNOWN,
     "`nt` site 9's search mode, the WHOLE footer — the row this replaces kept "
     "only `Enter to select · Esc to clear`, which is not what the site renders. "
     "⚠ the `Ctrl+A` field is conditional and its chord is rendered through a "
     "`format` option this row does not transcribe exactly; the verdict does not "
     "turn on it, and the ROW here is what the reader is driven against"),
    ("Enter to select · ctrl+e to edit · Esc to cancel", AGENT.FOOTER_UNKNOWN,
     "`nt` site 2, the theme picker: an EDIT field the ask does not have (the "
     "ask's is ctrl+g), and its chord really is `ctrl+e` — a keybinding lookup on "
     "`theme:editCustom` whose fallback is that literal"),
    ("←/→ to tabs · ↑/↓ to move · Enter to select · Esc to close",
     AGENT.FOOTER_UNKNOWN,
     "`nt` site 12, the WHOLE footer — the row this replaces dropped `←/→ to "
     "tabs`, and its nav field says `move`"),
    # 🔴 THE SCROLLABLE PICKER, WHICH NO GREP FOR A LITERAL CHORD/ACTION PAIR CAN
    # FIND: its action is DEFAULTED (`qJl = hfw === void 0 ? "select" : hfw`) and its
    # nav wording is computed from the terminal WIDTH (`KJl = columns < 120`). Both
    # shapes are rows, because they are refused by DIFFERENT rules — the wide one by
    # nav-first, the narrow one by the nav COUNT, since `↑/↓ to nav` is not a field
    # this file names. The wide shape's string is shared with the row above; this row
    # is the narrow one plus the optional `Tab` hint the component can carry.
    ("↑/↓ to nav · Enter to select · Esc to cancel", AGENT.FOOTER_UNKNOWN,
     "the generic scrollable picker below 120 columns — anchor first and the ask's "
     "own terminator, refused on having NO nav field this file recognises"),
    ("↑/↓ to navigate · Enter to select · Tab to toggle · Esc to cancel",
     AGENT.FOOTER_UNKNOWN,
     "the same picker at >= 120 columns with its optional Tab hint present"),
    # 🔴 A GRAMMAR ROW, NOT A SIGHTING, AND IT IS LABELLED AS ONE. No chooser in
    # 2.1.232 renders exactly `Enter to select · ↑/↓ to navigate · Esc to close`, so
    # nothing in the enumeration above needs the `Esc to cancel` LAST requirement —
    # and a mutation sweep proved it: dropping that half of the guard SURVIVED the
    # whole suite, because select-first and exactly-one-nav happened to reject every
    # real row on their own. A guard no mutant can kill reads as a guard while
    # guarding nothing, so the requirement is pinned on its own terms instead: the
    # ask's terminator is `Esc to cancel`, and an ask-shaped footer ending anywhere
    # else is not one. ⚠ SYNTHETIC. It is what the GRAMMAR forbids, not what any
    # pane shows, and saying so is the difference between this row and the fifteen
    # above it.
    ("Enter to select · ↑/↓ to navigate · Esc to close", AGENT.FOOTER_UNKNOWN,
     "GRAMMAR (synthetic): the ask's own field order, terminated by `Esc to close` "
     "— what the cancel-last requirement is for, since no live chooser needs it"),
    # 🔴 THE SECOND GRAMMAR ROW, AND THE SECOND MUTANT THAT FOUND ITS CLAUSE DEAD.
    # The reader refuses to extend the field run leftwards past the anchor only when
    # the anchor was NOT head-glued — the encoding of "`Enter to select` is field 1 in
    # both ask renderers". A mutant that treats every anchor as head-glued SURVIVED,
    # because `/permissions` and `/sandbox` are each rejected by TWO other clauses as
    # well (their `←/→ to switch` / `Esc to close` stops the run short of a
    # terminator). This row is the case where that clause is the ONLY thing standing:
    # a permitted field before the anchor, and a perfectly ask-shaped tail after it.
    # ⚠ SYNTHETIC — no chooser in 2.1.232 renders this — and it is the honest way to
    # keep a clause whose live cases are all double-covered.
    ("↑/↓ to navigate · Enter to select · ↑/↓ to navigate · Esc to cancel",
     AGENT.FOOTER_UNKNOWN,
     "GRAMMAR (synthetic): an ask-shaped tail with a permitted field BEFORE the "
     "anchor — the only case where refusing to extend the run leftwards is what "
     "decides it"),
]

#: The ledger's size at the commit that introduced the floor — MEASURED, not chosen:
#: 22 rows, 20 of them footers taken from the bundle and 2 labelled GRAMMAR. It is a
#: RATCHET, not a derived quantity: see
#: `test_the_footer_reader_resolves_EVERY_LEDGERED_FOOTER` for why a row count has no
#: arithmetic relationship to the producing-site count, and what lowering this number
#: obliges a commit message to say.
ASK_FOOTER_LEDGER_FLOOR = 22

#: 🔴 WHERE THE BUNDLE HALF LIVES, AND WHY IT IS NOT IN THIS FILE. Re-deriving the
#: producing-site counts needs the `claude` binary, which the `nix build` sandbox tier
#: does not have — and a `skipif` on a missing BINARY cannot be pinned in
#: `run-tests.sh`'s EXPECTED_SKIPS (its only conditional predicate is `unset:VAR`), so
#: putting it here would be an UNPINNED SKIP and would red the runner. It therefore
#: lives in the DEV-HOST tier, exactly where the nvim behavioural tests live and for
#: the same documented reason. CI keeps every verdict above; the dev host gains the
#: staleness alarm. `test_the_bundle_STALENESS_ALARM_still_exists` pins that the alarm
#: has not quietly vanished — a dev-host file is invisible from here, so without that
#: guard deleting it would cost nothing this tier can see.
FOOTER_SITE_ALARM = Path(__file__).resolve().parents[1] / (
    "devhost-tests/test_claude_footer_sites.py")

#: Footers that never spell the anchor at all, so they resolve before any parsing.
#: The audit's negative corpus, all of them live renders.
NON_ANCHOR_FOOTERS = [
    (MENU_FOOTER_APPROVAL, "the tool-approval prompt"),
    (MENU_FOOTER_STARTUP, "the trust-folder dialog and the bypass warning"),
    ("←/→ to switch · ↓ to select · Esc to cancel",
     "/permissions with its TAB STRIP focused"),
    ("Type to filter · Enter/↓ to select · Esc to cancel", "a filter picker"),
    ("Enter to approve · r to retry · ↑/↓ to navigate · Esc to cancel",
     "the approve/retry prompt"),
    ("↑/↓ to navigate · enter to resume as a background session · esc to close",
     "the resume picker"),
    ("", "the theme and login pickers, which render no footer at all"),
]


def test_a_DIGIT_can_never_reach_a_multiSelect_list(server, tmux_stub, tmp_path):
    """🔴 F6 END TO END, AND AS THE OUTCOME RATHER THAN THE CLASSIFICATION.

    `PANE_MULTI_SELECT` was UNREACHABLE for a whole round: the row pattern demanded
    `❯ [ ] 1. Ledger`, a render Claude Code does not produce, so
    `menu_checkbox_block` returned `[]` for every real multiSelect and the refusal it
    exists to emit was never emitted. What stopped a digit anyway was an ACCIDENT —
    the numbered labels carry the box (`[ ] Ledger`), so an operator's reply of
    `Ledger` matched nothing and `[ ] Type something` was not a recognised free-text
    row, and the pane was refused with "the reply matches none of the menu's N option
    rows": a true outcome with the wrong reason, one relabelling away from failing.

    In the multiSelect renderer a digit calls `toggleValue(n)` — it TICKS A BOX and
    does not submit — so a reply that did match would tick a box the operator never
    chose and leave the ask open.

    ⚠ BOTH DIRECTIONS DRIVEN: a reply naming a row's LABEL, and a reply naming the
    row's rendered text INCLUDING the box, which is the one an accidental guard lets
    through.

    🔴 AND THE FOOTER CANNOT HELP HERE, WHICH IS WHY THE CHECKBOX BLOCK CARRIES THE
    WHOLE REFUSAL. A multiSelect is a MODE of ask renderer 1 — `rB.multiSelect` swaps
    the list component, and that component emits no footer of its own — so a live
    multiSelect renders the ASK's footer and `menu_footer` answers FOOTER_SINGLE. That
    is not a bug: it IS an AskUserQuestion, just not one a digit can answer. It does
    mean `FOOTER_SINGLE` never licenses "driveable", and the assertion below pins that
    so no comment can quietly start implying otherwise.
    """
    assert AGENT.menu_footer(MULTI_SELECT_MENU_CAPTURE) == AGENT.FOOTER_SINGLE, (
        "a live multiSelect no longer carries the ask's own footer. If that is a real "
        "render change, the comments in MENU_CHECKBOX_ROW_RE and classify_pane saying "
        "the footer CANNOT separate a multiSelect from a driveable ask are now wrong "
        "and must be rewritten — do not delete this assertion to make them true")
    assert AGENT.classify_pane(MULTI_SELECT_MENU_CAPTURE)[0] == AGENT.PANE_MULTI_SELECT, (
        "the checkbox block is the ONLY thing refusing this shape, and it is no longer "
        "doing it — the footer read above returns the DRIVING verdict")
    for reply in ("Ledger", "[ ] Ledger", "Archive"):
        tmux_stub.reset()
        server.requests.clear()
        tmux_stub.set_captures(MULTI_SELECT_MENU_CAPTURE)
        server.claim_batches = [[write(text=reply)]]
        run_agent(server, tmux_stub, tmp_path)
        assert tmux_stub.send_keys_calls() == [], (
            f"a reply of {reply!r} reached a multiSelect list: "
            f"{tmux_stub.send_keys_calls()}")
        body = json.loads(
            [r for r in server.requests if r["path"].endswith("/result")][0]["body"])
        assert body["state"] == "refused", (reply, body)
        assert "multi-select" in body["detail"], (
            f"the refusal for {reply!r} no longer names the shape: {body['detail']!r}")


def test_the_answered_block_STOPS_on_every_SPINNER_frame():
    """🔴 F9: THE STOP SET HELD TWO OF THE SEVEN SPINNER FRAMES.

    A record block ends at a line opening a NEW top-level render, identified by its
    first glyph — and Claude Code's spinner is a top-level render whose glyph CHANGES
    per frame. The set named `✻` and `✽` and missed the rest, so a record followed by
    a spinner caught on any other frame was read as continuing INTO it.

    🔴 ENUMERATED FROM THE BINARY, NOT FROM SIGHTINGS. 2.1.232 picks its frames by
    `$TERM`: `["·", "✢", "✳", "✶", "✻", "✻"]` under `xterm-ghostty` and
    `["·", "✢", "*", "✶", "✻", "✽"]` otherwise, so the union is seven. A read-only
    sweep of all 58 live panes found `✶` twice and `✢` once at column 0, which is how
    the gap was noticed — but two more sightings would not have closed it.

    ⚠ `·` IS THE NAMED RESIDUAL and this test asserts it stays out: it also opens the
    SECOND and later records of a CHAIN, and truncating every chain to its first
    answer is the worse of the two losses. So a spinner caught on its `·` frame still
    does not end the block.
    """
    frames = ("·", "✢", "✳", "*", "✶", "✻", "✽")
    stop = AGENT.MENU_ANSWERED_STOP_GLYPHS
    assert set(frames) - {"·"} <= set(stop), (
        "a spinner frame is missing from the stop set, so a record block can be read "
        f"as continuing into a spinner line: {sorted(set(frames) - {'·'} - set(stop))}")
    assert "·" not in stop, (
        "`·` was added to the stop set, which truncates every CHAIN's record list to "
        "its first answer — see MENU_ANSWERED_STOP_GLYPHS for why it is excluded")

    # 🔴 THE BEHAVIOURAL HALF. A structural check on a tuple would pass with the
    # reader wired to nothing, so each frame is driven through a real capture: the
    # record must be read, and the spinner's own line must NOT become part of it.
    for frame in frames:
        cap = ("●\xa0User answered Claude's questions:\n"
               "  ⎿\xa0·\xa0Which store should the queue use? → Flatfile\n"
               + frame + " Thinking about the flatfile store → Postgres\n")
        got = AGENT.menu_answer_records(cap)
        if frame == "·":
            # The documented residual, asserted so it is a measurement and not a
            # hope: the `·` frame is indistinguishable from a chain's separator.
            assert got == ["Flatfile", "Postgres"], (frame, got)
        else:
            assert got == ["Flatfile"], (
                f"a spinner line opening with {frame!r} is being read as part of the "
                f"answered record: {got}")

    # And the POSITIVE CONTROL for this test: the reader must be capable of reading
    # past a line that is NOT a stop glyph, or every row above passes vacuously.
    wrapped = ("●\xa0User answered Claude's questions:\n"
               "  ⎿\xa0·\xa0Which store should the queue use? → Flatfile with the\n"
               "     journal enabled\n")
    assert AGENT.menu_answer_records(wrapped) == ["Flatfile with the journal enabled"], (
        AGENT.menu_answer_records(wrapped))


def test_the_footer_reader_resolves_EVERY_LEDGERED_FOOTER():
    """🔴 THE LEDGER THIS REPLACES NAMED FIVE FOOTERS AND CALLED THE SET MEASURED.

    Two greps over the binary found sixteen that can render `Enter to select`, and
    seven of those satisfied the guard the old ledger was pinning — so the ledger read
    as coverage while providing none, and its own docstring called itself "the
    unit-level ledger of the measurement". `ASK_FOOTER_LEDGER` carries the corpus, the
    four probes that re-derive the producing sites, and the RETRACTION of the claim
    that those probes close the set.

    ⚠ THIS TEST'S NAME USED TO SAY `EVERY_FOOTER_THAT_CAN_SPELL_THE_ANCHOR`, WHICH IS
    A COMPLETENESS CLAIM IT CANNOT MAKE — the anchor is reachable through composition
    paths no enumeration here has closed. What it asserts is that every footer ON THE
    LEDGER resolves as the ledger says, which is a verdict claim and is true.

    RED at `a324b00e` on the two live rows (`/sandbox`, `/permissions`) plus four
    more: both resolved FOOTER_SINGLE there and every reply naming a row was driven
    by digit.
    """
    for footer, want, what in ASK_FOOTER_LEDGER:
        # Bare, and as it really arrives — inside a whole pane capture, where the
        # text above the footer is glued to its first field by the normalising read.
        for shape, cap in (("bare", footer),
                           ("in a capture", SINGLE_MENU_CAPTURE.replace(
                               MENU_FOOTER_SINGLE, footer))):
            assert AGENT.menu_footer(cap) == want, (
                f"{what}: {shape} footer {footer!r} resolves "
                f"{AGENT.menu_footer(cap)!r}, wanted {want!r}")
    for footer, what in NON_ANCHOR_FOOTERS:
        assert AGENT.menu_footer(footer) == AGENT.FOOTER_NONE, (
            f"{what}: {footer!r} -> {AGENT.menu_footer(footer)!r}")

    # 🔴 THE POSITIVE CONTROL. A ledger whose every row says "refuse" cannot tell a
    # working reader from one wired to `return FOOTER_UNKNOWN`, so the counts are
    # asserted: the driving verdict must be REACHED, and by more than one row.
    assert sum(1 for _, v, _ in ASK_FOOTER_LEDGER if v == AGENT.FOOTER_SINGLE) >= 2
    assert sum(1 for _, v, _ in ASK_FOOTER_LEDGER if v == AGENT.FOOTER_CHAIN) >= 2
    assert sum(1 for _, v, _ in ASK_FOOTER_LEDGER if v == AGENT.FOOTER_UNKNOWN) >= 10
    # 🔴 THE RELATIONSHIP THIS ASSERTS, AND IT IS NOT A SITE COUNT. The previous
    # version asserted `len(non-GRAMMAR rows) == 16` with a message telling the
    # maintainer to re-derive a SITE count to fix a ROW count — two different
    # quantities, and the equality between them was a coincidence (the two ask sites
    # contribute three rows each, several non-ask sites share a row, and no row is
    # forced to exist for a site at all). What is true and worth pinning is that the
    # corpus only GROWS: a row is a measured verdict, and deleting one silently
    # removes a refusal from the record.
    #
    # ⚠ `ASK_FOOTER_LEDGER_FLOOR` is the size at the commit that introduced this
    # assertion. Raising it is the normal outcome of adding a row; LOWERING it means
    # a measured footer stopped being checked, and the commit message has to say
    # which one and why. The site counts live in the dev-host tier
    # (`scripts/devhost-tests/test_claude_footer_sites.py`), which is where a number
    # derived from the binary belongs — and where it can be re-derived on demand.
    assert len(ASK_FOOTER_LEDGER) >= ASK_FOOTER_LEDGER_FLOOR, (
        f"the ledger SHRANK: {len(ASK_FOOTER_LEDGER)} rows against a floor of "
        f"{ASK_FOOTER_LEDGER_FLOOR}. Every row is a footer whose verdict was measured "
        "once; dropping one removes that measurement from the record. If a row was "
        "genuinely wrong, say so in the commit message and lower the floor in the "
        "same change — do not adjust the floor to make a deletion quiet")
    # And the two SYNTHETIC rows stay distinguishable from the measured ones, because
    # a grammar row is what a rule forbids rather than what a pane shows.
    assert sum(1 for r in ASK_FOOTER_LEDGER if r[2].startswith("GRAMMAR")) == 2, (
        "the GRAMMAR rows are the two clauses whose only killing case is invented; "
        "adding or removing one changes what this file claims is measured")

    # `Enter to select` with a nav hint this file does not know resolves AWAY from
    # driving — the whole point of naming the single-question hint positively.
    assert AGENT.menu_footer(
        "Enter to select · j/k to navigate · Esc to cancel") == AGENT.FOOTER_UNKNOWN
    # The anchor quoted MID-SENTENCE is not a footer field at all.
    assert AGENT.menu_footer(
        "the footer said Enter to select and I pressed it") == AGENT.FOOTER_UNKNOWN
    # 🔴 THE TIE-BREAK, AND IT MUST FAVOUR THE REFUSING VALUE. A viewport holding a
    # finished chain's footer above a live single ask carries both hints; resolving
    # that to `single` would drive a pane that may be a chain.
    assert AGENT.menu_footer(
        MENU_FOOTER_CHAIN + "\n" + MENU_FOOTER_SINGLE) == AGENT.FOOTER_CHAIN
    assert AGENT.menu_footer(
        MENU_FOOTER_SINGLE + "\n" + MENU_FOOTER_CHAIN) == AGENT.FOOTER_CHAIN
    # 🔴 BOTTOM-MOST WINS OTHERWISE: a live `/permissions` under a stale ask footer
    # must not be driven on the strength of the older one.
    assert AGENT.menu_footer(
        MENU_FOOTER_SINGLE + "\n● work happened\n"
        + PERMISSIONS_RULE_LIST_FOOTER) == AGENT.FOOTER_UNKNOWN


def test_the_bundle_STALENESS_ALARM_still_exists():
    """🔴 THE SEAM BETWEEN THE TWO HALVES OF THE FOOTER MEASUREMENT.

    The ledger above used to argue that the producing-site counts could NOT be
    machine-checked, because "the `nix build` tier CI runs has no `claude` in it, so a
    test that greps the bundle would be a test that skips in the tier that matters".
    The premise is true; the conclusion is too wide. It argues against putting the grep
    in the SAME test as the verdicts — not against having it at all. So the work is
    split: the verdicts run in every tier from this file, and the bundle re-derivation
    runs on the dev host from `scripts/devhost-tests/test_claude_footer_sites.py`.

    🔴 THIS TEST IS WHAT STOPS THE SPLIT BECOMING A DISAPPEARANCE. A dev-host-tier
    file is invisible to the hermetic tier, so deleting it would cost nothing visible
    here and the ledger's attributions would silently stop being checked against
    anything. This asserts the alarm exists AND still names each probe — a file that
    exists but no longer greps for `lo` is the same loss with a filename.
    """
    assert FOOTER_SITE_ALARM.is_file(), (
        f"the dev-host staleness alarm is gone ({FOOTER_SITE_ALARM}). "
        "ASK_FOOTER_LEDGER's per-site attributions are then checked by nothing: "
        "re-add it, or delete the attributions it was checking and say so")
    body = FOOTER_SITE_ALARM.read_text()
    # One fragment per composition path, so a probe cannot be dropped quietly. These
    # are the four the ledger's header names; the alarm owns their expected counts.
    for probe in ('chord:"enter",action:"select"',
                  'fallback:"Enter",description:"select"',
                  'hfw===void 0',
                  "Enter to select"):
        assert probe in body, (
            f"{FOOTER_SITE_ALARM.name} no longer probes for {probe!r}, so one of the "
            "composition paths ASK_FOOTER_LEDGER names is unchecked against the "
            "bundle. Every path dropped here is a path the next reader will believe "
            "is still measured")


def test_the_ask_footer_survives_the_WRAP_at_every_measured_WIDTH():
    """🔴 WIDTH IS THE DIMENSION NOBODY HAD VARIED TWICE, AND THE OLD READER FAILED IT.

    The footer WRAPS, and the previous reader tested raw substrings against the
    capture: at 30 columns `↑/↓ to navigate` is split across two lines, so a LIVE
    single-question ask resolved FOOTER_NONE and every reply to it was refused —
    inert, not wrong, but inert for a reason nobody had measured. A chain at 30 and
    40 lost `Tab/Arrow keys to navigate` the same way.

    The fix is in the reader rather than in this test: it normalises the capture
    before looking, and `Nr` joins fields with ` · ` while a wrap breaks at a space,
    so re-joining the lines restores the field string exactly.

    ⚠ MEASURED AT FIVE WIDTHS, THE NARROWEST WELL BELOW ANY LIVE PANE (all 58 on this
    host are >= 79 columns). 30 is the boundary the previous reader broke at, so it is
    kept as the row that would go red if the normalisation were dropped.
    """
    for footer, want in ((MENU_FOOTER_SINGLE, AGENT.FOOTER_SINGLE),
                         (MENU_FOOTER_FREE_TEXT, AGENT.FOOTER_SINGLE),
                         (MENU_FOOTER_CHAIN, AGENT.FOOTER_CHAIN)):
        for width in (30, 40, 48, 60, 104):
            lines, cur = [], ""
            for word in footer.split(" "):
                if cur and len(cur) + 1 + len(word) > width:
                    lines.append(cur)
                    cur = word
                else:
                    cur = (cur + " " + word) if cur else word
            lines.append(cur)
            wrapped = SINGLE_MENU_CAPTURE.replace(
                MENU_FOOTER_SINGLE, "\n".join(lines))
            assert AGENT.menu_footer(wrapped) == want, (
                f"at {width} columns the footer wraps onto {len(lines)} lines and "
                f"resolves {AGENT.menu_footer(wrapped)!r}, wanted {want!r}")
            # And the POSITIVE CONTROL for this test: the wrap must really have
            # happened at the narrow widths, or it is asserting nothing.
            if width <= 40:
                assert len(lines) >= 2, (width, lines)


def test_the_ANSWERED_RENDER_is_read_with_the_NBSPs_the_pane_really_uses():
    """🔴 THE BYTES, NOT THE PRETTY-PRINTED QUOTE. Measured in live scrollback.

    The render is `●\\xa0User answered Claude's questions:` then
    `  ⎿\\xa0·\\xa0<question> → <label>` — NON-BREAKING spaces, and the module's own
    header quotes them as ordinary ones. A reader built from that quote matches
    nothing, which is why this asserts against the measured bytes.

    ⚠ AN INVARIANT GUARD on a new function, and the ledger of the measurement.
    """
    measured = ("●\xa0User answered Claude's questions:\n"
                "\n"
                "  ⎿\xa0·\xa0Which store should the queue use? → Flatfile\n")
    assert AGENT.menu_answer_records(measured) == ["Flatfile"], (
        AGENT.menu_answer_records(measured))
    # 🔴 A VERDICT NEEDS THE PRE-KEYPRESS BASELINE, AND `[]` IS ONE. It says "the
    # pane carried no record before the keypress", so any record here IS this ask's.
    assert AGENT.menu_answer_verdict(measured, "Flatfile", []) == "match"
    assert AGENT.menu_answer_verdict(measured, "Ledger", []) == "mismatch"
    assert AGENT.menu_answer_verdict(DEFAULT_PROMPT_CAPTURE, "Flatfile", []) == ""
    # 🔴 AND WITH **NO** BASELINE THERE IS NO VERDICT AT ALL — `before=None` means
    # attribution is impossible, not that the record set was empty. This is the F3
    # fix: the previous signature had no baseline, so it read a STALE record as this
    # ask's answer and reported `delivered` with "the pane records this option as the
    # answer" about a row that predated the keypress.
    assert AGENT.menu_answer_verdict(measured, "Flatfile") == ""
    assert AGENT.menu_answer_verdict(measured, "Ledger") == ""
    # 🔴 AND A RECORD THAT HAS NOT CHANGED SINCE THE BASELINE IS STALE, WHICHEVER
    # WAY IT POINTS. This is the F2 fix: the ask had closed, its own record was not
    # drawn yet, and the stale one was still bottom-most — which the snapshot reader
    # reported as `the pane records a DIFFERENT option as the answer to this ask`,
    # a false accusation against a correct delivery.
    assert AGENT.menu_answer_verdict(measured, "Ledger", ["Flatfile"]) == ""
    assert AGENT.menu_answer_verdict(measured, "Flatfile", ["Flatfile"]) == ""

    # A CHAIN records one row per question, and a reply matching either is a match —
    # which is also why a chain that has NOT reached Submit records nothing at all.
    chain = ("●\xa0User answered Claude's questions:\n"
             "  ⎿\xa0·\xa0Which store? → Flatfile\n"
             "     ·\xa0Which region? → Frankfurt\n")
    assert AGENT.menu_answer_records(chain) == ["Flatfile", "Frankfurt"], (
        AGENT.menu_answer_records(chain))
    assert AGENT.menu_answer_verdict(CLIPPED_CHAIN_CAPTURE, "Flatfile", []) == ""

    # 🔴 THE BOTTOM-MOST BLOCK ONLY. 18 of the 57 live panes on this host carry an
    # answered record from an EARLIER ask, so a reader that took any match would
    # read a stale row as evidence about THIS delivery.
    stale = ("●\xa0User answered Claude's questions:\n"
             "  ⎿\xa0·\xa0An older ask → Ledger\n"
             "\n"
             "● Some work happened here.\n"
             "\n"
             "●\xa0User answered Claude's questions:\n"
             "  ⎿\xa0·\xa0Which store? → Flatfile\n")
    assert AGENT.menu_answer_records(stale) == ["Flatfile"], AGENT.menu_answer_records(stale)

    # A WRAPPED label carries no truncation mark, so `menu_row_holds_reply`'s
    # tolerance — not `label_matches`' — is what has to be used here.
    wrapped = ("●\xa0User answered Claude's questions:\n"
               "  ⎿\xa0·\xa0Which store? → Flatfile with the journal\n")
    assert AGENT.menu_answer_verdict(
        wrapped, "Flatfile with the journal and compaction enabled", []) == "match"

    # 🔴 THE BLOCK ENDS ON A GLYPH, NOT ON AN INDENT — and this is a REAL defect
    # this reader shipped with for one draft. The question text WRAPS at the
    # terminal width, so a continuation line starts in COLUMN 0 with an ordinary
    # letter; a reader that treated that as "a new top-level render" stopped before
    # the `→` and returned NOTHING. Measured over the live corpus: the indent rule
    # read a record on 17 of the 18 panes that have one, the glyph rule on 18.
    # ⚠ SYNTHETIC, like every fixture here — this repo is public. What is
    # reproduced is the SHAPE: a question long enough to wrap, broken MID-WORD at
    # the pane's width so the continuation opens in column 0 with a letter.
    mid_word_wrap = ("●\xa0User answered Claude's questions:\n"
                     "  ⎿\xa0·\xa0Which store should the queue use once the journal grows too la\n"
                     "rge to replay on start? → Flatfile, and compact later\n"
                     "\n"
                     "● Wiring the flatfile store now.\n")
    assert AGENT.menu_answer_records(mid_word_wrap) == [
        "Flatfile, and compact later"], AGENT.menu_answer_records(mid_word_wrap)
    # And the stop glyph really does stop it: the following render's text must not
    # be absorbed into a record.
    assert "Wiring" not in " ".join(AGENT.menu_answer_records(mid_word_wrap))


def test_the_verify_attempt_FLOOR_survives_an_operator_setting_it_below_two(monkeypatch):
    """🔴 C2: BOTH `max(2, MENU_VERIFY_ATTEMPTS)` CALL SITES, PINNED.

    `MENU_VERIFY_ATTEMPTS` is operator-settable (`TMUX_REPLY_MENU_VERIFY_ATTEMPTS`,
    default 8) and no test drove it below 2, so each `max(2, …)` SURVIVED the
    module's mutation sweep individually. Two consecutive agreeing reads are what
    make an ABSENCE evidence rather than a snapshot — the half-drawn frame is the
    reason — so a one-read verdict must not be reachable by configuration.

    ⚠ AN INVARIANT GUARD, LABELLED: the floor was already in the shipped code, so
    this is not regression coverage. It is the mutation pin the sweep lacked, and
    it fails with each site's own observable (`menu_settled`'s verdict string and
    `menu_committed`'s boolean) rather than sharing one.
    """
    monkeypatch.setattr(AGENT, "MENU_SETTLE_SECONDS", 0.0)
    monkeypatch.setattr(AGENT, "MENU_VERIFY_ATTEMPTS", 1)
    labels = ["Ledger", "Flatfile", "Type something.", "Chat about this"]

    reads = {"n": 0}

    def always_gone(pane):
        reads["n"] += 1
        return ANSWERED_CAPTURE, ""

    # 🔴 THE PANE IS GONE ON EVERY READ, which is what makes the floor OBSERVABLE
    # in the verdict and not only in a read count. Two agreeing reads settle;
    # `range(1)` gives one read, which can never agree with a predecessor, so the
    # mutant reaches the "never held one state" fallthrough instead.
    monkeypatch.setattr(AGENT, "capture_pane", always_gone)
    settled, observed = AGENT.menu_settled("%12", labels)
    assert reads["n"] == 2, (
        "menu_settled did not read the pane exactly twice at MENU_VERIFY_ATTEMPTS=1, so the "
        f"floor of two consecutive reads is not being enforced. reads={reads['n']}")
    assert (settled, observed) == (True, "the menu is no longer on screen"), (settled, observed)

    # menu_committed: the same floor, its own observable — a boolean, not a string.
    reads["n"] = 0
    monkeypatch.setattr(AGENT, "capture_pane", always_gone)
    assert AGENT.menu_committed("%12", "Flatfile") is True, (
        "menu_committed concluded 'no row holds the reply' from fewer than two frames")
    assert reads["n"] == 2, (
        "menu_committed did not read the pane exactly twice at MENU_VERIFY_ATTEMPTS=1. "
        f"reads={reads['n']}")


def test_the_row_block_rule_has_NO_LOWER_BOUND_left_to_read_as_a_guard():
    """🔴 C1: THE CLAUSE NO MUTANT COULD KILL IS GONE, and this pins the contract.

    `rows_are_one_block` read `if not 0 < gap <= MENU_ROW_GAP`. Both callers walk
    rows in screen order off a `splitlines()` enumeration, so `gap >= 1` on every
    call and dropping the lower bound SURVIVED the sweep. The commit that deleted
    `len(a) < len(b)` from `menu_row_holds_reply` gave the reason: a clause no
    mutant can kill reads as a guard while guarding nothing.

    ⚠ RED at `c8cf7b15e` — and on a CALL NO CALLER MAKES, which is exactly what
    that reason means. `rows_are_one_block(lines, 1, 1)` returns False at base and
    True here, so this is a CONTRACT pin rather than regression coverage: it
    documents the invariant the deletion relies on (a zero-length span is one
    block, because `all([])` is True) and it would have failed had the deletion
    changed anything a caller can reach. The UPPER bound is the live one, and
    `test_a_BLANK_line_between_two_numbered_rows_…` is what kills a mutant on it.
    """
    lines = ["  ❯ 1. Ledger", "     desc", "    2. Flatfile"]
    # The degenerate call the deleted clause used to reject: an empty span is one
    # block, because `all([])` is True and there is no blank line in between.
    assert AGENT.rows_are_one_block(lines, 1, 1) is True
    # And the real contract is unchanged in both directions.
    assert AGENT.rows_are_one_block(lines, 0, 2) is True
    assert AGENT.rows_are_one_block(["  ❯ 1. Ledger", "", "    2. Flatfile"], 0, 2) is False
    assert AGENT.rows_are_one_block(
        ["  ❯ 1. Ledger"] + ["     d"] * (AGENT.MENU_ROW_GAP + 1) + ["    2. Flatfile"],
        0, AGENT.MENU_ROW_GAP + 2) is False


def test_every_menu_path_REFUSAL_SHAPE_is_enumerated():
    """🔴 C3: THE COUNT IS A LEDGER, NOT A SENTENCE.

    Three rounds of this work have quoted a refusal count wrong — nine-vs-eight,
    then eight-vs-ten, then one-vs-two divergences — every time by a prose number
    copied forward while the code beside it moved. The mechanism fix is to stop
    writing the number: this enumerates the refusal shapes the MENU PATH can
    produce and pins the set two-way, so adding or removing one fails HERE with the
    missing row named.

    ⚠ THE SCOPE IS THE QUALIFIER THAT WAS MISSING. These are the refusals reachable
    on the menu path. A claimed write also passes `validate()` and `should_refuse()`
    and can receive THEIR refusals, so this is not a count of "refusals a claimed
    write can receive" — it never was, and the unqualified sentence is what made
    the number wrong.

    ⚠ AN INVARIANT GUARD, LABELLED: it is a ledger over the shapes, not a behaviour
    that regressed.
    """
    # (name, capture-or-None, reply, the substring the refusal must carry)
    approval = PERMISSION_PROMPT_CAPTURE
    SHAPES = [
        ("a question chain, tab strip visible", DESCRIBED_MULTI_QUESTION_CAPTURE,
         "Flatfile", "MULTI-question"),
        ("a question chain, tab strip clipped", CLIPPED_CHAIN_CAPTURE,
         "Flatfile", "MULTI-question"),
        ("a multiSelect list", MULTI_SELECT_MENU_CAPTURE, "Ledger", "multi-select"),
        ("a live ask with no parseable block", SCROLLED_SINGLE_ASK_CAPTURE,
         "Flatfile", "none of its option rows parsed"),
        ("a live ask caught half-drawn", SINGLE_MENU_CAPTURE.replace("  ❯ 1.", "    1."),
         "Flatfile", "half-drawn"),
        ("numbered rows with two cursors", TWO_CURSOR_MENU_CAPTURE,
         "Flatfile", "exactly one cursor row"),
        ("a chooser that is not an ask", approval, "Yes", "tool-approval prompt"),
        ("a reply matching several rows",
         SINGLE_MENU_CAPTURE.replace("    2. Flatfile\n", "    2. Ledger\n"),
         "Ledger", "option rows, so"),
        ("a reply matching nothing, no free-text row", NO_FREE_TEXT_MENU_CAPTURE,
         "something else entirely", "free-text rows"),
    ]
    seen = {}
    for name, capture, reply, wanted in SHAPES:
        kind, labels, why = AGENT.classify_pane(capture)
        if kind != AGENT.PANE_MENU:
            detail = why
        else:
            state, detail = AGENT.deliver_to_menu("%12", labels, reply, True)
            assert state == "refused", (name, state, detail)
        assert wanted in detail, (
            f"the refusal for {name!r} no longer says {wanted!r}: {detail!r}")
        seen[name] = detail

    assert len(seen) == len(SHAPES), seen
    # Two-way, and the expected count is DERIVED from the ledger's own third column
    # rather than written as a literal — the whole point of this test. Two routes
    # SHARE the chain sentence on purpose (a chain reached with its tab strip
    # visible and with it clipped is one refusal reached two ways), so the number of
    # distinct sentences is the number of distinct expectations, not of rows.
    wanted_sentences = {w for _, _, _, w in SHAPES}
    assert len(set(seen.values())) == len(wanted_sentences), (
        "the menu path's refusal sentences no longer partition the way the ledger says — "
        "either two refusals collapsed into one wording, or a row's expectation now matches "
        f"a sentence it did not before. rows={len(SHAPES)} "
        f"expectations={len(wanted_sentences)} sentences={len(set(seen.values()))}")
    # And the two refusals that are NOT on this ledger, named so the scope is closed.
    assert AGENT.deliver_to_menu("%12", ["Ledger"], "Ledger", False)[0] == "refused"
    assert AGENT.deliver_to_menu("%12", ["Ledger"], "", True)[0] == "refused"


def _shape_product():
    """Every combination of the dimensions `classify_pane` reads, as captures.

    Built as a PRODUCT rather than a hand-picked list because the point is
    exhaustiveness over the dimensions, not plausibility of each cell — a shape
    nobody has seen still must not reach the typing path.
    """
    footers = {
        "none": "",
        "single": MENU_FOOTER_SINGLE,
        "free-text": MENU_FOOTER_FREE_TEXT,
        "chain": MENU_FOOTER_CHAIN,
        "unknown-nav": "Enter to select · j/k to navigate · Esc to cancel",
        "confirm": MENU_FOOTER_STARTUP,
        "approval": MENU_FOOTER_APPROVAL,
    }
    rows = {
        "no rows": "",
        "1,2 adjacent": "{c}1. Ledger\n    2. Flatfile\n",
        "1,2 with a blank between": "{c}1. Ledger\n\n    2. Flatfile\n",
        "2,3 only (row 1 clipped)": "{c}2. Flatfile\n    3. Archive\n",
        "1, then a scroll-arrow row": "{c}1. Ledger\n↓ 2. Flatfile\n",
        "1,2,3 adjacent": "{c}1. Ledger\n    2. Flatfile\n    3. Archive\n",
    }
    cursors = {"cursor on row 1": "  ❯ ", "no cursor": "    "}
    markers = {"marker": "  ☐ Store\n\n", "no marker": ""}
    tabstrips = {"tab strip": "  ←  ☐ Store  ☐ Region  ✔ Submit  →\n\n", "no tab strip": ""}
    checkboxes = {
        "checkbox rows": "  ❯ [ ] 1. Ledger\n    [ ] 2. Flatfile\n",
        "no checkbox rows": "",
    }
    for fname, foot in footers.items():
        for rname, rowtpl in rows.items():
            for cname, cur in cursors.items():
                for mname, mark in markers.items():
                    for tname, tab in tabstrips.items():
                        for xname, cbx in checkboxes.items():
                            body = rowtpl.format(c=cur) if rowtpl else ""
                            cap = ("● Some transcript above.\n\n" + tab + mark + body
                                   + cbx + ("\n" + foot + "\n" if foot else ""))
                            yield (cap, f"{fname} / {rname} / {cname} / {mname} / "
                                        f"{tname} / {xname}")


def test_NOTHING_CARRYING_AN_ASK_FOOTER_CAN_REACH_THE_TYPING_PATH():
    """🔴 THE FILE'S OWN INVARIANT, ENFORCED RATHER THAN ASSERTED IN A DOCSTRING.

    `classify_pane` says "THE ONLY OUTCOME THAT TYPES IS PANE_TEXT, so every
    uncertainty resolves AWAY from typing". That sentence was FALSE in two
    directions and the file said it anyway for a whole release — which is the shape
    RULES.md warns about: a comment is a claim, and a claim nothing checks is the
    one that rots.

    So this checks it, over the PRODUCT of every dimension `classify_pane` reads
    (footer state x row geometry x cursor x question marker x tab strip x
    checkbox rows), in both directions:

      * PANE_TEXT — the only outcome that types — is reachable ONLY from a pane
        whose footer is FOOTER_NONE. If an ask's own footer is on screen, nothing
        gets typed, whatever the geometry has done.
      * PANE_MENU — the only outcome that presses a digit — is reachable ONLY from
        FOOTER_SINGLE, i.e. positive evidence of a one-question ask.

    ⚠ A STRUCTURAL GUARD, and it is not a substitute for the behavioural ones: it
    cannot tell you the refusal reason is right, only that no shape slips past into
    typing or pressing. The per-defect guards above are what pin the reasons.

    ⚠ AND IT NEEDS ITS OWN POSITIVE CONTROLS, because a product that produced no
    typing cells and no menu cells would pass while measuring nothing.
    """
    typed, pressed, total = [], [], 0
    for cap, what in _shape_product():
        total += 1
        kind = AGENT.classify_pane(cap)[0]
        foot = AGENT.menu_footer(cap)
        if kind == AGENT.PANE_TEXT:
            typed.append((what, foot))
            assert foot == AGENT.FOOTER_NONE, (
                f"a pane carrying an ask footer ({foot!r}) reached the TYPING path: {what}\n"
                f"{cap!r}")
        if kind == AGENT.PANE_MENU:
            pressed.append((what, foot))
            assert foot == AGENT.FOOTER_SINGLE, (
                f"a pane whose footer is {foot!r} reached the DIGIT path: {what}\n{cap!r}")

    # 🔴 THE POSITIVE CONTROLS. A zero here would be indistinguishable from a
    # product wired to nothing.
    assert total >= 200, total
    assert len(typed) >= 10, (
        f"the product produced {len(typed)} typing cells, so the first assertion above "
        "never ran and this test proves nothing about the typing path")
    assert len(pressed) >= 5, (
        f"the product produced {len(pressed)} digit cells, so the second assertion above "
        "never ran — and a classifier that drives NOTHING would pass it")
