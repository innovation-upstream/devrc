"""The operator-surface PROBE table must agree with the code it routes off.

WHAT THIS GUARDS
----------------
`claude/skills/cairn/reference/operator-surface.md` tells an operator to settle
"is this scope REFUSED or merely ABSENT?" by running the create path and reading
its answer:

    | answer            | meaning | remedy |
    | **201**           | …       | done — that WAS the fix |
    | **rc 6 `[not-found]`** | …  | edit the secret, replace the pod, re-run |

That block is PAYLOAD: an operator executes it against a live k8s store, and its
two quoted answers are the whole routing decision. Until this file existed it was
guarded by nothing — `git grep -l operator-surface -- scripts/` returned no test —
and it had already been **wrong twice, in opposite directions**:

* the pre-#1504 draft said the allowlist edit was the remedy in BOTH readings (a
  safe superset, merely wasteful);
* the round-3 draft (`cd5b23a2`) replaced it with a CLASSIFICATION table whose
  `ABSENT` row prescribed `cairn create` alone — a precondition ("allowlisted
  already") that neither of its own discriminators can establish, so it was wrong
  AND stuck for the exact two scopes that page names as live.

WHY IT IS NOT A WORD MATCH
--------------------------
`claude/RULES.md`: *"A guard can be SPELLED rather than STRUCTURAL — ask: can it
pass while the hazard exists in a different shape?"* So nothing here asserts a
sentence. The doc's table is read as a **routing object** — the answers it quotes
are extracted and cross-checked against what `scripts/subsystem-store-api/
server.py` and `scripts/cairn` ACTUALLY produce:

* the success code is read out of the server's create handler by AST, not typed
  here, and the doc's must be that code;
* the refusal pair is produced by calling the client's REAL `_classify` with the
  REAL wire token the server's 404 arm emits — so `(rc, token)` is computed, not
  quoted, and the doc must equal the computation;
* the mechanism the whole procedure rests on — `create_entry` doing
  `path.parent.mkdir(...)`, which is why an allowlisted scope with no directory
  answers 201 rather than 404 — is pinned in the server source;
* the doc's parenthetical "on the create path, the allowlist arm is the ONLY 404"
  is pinned as structure: exactly one 404 arm in `_create_entry`, and the branch
  that reaches it tests a name bound from `visible_scope_set`.

Reword the prose freely. Change any of those four facts, or make the doc quote a
code the server cannot send, and this goes red.

SCOPE — AND IT IS NARROWER THAN "THE DOC IS CORRECT"
----------------------------------------------------
🔴 Stated so the docstring is no wider than the body, which is a failure this
very subsystem produced six of in one session. This file checks **the probe
table's answers against the code that produces them**, plus the two structural
facts that make those answers true. It does **not** check the remedy prose, the
rest of the page, or any other section. The sibling class — the page RE-ASSERTING
a retracted claim ("seeding is required", "there is no create route") — is owned
by `test_subsystem_store_api.py::_unmarked_retractions`, a repo-wide needle scan
derived from `git ls-files`; this file deliberately does not build a second one.
"""

from __future__ import annotations

import ast
import importlib.util
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest

REPO = Path(__file__).resolve().parents[2]
DOC = REPO / "claude" / "skills" / "cairn" / "reference" / "operator-surface.md"
SERVER_PY = REPO / "scripts" / "subsystem-store-api" / "server.py"
CAIRN_CLI = REPO / "scripts" / "cairn"
STORE_TESTS = REPO / "scripts" / "tests" / "test_subsystem_store_api.py"

# The test the doc CITES as its evidence for the 201 row. A citation that rots is
# a doc that claims a pin it no longer has.
CITED_TEST = "test_a_scopes_FIRST_entry_creates_the_directory"

# The handler method the probe runs against, and the module-level function whose
# `mkdir` is the reason a first entry can land at all.
CREATE_HANDLER = "_create_entry"
CREATE_FUNCTION = "create_entry"
NOT_FOUND_HANDLER = "_not_found"


# =============================================================================
# Reading the DOC — the probe table as a routing object, not as prose.
# =============================================================================

# `**rc 6 `[not-found]`**` with emphasis and code fences stripped.
_RC_ANSWER = re.compile(r"\brc\s+(\d+)\s+\[([a-z][a-z0-9-]*)\]")
_STATUS_ANSWER = re.compile(r"^(\d{3})$")
_DECORATION = re.compile(r"[*_`]+")

PROBE_TABLE_HEADER = ("answer", "meaning", "remedy")


def _cells(line: str) -> list[str]:
    inner = line.strip().strip("|")
    return [_DECORATION.sub("", c).strip() for c in inner.split("|")]


def _is_rule_row(cells: list[str]) -> bool:
    return all(set(c) <= set("-: ") and c for c in cells)


@dataclass(frozen=True)
class ProbeTable:
    """What the doc's probe table says, reduced to state."""

    success_codes: tuple[int, ...]
    refusals: tuple[tuple[int, str], ...]
    unreadable_rows: tuple[str, ...]


def parse_probe_table(text: str) -> ProbeTable | None:
    """The `| answer | meaning | remedy |` table, or `None` if there is none.

    🔴 A ROW THIS CANNOT CLASSIFY IS REPORTED, NEVER SKIPPED. Silently ignoring an
    answer it does not recognise is how a scan walks a table and returns a
    reassuring zero — the shape `claude/RULES.md` names under positive controls.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if "|" not in line:
            continue
        if tuple(c.lower() for c in _cells(line)) != PROBE_TABLE_HEADER:
            continue
        success: list[int] = []
        refusals: list[tuple[int, str]] = []
        unreadable: list[str] = []
        for row in lines[i + 1:]:
            if "|" not in row:
                break
            cells = _cells(row)
            if _is_rule_row(cells):
                continue
            answer = cells[0]
            if (m := _RC_ANSWER.search(answer)) is not None:
                refusals.append((int(m.group(1)), m.group(2)))
            elif (m := _STATUS_ANSWER.match(answer)) is not None:
                success.append(int(m.group(1)))
            else:
                unreadable.append(answer)
        return ProbeTable(tuple(success), tuple(refusals), tuple(unreadable))
    return None


# =============================================================================
# Reading the CODE — AST for the server, the live function for the client.
# =============================================================================


def _server_tree() -> ast.Module:
    return ast.parse(SERVER_PY.read_text(encoding="utf-8"), str(SERVER_PY))


def _find_function(tree: ast.AST, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(
        f"{name} is not a function in the tree being read — this guard's "
        f"extraction is broken, or the code it pins was renamed"
    )


def _calls(fn: ast.AST, attr: str) -> list[ast.Call]:
    return [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == attr
    ]


def _const_int_args(calls: list[ast.Call]) -> set[int]:
    out: set[int] = set()
    for call in calls:
        if call.args and isinstance(call.args[0], ast.Constant):
            value = call.args[0].value
            if isinstance(value, int):
                out.add(value)
    return out


def _header_literal(call: ast.Call, key: str) -> str | None:
    for kw in call.keywords:
        if kw.arg != "headers" or not isinstance(kw.value, ast.Dict):
            continue
        for k, v in zip(kw.value.keys, kw.value.values):
            if (
                isinstance(k, ast.Constant)
                and k.value == key
                and isinstance(v, ast.Constant)
            ):
                return v.value
    return None


def _load_cairn_cli():
    """Exec `scripts/cairn` as a module — it has no `.py` extension.

    Same loader shape as `test_cairn_skill_verb_ledger.py`. Reading the real
    module rather than regexing the source is what lets the refusal pair be
    COMPUTED by `_classify` instead of restated here.
    """
    spec = importlib.util.spec_from_loader(
        "cairn_cli_operator_surface", loader=None, origin=str(CAIRN_CLI)
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__file__ = str(CAIRN_CLI)
    exec(  # noqa: S102 — the file under test IS the thing being read
        compile(CAIRN_CLI.read_text(encoding="utf-8"), str(CAIRN_CLI), "exec"),
        mod.__dict__,
    )
    return mod


@dataclass(frozen=True)
class CodeFacts:
    """Everything the doc's probe table is checked AGAINST, read from source."""

    create_success_codes: frozenset[int]
    create_direct_404s: int
    create_not_found_audit_tokens: tuple[str, ...]
    not_found_wire_token: str
    allowlist_gates_the_404: bool
    creates_the_parent_directory: bool
    # `X-Store-Status` token -> every HTTP code `server.py` pairs it with.
    token_codes: dict[str, frozenset[int]]
    classify: Callable[[int, str, str], object]


def read_code_facts() -> CodeFacts:
    tree = _server_tree()
    handler = _find_function(tree, CREATE_HANDLER)
    not_found = _find_function(tree, NOT_FOUND_HANDLER)
    create_fn = _find_function(tree, CREATE_FUNCTION)

    responds = _calls(handler, "_respond")
    codes = _const_int_args(responds)

    nf_calls = _calls(handler, NOT_FOUND_HANDLER)
    audit_tokens = tuple(
        c.args[1].value
        for c in nf_calls
        if len(c.args) > 1 and isinstance(c.args[1], ast.Constant)
    )

    wire = ""
    for call in _calls(not_found, "_respond"):
        if call.args and getattr(call.args[0], "value", None) == 404:
            wire = _header_literal(call, "X-Store-Status") or ""

    return CodeFacts(
        create_success_codes=frozenset(c for c in codes if 200 <= c < 300),
        create_direct_404s=sum(1 for c in responds if _const_int(c) == 404),
        create_not_found_audit_tokens=audit_tokens,
        not_found_wire_token=wire,
        allowlist_gates_the_404=_allowlist_gates_the_404(handler),
        creates_the_parent_directory=_makes_the_parent_directory(create_fn),
        token_codes=_token_codes(tree),
        classify=_load_cairn_cli()._classify,
    )


def _token_codes(tree: ast.Module) -> dict[str, frozenset[int]]:
    """Every `X-Store-Status` token `server.py` sends, and with which codes.

    🔴 THIS IS WHAT KEEPS THE GUARD FROM BECOMING A PERMANENTLY-RED GATE, which
    `claude/RULES.md` calls worse than no gate. The first cut classified every
    refusal row the doc quotes as if it were the create path's 404 — so adding a
    perfectly correct third row (`rc 9 [already-exists]`, which the create path
    really does answer, at 412) would have gone red against a doc that had just
    got BETTER. The token is now looked up in the server's own emissions, so each
    row is checked against the code that row's token actually rides on.
    """
    out: dict[str, set[int]] = {}
    for call in _calls(tree, "_respond"):
        code = _const_int(call)
        token = _header_literal(call, "X-Store-Status")
        if code is None or not token:
            continue
        out.setdefault(token, set()).add(code)
    return {k: frozenset(v) for k, v in out.items()}


def _const_int(call: ast.Call) -> int | None:
    if call.args and isinstance(call.args[0], ast.Constant):
        value = call.args[0].value
        return value if isinstance(value, int) else None
    return None


def _allowlist_gates_the_404(handler: ast.FunctionDef) -> bool:
    """Is the branch reaching `_not_found` tested on the TOKEN ALLOWLIST?

    The doc's rc-6 row means "not in your token row", and its parenthetical says
    the allowlist arm is the only 404 on this path. Pinning only that *a* 404
    exists would pass if the arm were rewired to any other condition, which is
    exactly the "different shape" a spelled guard walks past. So this asks
    whether the `if` guarding the 404 tests a name bound from `visible_scope_set`
    — the one place the repo narrows a caller to its scopes.
    """
    allowlist_names = {
        t.id
        for node in ast.walk(handler)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and _callee_name(node.value) == "visible_scope_set"
        for t in node.targets
        if isinstance(t, ast.Name)
    }
    if not allowlist_names:
        return False
    for node in ast.walk(handler):
        if not isinstance(node, ast.If):
            continue
        if not any(_calls(stmt, NOT_FOUND_HANDLER) for stmt in node.body):
            continue
        tested = {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)}
        if tested & allowlist_names:
            return True
    return False


def _callee_name(call: ast.Call) -> str:
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    if isinstance(call.func, ast.Name):
        return call.func.id
    return ""


def _makes_the_parent_directory(create_fn: ast.FunctionDef) -> bool:
    """`path.parent.mkdir(...)` — the 201 row is only true because of this call."""
    for node in ast.walk(create_fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "mkdir"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "parent"
        ):
            return True
    return False


# =============================================================================
# THE CHECK — one pure function, so the controls exercise the real logic.
# =============================================================================


def disagreements(doc_text: str, facts: CodeFacts) -> list[str]:
    """Every way the doc's probe table disagrees with `facts`.

    🔴 PURE ON BOTH ARGUMENTS SO IT CAN BE DRIVEN FROM BOTH SIDES. The real test
    calls it with the real doc and the real code; the mutation battery calls it
    with a mutated doc, and the code-drift battery with mutated facts. A guard
    whose logic can only ever be run against the passing case is a guard nobody
    has watched fail.
    """
    table = parse_probe_table(doc_text)
    if table is None:
        return [
            f"{DOC.name} has no `| {' | '.join(PROBE_TABLE_HEADER)} |` table — the "
            f"operator has no probe to route off. The round-3 draft (cd5b23a2) "
            f"failed exactly this way: it shipped a CLASSIFICATION table instead."
        ]

    out: list[str] = []
    for answer in table.unreadable_rows:
        out.append(
            f"probe-table row {answer!r} is neither an HTTP status nor an "
            f"`rc N [token]` pair — this guard cannot check it, and an answer it "
            f"cannot check is an answer the operator routes off unguarded"
        )
    if not table.success_codes:
        out.append("the probe table quotes no success status at all")
    if not table.refusals:
        out.append("the probe table quotes no `rc N [token]` refusal at all")

    for code in table.success_codes:
        if code not in facts.create_success_codes:
            out.append(
                f"the doc routes a {code} to 'done', but `{CREATE_HANDLER}` can "
                f"only answer {sorted(facts.create_success_codes)} in the 2xx "
                f"range — the operator would be waiting for a status the server "
                f"never sends"
            )

    for rc, token in table.refusals:
        codes = facts.token_codes.get(token)
        if not codes:
            out.append(
                f"the doc tells the operator to expect `[{token}]`, but "
                f"`server.py` never sends that `X-Store-Status` token — it sends "
                f"{sorted(facts.token_codes)}"
            )
            continue
        produced = {
            (r.exit_code, r.status)
            for r in (facts.classify(code, token, "") for code in sorted(codes))
        }
        if produced != {(rc, token)}:
            out.append(
                f"the doc quotes `rc {rc} [{token}]`, but `scripts/cairn` "
                f"produces {sorted(produced)} for that token on the HTTP "
                f"code(s) `server.py` pairs it with ({sorted(codes)})"
            )

    if not facts.creates_the_parent_directory:
        out.append(
            f"`server.py::{CREATE_FUNCTION}` no longer creates the entry's parent "
            f"directory, so an allowlisted scope with no directory cannot answer "
            f"201 — the doc's success row, and the whole probe, are false"
        )
    if facts.create_direct_404s:
        out.append(
            f"`{CREATE_HANDLER}` answers 404 directly at "
            f"{facts.create_direct_404s} site(s) as well as through "
            f"`{NOT_FOUND_HANDLER}` — the doc's 'the allowlist arm is the ONLY "
            f"404' no longer holds, so rc 6 stops being diagnostic"
        )
    if len(facts.create_not_found_audit_tokens) != 1:
        out.append(
            f"`{CREATE_HANDLER}` reaches `{NOT_FOUND_HANDLER}` from "
            f"{len(facts.create_not_found_audit_tokens)} arms "
            f"{list(facts.create_not_found_audit_tokens)} — the doc reads rc 6 as "
            f"'not in your token row', which needs exactly one"
        )
    if not facts.allowlist_gates_the_404:
        out.append(
            f"the 404 arm in `{CREATE_HANDLER}` is no longer gated on a name "
            f"bound from `visible_scope_set` — the doc's remedy ('edit the "
            f"secret, replace the pod') is a remedy for the ALLOWLIST, and this "
            f"is what makes it the right one"
        )
    return out


@pytest.fixture(scope="module")
def facts() -> CodeFacts:
    return read_code_facts()


@pytest.fixture(scope="module")
def doc_text() -> str:
    return DOC.read_text(encoding="utf-8")


# =============================================================================
# POSITIVE CONTROLS — a zero from a scan that walked nothing is the failure mode.
# =============================================================================


def test_the_probe_table_was_actually_found(doc_text: str) -> None:
    """POSITIVE CONTROL. Without this every assertion below passes vacuously."""
    table = parse_probe_table(doc_text)
    assert table is not None, (
        f"no `| {' | '.join(PROBE_TABLE_HEADER)} |` table in {DOC} — either the "
        f"doc stopped routing off a probe, or this parser is broken. Both are "
        f"findings; neither is a pass."
    )
    assert table.success_codes, "the table yielded no success status"
    assert table.refusals, "the table yielded no `rc N [token]` refusal"
    assert not table.unreadable_rows, (
        f"rows this parser could not classify: {list(table.unreadable_rows)}"
    )


def test_the_code_facts_were_actually_read(facts: CodeFacts) -> None:
    """POSITIVE CONTROL for the OTHER side — an empty AST read is not agreement.

    Anchored on facts whose disappearance would be a redesign rather than a
    rename, so this cannot be satisfied by an extraction that returned nothing.
    """
    assert facts.create_success_codes, (
        f"no 2xx `_respond` found in `{CREATE_HANDLER}` — suspect the AST read"
    )
    assert facts.not_found_wire_token, (
        f"no `X-Store-Status` literal found on `{NOT_FOUND_HANDLER}`'s 404"
    )
    assert facts.create_not_found_audit_tokens, (
        f"`{CREATE_HANDLER}` appears to reach `{NOT_FOUND_HANDLER}` from no arm "
        f"at all — suspect the AST read, not the server"
    )
    assert facts.not_found_wire_token in facts.token_codes, (
        f"the token map does not even contain "
        f"{facts.not_found_wire_token!r} — suspect `_token_codes`, not the server"
    )
    assert callable(facts.classify), "`scripts/cairn::_classify` did not load"


# =============================================================================
# THE GUARD.
# =============================================================================


def test_the_probe_table_agrees_with_the_code_it_routes_off(
    doc_text: str, facts: CodeFacts
) -> None:
    """The doc's quoted answers ARE what the server sends and the client returns."""
    found = disagreements(doc_text, facts)
    assert not found, (
        "`claude/skills/cairn/reference/operator-surface.md` routes an operator "
        "off answers the code does not produce:\n  - " + "\n  - ".join(found)
    )


def test_the_doc_cites_a_test_that_exists_and_pins_the_success_code(
    doc_text: str, facts: CodeFacts
) -> None:
    """Citation integrity — the doc names a test as its evidence for 201.

    🔴 THE BODY IS EXACTLY AS WIDE AS THIS SENTENCE: it checks the named test
    exists and that its source mentions the success code the table quotes. It
    does NOT check what that test asserts at run time — `test_subsystem_store_api
    .py` owns that, and claiming otherwise here would be a description wider than
    its implementation.
    """
    assert CITED_TEST in doc_text, (
        f"{DOC.name} no longer cites {CITED_TEST}. If the evidence moved, move "
        f"the citation; a procedure citing nothing is the state this guard exists "
        f"to prevent."
    )
    tree = ast.parse(STORE_TESTS.read_text(encoding="utf-8"), str(STORE_TESTS))
    names = {
        n.name
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert CITED_TEST in names, (
        f"{DOC.name} cites `{CITED_TEST}`, which {STORE_TESTS.name} no longer "
        f"defines — the doc's 201 row cites a pin that is gone"
    )
    cited = _find_function(tree, CITED_TEST)
    body = ast.unparse(cited)
    success = sorted(facts.create_success_codes)
    assert any(str(code) in body for code in success), (
        f"`{CITED_TEST}` no longer mentions any of {success}, so it is no longer "
        f"evidence for the doc's success row"
    )


# =============================================================================
# THE MUTATION BATTERY — this guard has been watched to fail, on both sides.
# =============================================================================

# Each entry rewrites the REAL doc and must be REPORTED. `claude/RULES.md`: a
# mutant must die with THIS guard's own finding, not some other check's error —
# so `test_each_doc_mutation_is_reported` asserts the reported text names the
# thing that was broken, never just that the list is non-empty.
DOC_MUTATIONS: tuple[tuple[str, str, str, str], ...] = (
    ("rc drifts", "rc 6 `[not-found]`", "rc 7 `[not-found]`", "rc 7"),
    ("token drifts", "rc 6 `[not-found]`", "rc 6 `[ref-unknown]`", "ref-unknown"),
    ("success code drifts", "| **201** |", "| **200** |", "200"),
    (
        "the answer becomes unreadable prose",
        "| **201** |",
        "| **it works** |",
        "it works",
    ),
    (
        "the table reverts to a classification",
        "| answer | meaning | remedy |",
        "| reading | remedy | remedy |",
        "no `| answer | meaning | remedy |` table",
    ),
)


@pytest.mark.parametrize(
    "label,old,new,needle",
    DOC_MUTATIONS,
    ids=[m[0].replace(" ", "-") for m in DOC_MUTATIONS],
)
def test_each_doc_mutation_is_reported(
    doc_text: str, facts: CodeFacts, label: str, old: str, new: str, needle: str
) -> None:
    """Break the doc on purpose; watch THIS guard's own finding come back."""
    assert doc_text.count(old) >= 1, (
        f"the mutation battery's anchor {old!r} is not in {DOC.name} — the "
        f"battery is scoring survivors against a doc it cannot mutate, which is "
        f"a broken harness, not a clean result"
    )
    mutated = doc_text.replace(old, new, 1)
    assert mutated != doc_text
    found = disagreements(mutated, facts)
    assert found, f"MUTANT SURVIVED ({label}): {old!r} -> {new!r} went unreported"
    assert any(needle in f for f in found), (
        f"the mutant died for the WRONG REASON ({label}): nothing in {found} "
        f"names {needle!r}"
    )


def test_an_unmutated_doc_is_the_batterys_negative_control(
    doc_text: str, facts: CodeFacts
) -> None:
    """The control the battery needs: unmutated input must report NOTHING.

    Without it, a `disagreements` that returned a finding for every input would
    score every mutant as killed while checking nothing.
    """
    assert disagreements(doc_text, facts) == []


def _drifted(facts: CodeFacts, **changes) -> CodeFacts:
    from dataclasses import replace

    return replace(facts, **changes)


CODE_DRIFTS: tuple[tuple[str, dict, str], ...] = (
    (
        "the server stops sending 201",
        {"create_success_codes": frozenset({202})},
        "never sends",
    ),
    (
        "create_entry stops making the scope directory",
        {"creates_the_parent_directory": False},
        "parent directory",
    ),
    (
        "a second 404 arm appears on the create path",
        {"create_direct_404s": 1},
        "ONLY",
    ),
    (
        "the 404 arm stops being the allowlist",
        {"allowlist_gates_the_404": False},
        "visible_scope_set",
    ),
    (
        "the 404 is reachable from two arms",
        {"create_not_found_audit_tokens": ("scope-unknown", "ref-unknown")},
        "exactly one",
    ),
    (
        "the server renames the wire token",
        {"token_codes": {"gone": frozenset({404})}},
        "never sends that",
    ),
    (
        "the token starts riding on a second code the client maps differently",
        {"token_codes": {"not-found": frozenset({404, 429})}},
        "[404, 429]",
    ),
)


@pytest.mark.parametrize(
    "label,changes,needle",
    CODE_DRIFTS,
    ids=[d[0].replace(" ", "-") for d in CODE_DRIFTS],
)
def test_each_code_drift_is_reported(
    doc_text: str, facts: CodeFacts, label: str, changes: dict, needle: str
) -> None:
    """The OTHER direction: the doc stands still and the CODE moves.

    A relationship guard that only ever fires on one side is the "docstring
    claims a relationship, body inspects one side" defect `claude/RULES.md`
    names. Both sides are driven here.
    """
    found = disagreements(doc_text, _drifted(facts, **changes))
    assert found, f"CODE-DRIFT MUTANT SURVIVED ({label}): {changes}"
    assert any(needle in f for f in found), (
        f"the drift died for the WRONG REASON ({label}): nothing in {found} "
        f"names {needle!r}"
    )


def test_a_CORRECT_extra_refusal_row_does_not_go_red(
    doc_text: str, facts: CodeFacts
) -> None:
    """The false-red control, and it is the reason `_token_codes` exists.

    🔴 `claude/RULES.md`: *a permanently-red gate is worse than no gate*. The
    first cut of this file classified EVERY refusal row as if it were the create
    path's 404, so a correct third row — `rc 9 [already-exists]`, which
    `_entry_exists` really does answer at 412 — would have failed the guard
    against a doc that had just got BETTER. This pins that it does not.
    """
    anchor = "| **rc 6 `[not-found]`**"
    assert anchor in doc_text, "control anchor missing — the control is broken"
    widened = doc_text.replace(
        anchor,
        "| **rc 9 `[already-exists]`** | the ref is taken | `cairn append` |\n"
        + anchor,
        1,
    )
    table = parse_probe_table(widened)
    assert table is not None and len(table.refusals) == 2, (
        f"the control did not actually widen the table: {table}"
    )
    assert disagreements(widened, facts) == [], (
        "a correct second refusal row is reported as a disagreement — this guard "
        "would go red against a doc improvement"
    )


def test_the_refusal_pair_is_computed_end_to_end_not_quoted(
    facts: CodeFacts,
) -> None:
    """The seam: server wire token -> client `_classify` -> what the doc quotes.

    🔴 Pinned because it is the SEAM, and `claude/RULES.md` warns that "verified
    in isolation" is the new vacuous green: `server.py` and `scripts/cairn` are
    each well tested alone, and the doc's `rc 6 [not-found]` is a claim about the
    two of them TOGETHER. `_classify` consults a token table BEFORE the code
    table, so a future `not-found` row there would silently change the rc an
    operator sees while every isolated test stayed green.
    """
    refused = facts.classify(404, facts.not_found_wire_token, "")
    assert refused.status == facts.not_found_wire_token, (
        "the client no longer surfaces the server's own `X-Store-Status` token"
    )
    cli = _load_cairn_cli()
    assert refused.exit_code == cli.EXIT_WRITE_REFUSED, (
        f"a create-path 404 now exits {refused.exit_code}, not "
        f"EXIT_WRITE_REFUSED ({cli.EXIT_WRITE_REFUSED})"
    )
    assert facts.not_found_wire_token not in cli._WRITE_STATUS_TOKEN_EXITS, (
        f"`{facts.not_found_wire_token}` gained a row in "
        f"`_WRITE_STATUS_TOKEN_EXITS`, which is consulted BEFORE the code table "
        f"— the rc the doc quotes is no longer the rc the operator gets"
    )
