#!/usr/bin/env python3
"""opencode's permission resolver — ONE implementation, two callers.

WHY THIS FILE EXISTS
--------------------
`scripts/tests/test_opencode_config.py` already carried a faithful port of
opencode 1.18.18's `Wildcard.match` + its `findLast` resolver, validated against
the real engine (`opencode debug agent <a> --tool bash`: 18 deny cases and 3
allow cases agreed). `opencode-dispatch preflight` needs exactly that predicate
to answer "would this command in the brief resolve to `ask`, i.e. be
AUTO-REJECTED mid-run?".

RULES.md 🔴 "One rule, one place": a second copy of a resolver is a predicate
that will disagree eventually and silently. So the port moved HERE and both
callers import it. The test file keeps everything that is genuinely about the
TEST (the agent-frontmatter ruleset, the deletion mutants, the ledgers); this
module owns only the parts a non-test caller also needs.

SCOPE — read before using `effective_bash_action`
-------------------------------------------------
🔴 `command` must be a SINGLE command node. opencode parses the shell with
tree-sitter and matches each `command` descendant's text separately, denying the
whole call if ANY node resolves deny (measured: `cd /tmp && git status` is
checked as just `git status`, since `cd` is in opencode's skip set). This model
deliberately does NOT reimplement that AST split. Callers that start from a
whole command LINE must split it first — `scripts/claude-hooks/guard_core.py`'s
`split_commands()` is the repo's splitter and the one `brief_scan.py` uses.
Chains are SAFER than this model would suggest, never less safe: an extra node
can only add a deny.

🔴 `ask` is not a control. `opencode run` AUTO-REJECTS an `ask` (it prints
"auto-rejecting"); only the interactive TUI turns one into a prompt. That is the
whole reason preflight cares: an `ask` inside an unattended dispatch is a
mid-run abandonment, not a question.
"""
from __future__ import annotations

import functools
import json
import re
from pathlib import Path

# The repo's canonical config. `scripts/opencode/lib/` -> `scripts/opencode/`.
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "opencode.jsonc"


def strip_jsonc(text: str) -> str:
    """Strip `//` and `/* */` comments, leaving string literals untouched.

    Hand-rolled rather than regex because opencode.jsonc contains `https://`
    inside a string — a naive `//`-to-end-of-line regex would eat the rest of
    the `$schema` line and the file would not parse.
    """
    out = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def load_config(path: Path | None = None) -> dict:
    """Parse opencode.jsonc, PRESERVING key order.

    json.loads yields a dict, and dicts preserve insertion order — which is what
    makes the resolver model possible at all (LAST match wins over the file's
    own key order).

    Deliberately UNCACHED and MUTABLE: `scripts/tests/test_opencode_engine.py`
    mutates a parsed config in place to build its "trailing `git *: allow`"
    mutant, and a cached mutable result is poisonable.
    """
    return json.loads(strip_jsonc((path or DEFAULT_CONFIG_PATH).read_text()))


# --------------------------------------------------------------------------- #
# 🔴 the resolver model
#
# Faithful port of opencode 1.18.18's `Wildcard.match` + the `findLast` resolver:
#
#     pattern.replace(/[.+^${}()|[\]\\]/g,"\\$&").replace(/\*/g,".*")
#            .replace(/\?/g,".")   ->   new RegExp("^"+p+"$","s")
#
# so `*` is an unrestricted `.*` (dotAll) that CROSSES spaces, `/` and `-`.
# --------------------------------------------------------------------------- #
_GLOB_ESCAPE = re.compile(r"[.+^${}()|\[\]\\]")


@functools.lru_cache(maxsize=None)
def wildcard_match(text: str, pattern: str) -> bool:
    # Memoised: pure function of (text, pattern), and the test ledgers resolve
    # ~50 rules x ~115 commands x ~65 patterns per pass, once per detector
    # control. Without this that file took 46 s; with it, ~5 s.
    t = text.replace("\\", "/")
    p = _GLOB_ESCAPE.sub(lambda m: "\\" + m.group(0), pattern.replace("\\", "/"))
    p = p.replace("*", ".*").replace("?", ".")
    # opencode special case: a pattern ending in " *" also matches without it,
    # so `rg *` matches a bare `rg`.
    if p.endswith(" .*"):
        p = p[:-3] + "( .*)?"
    return re.match("^" + p + "$", t, re.S) is not None


def resolve_with_rule(rules, command: str):
    """`(action, pattern)` — LAST match wins, over an EXPLICIT ordered ruleset.

    🔴 THE ONLY implementation of the resolution loop in this repo. It briefly
    had two inside the test file alone, and two copies with nothing pinning them
    together is a predicate that will disagree eventually and silently.

    The PATTERN is returned alongside the action because a warning that says
    "this resolves to ask" and cannot name the glob is not actionable — the
    operator's next move is to widen `--dir`, reword the brief, or accept the
    stall, and all three need the rule's name. `pattern` is None only when
    nothing matched at all (opencode's fallback, `ask`).
    """
    action, matched = "ask", None      # opencode's fallback when nothing matches
    for pattern, act in rules:
        if wildcard_match(command, pattern):
            action, matched = act, pattern
    return action, matched


def resolve(rules, command: str) -> str:
    """`resolve_with_rule` without the pattern. One loop, two shapes."""
    return resolve_with_rule(rules, command)[0]


def base_bash_rules(config: dict | None = None) -> tuple:
    """The ORDERED (pattern, action) list opencode resolves a bash command
    against, for an agent with NO agent-level `permission.bash` of its own.

    Order mirrors the real merge: the built-in `permission:"*" pattern:"*"
    allow` first, then the global config block. A caller with an agent block
    appends it LAST — which is precisely why an agent-level `"*": "allow"` would
    nullify every global rule.
    """
    cfg = config if config is not None else load_config()
    return (("*", "allow"),) + tuple(cfg["permission"]["bash"].items())


def effective_bash_action(command: str, config: dict | None = None) -> str:
    """The action opencode's PRIMARY agent resolves for one command node."""
    return resolve(base_bash_rules(config), command)


def external_directory_rules(config: dict | None = None) -> tuple:
    """The ORDERED (pattern, action) list for `permission.external_directory`.

    🔴 TWO SHAPES, one code path. This key was a bare SCALAR (`"ask"`) until the
    skills-tree carve-out made it a map, and both shapes are live in the wild —
    an older checkout, another host, or a revert all produce the scalar. A caller
    that assumes a dict raises `AttributeError` on it, which is a crash where a
    verdict was wanted. A scalar is therefore modelled as a single `*` rule.

    Same resolver as bash (LAST match wins, `ask` when nothing matches), because
    two copies of a resolution loop disagree eventually and silently.
    """
    cfg = config if config is not None else load_config()
    ed = cfg["permission"].get("external_directory", "ask")
    if isinstance(ed, str):
        return ((("*"), ed),)
    return tuple(ed.items())


def effective_external_directory_action(path: str, config: dict | None = None) -> str:
    """The action opencode resolves for a read OUTSIDE the project directory."""
    return resolve(external_directory_rules(config), path)
