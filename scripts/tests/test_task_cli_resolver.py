"""The TASK-half CLI resolver: ONE ordered ledger, two languages, and a split that
must not quietly become a replacement.

WHY
---
muster's task/agent API is served by `muster`, and until 2026-10 the only client
anyone had for it was `clawgatectl` — built from a DIFFERENT and PRIVATE repository,
on its own release cadence, which muster's own gates could not see. The two drifted on
the provenance headers: the client sent `X-Clawgate-{Source,Session-Id,Host}` while
muster read `X-Muster-*`, so for six days every task comment was attributed to the
generic `api` caller and the task↔session thread recorded NOTHING — 798 rows, then
zero new ones. muster #26 made the server accept both spellings, which fixes the
symptom. The CAUSE was a client living somewhere its server could not gate it, and the
fix for that is to prefer muster's OWN CLI, which muster's suites DO gate (a cobra-tree
test plus `tests/verb-ledger.sh` run against the built binary).

So four devrc consumers now resolve the binary instead of spelling it:

    scripts/claude-hooks/clawgate-writeback-guard.py   Stop-gate, every session
    scripts/claude-hooks/clawgate-task-interview-guard.py   PreToolUse (recognises
                                                            both names; resolves none)
    scripts/lib/cairn_who.py                           `cairn-who`
    scripts/resume-state.sh                            every /resume

WHAT THIS FILE PINS, AND WHY EACH ONE
-------------------------------------
  1. THE PREFERENCE, BOTH LEGS. `muster` present -> chosen; `muster` absent ->
     `clawgatectl` chosen. A one-legged test is satisfied by a resolver that always
     returns the same name.
  2. THE THIRD STATE. Neither present -> `None`, never an invented name. Two of the
     consumers are ENFORCEMENT HOOKS, and "guessed a binary" there is a crash on a
     path that must degrade.
  3. THE TWO LANGUAGES AGREE. `scripts/lib/clawgate_handoff.sh` cannot import python,
     so it mirrors the ledger — the same deliberate duplication the task-API URL
     ledger already carries, and pinned the same way: by READING the shell file, and
     by RUNNING the shell resolver over both legs.
  4. 🔴 THE SPLIT IS STILL A SPLIT. The ROUTER verbs — approvals, attention, tmux,
     terminals, layout, transcripts — exist ONLY in `clawgatectl`; muster's CLI does
     not have them and must not grow them (upstream's verb ledger asserts its 12-verb
     set by EXACT equality and fails when it GROWS). A repo-wide scan fails if any
     router verb is ever typed behind `muster`.

🔴 WHAT THIS FILE STRUCTURALLY CANNOT SEE, so nobody reads a green run as more:
  * whether EITHER binary is installed, or works. `resolve_task_cli` answers "which
    name", never "does it run" — deciding what to do about a client that answers
    rc 6 belongs to the caller, and each caller's own suite covers that.
  * muster's real verb set. The authority is `tests/verb-ledger.sh` in
    `github.com/ZacxDev/muster`, a DIFFERENT repository that no test here can read.
    `MUSTER_VERBS` below is a reviewed literal, re-derived 2026-10-01 from the BUILT
    binary (`nix build .#…muster-cli` -> `tests/verb-ledger.sh $out/bin/muster` ->
    "exactly the specified set", 12 verbs). Same class of cross-repo seam as
    `CLAWGATE_TASK_STATUSES` in `clawgate_handoff.sh`.
  * a router verb reached through a VARIABLE (`$cli term send …`). The scan reads
    source text, so it sees a literal `muster` and nothing else. That is why the
    DISJOINTNESS assertion below exists beside it: it fails on the ledger rather than
    on a call site, which is the layer a variable cannot hide behind.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from testlib import mockbin, skip_dirs  # noqa: E402

PY_LIB = REPO / "scripts" / "lib" / "clawgate_tasks.py"
SH_LIB = REPO / "scripts" / "lib" / "clawgate_handoff.sh"

#: 🔴 THE LITERAL A HUMAN HAS REVIEWED. Asserted against the module as a SEQUENCE, so
#: it fails when the tuple GROWS (a third client arrived and nothing reviewed what it
#: now resolves to), when it SHRINKS (the fallback was dropped and a half-switched host
#: stopped measuring), and when it is REORDERED (the whole content of the ledger).
#: Spelled here rather than read from the module, because an expectation taken from the
#: thing under test asserts only that it agrees with itself.
EXPECTED_LEDGER = ("muster", "clawgatectl")

#: muster's CLI verbs — the TASK half. See the module docstring for the authority and
#: for how this was re-derived. Leaf verbs, space-joined exactly as the binary prints
#: them.
MUSTER_VERBS = (
    "agent ls", "agent messages", "agent resolve",
    "agent task comment", "agent task get", "agent task status",
    "chief ask",
    "task comment", "task create", "task get", "task ls", "task status",
)

#: 🔴 `clawgatectl`'s ROUTER verbs — the half that did NOT move and must not. Read from
#: `clawgatectl --help`'s own "Available Commands" on 2026-10-01, minus the three groups
#: muster took (`task`, `agent`) and minus `help`:
#:     health attention chief panel term tmux transcript view
#: `chief` is split rather than owned: `chief ask` is task-side (muster has it),
#: `chief write` / `chief launch` are router-side, so the group name is NOT in this
#: tuple and the two router leaves are listed separately below.
ROUTER_VERBS = (
    "health", "attention", "panel", "term", "tmux", "transcript", "view",
)
ROUTER_CHIEF_LEAVES = ("write", "launch")

#: A command-shaped `muster <router-verb>`: the binary name in command position
#: (optionally as a path), then the verb DIRECTLY after it.
#:
#: 🔴 DELIBERATELY NOT "the two words appear near each other". These files are prose as
#: well as code, and they are FULL of sentences like "`muster` has no `health` verb" and
#: "the `health` row is `clawgatectl`-ONLY" — a proximity match would fire on every one
#: of them and the guard would be deleted within a week. Requiring adjacency is what
#: makes it a claim about an INVOCATION. The positive and negative controls below are
#: what prove the distinction is real rather than asserted.
_ROUTER_CALL_RX = re.compile(
    r"(?<![\w./-])(?:[\w./-]*/)?muster\s+(?:"
    + "|".join(ROUTER_VERBS)
    + r"|chief\s+(?:" + "|".join(ROUTER_CHIEF_LEAVES) + r"))(?![\w-])"
)

#: Where the scan looks. The whole tracked corpus would include `.git` objects and the
#: operator's vendored venvs; these two trees are every place a command could be typed
#: into code or taught to a model.
SCAN_ROOTS = ("scripts", "claude")
SCAN_SUFFIXES = {".py", ".sh", ".md", ".json", ".nix", ""}
SKIP = set(skip_dirs.GENERATED | skip_dirs.VIRTUALENVS) | {".claude"}


def _load_py():
    loader = importlib.machinery.SourceFileLoader("_tcr_clawgate_tasks", str(PY_LIB))
    spec = importlib.util.spec_from_file_location("_tcr_clawgate_tasks", str(PY_LIB),
                                                 loader=loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


CG = _load_py()


# =========================================================================== #
# 1. THE LEDGER
# =========================================================================== #
def test_the_ordered_ledger_is_pinned_in_BOTH_directions():
    """🔴 A SEQUENCE, NOT A SET. `("clawgatectl", "muster")` and
    `("muster", "clawgatectl")` are two different deployments: the first one keeps
    writing every task comment through the client whose provenance headers the board
    mis-attributed for six days. Order IS the content here."""
    assert tuple(CG.TASK_CLI_NAMES) == EXPECTED_LEDGER, (
        "scripts/lib/clawgate_tasks.TASK_CLI_NAMES is %r, expected %r. GROWING means a "
        "third client arrived and nothing reviewed what four consumers now resolve to; "
        "SHRINKING means a half-switched host silently stopped measuring; REORDERING "
        "means the preference moved, which is the whole decision."
        % (tuple(CG.TASK_CLI_NAMES), EXPECTED_LEDGER))


def test_the_ledger_is_a_tuple_so_a_consumer_cannot_mutate_it_for_everyone():
    assert isinstance(CG.TASK_CLI_NAMES, tuple)


# =========================================================================== #
# 2. THE PREFERENCE — BOTH LEGS, AND THE THIRD STATE
# =========================================================================== #
def _which_over(present):
    """A `shutil.which` stand-in that knows only `present`."""
    return lambda name: ("/fake/bin/" + name) if name in present else None


def test_muster_WINS_when_both_are_on_PATH():
    got = CG.resolve_task_cli(which=_which_over({"muster", "clawgatectl"}))
    assert got == "muster", (
        "with BOTH clients installed the resolver chose %r. It must choose `muster` — "
        "the client muster's own suites gate. Inverting TASK_CLI_NAMES, or walking it "
        "in reverse, fails exactly here." % (got,))


def test_clawgatectl_is_chosen_when_muster_is_ABSENT():
    """🔴 THE LEG THAT KEEPS TWO ENFORCEMENT HOOKS WORKING on a host whose switch has
    not landed, or failed. Without it, "prefer muster" would be "require muster", and
    the write-back guard would reach NO VERDICT on a machine that has a perfectly good
    client — an observable identical to a session that wrote back correctly."""
    assert CG.resolve_task_cli(which=_which_over({"clawgatectl"})) == "clawgatectl"


def test_NEITHER_resolves_to_None_and_never_to_an_invented_name():
    assert CG.resolve_task_cli(which=_which_over(set())) is None


def test_the_NEGATIVE_CONTROL_the_stub_which_really_can_say_no():
    """Without this, the three cases above are satisfied by a `which` that answers
    truthfully for everything — including a dev host where BOTH binaries exist, which
    would make the absent-muster leg vacuous."""
    w = _which_over({"clawgatectl"})
    assert w("muster") is None
    assert w("clawgatectl") is not None


def _exe(d: Path, name: str):
    """An executable stub named `name` in `d`.

    🔴 `testlib.mockbin.write_exec`, NOT a hand-written shebang. It owns the shebang
    line precisely so no call site can reintroduce `#!/usr/bin/env` — which does not
    resolve inside the nix build sandbox — and
    `test_runtime_shebangs.py::test_no_test_writes_a_usr_bin_env_shebang_at_runtime`
    fails on any test that writes one itself. (It caught this file's first version, in
    the SANDBOX tier only: the dev-host tier had been green.)

    The body is a no-op. Every case here resolves a binary by NAME — nothing executes
    these — so what has to be true of them is only that `shutil.which` and
    `command -v` see an executable file.
    """
    d.mkdir(parents=True, exist_ok=True)
    return mockbin.write_exec(d / name, "exit 0\n")


@pytest.mark.parametrize("present,expect", [
    (("muster", "clawgatectl"), "muster"),
    (("clawgatectl",), "clawgatectl"),
    (("muster",), "muster"),
    ((), None),
])
def test_both_legs_through_the_REAL_shutil_which_over_a_REAL_PATH(tmp_path, monkeypatch,
                                                                 present, expect):
    """🔴 THE DEFAULT RESOLVER, NOT THE INJECTED ONE. Every case above drives a stub
    `which`, so all four would pass against a `resolve_task_cli` whose own default was
    broken — the injection seam cannot test itself. This builds real executables in a
    real directory and strips PATH to it, so the code path the hosts take is the one
    exercised.

    PATH is stripped to the fixture directory ALONE because this dev host has both
    binaries installed: left intact, the absent-`muster` leg would find the real one
    and silently become a second copy of the first leg.
    """
    b = tmp_path / "bin"
    b.mkdir()
    for name in present:
        _exe(b, name)
    monkeypatch.setenv("PATH", str(b))
    got = CG.resolve_task_cli()
    assert got == expect, (
        "with %r on a PATH stripped to the fixture dir, the DEFAULT resolver returned "
        "%r and should have returned %r." % (list(present), got, expect))


def test_the_resolver_consults_the_LEDGER_and_not_a_literal(tmp_path, monkeypatch):
    """🔴 Mutation-visible by construction: hand it a one-entry ledger naming a binary
    that EXISTS, and a resolver that had hardcoded either real name would return that
    name instead. A resolver reading its own argument returns the fixture's."""
    b = tmp_path / "bin"
    b.mkdir()
    _exe(b, "zzz-not-a-real-client")
    _exe(b, "muster")
    _exe(b, "clawgatectl")
    monkeypatch.setenv("PATH", str(b))
    assert CG.resolve_task_cli(names=("zzz-not-a-real-client",)) \
        == "zzz-not-a-real-client"


# =========================================================================== #
# 3. THE SHELL HALF — the same predicate, the other language
# =========================================================================== #
def test_the_shell_task_cli_ledger_matches_PYTHON():
    """🔴 THE DELIBERATE DUPLICATE, PINNED. `clawgate_handoff.sh` is sourced by
    `resume-state.sh` and cannot import python to read two words, exactly as it cannot
    for `TASK_API_URL_VARS` — so the ledger is mirrored and this is what stops the
    mirror drifting. It fails on a name AND on an ORDER change, in either direction."""
    m = re.search(r'CLAWGATE_TASK_CLI_NAMES="([^"]+)"',
                  SH_LIB.read_text(encoding="utf-8"))
    assert m, "CLAWGATE_TASK_CLI_NAMES is gone from %s" % SH_LIB
    assert tuple(m.group(1).split()) == tuple(CG.TASK_CLI_NAMES), (
        "the shell ledger %r and python's %r have drifted — they are the SAME "
        "preference and a shell consumer resolving the wrong one first is silent."
        % (m.group(1).split(), list(CG.TASK_CLI_NAMES)))


def _run_shell_resolver(bindir: Path):
    """Source the shared shell lib with PATH stripped to `bindir`, run the resolver."""
    script = (
        'set -u\n'
        '. "%s"\n'
        'if out=$(clawgate_task_cli); then printf "OK %%s\\n" "$out"; '
        'else printf "NONE %%s\\n" "$?"; fi\n' % SH_LIB
    )
    env = dict(os.environ)
    # 🔴 PATH stripped to the fixture PLUS a coreutils dir, because the lib is bash and
    # `command -v` is a builtin but `bash` itself has to be found by the runner. The
    # fixture dir comes FIRST and the real binaries are not in the second one.
    env["PATH"] = str(bindir)
    bash = shutil.which("bash") or "/bin/bash"
    p = subprocess.run([bash, "-c", script], capture_output=True, text=True, env=env)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


@pytest.mark.parametrize("present,expect", [
    (("muster", "clawgatectl"), "OK muster"),
    (("clawgatectl",), "OK clawgatectl"),
    (("muster",), "OK muster"),
    ((), "NONE 1"),
])
def test_the_shell_resolver_agrees_with_python_on_every_leg(tmp_path, present, expect):
    """🔴 THE SHELL CODE RUN, not read. The regex above pins the two LEDGERS; this pins
    the two IMPLEMENTATIONS, which is a different claim — a correct ledger walked by a
    loop that returns the last match instead of the first would pass the regex test and
    fail here."""
    b = tmp_path / "bin"
    b.mkdir()
    for name in present:
        _exe(b, name)
    rc, out, err = _run_shell_resolver(b)
    assert out == expect, (out, err, rc)


def test_the_shell_resolver_prints_NOTHING_on_stdout_when_it_finds_neither(tmp_path):
    """A caller does `if cli=$(clawgate_task_cli)`, so a stray line on stdout would be
    captured as a BINARY NAME and then executed."""
    b = tmp_path / "bin"
    b.mkdir()
    script = 'set -u\n. "%s"\nclawgate_task_cli\n' % SH_LIB
    env = dict(os.environ)
    env["PATH"] = str(b)
    bash = shutil.which("bash") or "/bin/bash"
    p = subprocess.run([bash, "-c", script], capture_output=True, text=True, env=env)
    assert p.returncode == 1, (p.returncode, p.stdout, p.stderr)
    assert p.stdout == "", p.stdout


# =========================================================================== #
# 4. 🔴 THE SPLIT IS STILL A SPLIT — the router half stays on `clawgatectl`
# =========================================================================== #
def _scan_files():
    for root in SCAN_ROOTS:
        base = REPO / root
        if not base.exists():                      # pragma: no cover
            continue
        for p in sorted(base.rglob("*")):
            rel = p.relative_to(REPO)
            if any(part in SKIP for part in rel.parts):
                continue
            if p.is_file() and p.suffix in SCAN_SUFFIXES:
                yield rel, p


def test_the_task_ledger_names_NO_router_verb():
    """🔴 THE LEDGER-LEVEL HALF, and the one a variable cannot hide behind. If a router
    verb ever entered muster's verb set, the split would have stopped being a split at
    the source rather than at a call site — which is also the direction upstream's
    `tests/verb-ledger.sh` fails on (exact equality, so GROWING is a failure there)."""
    muster_heads = {v.split()[0] for v in MUSTER_VERBS}
    overlap = muster_heads & set(ROUTER_VERBS)
    assert overlap == set(), (
        "muster's verb set has grown a ROUTER verb (%s). muster's CLI must not gain the "
        "approval/attention/terminal surface; that is `clawgatectl`'s, and upstream's "
        "verb ledger fails on exactly this growth." % sorted(overlap))
    assert "chief ask" in MUSTER_VERBS
    for leaf in ROUTER_CHIEF_LEAVES:
        assert ("chief " + leaf) not in MUSTER_VERBS, leaf


def test_no_router_verb_is_ever_INVOKED_through_muster():
    """🔴 THE CALL-SITE HALF. A router verb typed behind `muster` is not a style nit: it
    is a command that cannot work (muster's binary has 12 verbs and none of them is
    `term` or `attention`), and if it were taught in a skill a model would run it and
    read the cobra "unknown command" as a broken service.

    Reported with the offending LINE, because "some file mentions it" is not actionable.
    """
    hits = []
    for rel, p in _scan_files():
        if rel == Path(__file__).relative_to(REPO):
            continue                 # this guard has to spell what it hunts
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):       # pragma: no cover
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if _ROUTER_CALL_RX.search(line):
                hits.append("%s:%d: %s" % (rel, n, line.strip()[:120]))
    assert hits == [], (
        "a ROUTER verb is being sent to `muster`, whose CLI does not have it — the "
        "split has started turning into a replacement:\n  " + "\n  ".join(hits))


def test_the_POSITIVE_CONTROL_for_that_scan(tmp_path):
    """🔴 Without this, an empty hit list is indistinguishable from a regex wired to
    nothing — and that is not hypothetical: an earlier spelling of this pattern anchored
    on `^` and matched zero of the cases below. Every shape that MUST be caught, and
    every prose shape that must NOT be."""
    must_catch = [
        "muster term send foo",
        "muster attention ls",
        "muster tmux windows",
        "muster transcript query x",
        "muster view ls",
        "muster panel collapse 1",
        "muster health",
        "muster chief write --body x",
        "muster chief launch",
        "/nix/store/abc/bin/muster term send foo",
        "  muster   term   send foo",
        "$ muster health | jq .",
    ]
    for case in must_catch:
        assert _ROUTER_CALL_RX.search(case), case
    must_not_catch = [
        # the task half — the whole point of the resolver
        "muster task get 193",
        "muster task comment 193 --body x",
        "muster agent resolve operator --id",
        "muster chief ask operator --body x",
        # the router half on the RIGHT binary
        "clawgatectl term send foo",
        "clawgatectl health",
        "clawgatectl chief write --body x",
        # prose, which these files are full of
        "`muster` has no `health` verb at all",
        "the `health` row is `clawgatectl`-ONLY, not a muster verb",
        "muster does not grow the term surface",
        # ⚠ `a muster transcript of the meeting` was HERE as a must-NOT-catch and the
        # control failed on it, correctly: the words are adjacent, so the regex cannot
        # tell that English sentence from an invocation. Recorded rather than patched
        # around, because it names the guard's one false-positive direction — and that
        # direction is LOUD (a failing test naming the line) rather than silent. The
        # remedy if it ever fires on real prose is to reword the prose or to add a
        # counted allowlist entry, never to loosen the adjacency requirement, which is
        # the only thing making this a claim about a COMMAND.
        "demuster health",                        # not the binary
        "muster-cli health",                      # the ATTRIBUTE, not the binary
    ]
    for case in must_not_catch:
        assert not _ROUTER_CALL_RX.search(case), case


def test_the_scan_WALKED_something():
    """A corpus of zero files satisfies the assertion above perfectly. This checkout can
    itself sit under a skipped directory name (agent worktrees live under `.claude/`),
    which is exactly how a repo-wide walker reports a confident green over nothing."""
    files = list(_scan_files())
    assert len(files) > 200, len(files)
    rels = {rel.as_posix() for rel, _ in files}
    assert "claude/skills/clawgate/flows/task-pickup.md" in rels
    assert "scripts/resume-state.sh" in rels
    assert "scripts/lib/clawgate_tasks.py" in rels


def test_the_skills_TEACH_the_preferred_client_for_the_task_ritual():
    """🔴 The pickup flow is the one file a model opens when it picks up a card, and its
    bash block is what it copies. Pinned as the WHOLE normalised command lines, not as
    the word "muster": a file could mention muster in prose and still hand over
    `clawgatectl task status …` to run."""
    flow = (REPO / "claude" / "skills" / "clawgate" / "flows" / "task-pickup.md")
    text = flow.read_text(encoding="utf-8")
    for want in ("muster task get <id>",
                 "muster task comment <id> --body",
                 "muster task status <id> in_progress",
                 "muster task status <id> ready_for_review"):
        assert want in text, (
            "%s no longer teaches `%s`. The flow's bash block is what a pickup copies, "
            "so a stale client here is the one that actually gets run."
            % (flow.relative_to(REPO), want))
    # ...and the fallback is still NAMED, so a `command not found` is answerable.
    assert "clawgatectl" in text, (
        "the flow no longer names `clawgatectl` as the fallback — on a host where "
        "`muster` is missing the model is left with no working command.")
