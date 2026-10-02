#!/usr/bin/env python3
"""Resolve ONE level of `let`-binding indirection in `nix/home.nix`.

WHY THIS EXISTS
---------------
Several guards assert what a `home.file."…".source` entry deploys by reading the
right-hand side as TEXT. That works while the RHS spells its package path
inline, and it stops working the moment the entry is given a derivation bound in
the file's `let` block — the assertion then sees a bare identifier
(`= cairnWithReceipt`) and reports "not deployed from the pinned package" about a
tree that still is.

MEASURED, 2026-10-02: binding `.local/bin/cairn` to a wrapper derivation turned
`test_cairn_cli.py::test_cairn_is_deployed_from_the_pinned_package_not_a_bare_
store_copy` and `test_cairn_flake_pin.py::test_cairn_deploys_from_the_package_
and_the_devrc_only_launchers_stay_OUT_OF_STORE` red while the property each
describes — the pinned package is what ends up on PATH — was untouched.

🔴 WHY A SHARED MODULE AND NOT A COPY IN EACH TEST. Two call sites needed the
same predicate in the same change, which is `claude/RULES.md`'s stated trigger
for consolidating rather than patching the second copy: a predicate open-coded at
N sites is typically wrong at N-1 of them in the same direction, and here the
sites belong to different suites that are rarely run together.

🔴 WHAT THIS DELIBERATELY DOES NOT DO. It resolves exactly ONE level and REFUSES
an identifier it cannot find a binding for, rather than returning the identifier
unchanged. A silent passthrough would let a renamed or deleted binding read as "a
literal that simply does not contain the package path", which is the same red for
a completely different reason — and would let a FUTURE second level of
indirection hide a package swap behind two names while every assertion stayed
green. If a second level is ever genuinely needed, widen this with its own test;
do not loosen the refusal.

⚠ AND IT IS A TEXT HELPER, NOT AN EVALUATOR. It cannot see `//`, `if`, a function
call, or a value computed from another attrset — nix semantics are not reproduced
here. Its whole claim is: "if the RHS is a bare identifier, substitute that
identifier's `let` body so a text assertion can keep reading through it." Any
richer RHS is returned unchanged, so a guard reading it is exactly as strong (and
as weak) as it was before this module existed.
"""
from __future__ import annotations

import re

#: A nix identifier as it would appear as a whole right-hand side. Anchored, so
#: anything with a path, string, brace, interpolation or call in it is NOT one.
_BARE_IDENT = re.compile(r"^\s*=?\s*([A-Za-z_][A-Za-z0-9_'-]*)\s*$")


def assignment(text: str, key: str) -> str:
    """The whole right-hand side of `key = …;`, however many lines it spans.

    🔴 BRACE-AWARE, AND THE NAIVE VERSION IS MEASURED WRONG — the terminator is a
    `;` at brace depth 0. Splitting on the first `;` is right for a scalar and
    silently truncating for an attrset: against
    `extraSpecialArgs = { isNixOS = true; cairnPackage = …; };` it returns
    `{ isNixOS = true` and every assertion about the rest of the set fails for a
    reason that has nothing to do with the code — a red that reads like a real
    finding. (Lifted verbatim in behaviour from `test_cairn_flake_pin.py`'s own
    `_assignment`, which predates this module and still works; this copy exists so
    a caller that has no such helper does not grow a naive one.)
    """
    assert key in text, f"{key!r} is absent"
    rest = text.split(key, 1)[1]
    depth = 0
    for i, ch in enumerate(rest):
        if ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
        elif ch == ";" and depth == 0:
            return rest[:i]
    return rest


def let_binding(text: str, name: str) -> str | None:
    """The body of a top-level `name = …;` binding, or None if there is none.

    Matched at the start of a line so a MENTION of the name inside some other
    expression cannot be read as its definition.
    """
    pattern = re.compile(r"^\s*" + re.escape(name) + r"\s*=", re.MULTILINE)
    m = pattern.search(text)
    if m is None:
        return None
    return assignment(text, text[m.start():m.end()])


def resolved_source(text: str, key: str) -> str:
    """`key`'s right-hand side, with one level of `let` indirection substituted.

    Returns the assignment unchanged when it is not a bare identifier, so every
    existing text assertion keeps its exact previous meaning on an inline RHS.
    When it IS a bare identifier the binding's body is APPENDED rather than
    swapped in, so an assertion that wants to see the identifier's NAME (a guard
    pinning which wrapper is used) and one that wants to see the package path
    inside it can both read the same string.

    Raises AssertionError when the identifier has no binding — see the module
    docstring: a silent passthrough would disguise a renamed binding as a missing
    package path.
    """
    assigned = assignment(text, key)
    m = _BARE_IDENT.match(assigned)
    if m is None:
        return assigned
    name = m.group(1)
    body = let_binding(text, name)
    assert body is not None, (
        f"{key} is bound to the identifier {name!r}, but nix/home.nix has no "
        f"top-level `{name} = …;` binding to resolve it through. Either the "
        f"binding was renamed or deleted, or the RHS is not the indirection this "
        f"helper is for — do not loosen the refusal to get green."
    )
    return f"{assigned}\n{body}"
