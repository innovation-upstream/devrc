"""A TINY NIX STRING EVALUATOR for `nix/i3/config.nix`.

The file is `{ isLaptop ? false }: let <fragments> in ''<body>''`, where every
fragment is `if isLaptop then <string> else <string>`. Rendering it properly
would mean asking `nix`, which is ground truth and CANNOT RUN in the nix-build
check tier that gates merges (no nested nix, and that is the authoritative
tier). So: parse the three shapes this file actually uses, and REFUSE loudly on
anything else — `render` raises rather than returning a body with an unresolved
`${…}` in it, because a silently unsubstituted interpolation would make every
assertion built on it pass over text no host ever gets.

🔴 WHY THIS LIVES IN `testlib` RATHER THAN IN ONE TEST FILE. Two guards now read
the config THROUGH this evaluator — `test_i3_game_mode.py` (does the game mode
exist on both hosts) and `test_i3_picker_centering.py` (is the mention-open
picker centred on both hosts) — and a second copy of a parser is a second thing
that can silently disagree with the first about what a host actually receives.
One renderer, one place; its positive control is
`test_i3_render_seam.py::test_the_renderer_really_renders_two_different_configs`.
"""

from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]
I3_CONFIG = REPO / "nix" / "i3" / "config.nix"

#: The two values of `isLaptop`, in the order every parametrized guard uses.
HOSTS = ("workbench", "laptop")

_INTERP = re.compile(r"\$\{(\w+)\}")


def _nix_string_at(text, pos):
    """Parse the nix string literal starting at/after `pos`.

    Handles `''…''` and `"…"`. Returns (value, index just past the literal).
    """
    i = pos
    while i < len(text) and text[i] in " \t\n":
        i += 1
    if text.startswith("''", i):
        end = text.index("''", i + 2)
        return text[i + 2:end], end + 2
    if text[i] == '"':
        end = text.index('"', i + 1)
        return text[i + 1:end], end + 1
    raise AssertionError(
        "nix/i3/config.nix fragment at offset %d is not a string literal this "
        "evaluator understands (%r…) — extend it rather than letting the "
        "config render with an unresolved interpolation" % (i, text[i:i + 40]))


def fragments(header):
    """{name: (laptop_value, workbench_value)} for each `if isLaptop` binding."""
    out = {}
    for m in re.finditer(r"^  (\w+) =", header, re.M):
        name = m.group(1)
        region_end = len(header)
        nxt = re.search(r"^  \w+ =", header[m.end():], re.M)
        if nxt:
            region_end = m.end() + nxt.start()
        region = header[m.start():region_end]
        cond = region.find("if isLaptop then")
        if cond < 0:
            continue                      # not host-conditional; nothing to do
        then_val, after = _nix_string_at(region, cond + len("if isLaptop then"))
        els = region.index("else", after)
        else_val, _ = _nix_string_at(region, els + len("else"))
        out[name] = (then_val, else_val)
    return out


def render(is_laptop: bool) -> str:
    """The i3 config as it is written to ~/.config/i3/config on that host."""
    text = I3_CONFIG.read_text()
    marker = "\nin\n''\n"
    head, body = text.split(marker, 1)
    body = body[:body.rindex("''")]
    frags = fragments(head)

    def sub(m):
        name = m.group(1)
        assert name in frags, (
            "`${%s}` in the i3 config body is not an `if isLaptop` fragment "
            "this evaluator can resolve — extend `fragments`, or this test "
            "renders text no host ever receives" % name)
        return frags[name][0 if is_laptop else 1]

    rendered = _INTERP.sub(sub, body)
    assert "${" not in rendered, (
        "unresolved interpolation left in the rendered config: "
        + rendered[rendered.index("${"):rendered.index("${") + 60])
    return rendered
