"""THE policy for what may be delivered to a tmux pane as literal text.

🔴 ONE PREDICATE, ONE PLACE — AND THIS MODULE EXISTS BECAUSE THE SECOND COPY WAS
ALREADY WRONG. `session-write` reached this rule the hard way and wrote the
reason down; `tmux-reply-agent` then shipped its own version at the
NETWORK-FACING site, and that version was a DENYLIST:

    def has_control(value):
        return any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value)

It refuses NUL, `\\n`, `\\r`, ESC and DEL — and passes every C1 control
(U+0080–U+009F), U+0085 NEL (which some terminals treat as a line break), and
U+2028/U+2029. That is the exact shape `session-write`'s docstring records as the
bug it had already fixed: *"the previous version asked 'is this character one of
the four we banned', so U+000F (Ctrl-O) passed and `type` executed arbitrary
commands in any shell pane."* Two sites, one rule, wrong at the one reachable
from the network — which is `claude/RULES.md`'s "a predicate open-coded at N
sites is typically wrong at N−1 of them, in the same direction", measured.

So the rule now lives here and both callers import it. There is no second copy
to disagree with, and `test_tmux_text_policy_is_the_only_copy.py` fails if one
appears.

🔴 IT IS AN ALLOWLIST, AND THE DIRECTION IS THE WHOLE POINT. The question asked
is "is this character safe to deliver", never "is this one of the dangerous ones
we thought of". The set of characters bound to an accept-line variant is a
property of the OPERATOR'S shell config, which no process here can read — so a
denylist is a guess about someone else's keymap, and this one is not.

`str.isprintable()` is False for exactly the Unicode categories that make a
codepoint a control or an invisible: Cc (NUL, `\\n`, `\\r`, `\\x0f`, ESC, DEL),
Cf, Cs, Co (private use — the U+E000 that once rendered a diff identical to an
unedited line), Cn, Zl, Zp and Zs-except-ASCII-space. Tab is Cc and is
re-permitted explicitly; it is the one control character with an ordinary
typographic use and it cannot accept a line.
"""
from __future__ import annotations

import unicodedata

#: The character whose TRAILING occurrence tmux eats off an argv token. tmux's
#: own command parser treats it as a separator, so `send-keys -l -- 'x;'`
#: delivers `x`.
#:
#: 🔴 It is spelled here rather than imported from `session-resolve`, because a
#: long-running daemon must not load a 3,000-line CLI to learn one character.
#: `session-write` PINS the two against each other at import time, so the
#: duplication cannot drift silently — an assertion, not a convention.
TMUX_ARGV_SEPARATOR = ";"

#: The one control character re-permitted. See the module docstring.
TEXT_EXTRA_ALLOWED = ("\t",)

#: Characters `send-keys -l` delivers as a real Enter (measured).
#: 🔴 NOT A GATE — both are non-printable, so `TEXT_IS_ALLOWED` has already
#: refused them. This exists only to give them a message that names the hazard.
SUBMITTING_CHARS = ("\n", "\r")

#: Refused, but not control characters — a DIAGNOSIS set, never a gate.
TEXT_INVISIBLE_CATEGORIES = ("Cf", "Zl", "Zp", "Zs")


def TEXT_IS_ALLOWED(ch: str) -> bool:
    """THE allowlist predicate. Every delivered codepoint passes through here."""
    return ch in TEXT_EXTRA_ALLOWED or ch.isprintable()


def first_disallowed(text: str):
    """-> (index, char) of the first codepoint the allowlist refuses, or None.

    The offset is COMPUTED from the scan, INCLUDING offset 0 — `session-write`
    records that `enumerate(text[1:], 1)` survived its whole suite and its
    committed mutation sweep, because every fixture put the bad character later.
    """
    for idx, ch in enumerate(text):
        if not TEXT_IS_ALLOWED(ch):
            return idx, ch
    return None


def describe_disallowed(idx: int, ch: str) -> str:
    """A human reason for a refused codepoint. Wording only — the gate has
    already decided. Names the codepoint by NUMBER, never by rendering it: the
    whole class here is characters that render as nothing or as something else.
    """
    if ch == "\x00":
        return f"a NUL at offset {idx}"
    if ch in SUBMITTING_CHARS:
        name = "a newline" if ch == "\n" else "a carriage return"
        return f"{name} at offset {idx}, which tmux delivers as a real Enter"
    category = unicodedata.category(ch)
    if category in TEXT_INVISIBLE_CATEGORIES:
        return (f"an invisible character U+{ord(ch):04X} "
                f"({unicodedata.name(ch, 'unnamed')}, category {category}) at offset {idx}")
    return f"a non-printable character U+{ord(ch):04X} (category {category}) at offset {idx}"


def ends_with_separator(text: str) -> bool:
    """Whether tmux would EAT the last character off this token."""
    return text.endswith(TMUX_ARGV_SEPARATOR)
