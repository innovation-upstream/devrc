"""Parity gates for the two hand-maintained links in the agent-facing surface:
the `browser` CLI's SUBCOMMANDS list, and SKILL.md's ops table.

WHY THIS EXISTS
---------------
`context` once shipped DEAD on `main`: it was in `extension/protocol.js`, the
service worker, the CLI and `manifest.json`, but never in `server.py`'s
ALLOWED_OPS. That regression class is now structurally guarded at the wire layer
by ``test_server.py::test_ping_op_set_mirrors_the_extension_protocol_js``, which
PARSES protocol.js and asserts set-equality with server.py rather than restating
a list.

Nothing did the same one layer OUT, at the surface an agent actually touches:

* `browser`'s ``SUBCOMMANDS`` (24 names) maps to the wire ops entirely by hand,
  across ~20 ``case`` arms. An op added to the server + extension but never
  given a CLI name is invisible to every agent; a CLI arm dispatching an op the
  server no longer allows is a dead subcommand that fails at runtime with a 400.
* ``SKILL.md`` is the ONLY surface a Claude agent reads. An op missing from its
  ops table is functionally dead to the agent even when every wire layer is
  perfect -- the same failure mode as `context`, relocated into the docs.

Both gaps were called out by the browser-bridge surface audit (2026-08-02, G1
and G2). This module closes them the same way the wire layer was closed: parse
the authoritative source, do not restate it.

🔴 HARNESS DISCIPLINE. A parser that silently returns an empty set makes every
parity assertion below pass vacuously -- that is exactly how a harness reports
success while testing nothing. Every test therefore begins with
``_inventory()``, which asserts a NON-EMPTY, plausible-cardinality result before
any parity claim is made, and ``test_parsers_fail_loudly_*`` demonstrates those
guards firing against known-bad input.

This module is part of the hermetic set (``scripts/run-tests.sh``), so it runs
in ``nix build .#checks.x86_64-linux.pytests`` -- the repo's real pre-merge gate.
"""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server as S  # noqa: E402

BB = Path(__file__).resolve().parent.parent
BROWSER_CLI = BB / "browser"
SKILL_MD = BB / "SKILL.md"

# Plausible-cardinality floors. These are NOT the contract (the parsed sets are);
# they exist so a broken regex fails as "the harness is broken" instead of
# silently satisfying every set comparison with the empty set.
MIN_WIRE_OPS = 18          # 18 since `context` landed
MIN_SUBCOMMANDS = 20       # 24 today
MIN_DOC_ROWS = 15          # 23 today (incl. the header + separator rows)


# --------------------------------------------------------------------------- #
# Parsers -- each RAISES on a source it cannot make sense of, never returns {}.
# --------------------------------------------------------------------------- #
def _read(path: Path) -> str:
    if not path.is_file():
        raise AssertionError(
            f"HARNESS BROKEN: {path} does not exist -- a parity test cannot be "
            f"green against a source it never read")
    return path.read_text(encoding="utf-8")


def parse_subcommands(path: Path = BROWSER_CLI) -> list[str]:
    """The CLI's single-source subcommand list (`browser`'s SUBCOMMANDS=)."""
    src = _read(path)
    m = re.search(r'^SUBCOMMANDS="([^"]+)"', src, re.M)
    if not m:
        raise AssertionError(
            f"HARNESS BROKEN: no `SUBCOMMANDS=\"...\"` assignment in {path.name}. "
            f"The CLI's subcommand list moved or changed shape -- fix this parser "
            f"before reading any verdict from this module.")
    return m.group(1).split()


def mask_shell_noncode(src: str) -> str:
    """Blank out everything in a shell script that is NOT live code, preserving
    every byte offset and newline so the result can be regex-scanned positionally.

    Masked: comments, single-/double-quoted strings (INCLUDING multi-line ones),
    backquoted spans, and heredoc bodies. Command substitutions ``$( … )`` are
    NOT masked even inside double quotes, because that is real command position
    -- the CLI genuinely dispatches from there (``resp="$(cmd_op screenshot …)"``).

    WHY. A `cmd_op X` MENTION inside a docstring, heredoc, error message or
    prose string is not a dispatch of `X`. The first version of this parser
    matched any line whose first non-space character was not `#`, and harvested a
    phantom op from a Python docstring on a merged tree (see
    tests/fixtures/cmd_op_parse_rig.sh for the measurement and the pinned cases).

    Deliberately a small lexer, not a shell parser: it handles the constructs the
    `browser` CLI actually uses. If it over-masks, the non-empty guard in
    ``parse_dispatched_ops`` and the exact-set control in
    ``test_the_dispatch_parser_ignores_mentions_and_keeps_calls`` both fail loudly
    -- over-tightening cannot pass vacuously.
    """
    out = list(src)
    n = len(src)
    i = 0
    state = "CODE"          # CODE | SQ | DQ | BQ
    substack: list[str] = []  # states suspended by an open `$(`
    heredocs: list[tuple[str, bool]] = []  # (delimiter, strip_leading_tabs)

    def blank(a: int, b: int) -> None:
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        c = src[i]

        if state == "CODE":
            if c == "\\" and i + 1 < n:
                i += 2
                continue
            # `#` starts a comment only at the start of a word.
            if c == "#" and (i == 0 or src[i - 1] in " \t\n;&|(<"):
                j = src.find("\n", i)
                j = n if j < 0 else j
                blank(i, j)
                i = j
                continue
            if src.startswith("$(", i):
                substack.append(state)
                i += 2
                continue
            if c == ")" and substack:
                state = substack.pop()
                i += 1
                continue
            if c == "'":
                state = "SQ"
                blank(i, i + 1)
                i += 1
                continue
            if c == '"':
                state = "DQ"
                blank(i, i + 1)
                i += 1
                continue
            if c == "`":
                state = "BQ"
                blank(i, i + 1)
                i += 1
                continue
            m = re.match(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2", src[i:])
            if m and not src.startswith("<<<", i):
                heredocs.append((m.group(3), bool(m.group(1))))
                i += m.end()
                continue
            if c == "\n" and heredocs:
                i += 1
                for delim, strip_tabs in heredocs:
                    while i < n:
                        eol = src.find("\n", i)
                        eol = n if eol < 0 else eol
                        line = src[i:eol]
                        probe = line.lstrip("\t") if strip_tabs else line
                        blank(i, eol)
                        i = min(eol + 1, n)
                        if probe.strip() == delim:
                            break
                heredocs = []
                continue
            i += 1
            continue

        if state == "SQ":
            if c == "'":
                state = "CODE"
            blank(i, i + 1)
            i += 1
            continue

        if state == "BQ":
            if c == "`":
                state = "CODE"
            blank(i, i + 1)
            i += 1
            continue

        # DQ
        if c == "\\" and i + 1 < n:
            blank(i, i + 2)
            i += 2
            continue
        if src.startswith("$(", i):
            # NOT blanked: `$(` inside double quotes REOPENS command position,
            # and the `(` is what the command-position regex anchors on. Blanking
            # it here silently loses `resp="$(cmd_op screenshot "$full")"` -- a
            # real dispatch (measured: the parser returned 18 ops instead of 19).
            substack.append("DQ")
            state = "CODE"
            i += 2
            continue
        if c == '"':
            state = "CODE"
            blank(i, i + 1)
            i += 1
            continue
        blank(i, i + 1)
        i += 1

    return "".join(out)


#: A `cmd_op` call in genuine COMMAND POSITION: at the start of a statement, or
#: immediately after a separator/opening construct. A substring mention such as
#: "from `cmd_op stderr` it emits" or "usage: cmd_op OP [FIELDS]" is not a call,
#: and neither is one preceded by a word character or a quote.
_CMD_OP_CALL = re.compile(r"(?:^|[;&|(]|\bthen\b|\bdo\b|\belse\b)\s*cmd_op\s+([A-Za-z]+)", re.M)


def parse_dispatched_ops(path: Path = BROWSER_CLI) -> set[str]:
    """Ops the CLI actually puts on the wire, parsed from its `cmd_op <op>` call
    sites.

    Two independent conditions, both required -- a MENTION of `cmd_op X` is not a
    dispatch of `X`:
      1. the occurrence survives ``mask_shell_noncode`` (not in a comment, string
         or heredoc body), and
      2. it sits in genuine command position (start of statement, or after
         ``;`` ``&&`` ``||`` ``|`` ``(`` ``$(`` ``then`` ``do`` ``else``).
    """
    ops = set(_CMD_OP_CALL.findall(mask_shell_noncode(_read(path))))
    if not ops:
        raise AssertionError(
            f"HARNESS BROKEN: parsed ZERO `cmd_op <op>` dispatch sites from "
            f"{path.name}. Every op-parity assertion in this module would pass "
            f"vacuously. If the CLI is fine, the command-position regex or the "
            f"shell masker has been over-tightened -- fix the parser, do not "
            f"relax the callers.")
    return ops


def parse_skill_ops_table(path: Path = SKILL_MD) -> set[str]:
    """Command names documented in SKILL.md's `## Ops` table.

    Takes the first column of every table row and extracts the leading
    identifier of each backtick span, so a row like ``| `close` / `release` |``
    yields both names and ``| `key <Enter\\|Tab>` |`` yields ``key``. The row is
    split on UNESCAPED pipes only -- several rows carry ``\\|`` inside a backtick
    span, and splitting naively silently drops those rows (measured: it loses
    `emulate` and `key`, i.e. it under-reports and would make a missing-doc test
    fail for the wrong reason).
    """
    src = _read(path)
    sec = re.search(r"^## Ops\s*$(.*?)^## ", src, re.M | re.S)
    if not sec:
        raise AssertionError(
            f"HARNESS BROKEN: no `## Ops` section found in {path.name} -- the "
            f"documentation-parity tests would be asserting against nothing.")
    rows = [ln for ln in sec.group(1).splitlines() if ln.startswith("|")]
    if len(rows) < MIN_DOC_ROWS:
        raise AssertionError(
            f"HARNESS BROKEN: parsed only {len(rows)} table rows from the `## Ops` "
            f"section of {path.name} (expected >= {MIN_DOC_ROWS}).")
    names: set[str] = set()
    for row in rows:
        cells = re.split(r"(?<!\\)\|", row)
        if len(cells) < 2:
            continue
        for span in re.findall(r"`([^`]+)`", cells[1]):
            word = re.match(r"[a-zA-Z][a-zA-Z0-9]*", span)
            if word:
                names.add(word.group(0))
    if not names:
        raise AssertionError(
            f"HARNESS BROKEN: parsed ZERO command names from the `## Ops` table.")
    return names


def _inventory() -> tuple[set[str], set[str], list[str]]:
    """Parse + SELF-CHECK everything. Called FIRST by every test below, so a
    broken parser reports itself instead of certifying a vacuous green."""
    wire = set(S.ALLOWED_OPS)
    server_ops = set(S.SERVER_OPS)
    assert len(wire) >= MIN_WIRE_OPS, (
        f"HARNESS BROKEN: server.py ALLOWED_OPS has only {len(wire)} ops "
        f"(expected >= {MIN_WIRE_OPS})")
    assert server_ops, "HARNESS BROKEN: server.py SERVER_OPS is empty"
    subs = parse_subcommands()
    assert len(subs) >= MIN_SUBCOMMANDS, (
        f"HARNESS BROKEN: parsed only {len(subs)} names from the CLI's "
        f"SUBCOMMANDS (expected >= {MIN_SUBCOMMANDS})")
    return wire, server_ops, subs


# --------------------------------------------------------------------------- #
# The CLI-name classification.
#
# This is NOT a plain set-equality with the wire ops, and modelling it as one
# would be wrong: the CLI has 24 subcommand names against 18 wire ops + 1 server
# op. The asymmetry is real and deliberate -- some names are ALIASES for another
# name, and some are CLIENT-ONLY (an HTTP GET, or an exec of another program)
# that never produce a /cmd body at all.
#
# So classify every name explicitly, and assert the classification is
# EXHAUSTIVE. A new CLI name cannot be added without landing in exactly one
# bucket; an unclassified name fails the test by name.
# --------------------------------------------------------------------------- #

#: CLI subcommand name -> the wire op it dispatches (`cmd_op <op>`).
CLI_WIRE_OPS = {
    "ping": "ping",
    "context": "context",
    "open": "open",
    "close": "close",
    "wake": "wake",
    "emulate": "emulate",
    "activate": "activate",
    "html": "getHtml",        # the one name that differs from its wire op
    "text": "text",
    "eval": "eval",
    "tabs": "tabs",
    "nav": "nav",
    "screenshot": "screenshot",
    "frames": "frames",
    "click": "click",
    "type": "type",
    "key": "key",
    "upload": "upload",
}

#: CLI subcommand name -> the server-side-only op it dispatches (never reaches
#: the extension; see server.py SERVER_OPS).
CLI_SERVER_OPS = {
    "release": "release",
}

#: CLI alias -> the subcommand it is an alias OF. Both must dispatch the same
#: wire op; the test asserts that rather than trusting the label.
CLI_ALIASES = {
    # `js` exists because Claude Code's worktree-isolation guard refuses any
    # command containing the literal token `eval` (it matches the WORD, not the
    # behaviour). Same op on the wire either way.
    "js": "eval",
}

#: CLI name -> why it dispatches no op at all. A reason string is REQUIRED so a
#: name cannot be parked here to silence the exhaustiveness check.
CLI_CLIENT_ONLY = {
    "health": "GET /health -- an HTTP endpoint on the bridge server, not a /cmd "
              "op. Triage view: connected instances, liveness ages, active-tab "
              "url/title.",
    "whoami": "GET /whoami -- read-only global identity (host label, per-instance "
              "labels/versions/extension_stale, active-tab DOMAIN only). No tab, "
              "no /cmd body.",
    "instances": "GET /instances -- the JSON form of the connected-instance list. "
                 "An HTTP endpoint, not a /cmd op.",
    "agent": "execs the `browser-agent` wrapper (the autonomous opencode "
             "browser-agent). It is a separate PROGRAM, not a bridge op; the ops "
             "it may itself use are gated in browser_tool_impl.mjs.",
}


# --------------------------------------------------------------------------- #
# Harness negative controls -- watch the guards fire before trusting any green.
# --------------------------------------------------------------------------- #
def test_parsers_fail_loudly_on_a_missing_source(tmp_path):
    """A parser pointed at a file that does not exist must RAISE, not return an
    empty set. An empty set would satisfy every parity assertion in this module
    while testing nothing."""
    missing = tmp_path / "nope"
    for fn in (parse_subcommands, parse_dispatched_ops, parse_skill_ops_table):
        with pytest.raises(AssertionError, match="HARNESS BROKEN|does not exist"):
            fn(missing)


def test_parsers_fail_loudly_on_a_source_of_the_wrong_shape(tmp_path):
    """The other half of the negative control: a file that EXISTS but has no
    recognizable structure must also raise."""
    decoy = tmp_path / "decoy"
    decoy.write_text("# nothing to see here\nprint('hi')\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="no `SUBCOMMANDS="):
        parse_subcommands(decoy)
    with pytest.raises(AssertionError, match="parsed ZERO `cmd_op"):
        parse_dispatched_ops(decoy)
    with pytest.raises(AssertionError, match="no `## Ops` section"):
        parse_skill_ops_table(decoy)


#: The rig's dispatch sites, stated INDEPENDENTLY of the parser (read them off
#: tests/fixtures/cmd_op_parse_rig.sh, which is written to be read by a human).
RIG = Path(__file__).resolve().parent / "fixtures" / "cmd_op_parse_rig.sh"
RIG_REAL_DISPATCHES = {
    "realplain", "realindented", "realsubshell", "realafterthen",
    "realafterand", "realafteror", "realsubgroup", "realaftersemi",
}


def test_the_dispatch_parser_ignores_mentions_and_keeps_calls():
    """A `cmd_op X` MENTION is not a dispatch of `X`.

    RED-FIRST, MEASURED against this rig with the previous parser (which matched
    any line not starting with `#`): it harvested 7 phantom ops --
    phantombacktick, phantomdocstring, phantomdq, phantomheredoc,
    phantommultilinedq, phantomquotedheredoc, phantomsq -- while keeping all 8
    real ones. The real-world instance was a Python docstring inside a
    `python3 -c` block on a merged tree, which produced a phantom wire op named
    `stderr` and a diagnostic that sent the reader hunting for an op that does
    not exist.
    """
    got = parse_dispatched_ops(RIG)
    leaked = sorted(o for o in got if o.startswith("phantom"))
    assert not leaked, (
        f"the dispatch parser harvested MENTION(s) of cmd_op as if they were "
        f"dispatches: {', '.join(leaked)} -- a phantom op makes the parity gate "
        f"red pointing at a wire op that does not exist. See the rig for which "
        f"shell construct each one lives in. [phantoms: {', '.join(leaked)}]")
    missing = sorted(RIG_REAL_DISPATCHES - got)
    assert not missing, (
        f"the dispatch parser MISSED genuine dispatch(es): {', '.join(missing)} "
        f"-- it has been over-tightened, which silently shrinks the op set every "
        f"parity test in this module compares against. [missing: "
        f"{', '.join(missing)}]")
    assert got == RIG_REAL_DISPATCHES, (
        f"rig parse is not exactly the declared dispatch set. "
        f"extra={sorted(got - RIG_REAL_DISPATCHES)} "
        f"missing={sorted(RIG_REAL_DISPATCHES - got)}")


def test_the_dispatch_parser_did_not_over_tighten_on_the_real_cli():
    """The other half of the over-tightening control, against the REAL script.

    A hardened matcher is trivially "fixed" by tightening it into a permanent
    no-op, and the empty set satisfies every parity assertion in this module. So
    pin the shapes the CLI actually uses -- in particular `screenshot`, which is
    dispatched from inside a command substitution nested in double quotes
    (`resp="$(cmd_op screenshot "$full")"`). MEASURED: an earlier version of the
    masker blanked the `$(` and lost exactly that one op, 19 -> 18, while every
    other test in this file stayed green.
    """
    wire, server_ops, _subs = _inventory()
    dispatched = parse_dispatched_ops()
    assert len(dispatched) >= MIN_WIRE_OPS, (
        f"HARNESS BROKEN: only {len(dispatched)} dispatch sites parsed from the "
        f"`browser` CLI (expected >= {MIN_WIRE_OPS})")
    assert "screenshot" in dispatched, (
        "`screenshot` is dispatched from `resp=\"$(cmd_op screenshot ...)\"` -- a "
        "command substitution nested inside double quotes. Losing it means the "
        "masker stopped treating `$(` as reopening command position.")
    assert "getHtml" in dispatched, "`getHtml` is dispatched from a plain call site"
    assert dispatched == (wire | server_ops), (
        f"the CLI's real dispatch sites no longer equal server.py's op inventory. "
        f"only-in-CLI={sorted(dispatched - (wire | server_ops))} "
        f"only-in-server={sorted((wire | server_ops) - dispatched)}")


def test_the_cli_uses_no_live_backtick_command_substitution():
    """`mask_shell_noncode` masks backquoted spans conservatively, because the
    only backticks in the CLI are inside prose comments (113 of them, MEASURED).
    If a real ```...``` substitution were ever introduced, a dispatch inside it
    would be silently masked away -- so pin the assumption rather than leave it
    implicit."""
    masked = mask_shell_noncode(_read(BROWSER_CLI))
    assert "`" not in masked, (
        "the `browser` CLI now contains a backtick OUTSIDE a comment/string. "
        "mask_shell_noncode treats backquoted spans as non-code, so a `cmd_op` "
        "dispatch inside one would be silently dropped. Rewrite it as $( ) or "
        "teach the masker about backtick substitution.")


def test_the_parsed_inventory_is_non_empty_and_plausible():
    """The positive control for the guards above: on the REAL sources every
    parser must return a plausible, non-trivial set containing known members."""
    wire, server_ops, subs = _inventory()
    assert {"getHtml", "eval", "screenshot", "context"} <= wire
    assert "release" in server_ops
    assert {"html", "js", "whoami"} <= set(subs)
    assert len(parse_dispatched_ops()) >= MIN_WIRE_OPS
    assert len(parse_skill_ops_table()) >= MIN_SUBCOMMANDS


# --------------------------------------------------------------------------- #
# T2 -- CLI SUBCOMMANDS <-> op inventory parity.
# --------------------------------------------------------------------------- #
def test_every_cli_subcommand_is_classified():
    """EXHAUSTIVENESS. A new name in SUBCOMMANDS must be classified as a wire
    op, a server op, an alias, or client-only -- an unclassified name fails
    here, by name, rather than drifting in unnoticed."""
    _wire, _server_ops, subs = _inventory()
    buckets = {
        "CLI_WIRE_OPS": set(CLI_WIRE_OPS),
        "CLI_SERVER_OPS": set(CLI_SERVER_OPS),
        "CLI_ALIASES": set(CLI_ALIASES),
        "CLI_CLIENT_ONLY": set(CLI_CLIENT_ONLY),
    }
    classified = set().union(*buckets.values())

    unclassified = sorted(set(subs) - classified)
    assert not unclassified, (
        f"UNCLASSIFIED CLI subcommand(s): {', '.join(unclassified)} -- present in "
        f"`browser`'s SUBCOMMANDS but in none of {', '.join(buckets)}. Put each in "
        f"exactly one bucket in {Path(__file__).name}: CLI_WIRE_OPS if it "
        f"dispatches a wire op, CLI_SERVER_OPS for a server-side-only op, "
        f"CLI_ALIASES if it is another name for an existing subcommand, or "
        f"CLI_CLIENT_ONLY (with a written reason) if it produces no /cmd body.")

    stale = sorted(classified - set(subs))
    assert not stale, (
        f"STALE classification: {', '.join(stale)} are classified in "
        f"{Path(__file__).name} but are NOT in `browser`'s SUBCOMMANDS -- the "
        f"subcommand was renamed or removed and the classification outlived it.")

    for a, b in ((x, y) for x in buckets for y in buckets if x < y):
        overlap = sorted(buckets[a] & buckets[b])
        assert not overlap, (
            f"CLI name(s) in TWO buckets ({a} and {b}): {', '.join(overlap)} -- "
            f"the classification must be a partition, not a tagging.")


def test_every_wire_op_has_a_cli_name():
    """server.py -> CLI direction. An op the bridge accepts but the CLI cannot
    dispatch is invisible to every agent: this is the `context` bug one layer
    out, at the surface the agent actually touches."""
    wire, server_ops, _subs = _inventory()
    named = set(CLI_WIRE_OPS.values()) | set(CLI_SERVER_OPS.values())
    missing = sorted((wire | server_ops) - named)
    assert not missing, (
        f"wire op(s) with NO `browser` CLI subcommand: {', '.join(missing)} -- "
        f"server.py accepts {'them' if len(missing) > 1 else 'it'}, but no agent "
        f"can reach {'them' if len(missing) > 1 else 'it'} through the CLI. Add a "
        f"subcommand (and a SKILL.md ops-table row), or drop the op from "
        f"server.py's ALLOWED_OPS/SERVER_OPS.")


def test_every_classified_cli_op_exists_on_the_wire():
    """CLI -> server.py direction. A subcommand dispatching an op the server no
    longer allows is a DEAD subcommand that fails at runtime with a 400."""
    wire, server_ops, _subs = _inventory()
    known = wire | server_ops
    for name, op in sorted({**CLI_WIRE_OPS, **CLI_SERVER_OPS}.items()):
        assert op in known, (
            f"CLI subcommand `{name}` dispatches op `{op}`, which is in NEITHER "
            f"server.py's ALLOWED_OPS nor SERVER_OPS -- `browser {name}` is a dead "
            f"subcommand and will fail at runtime with a server 400. "
            f"[subcommand: {name}] [op: {op}]")
    for name, op in sorted(CLI_WIRE_OPS.items()):
        assert op in wire, (
            f"CLI subcommand `{name}` is classified as a WIRE op but `{op}` is a "
            f"server-side-only op -- reclassify it into CLI_SERVER_OPS. "
            f"[subcommand: {name}] [op: {op}]")
    for name, op in sorted(CLI_SERVER_OPS.items()):
        assert op in server_ops, (
            f"CLI subcommand `{name}` is classified as a SERVER-only op but `{op}` "
            f"is not in server.py's SERVER_OPS. [subcommand: {name}] [op: {op}]")


def test_the_classification_matches_what_the_cli_actually_dispatches():
    """Anchors the hand-written classification above to the CLI's real `cmd_op`
    call sites, so the table cannot claim a mapping the script does not make."""
    _wire, _server_ops, _subs = _inventory()
    dispatched = parse_dispatched_ops()
    claimed = set(CLI_WIRE_OPS.values()) | set(CLI_SERVER_OPS.values())
    missing = sorted(dispatched - claimed)
    assert not missing, (
        f"the `browser` CLI dispatches op(s) no classification entry claims: "
        f"{', '.join(missing)} -- a subcommand is sending {'these' if len(missing) > 1 else 'this'} "
        f"on the wire while {Path(__file__).name} says nothing does.")
    phantom = sorted(claimed - dispatched)
    assert not phantom, (
        f"classification claims op(s) the CLI never dispatches: "
        f"{', '.join(phantom)} -- no `cmd_op {phantom[0] if phantom else ''}` call "
        f"site exists in `browser`. The mapping is fiction.")


def test_every_alias_resolves_to_the_same_wire_op_as_its_target():
    """An alias whose target changed (or whose target is itself an alias) is a
    silent behaviour change: `browser js` and `browser eval` must stay the SAME
    op on the wire."""
    _wire, _server_ops, subs = _inventory()
    for alias, target in sorted(CLI_ALIASES.items()):
        assert alias in subs, (
            f"alias `{alias}` is declared but is not a real subcommand. [alias: {alias}]")
        assert target in subs, (
            f"alias `{alias}` points at `{target}`, which is not in SUBCOMMANDS. "
            f"[alias: {alias}] [target: {target}]")
        assert target not in CLI_ALIASES, (
            f"alias `{alias}` points at `{target}`, which is ITSELF an alias -- "
            f"aliases must resolve in one hop. [alias: {alias}] [target: {target}]")
        assert target in CLI_WIRE_OPS or target in CLI_SERVER_OPS, (
            f"alias `{alias}` points at `{target}`, which dispatches no op. "
            f"[alias: {alias}] [target: {target}]")
    # The wire op behind `js` must be `eval` specifically -- SKILL.md, the
    # isolation-guard workaround, and reference/ all depend on that identity.
    assert CLI_WIRE_OPS.get(CLI_ALIASES["js"]) == "eval", (
        "the `js` alias must resolve to the `eval` wire op -- SKILL.md documents "
        "`js` as the isolation-guard-safe spelling of the SAME op")


def test_client_only_names_are_declared_with_a_reason_and_dispatch_nothing():
    """A name may be parked in CLI_CLIENT_ONLY only WITH a written reason, and
    only if it genuinely is not a wire op -- otherwise the bucket becomes a place
    to hide an unclassified name."""
    wire, server_ops, _subs = _inventory()
    for name, reason in sorted(CLI_CLIENT_ONLY.items()):
        assert isinstance(reason, str) and len(reason) >= 40, (
            f"CLI_CLIENT_ONLY[{name!r}] needs a written reason, not a placeholder "
            f"(got {reason!r}). [subcommand: {name}]")
        assert name not in wire and name not in server_ops, (
            f"`{name}` is classified CLIENT-ONLY but server.py now accepts it as an "
            f"op -- it must move to CLI_WIRE_OPS/CLI_SERVER_OPS. [subcommand: {name}]")


# --------------------------------------------------------------------------- #
# T3 -- SKILL.md ops table <-> CLI SUBCOMMANDS parity.
# --------------------------------------------------------------------------- #
def test_every_cli_subcommand_is_documented_in_the_skill_ops_table():
    """SKILL.md is the ONLY surface a Claude agent reads. A subcommand missing
    from the ops table is functionally dead to the agent even when every wire
    layer is perfect."""
    _wire, _server_ops, subs = _inventory()
    documented = parse_skill_ops_table()
    undocumented = sorted(set(subs) - documented)
    assert not undocumented, (
        f"CLI subcommand(s) ABSENT from SKILL.md's `## Ops` table: "
        f"{', '.join(undocumented)} -- an agent reads only SKILL.md, so "
        f"{'these are' if len(undocumented) > 1 else 'this is'} unreachable in "
        f"practice. Add a row (and evict something: test_skill_size.py caps the "
        f"file). [direction: CLI -> SKILL.md] [ops: {', '.join(undocumented)}]")


def test_every_documented_op_is_a_real_cli_subcommand():
    """The other direction: a documented command that does not exist sends the
    agent to a guaranteed `unknown subcommand` error."""
    _wire, _server_ops, subs = _inventory()
    documented = parse_skill_ops_table()
    phantom = sorted(documented - set(subs))
    assert not phantom, (
        f"SKILL.md's `## Ops` table documents command(s) the `browser` CLI does "
        f"not have: {', '.join(phantom)} -- an agent following the docs gets "
        f"`unknown subcommand`. Remove the row, or add the subcommand. "
        f"[direction: SKILL.md -> CLI] [ops: {', '.join(phantom)}]")


def test_every_wire_op_is_documented_under_some_cli_name():
    """Closes the triangle server.py -> CLI -> SKILL.md: an op can have a CLI
    name and still be invisible if that name never made it into the table."""
    wire, server_ops, _subs = _inventory()
    documented = parse_skill_ops_table()
    name_for = {op: n for n, op in {**CLI_WIRE_OPS, **CLI_SERVER_OPS}.items()}
    undocumented = sorted(
        op for op in (wire | server_ops)
        if name_for.get(op) is None or name_for[op] not in documented)
    assert not undocumented, (
        f"wire op(s) not documented in SKILL.md's ops table: "
        f"{', '.join(undocumented)} (CLI name(s): "
        f"{', '.join(str(name_for.get(op)) for op in undocumented)}) -- the bridge "
        f"accepts {'them' if len(undocumented) > 1 else 'it'} and the CLI can "
        f"dispatch {'them' if len(undocumented) > 1 else 'it'}, but no agent will "
        f"ever know. [direction: server.py -> SKILL.md] "
        f"[ops: {', '.join(undocumented)}]")
