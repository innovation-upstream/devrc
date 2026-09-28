"""STALENESS ALARM for `ASK_FOOTER_LEDGER`'s per-site attributions. Dev-host tier.

The verdict half of that measurement — every ledgered footer resolved through
`menu_footer` — lives in `scripts/tests/test_tmux_reply_agent.py` and runs in every
tier, because it needs nothing but the module. THIS half re-derives the set of
renderer SITES that can produce `Enter to select` out of the shipped Claude Code
bundle, so it needs the `claude` binary.

🔴 WHY IT IS IN DEVHOST_TARGETS AND NOT BESIDE THE LEDGER. The `nix build` sandbox
tier has no `claude`, and a skip there cannot be pinned: `run-tests.sh`'s
EXPECTED_SKIPS has one conditional predicate, `unset:VAR`, and it must be the SAME
predicate the test uses — while this one keys on a missing BINARY. A flat pin would
then red the dev host, where the binary exists and the test runs. That is the exact
reasoning recorded in `test_nvim_clipboard_behaviour.py`'s own docstring, which is why
this directory exists at all; this file applies it rather than inventing it.

The ledger's header used to conclude from the same premise that the counts could not
be machine-checked AT ALL. That conclusion was too wide — it argues against one test
doing both jobs, not against doing the second job somewhere. CI keeps the verdicts;
the dev host gains this.

🔴 WHAT THIS IS, AND WHAT IT IS NOT. It is an alarm on the LEDGER's prose going
stale, not a gate on the agent being correct. `menu_footer` refuses any footer field
its permitted set does not name, so a Claude Code whose renderer set has moved makes
these attributions out of date and the delivered BEHAVIOUR safe (FOOTER_UNKNOWN, a
named refusal, nothing typed). A red here means "go re-read the renderers and correct
the ledger", never "the agent is unsafe".

🔴 IT WILL RED ON MOST CLAUDE CODE BUMPS, AND THIS BLOCK USED TO CLAIM OTHERWISE. It
said pinning `2.1.232` would be "a permanently-red gate" while keying on the counts
means "a bump that leaves the producing sites alone stays green" — implying the counts
survive bumps the version string does not. MEASURED over the five other Claude Code
bundles on this host (1.0.128, 2.0.75, 2.0.76, 2.1.4, 2.1.9), with this file's own
probes:

    probe                          1.0.128  2.0.75  2.0.76  2.1.4  2.1.9  2.1.232
    chord:"enter",action:"select"        0       0       0      0      0       14
    {…fallback:"Enter",…"select"}        0       0       0      0      0        6
    ===void 0?"select":                  0       0       0      0      0        1
    Enter to select                     10       9       9      4      4        2

Bare `chord:"enter"` occurs 0 times in every one of those and 107 in 2.1.232, so the
footer component is new WITHIN 2.1.x — and the plain user-visible literal moved
10 -> 9 -> 9 -> 4 -> 4 -> 2. Every probe would red on every bundle above. So a red
here is the EXPECTED outcome of a bump, not a rare one, and the honest reading of one
is "the ledger is stale, go re-derive it" — which is what the failure message says and
what the block below already promises. What keying on counts buys is the RE-DERIVATION
INSTRUCTION, not immunity to bumps; the version is printed in the failure so the reader
knows which bundle to read.

⚠ AND IT IS NOT A PERMANENTLY-RED GATE IN THE SENSE THAT PHRASE NAMES, because it does
not gate: `scripts/gate.sh` defaults to `--set hermetic` and `flake.nix`'s check
derivation passes it explicitly, and `run-tests.sh` adds `DEVHOST_TARGETS` to the
target list only when the set is `all` — so neither collects this file. The one
caller that does is
`githooks/tests-on-push.sh` (`--set all`), so an INSTALLED pre-push gate would block
every push until the ledger is re-derived. `core.hooksPath` measured unset — local and
global — when this was written, and CLAUDE.md records that the value is volatile, so do
not read that as a property of the repo.

🔴 NO PROBE MAY BE KEYED ON A MINIFIER-GENERATED IDENTIFIER. Two of the four were
(`lo,` on the second, `qJl=hfw`/`:hfw` on the third): those names are BUILD ARTIFACTS,
so a rebuild that leaves the renderer untouched can still rename them and red this file
for a reason that has nothing to do with the footers. Dropping them leaves both counts
identical on 2.1.232 (6 and 1, MEASURED both ways) and on all five bundles above (0),
so the de-minified forms are what is pinned. The prose below still NAMES `lo` and the
defaulted prop, because that is how a reader finds the sites again.

⚠ AND THE PROBES ARE A FLOOR, NOT A CLOSURE — this is the whole F1 lesson, kept where
it can be read. Two of the four below exist only because someone read `lo`/`WT` and
the picker's defaulted prop after the first grep had been called complete. A third
composition path would pass all four of these unseen. Four agreeing probes are
evidence about four paths and about nothing else; `ASK_FOOTER_LEDGER`'s header says so
too, and neither place may be turned back into a completeness argument.
"""
from __future__ import annotations

import mmap
import re
import shutil
from pathlib import Path

#: (regex, expected count, what it finds). Claude Code 2.1.232.
#:
#: `lo({action,context,fallback,description})` renders
#: `nt({chord: WT(action,context,fallback), action: description})`, and `WT` returns
#: the FALLBACK when that action has no configured keybinding — which is why the six
#: `lo` sites spell `Enter to select` while carrying no literal `chord:"enter"`.
#: The picker's select action is DEFAULTED from a prop
#: (`qJl = hfw === void 0 ? "select" : hfw`) and then rendered through
#: `nt({chord:"enter", action: …})`, which is why it carries no literal either.
FOOTER_SITE_PROBES = (
    (r'chord:"enter",action:"select"', 14,
     "literal chord/action pairs — the only path the original grep could see. TWO of "
     "these are the AskUserQuestion renderers; the other 12 are other choosers"),
    (r'\{action:"[^"]*",context:"[^"]*",fallback:"Enter",description:"select"\}', 6,
     "the `lo` sites whose keybinding FALLBACK is `Enter` and whose action prop is "
     "`select`. All six resolve FOOTER_UNKNOWN, every one of them on the LAST-field "
     "rule — their terminator is `Esc to go back`. ⚠ the `lo,` prefix is DROPPED from "
     "this probe: it is a minifier-generated name, and the count is 6 with or without "
     "it"),
    (r'===void 0\?"select":', 1,
     "the generic scrollable picker's DEFAULTED select action. FOOTER_UNKNOWN in both "
     "width-dependent shapes: the FIRST-field rule at >= 120 columns, the NAV-COUNT "
     "rule below it (`↑/↓ to nav`). ⚠ the `qJl=hfw` spelling is DROPPED for the same "
     "reason as above; the count is 1 either way"),
    (r'Enter to select', 2,
     "whole literals — the `/sandbox` and `/permissions` footers, both of which were "
     "DRIVEN BY DIGIT at `c8cf7b15e`"),
)

#: A pattern that must NOT occur: the ask's anchor with one letter changed. Every
#: probe above expects a NON-ZERO count, so a reader wired to nothing fails loudly —
#: but a reader that matched EVERYTHING would pass all four by accident on a 320 MB
#: file. This is the other direction of the control.
NEGATIVE_CONTROL = rb'chord:"enterr",action:"select"'


def _bundle() -> Path | None:
    """The shipped Claude Code JS bundle, or None if `claude` is not on PATH.

    `bin/claude` is a small launcher; the JS sits beside it as `.claude-wrapped`,
    a DOTFILE and therefore invisible to a bare `ls` of that directory.
    """
    exe = shutil.which("claude")
    if not exe:
        return None
    bundle = Path(exe).resolve().parent / ".claude-wrapped"
    return bundle if bundle.is_file() else None


def test_the_footer_producing_SITE_COUNTS_still_match_the_BUNDLE():
    """Re-derive the producing-site counts and compare them with the ledger's.

    ⚠ A MISSING BINARY FAILS HERE RATHER THAN SKIPPING, and that is the contract of
    this directory: a skip in any tier is an UNPINNED SKIP that reds `run-tests.sh`,
    and the whole reason this file is in DEVHOST_TARGETS is that the tier which has no
    `claude` does not collect it at all. So absence here means the dev host lost its
    Claude Code, which is a fact worth a red line rather than a silent pass.
    """
    bundle = _bundle()
    assert bundle is not None, (
        "no `claude` on PATH, or no `.claude-wrapped` beside it. This file is in "
        "DEVHOST_TARGETS precisely because the sandbox tier does not collect it, so "
        "reaching this assertion means the DEV HOST has no Claude Code bundle to "
        "probe. It fails rather than skipping because an unpinned skip reds the runner "
        "and a silent pass would be a green that measured nothing")

    with open(bundle, "rb") as fh:
        with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as buf:
            counts = {pat: len(re.findall(pat.encode(), buf))
                      for pat, _want, _what in FOOTER_SITE_PROBES}
            bogus = len(re.findall(NEGATIVE_CONTROL, buf))

    assert bogus == 0, (
        f"the negative control matched {bogus} times in {bundle}, so these probes are "
        "not measuring what they claim. Fix the instrument before reading any count "
        "under it")

    stale = [(pat, want, counts[pat], what)
             for pat, want, what in FOOTER_SITE_PROBES if counts[pat] != want]
    assert not stale, (
        "the footer-producing SITE COUNTS in this bundle no longer match "
        "FOOTER_SITE_PROBES, so ASK_FOOTER_LEDGER's per-site attributions in "
        "scripts/tests/test_tmux_reply_agent.py are STALE:\n"
        + "\n".join(f"  {pat!r}\n      bundle has {got}, this ledger says {want}\n"
                    f"      ({what})"
                    for pat, want, got, what in stale)
        + f"\n\nbundle: {bundle}\n\n"
        "WHAT THIS IS NOT: a defect in tmux-reply-agent. An unrecognised footer field "
        "resolves FOOTER_UNKNOWN and REFUSES, so the feature fails safe whatever this "
        "count says.\n"
        "WHAT TO DO: read the renderer(s) that moved, add or correct the affected "
        "ASK_FOOTER_LEDGER rows WITH the verdict `menu_footer` gives each, then update "
        "the expected count here. Do NOT adjust the count alone — the count is what "
        "makes the rows re-derivable, and a ledger nobody can re-derive is the state "
        "this alarm exists to end.")
