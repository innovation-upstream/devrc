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
  5. 🔴 THAT THE RESOLVED CLIENT CAN ACTUALLY ANSWER. Preferring `muster` without
     also handing it `MUSTER_API_URL`/`MUSTER_HOOK_TOKEN` made every invoking site
     fail at once — the whole of section 5, which carries the measurement.

🔴 WHAT THIS FILE STRUCTURALLY CANNOT SEE, so nobody reads a green run as more:
  * whether EITHER binary is installed. `resolve_task_cli` answers "which name",
    never "is it there" — and section 5's behavioural legs run a FAKE client, so
    they assert devrc's side of the config contract and never muster's own.
  * whether the live board accepts the credential. Nothing here touches the network;
    deciding what to do about a client that answers rc 3 or rc 6 belongs to the
    caller, and each caller's own suite covers that.
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
import json
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

#: 🔴 WHICH OF THOSE SUFFIXES IS A SHELL SCRIPT — the ledger `_bare_sh_hits` filters
#: on, declared ONCE here beside the walk's own ledger instead of open-coded as
#: `p.suffix != ".sh"` at the filter. That open-coding was NARROWER than the walk:
#: `SCAN_SUFFIXES` deliberately yields extensionless files, and measured 2026-10-03
#: there are **17 extensionless `#!/usr/bin/env bash` scripts** under
#: `scripts/`+`claude/` (`scripts/run3`, `scripts/muse/muse`, `scripts/stt`,
#: `scripts/dogfood-cycle`, … ) that the scan silently skipped. The remaining 53 are
#: 48 `#!/usr/bin/env python3` plus 5 with no shebang at all — no other shebang
#: spelling exists in the tree, so the shebang test below is a clean discriminator
#: rather than a lucky one.
#:
#: ⚠ NO LIVE MISS WAS BEING HIDDEN. A deliberately WIDE probe over those 17 files —
#: any mention of `muster`, `clawgatectl`, `$cli`, `${cli}`, `$task_cli` on a
#: non-comment line — returned **0**. This closes a gap between a docstring and its
#: code, it does not fix a known escape.
#:
#: 🔴 AND IT IS A SUBSET OF `SCAN_SUFFIXES`, pinned by
#: `test_the_shell_suffix_ledger_is_a_SUBSET_of_what_the_walk_yields`. A shell suffix
#: the walk does not yield is a filter that can never see it — the same
#: claim-wider-than-code defect, one layer up.
SHELL_SUFFIXES = {".sh", ""}
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


#: The pickup flow, which is the one file a model opens when it picks up a card.
TASK_PICKUP_FLOW = REPO / "claude" / "skills" / "clawgate" / "flows" / "task-pickup.md"

#: 🔴 WHAT THE FLOW PRINTS, AND WHAT IT COSTS TO TYPE IT. Measured 2026-10-03 from a
#: fresh interactive login shell on the operator's host, with neither `MUSTER_*`
#: variable exported and `~/.muster/muster.env` absent:
#:
#:     muster      task get <id>  -> rc 2, "no API URL"
#:     clawgatectl task get <id>  -> rc 0, the task as JSON
#:
#: So all four commands below are NON-FUNCTIONAL as printed for a human or a model
#: typing them. The automated paths are fine — the hooks and `/resume` go through
#: `task_cli_env` / `clawgate_task_cli_exec`, which export both variables — but this
#: file is read by whoever types, not by them.
_PICKUP_COMMANDS = (
    "muster task get <id>",
    "muster task comment <id> --body",
    "muster task status <id> in_progress",
    "muster task status <id> ready_for_review",
)

#: 🔴 THE RECOVERY SENTENCE, PINNED AS ONE WHOLE NORMALISED STRING rather than as the
#: words "clawgatectl" or "rc 2". The artefact under test is PROSE, and a guard on
#: words is walkable by rewording: the sentence this replaced said "If `muster:
#: command not found`, use `clawgatectl`", which names a failure mode that does NOT
#: occur — the binary IS on PATH; it is unconfigured. A model that read it, hit rc 2,
#: and concluded the fallback did not apply is left with no working command. A
#: cosmetic reword now fails this test; that is the price of a machine-readable claim.
_PICKUP_RECOVERY = (
    "So, when you type a command from this flow: **on rc 2 `no API URL`, OR on "
    "`muster: command not found`, re-run it with `clawgatectl` and say so — do not "
    "fall back to `curl`.**"
)


def _normalise_prose(text: str) -> str:
    """Collapse whitespace so a claim can be pinned across line wrapping.

    🔴 `"` IS STRIPPED TOO, and not as cosmetics: two of the sites this serves hold
    the claim inside ADJACENT PYTHON STRING LITERALS, which the source text joins
    with a quote pair that no amount of whitespace folding removes. `#` goes for the
    same reason one level over — a claim living in a comment block carries a marker
    per line. Both make this a slightly LOOSER match than byte equality, which is
    stated here rather than left for a reader to discover.
    """
    out = text.replace('"', "").replace("#", " ")
    return " ".join(out.split())


def test_the_skills_TEACH_the_preferred_client_for_the_task_ritual():
    """🔴 The flow's bash block is what a pickup copies. Pinned as the WHOLE
    normalised command lines, not as the word "muster": a file could mention muster
    in prose and still hand over `clawgatectl task status …` to run.

    ⚠ RE-AIMED 2026-10-03, AND THE REASON IS THE POINT. This test was GREEN while
    pinning four commands that exit rc 2 from a fresh login shell — i.e. it
    certified broken prose as correct, which is worse than no test because it stops
    anyone looking. It could not see that, because "the flow prints this string" and
    "that string works when typed" are different claims and it only ever made the
    first.

    🔴 WHAT THIS NOW ALSO PINS, AND WHAT IT STILL CANNOT. It pins that the flow
    carries the measured RECOVERY — the real failure mode (rc 2, not `command not
    found`) and the client that does answer. That is red while the flow is silent
    about it. It does NOT, and cannot here, assert that `muster task get <id>` works
    when typed: making it do so means exporting `MUSTER_API_URL`/`MUSTER_HOOK_TOKEN`
    into the login environment, which is a home-manager change to this repo's shell
    layer and is NOT done by the change that re-aimed this test. Until that lands the
    printed commands remain non-functional as typed, and the honest guard is that the
    flow SAYS SO. Named here rather than faked green: the closing condition is a
    `sessionVariables`-style export of both keys plus this test tightened to drop the
    recovery pin and assert a zero-exit invocation instead.
    """
    text = TASK_PICKUP_FLOW.read_text(encoding="utf-8")
    for want in _PICKUP_COMMANDS:
        assert want in text, (
            "%s no longer teaches `%s`. The flow's bash block is what a pickup copies, "
            "so a stale client here is the one that actually gets run."
            % (TASK_PICKUP_FLOW.relative_to(REPO), want))
    # ...and the fallback is still NAMED, so an unconfigured client is answerable.
    assert "clawgatectl" in text, (
        "the flow no longer names `clawgatectl` as the fallback — on a host where "
        "`muster` is missing or unconfigured the model is left with no working "
        "command.")
    assert _normalise_prose(_PICKUP_RECOVERY) in _normalise_prose(text), (
        "%s prints four `muster task …` commands that exit rc 2 `no API URL` when "
        "typed, and no longer carries the recovery that says so. The reader is a "
        "model that will type them. Expected this sentence, normalised:\n  %s"
        % (TASK_PICKUP_FLOW.relative_to(REPO), _PICKUP_RECOVERY))


def test_the_flow_does_NOT_still_claim_the_failure_is_COMMAND_NOT_FOUND():
    """🔴 THE DIRECTION THE PIN ABOVE CANNOT COVER: a revert that ADDS the old
    sentence back while leaving the new one in place would satisfy it. The retracted
    claim is pinned as its own whole normalised string — `muster` is on PATH on both
    hosts, so `command not found` as the ONLY stated trigger sends a model that hit
    rc 2 looking for a different problem."""
    retracted = ("If `muster: command not found`, use `clawgatectl` and say so — "
                 "do not fall back to `curl`.")
    text = _normalise_prose(TASK_PICKUP_FLOW.read_text(encoding="utf-8"))
    assert _normalise_prose(retracted) not in text, (
        "the retracted trigger is back in %s as a standalone sentence: it names the "
        "ONE failure mode that does not happen. Measured: the binary is present and "
        "exits 2 unconfigured.\n  %s"
        % (TASK_PICKUP_FLOW.relative_to(REPO), retracted))


# =========================================================================== #
# 4b. 🔴 THE FALLBACK-CHAIN MECHANISM, PINNED WHERE IT IS CLAIMED
#
# #2005 shipped the sentence "the read silently degrades to the curl fallback" into
# FOUR files, and it is MEASURED FALSE. `_read_task` walks `muster -> clawgatectl ->
# curl`; `clawgatectl` configures itself from the same `~/.claude/clawgate.env`, so
# it answers rc 0 and the walk STOPS at entry 2. Re-measured 2026-10-03 by driving
# `_read_task`'s own chain on the operator's host:
#
#     muster      --api-url <base> task get <id>  -> rc 3, 401 (no token)
#     clawgatectl --api-url <base> task get <id>  -> rc 0, the task as JSON
#     => chain stops here; curl unreached
#
# 🔴 WHY A TEST AND NOT JUST A CORRECTION. A wrong mechanism in a comment is a claim
# that outlives the person who wrote it, and this one had already been copied three
# times — the second copy is the moment to consolidate. The artefact under test is
# PROSE, so a guard on the WORD "curl" is walkable by rewording; what is pinned is
# the WHOLE NORMALISED claim at each site, in BOTH directions (the correction present
# AND the retraction absent). A cosmetic reword fails this test on purpose.
# =========================================================================== #
#: (path, whole normalised claim) — one row per site that states the mechanism.
#: TWO rows share a file on purpose: a section header and an assertion MESSAGE are
#: separately reachable, and the message is the one a failing run actually prints.
_CHAIN_CLAIM_SITES = (
    ("scripts/claude-hooks/clawgate-writeback-guard.py",
     "So the walk stops at entry 2 and **curl is never reached at all**."),
    ("scripts/claude-hooks/tests/test_clawgate_writeback_guard.py",
     "so it answers rc 0 and the walk stops at entry 2 with curl never reached."),
    ("scripts/claude-hooks/tests/test_clawgate_writeback_guard.py",
     "`clawgatectl` answers rc 0 off the same env file, so the walk stops at entry "
     "2 and the curl fallback is never reached."),
    ("scripts/tests/test_task_cli_resolver.py",
     "answers rc 0 and the walk STOPS at entry 2 — the curl fallback is never "
     "reached."),
)

#: The retracted claim, in every spelling it shipped in. Pinned as whole normalised
#: strings so a REVERT is caught, not merely a deletion of the correction.
_RETRACTED_CHAIN_CLAIMS = (
    "client ALWAYS fails here and the read silently degrades to the curl fallback,",
    "client and then falls back to curl, so the live read still SUCCEEDED — through "
    "curl,",
    "before it sends anything and the read degrades to curl.",
    "guard's preferred client failed every read and silently degraded to its curl "
    "fallback",
)


def test_every_site_that_STATES_the_fallback_chain_states_the_MEASURED_one():
    """🔴 THE CORRECTION, AT EVERY SITE THAT CARRIES THE CLAIM. A fix applied to
    three of four files leaves the fourth teaching the wrong mechanism, which is how
    this got to four copies in the first place."""
    missing = []
    for rel, claim in _CHAIN_CLAIM_SITES:
        p = REPO / rel
        assert p.exists(), rel
        if _normalise_prose(claim) not in _normalise_prose(
                p.read_text(encoding="utf-8")):
            missing.append("%s: expected, normalised:\n      %s" % (rel, claim))
    assert missing == [], (
        "a site states the muster -> clawgatectl -> curl walk and no longer states "
        "the MEASURED outcome (clawgatectl answers; curl is never reached). The "
        "provenance consequence is real; the curl mechanism is not:\n  "
        + "\n  ".join(missing))


def test_the_RETRACTED_curl_fallback_claim_is_absent_from_the_whole_corpus():
    """🔴 THE OTHER DIRECTION, and the one the pin above cannot see: a file may
    carry the correction AND the retracted sentence at once, which reads as two
    mechanisms and resolves to neither. Scanned over the SHARED `_scan_files()` walk
    rather than the four known paths, so a FIFTH copy pasted into a skill or a doc
    is caught too — that is the growth this started as."""
    found = []
    for rel, p in _scan_files():
        if rel.as_posix() == Path(__file__).relative_to(REPO).as_posix():
            continue                      # this file QUOTES them, by necessity
        try:
            text = _normalise_prose(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):       # pragma: no cover
            continue
        for claim in _RETRACTED_CHAIN_CLAIMS:
            if _normalise_prose(claim) in text:
                found.append("%s: %s" % (rel, claim))
    assert found == [], (
        "the retracted curl-fallback mechanism is back. Measured 2026-10-03: "
        "`clawgatectl` answers rc 0 off the same env file, so the walk stops at "
        "entry 2 and curl is NEVER reached. The true consequence is wrong "
        "PROVENANCE, not a degraded transport:\n  " + "\n  ".join(found))


def test_the_POSITIVE_CONTROL_for_the_retraction_scan():
    """🔴 A ZERO FROM THE SCAN ABOVE IS INDISTINGUISHABLE FROM A SCAN WIRED TO
    NOTHING — and `_normalise_prose` is exactly the kind of filter that can swallow
    everything (it rewrites `#` and `"`). Feed the REAL predicate a fixture that
    MUST match, in the two shapes the corpus actually holds: a `#` comment block and
    a split python string literal."""
    as_comment = ("# client and then falls back to curl, so the live read still\n"
                  "# SUCCEEDED — through curl, which sends no provenance headers.\n")
    as_literal = ('        "before it sends anything and the read degrades "\n'
                  '        "to curl.")\n')
    for shape, blob in (("comment", as_comment), ("literal", as_literal)):
        hits = [c for c in _RETRACTED_CHAIN_CLAIMS
                if _normalise_prose(c) in _normalise_prose(blob)]
        assert len(hits) == 1, (shape, hits)
    # ...and the DISCRIMINATING case: the CORRECTED prose must NOT match, or the
    # scan would be permanently red and get deleted.
    corrected = ("# so it answers rc 0 and the walk stops at entry 2 with curl\n"
                 "# never reached.\n")
    assert [c for c in _RETRACTED_CHAIN_CLAIMS
            if _normalise_prose(c) in _normalise_prose(corrected)] == []


# =========================================================================== #
# 5. 🔴 RESOLVING A CLIENT IS HALF THE JOB — IT MUST ALSO BE ABLE TO ANSWER
#
# THE DEFECT, MEASURED ON BOTH HOSTS 2026-10-02
# ---------------------------------------------
# The ledger above was repointed to prefer `muster`, and `muster` reads its config
# out of its OWN namespace — `MUSTER_API_URL` / `MUSTER_HOOK_TOKEN`, or
# `~/.muster/muster.env`. That file exists on NEITHER host and nothing exports those
# two variables, while the credential has been in `~/.claude/clawgate.env` under
# `CLAWGATE_{TASK_,}API_URL` / `CLAWGATE_HOOK_TOKEN` all along — which is exactly
# what `clawgate_tasks.py` already resolves for the HTTP path. In a plain shell:
#
#     clawgatectl task get <id>                      -> rc 0, the task as JSON
#     muster      task get <id>                      -> rc 2, "no API URL: set
#                                                        MUSTER_API_URL in
#                                                        ~/.muster/muster.env"
#     muster --api-url <resolved base> task get <id> -> rc 3, 401 Unauthorized
#     MUSTER_API_URL=<base> MUSTER_HOOK_TOKEN=<tok> muster task get <id> -> rc 0
#
# So the base and the token were BOTH present and BOTH correct, and the resolved
# client was simply never handed either. Every invoking site was wrong at once,
# because each had to remember a step the resolver did not give it: `/resume`'s
# CLAWGATE block printed `muster exit 2 … its status is UNKNOWN` on both machines,
# `cairn-who` raised `ClawgateUnreachable: muster exited 2`, and the write-back
# guard's preferred client failed every read — which was then served by
# `clawgatectl`, the SECOND ledger entry, under the WRONG provenance headers.
#
# ⚠ THIS PARAGRAPH USED TO SAY "silently degraded to its curl fallback", AND THAT IS
# MEASURED FALSE. Re-measured 2026-10-03 by driving `_read_task`'s own walk:
# `clawgatectl` configures itself out of the same `~/.claude/clawgate.env`, so it
# answers rc 0 and the walk STOPS at entry 2 — the curl fallback is never reached.
# ⚠ SCOPE: that re-measurement is THIS HOST only. The both-hosts half is #2005's own
# commit body ("Measured on both hosts 2026-10-02: clawgatectl task get <id> -> rc 0,
# the task as JSON"), quoted rather than re-derived — so "never reached on either
# host" rests on that measurement, not on mine. The provenance consequence is the
# true and surviving half: the
# fallback client sends `X-Clawgate-{Source,Session-Id,Host}` where muster reads
# `X-Muster-*`, so the board attributed every read to the generic `api` caller, which
# is exactly the thing preferring muster was supposed to fix.
#
# WHAT THIS SECTION PINS, AND WHY EACH ONE
# ----------------------------------------
#   1. THE TWO VARIABLE NAMES, as reviewed literals, in BOTH languages. The whole
#      fix is "write the resolved base and token into these two keys"; a drift here
#      is silent because an unset variable and a misspelled one look identical.
#   2. THE DERIVATION reuses the EXISTING precedence — `TASK_API_URL_VARS` order,
#      the `${A:-$B}` fall-through, the file-then-process layering. A second reader
#      of the env file would be a second source of truth for the credential.
#   3. 🔴 BEHAVIOURALLY, IN BOTH LANGUAGES, THROUGH THE REAL RESOLVER. A structural
#      check type-checks past a wrong argument, so a fake client that REFUSES to
#      work unless it sees both variables is executed for real — with a NEGATIVE
#      CONTROL running the same fake bare, which must reproduce the measured rc 2.
#   4. THE TOKEN IS NOT IN ARGV. An argv is world-readable through /proc and these
#      run after every turn; `muster --help` says so itself of its `--token` flag.
#   5. THE LEDGER OF INVOKING SITES — a RELATIONSHIP, not one side: resolving a
#      client must imply a configured invocation, at every site that runs one.
#
# 🔴 WHAT THIS SECTION STRUCTURALLY CANNOT SEE
#   * whether the REAL `muster` honours these two variables. The authority is
#     `muster --help` ("Config, lowest to highest precedence: ~/.muster/muster.env
#     -> environment -> --api-url / --token"; "The token is read from
#     MUSTER_HOOK_TOKEN only"), re-read from the installed binary 2026-10-02. Same
#     cross-repo seam class as `MUSTER_VERBS` above. The fake below asserts our side
#     of that contract, never muster's.
#   * whether the live board accepts the token. These tests never touch the network.
# =========================================================================== #
#: 🔴 THE TWO NAMES A HUMAN HAS REVIEWED, spelled here rather than read from the
#: module, because an expectation taken from the thing under test asserts only that
#: it agrees with itself.
EXPECTED_CONFIG_ENV = ("MUSTER_API_URL", "MUSTER_HOOK_TOKEN")

#: A fake task CLI that REFUSES TO WORK unless it is configured, with the REAL
#: client's own exit codes — rc 2 for a missing base ("usage error") and rc 3 for a
#: missing token ("auth failure"), per `muster --help`'s table. Realistic rather
#: than a textbook sentinel: a stub that exited 1 for both could not tell the two
#: halves of the fix apart, and reproducing rc 2 is what makes the negative control
#: recognisable as the MEASURED defect rather than as a stub's invention.
#:
#: It echoes its own `$*` so the token can be asserted ABSENT from argv, and the
#: base and token it actually received so neither can be asserted by accident.
_FAKE_CLI = r"""
if [ -z "${MUSTER_API_URL:-}" ]; then
  echo "fake: no API URL: set MUSTER_API_URL in ~/.muster/muster.env" >&2
  exit 2
fi
if [ -z "${MUSTER_HOOK_TOKEN:-}" ]; then
  echo "fake: 401 Unauthorized" >&2
  exit 3
fi
printf '{"base":"%s","token":"%s","argv":"%s"}\n' \
  "$MUSTER_API_URL" "$MUSTER_HOOK_TOKEN" "$*"
exit 0
"""

#: The fixture env file's contents. THREE keys, and the two URLs are DISTINCT so the
#: `TASK_API_URL_VARS` precedence is observable: a derivation that read the generic
#: key would resolve the router value and this would fail. The token is distinct
#: from both URLs for the same reason.
_FIXTURE_TASK_URL = "http://pinned-task.invalid:30306"
_FIXTURE_ROUTER_URL = "http://pinned-router.invalid:30302"
_FIXTURE_TOKEN = "pinned-fixture-token-7a3f"
_FIXTURE_ENV = (
    "CLAWGATE_TASK_API_URL=%s\n"
    "CLAWGATE_API_URL=%s\n"
    "CLAWGATE_HOOK_TOKEN=%s\n" % (_FIXTURE_TASK_URL, _FIXTURE_ROUTER_URL,
                                  _FIXTURE_TOKEN)
)

#: 🔴 EVERY VARIABLE THAT COULD LET THE HOST'S OWN STATE ANSWER THESE TESTS. The
#: module layers the process environment OVER the env file, so a host that exports
#: `CLAWGATE_API_URL` (this one does, through `~/.claude/clawgate.env` consumers)
#: would have the fixture file silently overridden and the assertions would be about
#: the operator's box. Cleared in every test that reads the real `os.environ`.
_HOST_VARS = ("CLAWGATE_TASK_API_URL", "CLAWGATE_API_URL", "CLAWGATE_HOOK_TOKEN",
              "MUSTER_API_URL", "MUSTER_HOOK_TOKEN")


def _clear_host_vars(monkeypatch):
    for name in _HOST_VARS:
        monkeypatch.delenv(name, raising=False)


def _env_file(tmp_path) -> Path:
    p = tmp_path / "clawgate.env"
    p.write_text(_FIXTURE_ENV, encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# 5a. THE TWO NAMES, BOTH LANGUAGES
# --------------------------------------------------------------------------- #
def test_the_config_env_ledger_is_pinned_in_BOTH_directions():
    """🔴 The whole fix is "write the resolved base and token into these two keys".
    A typo in either is SILENT — an unset variable and a misspelled one are the same
    observable — so the names are pinned as reviewed literals, and this fails if
    either is renamed, dropped or swapped for the other's slot."""
    assert CG.TASK_CLI_BASE_ENV == EXPECTED_CONFIG_ENV[0], CG.TASK_CLI_BASE_ENV
    assert CG.TASK_CLI_TOKEN_ENV == EXPECTED_CONFIG_ENV[1], CG.TASK_CLI_TOKEN_ENV
    assert CG.TASK_CLI_BASE_ENV != CG.TASK_CLI_TOKEN_ENV


# 🔴 `test_the_shell_task_cli_CONFIG_ledger_matches_PYTHON` STOOD HERE AND IS DELETED.
# It re-asserted, by regex over `clawgate_handoff.sh`, that the shell's two config
# constants spell `MUSTER_API_URL` / `MUSTER_HOOK_TOKEN`. A shell-side typo in either
# is already caught BEHAVIOURALLY and harder: section 5c's fake client REFUSES to run
# without both variables, so a misspelling breaks the real wrapper running the real
# resolver.
#
# MEASURED 2026-10-03, one mutant at a time, under PYTHONDONTWRITEBYTECODE=1 with
# `__pycache__` purged between runs, each mutation the NARROWEST edit that can be
# wrong (one character off the end of one constant's value):
#
#   CLAWGATE_TASK_CLI_BASE_ENV="MUSTER_API_UR"    -> 4 failures, 3 of them behavioural:
#       test_the_SHELL_exec_wrapper_hands_the_resolved_client_BOTH_values
#       test_the_SHELL_wrapper_works_for_the_clawgatectl_SPELLING_TOO
#       test_the_SHELL_wrapper_does_not_LEAK_the_two_variables_to_its_caller
#   CLAWGATE_TASK_CLI_TOKEN_ENV="MUSTER_HOOK_TOKE" -> 5 failures, 4 behavioural
#       (the three above plus test_the_SHELL_wrapper_does_NOT_overwrite_an_already_set_value)
#
# Each behavioural failure is its OWN assertion (`p.returncode == 0`) carrying the
# fake's own refusal, `fake: no API URL` — not a neighbouring guard's error, which is
# the "green for the wrong reason" shape one level over. The deleted test was the 4th
# and 5th failure in those runs: a second, weaker report of a defect three tests
# already name with the failing command in hand.
#
# 🔴 ITS SIBLING `test_the_config_env_ledger_is_pinned_in_BOTH_directions` IS NOT
# DELETED, AND THE DIFFERENCE IS NOT COSMETIC. That one pins python's two constants
# against REVIEWED LITERALS, so it catches BOTH languages renamed CONSISTENTLY but
# wrong against the real client — a state in which every behavioural test above still
# passes, because the fake reads whatever the ledger says. The deleted test could
# never see that case: it only compared the two ledgers to EACH OTHER.


# --------------------------------------------------------------------------- #
# 5b. THE DERIVATION — the EXISTING precedence, not a second reader
# --------------------------------------------------------------------------- #
def test_task_cli_env_carries_the_TASK_base_and_the_token_from_the_env_FILE(tmp_path):
    """🔴 LITERAL EXPECTED VALUES, and the TASK url rather than the router one. The
    env file holds two DIFFERENT base URLs on purpose: a derivation that reached for
    `CLAWGATE_API_URL` would resolve the router — which answers the same routes with
    a board that is stale for exactly the in-flight tasks, so it would be a
    confident 200 and a wrong answer."""
    got = CG.task_cli_env(env={}, path=str(_env_file(tmp_path)))
    assert got["MUSTER_API_URL"] == "http://pinned-task.invalid:30306"
    assert got["MUSTER_HOOK_TOKEN"] == "pinned-fixture-token-7a3f"


def test_task_cli_env_falls_THROUGH_an_empty_task_key_to_the_generic_one(tmp_path):
    """The `${A:-$B}` rule, which is `_base_from`'s and the shell's everywhere else:
    an EMPTY value counts as unset. `CLAWGATE_TASK_API_URL=` must not resolve to a
    bare path — it must fall through, so a host that never heard of the task/router
    split keeps working."""
    p = tmp_path / "clawgate.env"
    p.write_text("CLAWGATE_TASK_API_URL=\n"
                 "CLAWGATE_API_URL=http://pinned-router.invalid:30302\n"
                 "CLAWGATE_HOOK_TOKEN=pinned-fixture-token-7a3f\n", encoding="utf-8")
    got = CG.task_cli_env(env={}, path=str(p))
    assert got["MUSTER_API_URL"] == "http://pinned-router.invalid:30302"


def test_task_cli_env_does_NOT_overwrite_an_ALREADY_SET_muster_value(tmp_path):
    """🔴 `muster`'s own precedence is its env file, then the environment, then
    flags — so a deliberate `MUSTER_API_URL=… cmd` or an operator's export must
    still win. This only FILLS IN what nothing has set."""
    got = CG.task_cli_env(
        env={"MUSTER_API_URL": "http://operator-override.invalid:1",
             "MUSTER_HOOK_TOKEN": "operator-override-token"},
        path=str(_env_file(tmp_path)))
    assert got["MUSTER_API_URL"] == "http://operator-override.invalid:1"
    assert got["MUSTER_HOOK_TOKEN"] == "operator-override-token"


def test_an_EMPTY_muster_value_does_not_mask_the_file(tmp_path):
    """The other half of the `${A:-$B}` rule, in the OVERLAY direction: empty means
    unset everywhere in this module, so `MUSTER_HOOK_TOKEN= cmd` must be "no token"
    and get filled in, not "a bearer header reading `Bearer `"."""
    got = CG.task_cli_env(env={"MUSTER_API_URL": "", "MUSTER_HOOK_TOKEN": ""},
                          path=str(_env_file(tmp_path)))
    assert got["MUSTER_API_URL"] == "http://pinned-task.invalid:30306"
    assert got["MUSTER_HOOK_TOKEN"] == "pinned-fixture-token-7a3f"


def test_task_cli_env_OMITS_the_token_key_when_the_file_carries_none(tmp_path):
    """No token is a STATE, not an empty string. The key is absent rather than
    present-and-blank, so a caller inspecting the mapping sees the truth — and the
    base is still resolved, because "where the board is" is answerable without a
    credential and the client's own rc 3 is the honest report."""
    p = tmp_path / "clawgate.env"
    p.write_text("CLAWGATE_TASK_API_URL=http://pinned-task.invalid:30306\n",
                 encoding="utf-8")
    got = CG.task_cli_env(env={}, path=str(p))
    assert "MUSTER_HOOK_TOKEN" not in got
    assert got["MUSTER_API_URL"] == "http://pinned-task.invalid:30306"


def test_task_cli_env_returns_the_WHOLE_environment_not_just_the_overlay(tmp_path):
    """🔴 `subprocess`' `env=` REPLACES rather than merges, so a mapping holding only
    the two new keys would run the client with no PATH, no HOME and no TMPDIR. Every
    consumer hands this straight to `subprocess.run(env=…)`."""
    got = CG.task_cli_env(env={"PATH": "/pinned/bin", "HOME": "/pinned/home"},
                          path=str(_env_file(tmp_path)))
    assert got["PATH"] == "/pinned/bin"
    assert got["HOME"] == "/pinned/home"


def test_task_cli_env_reads_the_file_through_the_SHARED_reader_and_not_a_new_one(
        tmp_path, monkeypatch):
    """🔴 NOT A SECOND SOURCE OF TRUTH FOR THE CREDENTIAL. Both values must come out
    of this module's existing readers, so stubbing `task_base_url` and `hook_token`
    — the two that own `TASK_API_URL_VARS` and the file/process layering — changes
    what the overlay carries. A private `open()` inside `task_cli_env` would ignore
    both stubs and this would fail."""
    monkeypatch.setattr(CG, "task_base_url",
                        lambda env=None, path=None: "http://via-shared-reader.invalid:9")
    monkeypatch.setattr(CG, "hook_token",
                        lambda env=None, path=None: "via-shared-reader-token")
    got = CG.task_cli_env(env={}, path=str(_env_file(tmp_path)))
    assert got["MUSTER_API_URL"] == "http://via-shared-reader.invalid:9"
    assert got["MUSTER_HOOK_TOKEN"] == "via-shared-reader-token"


# --------------------------------------------------------------------------- #
# 5c. 🔴 BEHAVIOURAL — a fake client that REFUSES to work unconfigured
# --------------------------------------------------------------------------- #
def _fake_cli_bin(tmp_path, name="muster") -> Path:
    b = tmp_path / "bin"
    b.mkdir(parents=True, exist_ok=True)
    mockbin.write_exec(b / name, _FAKE_CLI)
    return b


def test_the_NEGATIVE_CONTROL_the_fake_client_really_refuses_an_UNCONFIGURED_run(
        tmp_path, monkeypatch):
    """🔴 WITHOUT THIS, EVERY GREEN BELOW IS INDISTINGUISHABLE FROM A FAKE THAT
    ALWAYS EXITS 0. This is also the MEASURED DEFECT reproduced in miniature: the
    resolver's own answer, run bare, exits 2 with "no API URL" — which is byte-for-
    byte the shape `/resume` printed on both hosts."""
    b = _fake_cli_bin(tmp_path)
    monkeypatch.setenv("PATH", str(b))
    _clear_host_vars(monkeypatch)
    cli = CG.resolve_task_cli()
    assert cli == "muster"
    p = subprocess.run([cli, "task", "get", "686"], capture_output=True, text=True,
                       env=dict(os.environ))
    assert p.returncode == 2, (p.returncode, p.stdout, p.stderr)
    assert "no API URL" in p.stderr, p.stderr


def test_the_SECOND_NEGATIVE_CONTROL_a_base_WITHOUT_a_token_is_still_refused(
        tmp_path, monkeypatch):
    """🔴 THE HALF-FIX LEG, and it is not hypothetical — `--api-url <base>` alone
    was measured as rc 3 / 401 against the live board. A fake that only checked the
    base would score the token half of this change as covered when it is not."""
    b = _fake_cli_bin(tmp_path)
    monkeypatch.setenv("PATH", str(b))
    _clear_host_vars(monkeypatch)
    env = dict(os.environ)
    env["MUSTER_API_URL"] = "http://pinned-task.invalid:30306"
    p = subprocess.run(["muster", "task", "get", "686"], capture_output=True,
                       text=True, env=env)
    assert p.returncode == 3, (p.returncode, p.stdout, p.stderr)
    assert "401" in p.stderr, p.stderr


def test_the_PYTHON_half_RUNS_the_resolved_client_successfully(tmp_path, monkeypatch):
    """🔴 THE RELATIONSHIP, DRIVEN: resolve a client through the REAL resolver, give
    it the REAL `task_cli_env`, and the client that refuses an unconfigured run
    answers. Not a structural check — a structural check type-checks past a wrong
    argument, and the two controls above prove this fake can go red."""
    b = _fake_cli_bin(tmp_path)
    monkeypatch.setenv("PATH", str(b))
    _clear_host_vars(monkeypatch)
    envf = _env_file(tmp_path)
    cli = CG.resolve_task_cli()
    p = subprocess.run([cli, "task", "get", "686"], capture_output=True, text=True,
                       env=CG.task_cli_env(path=str(envf)))
    assert p.returncode == 0, (p.returncode, p.stdout, p.stderr)
    got = json.loads(p.stdout)
    assert got["base"] == "http://pinned-task.invalid:30306", got
    assert got["token"] == "pinned-fixture-token-7a3f", got
    assert got["argv"] == "task get 686", got


def test_the_token_never_reaches_the_client_ARGV(tmp_path, monkeypatch):
    """🔴 An argv is world-readable through /proc and this runs after every turn.
    `muster --help` says it of its own `--token` flag ("flags are visible in ps"),
    and the write-back guard's curl path already puts the bearer on stdin for the
    same reason. The fake echoes its `$*`, so this reads the real argv."""
    b = _fake_cli_bin(tmp_path)
    monkeypatch.setenv("PATH", str(b))
    _clear_host_vars(monkeypatch)
    p = subprocess.run([CG.resolve_task_cli(), "task", "get", "686"],
                       capture_output=True, text=True,
                       env=CG.task_cli_env(path=str(_env_file(tmp_path))))
    got = json.loads(p.stdout)
    assert _FIXTURE_TOKEN not in got["argv"], got["argv"]
    assert _FIXTURE_TOKEN == got["token"], "the fake did not actually see the token"


def _run_shell_exec(bindir: Path, home: Path, extra_env=None, bare=False):
    """Source the shared shell lib, resolve the client, and run it — through the
    wrapper (`bare=False`) or exactly as the defective call site did (`bare=True`).

    HOME is repointed because the lib expands `$HOME/$CLAWGATE_ENV_REL` at CALL
    time; PATH is stripped to the fixture so the resolver cannot find a real client.
    """
    invoke = ('"$cli" task get 686' if bare
              else 'clawgate_task_cli_exec "$cli" task get 686')
    script = (
        'set -u\n'
        '. "%s"\n'
        'cli=$(clawgate_task_cli) || { echo "NO CLI" >&2; exit 90; }\n'
        '%s\n' % (SH_LIB, invoke)
    )
    env = dict(os.environ)
    for name in _HOST_VARS:
        env.pop(name, None)
    env["PATH"] = str(bindir)
    env["HOME"] = str(home)
    env.update(extra_env or {})
    bash = shutil.which("bash") or "/bin/bash"
    p = subprocess.run([bash, "-c", script], capture_output=True, text=True, env=env)
    return p


def _shell_home(tmp_path) -> Path:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    (home / ".claude" / "clawgate.env").write_text(_FIXTURE_ENV, encoding="utf-8")
    return home


def test_the_SHELL_NEGATIVE_CONTROL_reproduces_the_MEASURED_resume_failure(tmp_path):
    """🔴 THE DEFECT ITSELF, in the shell, through the real resolver and the real
    lib: `$("$cli" task get "$id")` — what `resume-state.sh` did — exits 2 with "no
    API URL" even though the env file beside it carries base and token. This is the
    `(muster exit 2 — task #N NOT checked)` line that every `/resume` on both hosts
    printed, and without it the pass below proves nothing."""
    p = _run_shell_exec(_fake_cli_bin(tmp_path), _shell_home(tmp_path), bare=True)
    assert p.returncode == 2, (p.returncode, p.stdout, p.stderr)
    assert "no API URL" in p.stderr, p.stderr


def test_the_SHELL_exec_wrapper_hands_the_resolved_client_BOTH_values(tmp_path):
    """🔴 THE SHELL CODE RUN, not read — the twin of the python leg above, and a
    different claim from the ledger regex in 5a: two agreeing ledgers walked by a
    wrapper that exports neither would pass that test and fail here."""
    p = _run_shell_exec(_fake_cli_bin(tmp_path), _shell_home(tmp_path))
    assert p.returncode == 0, (p.returncode, p.stdout, p.stderr)
    got = json.loads(p.stdout)
    assert got["base"] == "http://pinned-task.invalid:30306", got
    assert got["token"] == "pinned-fixture-token-7a3f", got
    assert got["argv"] == "task get 686", got
    assert _FIXTURE_TOKEN not in got["argv"], got["argv"]


def test_the_SHELL_wrapper_works_for_the_clawgatectl_SPELLING_TOO(tmp_path):
    """🔴 NO REGRESSION FOR THE FALLBACK, which already reads `~/.claude/clawgate.env`
    itself and ignores `MUSTER_*`. One mapping has to be right for BOTH spellings —
    a per-binary branch is the N-sites shape this whole module exists to avoid — so
    the wrapper must not special-case the preferred name. The fake stands in for
    `clawgatectl` here: what is asserted is that the wrapper configured whatever the
    resolver returned, not that the real clawgatectl needs these keys."""
    b = tmp_path / "bin"
    b.mkdir()
    mockbin.write_exec(b / "clawgatectl", _FAKE_CLI)
    p = _run_shell_exec(b, _shell_home(tmp_path))
    assert p.returncode == 0, (p.returncode, p.stdout, p.stderr)
    assert json.loads(p.stdout)["base"] == "http://pinned-task.invalid:30306"


def test_the_SHELL_wrapper_does_NOT_overwrite_an_already_set_value(tmp_path):
    """The shell half of the override rule asserted in 5b for python."""
    p = _run_shell_exec(
        _fake_cli_bin(tmp_path), _shell_home(tmp_path),
        extra_env={"MUSTER_API_URL": "http://operator-override.invalid:1"})
    assert p.returncode == 0, (p.returncode, p.stdout, p.stderr)
    assert json.loads(p.stdout)["base"] == "http://operator-override.invalid:1"


def test_the_SHELL_wrapper_does_not_LEAK_the_two_variables_to_its_caller(tmp_path):
    """The subshell is the point: a `/resume` that exported a bearer token into its
    own environment would hand it to every later command in the digest.

    🔴 IT ASSERTS THE CLIENT RAN FIRST, and that is not padding. An absent wrapper
    is `command not found` — rc 127 and nothing exported — so a test that checked
    only the two variables being empty would PASS against the defect, vacuously.
    Measured: the first version of this test did exactly that at origin/main.
    """
    b = _fake_cli_bin(tmp_path)
    script = (
        'set -u\n'
        '. "%s"\n'
        'cli=$(clawgate_task_cli)\n'
        'out=$(clawgate_task_cli_exec "$cli" task get 686) || exit 91\n'
        'printf "RAN[%%s]LEAK[%%s][%%s]\\n" "$out" '
        '"${MUSTER_API_URL:-}" "${MUSTER_HOOK_TOKEN:-}"\n'
        % SH_LIB
    )
    env = dict(os.environ)
    for name in _HOST_VARS:
        env.pop(name, None)
    env["PATH"] = str(b)
    env["HOME"] = str(_shell_home(tmp_path))
    bash = shutil.which("bash") or "/bin/bash"
    p = subprocess.run([bash, "-c", script], capture_output=True, text=True, env=env)
    assert p.returncode == 0, (p.returncode, p.stdout, p.stderr)
    ran, _, leak = p.stdout.strip().partition("LEAK")
    # The client really ran AND really saw the token...
    assert _FIXTURE_TOKEN in ran, p.stdout
    # ...and the caller's own environment carries neither value afterwards.
    assert leak == "[][]", p.stdout


def test_the_SHELL_wrapper_PASSES_THROUGH_the_clients_exit_code(tmp_path):
    """🔴 Every caller branches on the rc — `/resume` turns a non-zero into a `!`
    gap naming the code (3=auth 4=no such task 6=unreachable). A wrapper that
    swallowed it would turn an auth failure into a clean read, which is the
    false-clean the whole block exists to prevent."""
    b = tmp_path / "bin"
    b.mkdir()
    mockbin.write_exec(b / "muster", 'echo "boom" >&2\nexit 6\n')
    p = _run_shell_exec(b, _shell_home(tmp_path))
    assert p.returncode == 6, (p.returncode, p.stdout, p.stderr)
    assert "boom" in p.stderr, p.stderr


def test_the_SHELL_wrapper_keeps_the_two_STREAMS_apart(tmp_path):
    """Both clients document that JSON goes to stdout and nothing else does, and
    `resume-state.sh` pipes stdout into `jq`. A wrapper that merged them would feed
    a diagnostic to the parser."""
    b = tmp_path / "bin"
    b.mkdir()
    mockbin.write_exec(b / "muster", 'echo "a diagnostic" >&2\necho "{}"\n')
    p = _run_shell_exec(b, _shell_home(tmp_path))
    assert p.stdout.strip() == "{}", p.stdout
    assert "a diagnostic" in p.stderr, p.stderr


# --------------------------------------------------------------------------- #
# 5d. 🔴 THE LEDGER OF INVOKING SITES — a relationship, not one side
# --------------------------------------------------------------------------- #
#: 🔴 EVERY SITE THAT RUNS A RESOLVED TASK CLI, AND THE LITERAL THAT MAKES ITS
#: INVOCATION CONFIGURED. Asserted as a SET: it fails when a site stops configuring
#: its invocation (the defect), and a NEW site cannot be added without this ledger
#: moving — which is the half a per-site test cannot see, because the defect was
#: never one site being wrong. It was the resolver handing every site an answer that
#: was not runnable, so all of them were wrong at once.
#:
#: ⚠ `clawgate-task-interview-guard.py` is deliberately ABSENT: it RECOGNISES both
#: spellings in a Bash command someone else typed and resolves/invokes nothing.
INVOKING_SITES = {
    "scripts/resume-state.sh":
        'clawgate_task_cli_exec "$cli" task get "$id"',
    "scripts/lib/cairn_who.py":
        "env=_CG.task_cli_env()",
    "scripts/claude-hooks/clawgate-writeback-guard.py":
        "env=cli_env",
}


def test_every_site_that_INVOKES_a_resolved_task_CLI_CONFIGURES_it():
    """🔴 PINS THE RELATIONSHIP: resolving a client implies a CONFIGURED invocation,
    at every site that runs one. Each site's own suite drives its behaviour; what no
    per-site test can see is the SET — and the set is what was wrong."""
    missing = []
    for rel, literal in sorted(INVOKING_SITES.items()):
        p = REPO / rel
        assert p.exists(), rel
        if literal not in p.read_text(encoding="utf-8"):
            missing.append("%s: expected to find %r" % (rel, literal))
    # ⚠ THE MESSAGE NAMES THE REVIEWED LITERALS, NOT `CG.TASK_CLI_*`. Reading them
    # off the module under test makes the FORMATTER fail (AttributeError) before the
    # assertion can — so the test goes red for someone else's reason, which is the
    # "green for the wrong reason" shape one level over. Measured: that is exactly
    # how this test first failed at origin/main.
    assert missing == [], (
        "a site resolves a task CLI and no longer hands it its config — the "
        "resolved binary reads its base URL and token from %s/%s and nothing else "
        "sets them:\n  %s"
        % (EXPECTED_CONFIG_ENV[0], EXPECTED_CONFIG_ENV[1], "\n  ".join(missing)))


#: The variable NAMES this scan recognises in command position. Spelled out as a
#: ledger because the docstring below has to state exactly this and nothing wider.
_RESOLVED_CLI_VARS = ("cli", "task_cli", "taskcli")

#: A resolved-CLI variable in COMMAND POSITION with a task verb straight after it —
#: i.e. an invocation that was NOT routed through the exec wrapper. The negative
#: lookbehind is what distinguishes the two, and it is the whole content of the
#: pattern, so the controls below are not optional.
#:
#: 🔴 WIDENED 2026-10-03 TO THE THREE SPELLINGS OF THE SAME VARIABLE. It matched only
#: `"$cli"`, so `${cli}` and a bare unquoted `$cli` — the same one-character-wide
#: hazard, written differently — walked straight past it. All three now carry the
#: same `_exec ` lookbehind, so the WRAPPED form is still exempt in every spelling.
#:
#: ⚠ WHAT IT STILL DOES NOT SEE, STATED HERE RATHER THAN IMPLIED BY SILENCE: a
#: DIFFERENT variable name (`$bin`, `$c`), and a GLOBAL FLAG between the binary and
#: the verb (`"$cli" --api-url X task get`), because the verb must follow the
#: variable with only whitespace between. Those are deliberate: a pattern loose
#: enough to catch an arbitrary variable in command position fires on ordinary shell
#: and a permanently-red gate trains everyone to click through. The `INVOKING_SITES`
#: ledger above is the guard that does NOT depend on spelling — it pins the SET of
#: invoking sites, so a fourth site under any variable name fails there.
#: ⚠ NO RIGHT-HAND `(?![A-Za-z0-9_])` ON THE UNQUOTED ALTERNATIVE, DELIBERATELY. One
#: was written here and MEASURED REDUNDANT: the `\s+` that follows already requires
#: whitespace immediately after the variable name, and whitespace is non-alnum, so
#: the lookahead can never reject an input `\s+` accepts. It SURVIVED its own mutant
#: (battery row S7, now withdrawn) over 12 spellings including `$cli_other`,
#: `$clix`, `$cli2` and `$cli-x` — every one of which is excluded by `\s+` alone.
#: Deleted rather than hardened: an unkillable guard is redundant or unreachable,
#: and leaving it reads as protection nobody needs to check.
_BARE_SH_INVOKE_RX = re.compile(
    r'(?<!_exec )'
    r'(?:"\$(?:%(v)s)"|\$\{(?:%(v)s)\}|\$(?:%(v)s))'
    r'\s+(?:task|agent|chief)\b' % {"v": "|".join(_RESOLVED_CLI_VARS)})


def _is_shell_file(p) -> bool:
    """A `.sh`, or an extensionless file whose SHEBANG says shell.

    🔴 THE SHEBANG, NOT THE ABSENCE OF A SUFFIX. `SHELL_SUFFIXES` admits `""`, and
    measured 2026-10-03 the 70 extensionless files under the scan roots are 17 bash,
    48 `python3` and 5 with no shebang — so reading "extensionless" as "shell" would
    hand 53 non-shell files to a shell regex. A false positive there is a
    permanently-red gate, which is worse than the narrow scan this replaces.
    """
    if p.suffix not in SHELL_SUFFIXES:
        return False
    if p.suffix == ".sh":
        return True
    try:
        with p.open("r", encoding="utf-8", errors="replace") as fh:
            first = fh.readline()
    except OSError:                                 # pragma: no cover
        return False
    return first.startswith("#!") and ("bash" in first or "sh" in first.split("/")[-1])


def _bare_sh_hits(files):
    """`<rel>:<line>: <text>` for every invocation of a resolved task CLI through one
    of `_RESOLVED_CLI_VARS`, in command position, with a task verb straight after
    it, that is not prefixed by the exec wrapper — comments aside.

    ⚠ THAT SENTENCE IS DELIBERATELY NARROWER THAN "every BARE invocation", which is
    what it used to claim. The pattern is SPELLED: it is walkable by a different
    variable name or by a global flag between the binary and the verb (both listed
    at `_BARE_SH_INVOKE_RX`). A guard's description claims coverage, so the sentence
    must not be wider than the code — and reading as coverage while providing none is
    worse than none, because it stops anyone looking. `INVOKING_SITES` is the guard
    that holds regardless of spelling.

    🔴 WHICH FILES — `SHELL_SUFFIXES`, the ledger declared beside `SCAN_SUFFIXES`,
    plus a shebang test for the extensionless half. This was `p.suffix != ".sh"`
    open-coded here, which skipped the 17 extensionless bash scripts `_scan_files()`
    deliberately yields. One rule, one place.

    🔴 COMMENT LINES ARE NOT INVOCATIONS, and skipping them is not a loophole — it
    is what keeps this guard alive. These files are prose as well as code, and the
    comments that explain THIS defect necessarily quote the defective line verbatim
    (`scripts/resume-state.sh` and `clawgate_handoff.sh` both do, right above the
    fix). A guard that fired on its own explanation would be permanently red, and a
    permanently-red gate trains everyone to click through.

    Takes the file list as an ARGUMENT so the pipeline — the walk, the suffix
    filter, the comment skip and the regex together — can be driven over a fixture
    that MUST produce a hit. The regex alone passing its controls does not establish
    that the thing built on top of it can still count.
    """
    hits = []
    for rel, p in files:
        if not _is_shell_file(p):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):       # pragma: no cover
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if _BARE_SH_INVOKE_RX.search(line):
                hits.append("%s:%d: %s" % (rel, n, line.strip()[:120]))
    return hits


def test_the_shell_suffix_ledger_is_a_SUBSET_of_what_the_walk_yields():
    """🔴 THE SEAM BETWEEN THE TWO LEDGERS, which is the defect this replaced: a
    suffix the FILTER wants but the WALK never yields is a scan that can never see
    those files, and nothing in either ledger alone says so."""
    assert SHELL_SUFFIXES <= SCAN_SUFFIXES, (
        "the shell filter admits %r, which `_scan_files()` does not yield — so those "
        "files are unreachable by the scan no matter what the filter says."
        % sorted(SHELL_SUFFIXES - SCAN_SUFFIXES))
    assert "" in SHELL_SUFFIXES, (
        "extensionless files are back out of the shell ledger; measured 2026-10-03 "
        "there are 17 extensionless `#!/usr/bin/env bash` scripts under the scan "
        "roots, and dropping them is exactly the narrowing this closed.")


def test_the_scan_really_READS_the_extensionless_bash_scripts():
    """🔴 THE POSITIVE CONTROL FOR THE WIDENING, over the REAL corpus walk and not a
    fixture. The scan over this repo returns ZERO hits either way — measured, no live
    miss was being hidden — so "I widened it" is indistinguishable from "I changed a
    constant nothing reads" unless the file SET is checked directly."""
    shell = [rel for rel, p in _scan_files() if _is_shell_file(p)]
    extless = [rel for rel in shell if rel.suffix == ""]
    assert len(extless) >= 15, (
        "only %d extensionless shell script(s) reached the scan; 17 were measured "
        "2026-10-03, and a count near zero means the shebang test or the suffix "
        "ledger stopped working: %r" % (len(extless), sorted(map(str, extless))))
    rels = {r.as_posix() for r in extless}
    # Two named exemplars, so a glob that silently stopped matching is visible.
    assert "scripts/run3" in rels, sorted(rels)
    assert "scripts/muse/muse" in rels, sorted(rels)
    # ...and the python ones are NOT handed to a shell regex.
    assert "scripts/cairn-who" not in rels, sorted(rels)
    assert "scripts/obs-read" not in rels, sorted(rels)


def test_no_SHELL_site_invokes_a_resolved_task_CLI_without_the_wrapper():
    """🔴 THE MECHANICAL HALF of the ledger above, for the language where the hazard
    is one character wide. `"$cli" task get` and
    `clawgate_task_cli_exec "$cli" task get` differ only by the prefix, and the
    first is what shipped."""
    me = Path(__file__).relative_to(REPO)
    hits = _bare_sh_hits((rel, p) for rel, p in _scan_files() if rel != me)
    assert hits == [], (
        "a resolved task CLI is being run WITHOUT its config — `muster` reads its "
        "base URL and token only from %s/%s, so this exits 2 before it sends "
        "anything. Route it through `clawgate_task_cli_exec`:\n  %s"
        % (EXPECTED_CONFIG_ENV[0], EXPECTED_CONFIG_ENV[1], "\n  ".join(hits)))


def test_the_POSITIVE_CONTROL_for_that_scan(tmp_path):
    """🔴 A ZERO IS INDISTINGUISHABLE FROM A SCAN WIRED TO NOTHING, and the comment
    skip above is exactly the kind of filter that can swallow everything. Feed the
    REAL pipeline a file that MUST score 1 — the defective line as CODE plus the
    same line inside a comment — and watch the number move."""
    f = tmp_path / "fixture.sh"
    f.write_text(
        '# this line QUOTES the defect: json=$("$cli" task get "$id")\n'
        'json=$("$cli" task get "$id" 2>/dev/null); rc=$?\n'
        'json=$(clawgate_task_cli_exec "$cli" task get "$id"); rc=$?\n',
        encoding="utf-8")
    hits = _bare_sh_hits([(Path("fixture.sh"), f)])
    assert len(hits) == 1, hits
    assert hits[0].startswith("fixture.sh:2:"), hits


def test_the_CONTROLS_for_that_bare_invocation_scan():
    """🔴 An empty hit list is indistinguishable from a regex wired to nothing, and
    the discriminating case here is the WRAPPED form — a pattern that caught both
    would be permanently red and get deleted.

    🔴 ALL THREE SPELLINGS, IN BOTH DIRECTIONS. The widening is only real if each
    new alternative is shown to catch the bare form AND to exempt the wrapped one;
    a lookbehind written for `"$cli"` and not re-applied to `${cli}` would turn the
    wrapper itself into a permanent hit."""
    for case in (
            # the original quoted spelling
            'json=$("$cli" task get "$id" 2>/dev/null); rc=$?',
            '  "$cli" task get 686',
            '"$task_cli" agent ls',
            '"$cli" chief ask operator --body x',
            # 🔴 the two spellings that walked straight past this before
            'json=$(${cli} task get "$id"); rc=$?',
            '  ${task_cli} agent ls',
            'out=$($cli task get 686)',
            '$taskcli chief ask operator --body x',
    ):
        assert _BARE_SH_INVOKE_RX.search(case), case
    for case in (
            'json=$(clawgate_task_cli_exec "$cli" task get "$id"); rc=$?',
            'clawgate_task_cli_exec "$cli" task get 686',
            # the WRAPPED form in the two new spellings — the lookbehind must apply
            # to each alternative, not only the quoted one
            'clawgate_task_cli_exec ${cli} task get 686',
            'json=$(clawgate_task_cli_exec $cli task get "$id")',
            'echo "  ($cli exit $rc — task #$id NOT checked)"',
            'if ! cli=$(clawgate_task_cli); then',
            # a LONGER variable that merely starts with a recognised name. ⚠ It is
            # the `\\s+` that excludes this, NOT a right-hand boundary — measured,
            # see `_BARE_SH_INVOKE_RX`. Kept because the CASE is real; the
            # mechanism note is corrected.
            '$cli_other task get 686',
            '$clix task get 686',
    ):
        assert not _BARE_SH_INVOKE_RX.search(case), case


def test_the_DOCUMENTED_LIMITS_of_that_pattern_really_are_limits():
    """🔴 A GUARD'S DESCRIPTION CLAIMS COVERAGE, SO THE LIMITS MUST BE TRUE TOO.
    `_bare_sh_hits`' docstring names exactly two escapes — another variable name, and
    a global flag between the binary and the verb. Asserted rather than asserted-in-
    prose: if a later widening closes one, this test fails and the docstring gets
    corrected in the same change instead of drifting the other way (a sentence
    narrower than the code is merely untidy; one WIDER is the defect)."""
    for case in ('"$bin" task get 686',            # a name outside the ledger
                 '"$c" task get 686',
                 '"$cli" --api-url http://x.invalid task get 686',   # a global flag
                 '${cli} -v task get 686'):
        assert not _BARE_SH_INVOKE_RX.search(case), (
            "the pattern now catches %r, which `_bare_sh_hits` documents as an "
            "escape it does NOT see. Widening it is fine — update that docstring in "
            "the same change." % case)
