"""🔴 AN i3 `workspace … output <NAME>` IS SILENT WHEN THE OUTPUT NAME IS WRONG.

i3 matches a `workspace … output` directive against the outputs RandR reports.
A name matching no output is not an error and not a warning — the directive is
simply inert, i3 falls back to picking an output itself, and the workspace lands
somewhere arbitrary. The config parses, the session starts, and the only symptom
is that a workspace shows up on the wrong physical screen, on a host where the
second monitor is usually unplugged anyway. Nothing fails loudly.

So the hazard is NOT "somebody deleted the pinning". It is that the pinning can
be PRESENT, well-formed, and bound to a name nothing will ever match — a typo
(`HDMI0`, `hdmi-0`, `DP0`), or an output renamed in the xrandr line above it
while these lines were left behind.

WHY THE GUARD IS SHAPED LIKE THIS. Asserting the literal strings `DP-0` and
`HDMI-0` would be a guard on WORDS: it would pass for a config whose xrandr line
had been rewritten for different hardware, which is exactly when the pinning goes
stale. The real invariant is a RELATIONSHIP between two statements that must
agree — every output named by a `workspace … output` directive must be an output
the `xrandr` line in the SAME block actually configures. That is what this file
pins, so re-hardware the machine and the guard follows you; break the agreement
and it goes red naming the offending output.

It also pins CO-LOCATION: both statements must live inside the `isLaptop`-guarded
`monitorLayout` branch. Hoisting the pinning out of that branch would send
`workspace 3 output HDMI-0` to a laptop that has no HDMI-0 — inert in exactly the
way described above, and invisible.

NOT A SNAPSHOT, deliberately. This mechanism keeps no state: i3 re-evaluates the
directives on every start, so there is nothing to refresh and nothing to go
stale. That is the whole reason workspace-output pinning was chosen over an i3
layout snapshot (`i3-save-tree` / `append_layout`, `i3-resurrect`, `i3-restore`),
each of which reintroduces a periodic-save-with-no-verifier — the failure class
that had already cost this repo two silent outages on 2026-09-04 (see
`56c68cc7` and `cc409f82`). Researched and rejected the same day; the i3 layer
was measured at 2 workspaces and 6 windows, 5 of them the same terminal, with
every swallow criterion identical across them.
"""

from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]
CONFIG = REPO / "nix" / "i3" / "config.nix"

# `workspace <n> output <OUT> [<OUT> …]` — the target is a FALLBACK LIST, so every
# name on the line must resolve, not just the first.
_WS_OUTPUT = re.compile(r"^\s*workspace\s+(\S+)\s+output\s+(.+?)\s*$", re.MULTILINE)
# `--output <NAME>` as written by the xrandr invocation.
_XRANDR_OUTPUT = re.compile(r"--output\s+(\S+)")


def _config_text() -> str:
    assert CONFIG.is_file(), f"{CONFIG} is missing — did nix/i3 move?"
    return CONFIG.read_text()


def _monitor_layout_block(text: str) -> str:
    """The `else ''…''` body of `monitorLayout`, which both statements must share.

    Sliced rather than parsed: this is a nix string literal, and the guard only
    needs the region between `monitorLayout =` and the `'';` that closes it.
    """
    start = text.index("monitorLayout =")
    end = text.index("'';", start)
    return text[start:end]


def test_workspace_output_pinning_is_present_and_inside_the_monitor_layout_block():
    """Deletion, and hoisting it out of the isLaptop guard, both go red here."""
    block = _monitor_layout_block(_config_text())
    found = _WS_OUTPUT.findall(block)
    # Positive control: a regex that matches nothing would make every other
    # assertion in this file vacuously true.
    assert found, (
        "NO `workspace … output` DIRECTIVE inside the monitorLayout block of "
        f"{CONFIG.relative_to(REPO)}. Either the pinning was removed — i3 will "
        "then place workspaces on whichever output it picks — or it was moved "
        "OUT of the `isLaptop`-guarded branch, which would pin a laptop with no "
        "second monitor to an output that does not exist there. Both are silent "
        "at runtime; that is why this is a test."
    )


def test_every_pinned_output_is_one_the_xrandr_line_configures():
    """The seam: the two statements must name the same outputs.

    A name matching no output is inert — i3 neither errors nor warns — so a typo
    or a rename in the xrandr line leaves a directive that looks correct and does
    nothing.
    """
    block = _monitor_layout_block(_config_text())
    configured = set(_XRANDR_OUTPUT.findall(block))
    assert configured, (
        "NO `--output` FOUND in the monitorLayout block — the xrandr invocation "
        "this guard compares against is gone or was reworded. Without it the "
        "comparison below would pass trivially for every possible name."
    )

    pinned: dict[str, str] = {}
    for ws, targets in _WS_OUTPUT.findall(block):
        for out in targets.split():
            pinned[out] = ws

    unknown = {out: ws for out, ws in pinned.items() if out not in configured}
    assert not unknown, (
        "PINNED TO AN OUTPUT THE xrandr LINE DOES NOT CONFIGURE — i3 silently "
        "IGNORES an output name it cannot match, so the workspace falls back to "
        "whichever output i3 picks and the pinning is inert:\n"
        + "\n".join(
            f"  workspace {ws} output {out}   <- {out!r} is not configured"
            for out, ws in sorted(unknown.items())
        )
        + f"\nOutputs the xrandr line configures: {', '.join(sorted(configured))}"
    )
