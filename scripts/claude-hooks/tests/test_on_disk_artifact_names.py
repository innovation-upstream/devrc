#!/usr/bin/env python3
"""🔴 THE ON-DISK ARTIFACT-NAME REGISTRY — the CLASS, not one instance of it.

WHY THIS FILE EXISTS

  Every hook here keeps state under `~/.cache/<something>/…`, and in each one the
  WRITER and the READER of a given artifact go through ONE shared function or one
  shared constant. That makes a rename SELF-CONSISTENT: `dismiss` writes what
  `is_dismissed` looks for, `claim` writes what `already_fired` reads, so nothing
  inside the module ever disagrees with itself and no behavioural test can move.
  The name is real, it is on a real disk, and it is invisible to the suite.

  Measured 2026-08-17 against a GREEN 1,133-test baseline — each name renamed on
  its own, the module re-imported, the whole suite re-run. Fifteen survived:

    scripts/claude-hooks/clawgate-writeback-guard.py
      `.cache` · `claude-clawgate-writeback` · `s` · `work` · `dismissals` · `.tmp`
    scripts/claude-hooks/next-step-nudge.py
      `.cache` · `claude-next-step-nudge` · `s` · `fired`
    scripts/claude-hooks/shell-env-nudge.py
      `.cache` · `claude-shell-env-nudge`
    scripts/claude-hooks/search-tool-nudge.py
      the `@` that joins session to agent in a state-dir name
    scripts/lib/agent_ledger.py
      `AGENT_LEDGER_V1` (the wire sentinel) · `.ledger.` (the temp-file prefix)

  The controls ran in the same sweep and went red, so this is a fact about those
  fifteen names and not about the harness: renaming `dismissed-`, `read-`,
  `fires-`, `unknown-`, `agent-ledger`, the record extension, the `*.json` read
  glob, every `claude-notify` name and three of the four `search-tool-nudge` names
  all FAILED tests that already existed.

  Those four already-pinned guard names are the tell. `dismissed-` is pinned
  because a mutation sweep once found an off-by-one in `_dismissed_path`, and the
  fix was applied to the instance it found — a whole-string assertion on one
  message — and not to the class the sweep had just demonstrated. This file is the
  class.

WHAT A RENAME ACTUALLY COSTS

  These caches are read by code that `home-manager switch` REPLACES underneath
  live sessions. That is not a hypothetical: this repo's own CLAUDE.md warns that
  `git pull` changes nothing nix manages and the switch is what swaps the file, so
  there is always a window in which sessions started before the switch are read by
  a hook deployed after it. Rename the root and every one of them silently loses
  its anchors:

    * `claude-clawgate-writeback/s/<session>/read-<id>` is the ONLY record that a
      card was picked up. Orphan it and the write-back guard stops blocking — the
      exact failure the guard exists to prevent, arriving as silence.
    * `…/<session>/work` is the ONLY record that work happened. Orphan it and the
      guard cannot fire at all: no work, nothing owed.
    * `claude-clawgate-writeback/dismissals` is the audit log for a deliberate
      bypass of a deterministic guard. Orphan it and "is `--dismiss` being used
      honestly?" becomes unanswerable across the rename.
    * `claude-next-step-nudge/s/<session>/fired` is the once-per-session token.
      Orphan it and a session that was already nudged is nudged again.

  None of that raises. All of it reads as the tool being quiet.

HOW EACH NAME IS PINNED HERE

  BEHAVIOURALLY, and as a WHOLE PATH. Each test drives the module's real writers
  against a throwaway $HOME, then walks that $HOME and compares the COMPLETE set
  of relative paths against a literal list. That form is deliberate:

    * a whole-path literal cannot be walked by renaming one component, the way a
      `"claude-clawgate-writeback" in path` check could be;
    * comparing the COMPLETE set means the assertion fails when the set GROWS as
      well as when it shrinks — a new artifact written by a future change arrives
      as a red test naming the new path, not as an unpinned sixteenth name;
    * driving the real writer means the pin survives a call site that stops using
      the constant, which a `MODULE.STATE_WORK == "work"` assertion would not.

  Fixture values are pairwise distinct and distinct from every constant the code
  names — task ids 307 and 911 against `MAX_TASKS=5` / `MAX_FIRES=3` /
  `MAX_BLOCKS=2`, and session/agent ids that no module defines — so a mutant that
  hardcodes a literal cannot satisfy an assertion by accident.

  ONE of the fifteen is deliberately NOT pinned as a literal: `agent_ledger`'s
  `.ledger.` temp prefix. What is load-bearing there is that a temp file cannot be
  picked up by that module's own `*.json` read glob, and that property survives
  many spellings — so the test asserts the property and lets the name move. Its
  own docstring carries the four-row measurement showing which spellings it does
  and does not go red for.

  A FIX RIDES WITH THIS. Pinning the guard's temp name surfaced a real defect in
  it: `record_read`'s temp file was `read-<id>.tmp`, inside the `read-` namespace
  that both readers of that directory select on. See
  `test_a_LEFTOVER_read_temp_file_is_invisible_to_the_ledger_and_the_census` —
  that one IS a regression test, red at `origin/main` for the right reason.

  run:  python -m pytest scripts/claude-hooks/tests/test_on_disk_artifact_names.py -q
"""
import importlib.machinery
import importlib.util
import os
import re
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
HOOKS = os.path.abspath(os.path.join(HERE, os.pardir))
ROOT = os.path.abspath(os.path.join(HOOKS, os.pardir, os.pardir))
LIB = os.path.join(ROOT, "scripts", "lib")

# Fixture ids: pairwise distinct, and distinct from MAX_TASKS/MAX_FIRES/MAX_BLOCKS
# and from every task id the other suites use (193, 200, 201).
TASK_A = 307
TASK_B = 911
SESSION = "sess-on-disk-a1"
AGENT = "agent-on-disk-b2"


def load(path, name):
    """Import a module by path. Called INSIDE a test, after $HOME is redirected,
    because three of these modules resolve `~` at IMPORT time into a module
    constant — importing at collection time would bake this developer's real
    $HOME into `CACHE_DIR` and the walk below would find nothing."""
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    return h


def paths_under(home_dir):
    """Every FILE below `home_dir`, as `/`-joined relative paths, sorted.

    Directories are not listed separately: every directory in these trees exists
    only to hold a file, so the file paths already carry each directory name —
    and a listing that included empty directories would make the expected sets
    depend on `makedirs` order rather than on what was written.
    """
    out = []
    for dirpath, _dirnames, filenames in os.walk(str(home_dir)):
        for name in filenames:
            full = os.path.join(dirpath, name)
            out.append(os.path.relpath(full, str(home_dir)).replace(os.sep, "/"))
    return sorted(out)


# --------------------------------------------------------------------------- #
# 1. clawgate-writeback-guard.py — six unpinned names
# --------------------------------------------------------------------------- #
def test_the_writeback_guard_writes_EXACTLY_these_paths(home):
    """🔴 The whole tree, as literals. Six names in this list survived a rename
    against a green suite: `.cache`, `claude-clawgate-writeback`, `s`, `work`,
    `dismissals` and (below) `.tmp`.

    `read-`, `fires-`, `unknown-` and `dismissed-` are in the list too and were
    already killable — they are carried here anyway, because the value of this
    assertion is that it covers the WHOLE set. Pinning only the names a past
    sweep happened to find is how this class survived the sweep that found
    `dismissed-`.
    """
    guard = load(os.path.join(HOOKS, "clawgate-writeback-guard.py"), "wbguard_names")
    sd = guard._state_dir({"session_id": SESSION})

    guard.record_read(sd, TASK_A, now=1786795200.0)
    guard.record_work(sd, now=1786797000.0)
    guard.bump_fires(sd, TASK_A)                       # the MEASURED counter
    guard.bump_fires(sd, TASK_A, "unknown")            # the COULD-NOT-MEASURE one
    guard.write_dismissal_tombstone(sd, TASK_B, now=1786797000.0)
    guard.record_dismissal(TASK_B, SESSION, [], now=1786797000.0)

    root = ".cache/claude-clawgate-writeback"
    assert paths_under(home) == sorted([
        "%s/dismissals" % root,
        "%s/s/%s/dismissed-%d" % (root, SESSION, TASK_B),
        "%s/s/%s/fires-%d" % (root, SESSION, TASK_A),
        "%s/s/%s/read-%d" % (root, SESSION, TASK_A),
        "%s/s/%s/unknown-%d" % (root, SESSION, TASK_A),
        "%s/s/%s/work" % (root, SESSION),
    ])


def test_the_guards_ids_are_in_the_NAME_not_only_in_the_body(home):
    """The positive control for the assertion above: it must be able to MOVE.

    A single-id fixture could be satisfied by a writer that hardcodes that id, so
    the two task ids are driven through the same writers and the two resulting
    trees are compared. 307 and 911 share no digit, so a mutant that truncates,
    increments or transposes cannot produce one from the other.
    """
    guard = load(os.path.join(HOOKS, "clawgate-writeback-guard.py"), "wbguard_names_2")
    sd = guard._state_dir({"session_id": SESSION})
    guard.record_read(sd, TASK_A, now=1786795200.0)
    guard.record_read(sd, TASK_B, now=1786795200.0)

    root = ".cache/claude-clawgate-writeback/s/%s" % SESSION
    assert paths_under(home) == sorted([
        "%s/read-%d" % (root, TASK_A),
        "%s/read-%d" % (root, TASK_B),
    ])


def test_the_guards_state_root_is_this_exact_path_under_HOME(home):
    """🔴 The root as a whole string, resolved through the REAL `_state_root`.

    `_state_root` reads $HOME at CALL time on purpose (a test can point it
    somewhere safe), so this also pins that: if it ever moved to import time,
    the path here would carry the developer's real home and not `home`.
    """
    guard = load(os.path.join(HOOKS, "clawgate-writeback-guard.py"), "wbguard_names_3")
    assert guard._state_root() == os.path.join(
        str(home), ".cache", "claude-clawgate-writeback", "s")
    # The audit log is the root's SIBLING, not its child — that relationship is
    # what keeps a dismissal record from ageing out with the session that made it,
    # and it is asserted elsewhere. What is asserted HERE is its literal name.
    assert guard._dismissals_path() == os.path.join(
        str(home), ".cache", "claude-clawgate-writeback", "dismissals")


# --------------------------------------------------------------------------- #
# 2. The `read-` temp file — a name that is load-bearing because it COLLIDES
# --------------------------------------------------------------------------- #
def test_a_LEFTOVER_read_temp_file_is_invisible_to_the_ledger_and_the_census(home):
    """🔴 REGRESSION. `record_read` wrote its temp file as `read-<id>.tmp`, which
    starts with `read-` — the exact prefix BOTH readers of that directory select
    on. So a temp file that outlived its `os.replace` was not inert:

      * `tracked_ids` parsed it and reported the task as READ, so the Stop gate
        would demand a write-back for a card whose read was never committed;
      * the `MAX_TASKS` census counted it, so garbage could consume a session's
        tracking slots — and a HALF-written temp file, which `tracked_ids` skips
        as unparseable, was still counted there. Measured on the pre-fix code:
        one complete leftover → `tracked_ids` returns `{307: …}` and census 1;
        add a truncated one → census 2.

    The window is small (between `open` and `os.replace`) but it is not closed:
    a hook is killed on the CLI's timeout, and a `home-manager switch` swaps the
    file mid-run. `lib/agent_ledger.py` already solves the same problem the right
    way — its temp files are `.ledger.*.tmp`, deliberately outside its own
    `*.json` read glob — so this is that design, applied here.

    Pinned on the OBSERVABLE (what the two readers see), not on the temp name, so
    any future temp-naming scheme is free as long as it stays out of the way.

    🔴 THE LEFTOVER IS PRODUCED BY INTERRUPTING A REAL WRITE, not by writing a
    file this test named itself. Naming it would make the test's own idea of the
    temp name the thing under test — and against the pre-fix code it would fail
    with `AttributeError`, i.e. red for the wrong reason. Killing `os.replace` is
    the exact failing path: the bytes land, the rename never happens.
    """
    guard = load(os.path.join(HOOKS, "clawgate-writeback-guard.py"), "wbguard_tmp")
    sd = guard._state_dir({"session_id": SESSION})
    os.makedirs(sd, exist_ok=True)

    real_replace = guard.os.replace
    real_dump = guard.json.dump

    # Leftover 1 — COMPLETE: `json.dump` finished, the rename never happened.
    guard.os.replace = lambda *a: (_ for _ in ()).throw(OSError("no rename"))
    with pytest.raises(OSError):
        guard.record_read(sd, TASK_A, now=1786795200.0)
    # Leftover 2 — TRUNCATED: the write itself died part-way, which is what a
    # killed process actually leaves. `tracked_ids` skips it as unparseable; the
    # `MAX_TASKS` census does not, so it needs its own case.
    guard.json.dump = lambda obj, fh: fh.write('{"task_id": 9')
    try:
        with pytest.raises(OSError):
            guard.record_read(sd, TASK_B, now=1786795200.0)
    finally:
        guard.os.replace = real_replace
        guard.json.dump = real_dump

    # The interrupted writes really did leave two files — without this the two
    # empties below would be satisfied by an empty directory.
    assert len(os.listdir(sd)) == 2, os.listdir(sd)
    assert guard.tracked_ids(sd) == {}
    assert [n for n in os.listdir(sd) if n.startswith("read-")] == []

    # POSITIVE CONTROL: the same two readers DO see a committed read, so the two
    # empties above are a measurement and not a reader wired to nothing.
    guard.record_read(sd, TASK_A, now=1786795200.0)
    assert list(guard.tracked_ids(sd)) == [TASK_A]
    assert [n for n in os.listdir(sd) if n.startswith("read-")] == ["read-%d" % TASK_A]


def test_the_read_write_is_still_ATOMIC_via_a_temp_file(home):
    """The other half of the fix: moving the temp file out of the `read-` namespace
    must not turn the write into a truncating one. A reader that catches the file
    mid-write would see half a record where it used to see the previous one.

    Asserted on the call sequence, because "atomic" is a claim about ORDER: the
    bytes go to a path that is not the final one, and `os.replace` is what makes
    them visible.

    🟡 LABELLED HONESTLY: this is a DELTA guard on the fix above, not a regression
    test. It is red at `origin/main` only because `_read_tmp_name` does not exist
    there — an `AttributeError`, not an assertion about behaviour that was wrong.
    What it earns its place for is the way this fix could have gone wrong: swapping
    the temp write for a direct `open(path, "w")` would also have emptied the
    `read-` namespace of temp files, and every other assertion in this file would
    still have passed while a reader could catch a half-written record.
    """
    guard = load(os.path.join(HOOKS, "clawgate-writeback-guard.py"), "wbguard_atomic")
    sd = guard._state_dir({"session_id": SESSION})
    os.makedirs(sd, exist_ok=True)
    final = guard._read_path(sd, TASK_A)
    tmp = os.path.join(sd, guard._read_tmp_name(TASK_A))
    assert tmp != final

    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append((src, dst))
        return real_replace(src, dst)

    guard_os_replace = guard.os.replace
    guard.os.replace = spy
    try:
        guard.record_read(sd, TASK_A, now=1786795200.0)
    finally:
        guard.os.replace = guard_os_replace

    assert seen == [(tmp, final)]
    assert os.path.exists(final) and not os.path.exists(tmp)


# --------------------------------------------------------------------------- #
# 3. next-step-nudge.py — four unpinned names
# --------------------------------------------------------------------------- #
def test_the_next_step_nudge_writes_EXACTLY_this_path(home):
    """`.cache`, `claude-next-step-nudge`, `s` and the `fired` token all survived
    a rename. The token is the whole once-per-session promise: `already_fired`
    asks whether a file of that name is in the session directory, so renaming
    either the directory or the token makes every session look un-nudged."""
    nudge = load(os.path.join(HOOKS, "next-step-nudge.py"), "nudge_names")
    sd = nudge._state_dir({"session_id": SESSION})
    assert nudge.claim(sd) is True

    assert paths_under(home) == [
        ".cache/claude-next-step-nudge/s/%s/fired" % SESSION]
    # The reader agrees with the writer about that exact name — the property a
    # rename preserves and this test does not rely on.
    assert nudge.already_fired(sd) is True
    assert nudge._state_root() == os.path.join(
        str(home), ".cache", "claude-next-step-nudge", "s")


# --------------------------------------------------------------------------- #
# 4. shell-env-nudge.py — two unpinned names
# --------------------------------------------------------------------------- #
def test_the_shell_env_nudge_writes_EXACTLY_this_path(home):
    """`.cache` and `claude-shell-env-nudge` both survived. This hook has no
    per-session subdirectory: the session file sits directly in the cache dir and
    accumulates one variable name per line, so the path is two components deep."""
    env = load(os.path.join(HOOKS, "shell-env-nudge.py"), "shellenv_names")
    assert env._already_nudged(SESSION, "DEVRC") is False
    assert env._already_nudged(SESSION, "DEVRC") is True     # the dedupe it exists for

    assert paths_under(home) == [".cache/claude-shell-env-nudge/%s" % SESSION]
    assert env.CACHE_DIR == os.path.join(str(home), ".cache", "claude-shell-env-nudge")


# --------------------------------------------------------------------------- #
# 5. search-tool-nudge.py — the session/agent separator
# --------------------------------------------------------------------------- #
def test_the_search_tool_nudge_joins_session_and_agent_with_an_AT_SIGN(home):
    """The three path roots here are already pinned by that hook's own suite; the
    `@` that joins session to agent was not.

    Its injectivity property IS asserted (`_sanitize` maps `@` to `_`, so it
    cannot appear inside a component, so one key means one pair) — but that
    property is shared by every character `_sanitize` rewrites, so an injectivity
    test cannot see a swap to `+` or `#`. Only the literal can, and the literal is
    what an in-flight session's directory is actually called.
    """
    stn = load(os.path.join(HOOKS, "search-tool-nudge.py"), "searchtool_names")
    # Both keys in ONE tree: with an agent and without. The second is what makes
    # the `@` a JOIN rather than a decoration on the session id — the agentless
    # key must not carry one.
    assert stn._claim(stn._state_dir(
        {"session_id": SESSION, "agent_id": AGENT}), "content") is True
    assert stn._claim(stn._state_dir({"session_id": SESSION}), "files") is True

    root = ".cache/claude-search-tool-nudge/s"
    assert paths_under(home) == sorted([
        "%s/%s@%s/content" % (root, SESSION, AGENT),
        "%s/%s/files" % (root, SESSION),
    ])


# --------------------------------------------------------------------------- #
# 6. lib/agent_ledger.py — the temp prefix, pinned as the RELATIONSHIP it holds
# --------------------------------------------------------------------------- #
def test_the_ledgers_temp_file_cannot_be_picked_up_by_its_OWN_read_glob(home):
    """🔴 THE SEAM, not the literal. `.ledger.` survived a rename, but the name
    itself is free — what is load-bearing is that a temp file does not match the
    `*.json` glob `read_command` ships. Break that and a reader `awk`s a
    half-written record, which lands as a rising `unparseable` count rather than
    as an error.

    Driven through the REAL `read_argv`, against a real directory holding a temp
    file whose name came from the REAL writer — captured by spying on the
    `mkstemp` call the shipped `write_record` makes, never by this test restating
    `prefix=`/`suffix=` itself. That distinction is not cosmetic: the first version
    of this test DID restate them, and a mutant that changed the writer's suffix to
    `.json` — the whole hazard — SURVIVED it, because the test went on comparing
    its own copy against itself. `agent_ledger`'s own docstring names that trap
    ("a copy of the command in a test validates the copy"); this is it, one level
    over.

    🔴 WHAT THIS DOES AND DOES NOT KILL, measured. The temp name is protected
    TWICE over — a leading `.` (which a shell `*` does not match) and a `.tmp`
    suffix — so changing only one of them is a genuinely equivalent mutant and
    this test stays green for it, correctly:

        prefix=".ledger." suffix=".tmp"   (shipped)          13 passed
        prefix=".ledger." suffix=".json"                     13 passed
        prefix="ledger."  suffix=".tmp"                      13 passed
        prefix="ledger."  suffix=".json"  (both removed)      1 FAILED

    That is the point of pinning the RELATIONSHIP instead of the literal: any
    naming that keeps the temp file out of the read glob is allowed, and the one
    that does not is red. A literal pin would have reported the two middle rows as
    defects and taught the next person to change the constant in the test.
    """
    AL = load(os.path.join(LIB, "agent_ledger.py"), "agent_ledger_names")
    d = os.path.join(str(home), "ledgerdir")
    os.makedirs(d)

    made = []
    real_mkstemp = AL.tempfile.mkstemp

    def spy(**kw):
        fd, path = real_mkstemp(**kw)
        made.append(path)
        return fd, path

    rec = AL.build_record(runtime="claude", session_id="sess-cccc",
                          last_activity_ts="2026-08-17T09:00:00Z",
                          window_id="@41", pane_id="%77", tmux_pid="4025325")
    AL.tempfile.mkstemp = spy
    try:
        assert AL.write_record(rec, directory=d)["written"] is True
    finally:
        AL.tempfile.mkstemp = real_mkstemp
    assert len(made) == 1, made          # the writer really did go through mkstemp

    # Re-create the temp file at the name the SHIPPED writer chose, holding the
    # half-written record a killed process leaves behind.
    with open(made[0], "w") as fh:
        fh.write('{"schema": 1, "runtime": "claude", "session_id": "HALF')

    out = subprocess.run(AL.read_argv(abs_dir=d), capture_output=True, text=True,
                         timeout=60).stdout
    parsed = AL.parse_ledger(out)

    assert parsed["measured"] is True                       # the sentinel arrived
    assert parsed["unparseable"] == 0, out                  # the temp file was NOT read
    assert [r["session_id"] for r in parsed["records"]] == ["sess-cccc"]

    # POSITIVE CONTROL: the same command DOES read a `.json` in that directory, so
    # the zero above is a measurement and not a glob pointed at nothing.
    with open(os.path.join(d, "claude-99.json"), "w") as fh:
        fh.write("not json at all\n")
    again = AL.parse_ledger(subprocess.run(AL.read_argv(abs_dir=d),
                                           capture_output=True, text=True,
                                           timeout=60).stdout)
    assert again["unparseable"] == 1


def test_the_ledgers_wire_SENTINEL_is_this_exact_token():
    """`AGENT_LEDGER_V1` survived a rename because reader and writer both take it
    from this one constant — the same shared-constant shape as every name above,
    with the difference that this one travels over SSH rather than sitting on
    disk.

    It is the ledger read's positive control: without the sentinel line,
    "this host has no agent records" and "something swallowed the output" arrive
    as the same empty string, which is the fabricated zero the #419 regression
    was made of. Pinned as a literal, and pinned INSIDE the shipped command, so a
    rename that reaches only one of the two is visible.
    """
    AL = load(os.path.join(LIB, "agent_ledger.py"), "agent_ledger_sentinel")
    assert AL.SENTINEL == "AGENT_LEDGER_V1"
    assert AL.read_command().startswith('echo "AGENT_LEDGER_V1 ')
    assert AL.parse_ledger("AGENT_LEDGER_V1 4025325\n")["measured"] is True
    # ...and a DIFFERENT token is not accepted, so the assertion above is about
    # this token and not about any first line at all.
    assert AL.parse_ledger("AGENT_LEDGER_V2 4025325\n")["measured"] is False


def test_the_ledger_directory_is_this_exact_path_under_HOME(home):
    """Already killable (13 tests move on a rename) and carried here so the
    registry below can be read as the complete list rather than as a subset."""
    AL = load(os.path.join(LIB, "agent_ledger.py"), "agent_ledger_dir")
    assert AL.LEDGER_SUBPATH == os.path.join(".cache", "agent-ledger")
    assert AL.LEDGER_DIR == os.path.join(str(home), ".cache", "agent-ledger")


# --------------------------------------------------------------------------- #
# 7. The growth guard — a NEW state-owning module must land with a pin
# --------------------------------------------------------------------------- #
#
# 🔴 AN ENUMERATION, NOT A PATTERN. Every hook module is in exactly one of these
# two lists. A new file in `scripts/claude-hooks/` fails the test below until
# someone puts it in one of them — and putting it in PINNED_HERE without adding a
# path assertion above is caught by the corroboration in the same test.
PINNED_HERE = {
    "clawgate-writeback-guard.py",
    "next-step-nudge.py",
    "shell-env-nudge.py",
    "search-tool-nudge.py",
    "claude-notify.py",          # pinned by test_claude_notify.py + test_notifs.py
}
OWNS_NO_ON_DISK_STATE = {
    "agent-ledger-hook.py",      # delegates every path to lib/agent_ledger.py
    "audit-pr-nudge.py",         # stateless: decides from the payload alone
    "bash-guard.py",             # thin wrapper over guard_core.py
    "guard_core.py",             # pure predicate library
    "register-nudge-hook.py",    # writes ~/.claude/settings.json; the deployed-path
                                 # seam is pinned by test_registrar_activation.py
}

CACHE_LITERAL = re.compile(r'["\'/]\.cache\b|XDG_CACHE_HOME|\.local/share')


def hook_modules():
    return {n for n in os.listdir(HOOKS)
            if n.endswith(".py") and not n.startswith("_")}


def test_every_hook_module_is_classified_as_owning_state_or_not():
    """🔴 THE SET, FROM BOTH SIDES. It fails when a module appears that is in
    neither list (a new hook grew a cache directory and nobody pinned its names)
    AND when a listed module disappears (a stale entry makes the registry read as
    covering something that no longer exists)."""
    listed = PINNED_HERE | OWNS_NO_ON_DISK_STATE
    assert PINNED_HERE.isdisjoint(OWNS_NO_ON_DISK_STATE)
    assert hook_modules() == listed, (
        "unclassified: %s ; stale: %s"
        % (sorted(hook_modules() - listed), sorted(listed - hook_modules())))


def test_the_no_state_claim_is_corroborated_against_the_sources():
    """The `OWNS_NO_ON_DISK_STATE` half is a CLAIM, so it gets a check rather than
    a promise: none of those files may name a cache root.

    🔴 ITS BLIND SPOT, STATED. This is a text scan. A module that derived a cache
    path some other way — an env var this pattern does not name, a helper in
    another module — would pass it while owning a name nobody pinned. It is a
    corroboration of the enumeration above, never a replacement for reading the
    file when adding one.
    """
    for name in sorted(OWNS_NO_ON_DISK_STATE):
        src = open(os.path.join(HOOKS, name)).read()
        hits = [ln for ln in src.splitlines()
                if CACHE_LITERAL.search(ln) and not ln.lstrip().startswith("#")]
        assert hits == [], "%s names a cache root: %r" % (name, hits[:3])

    # POSITIVE CONTROL: the scanner CAN find one. Without this, a pattern that
    # matched nothing anywhere would produce the same all-clear.
    for name in sorted(PINNED_HERE):
        src = open(os.path.join(HOOKS, name)).read()
        assert CACHE_LITERAL.search(src), name


if __name__ == "__main__":                                  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
