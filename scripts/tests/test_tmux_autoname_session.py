"""Behavioural tests for scripts/tmux-autoname-session.sh — the `session-created`
hook that names an auto-numbered tmux session after the directory it opened in.

🔴 NOTHING HERE MAY REACH THE REAL TMUX SERVER
----------------------------------------------
This suite runs on a live workbench holding ~30 real tmux windows of the
operator's in-flight work, and the script under test calls `rename-session`.
A rename changes a session's ADDRESS mid-flight, so a leak is not a cosmetic
failure — every `session:window` anyone wrote down stops resolving.

`TMUX_TMPDIR` is NOT sufficient isolation: `$TMUX` overrides it, so a child that
inherits the operator's `$TMUX` reaches the live server regardless. So the
mechanism here is a STUB `tmux` first on PATH, and the child's `$TMUX` is
removed as well. `test_the_stub_is_what_the_script_actually_reaches` is the
POSITIVE CONTROL on that: a guard nobody watched work is not a guard, and a
suite of green "0 renames" assertions is indistinguishable from a harness wired
to nothing unless something proves a rename CAN be observed.

WHAT THE LOAD-BEARING TESTS ARE
-------------------------------
Not the happy path. The gate — `^[0-9]+$` — is the only thing standing between
this hook and `scratch7`'s name, which is a hotkey target, a colour and a
codename rather than just a string. So it gets a MUTATION test that widens the
pattern and watches the owning assertion go red, proving the gate is REACHED
rather than shadowed by an earlier check.

The fixtures are synthetic and pairwise distinct. The one place the REAL
`tmux-scratch-slots.sh` is used is the seam test, which reads a codename out of
that file at runtime rather than restating one — the point there is that the
script and the canonical table agree, which a copied constant cannot show.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from testlib.mockbin import write_exec  # noqa: E402

SCRIPT = REPO / "scripts" / "tmux-autoname-session.sh"
REAL_SLOTS = REPO / "scripts" / "tmux-scratch-slots.sh"
TMUX_CONF = REPO / ".tmux.conf"
HOME_NIX = REPO / "nix" / "home.nix"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None,
                                reason="needs bash on PATH")

# Synthetic slot table: pairwise-distinct session names AND codenames, none of
# which collide with anything else in this file. Deliberately NOT the real
# table — a fixture that borrows production values cannot show which of the two
# a behaviour came from.
SYNTH_SLOTS = "\n".join([
    "SCRATCH_SLOTS=(",
    '    "scratch:q:#111111:quartz"',
    '    "scratch2:Z:#222222:Zephyr"',
    '    "scratch3:k:#333333:kelp"',
    ")",
    "",
])

# The gate, verbatim. Quoted here so the mutation test can rewrite THAT LINE and
# nothing else — see its docstring.
GATE_LINE = '[[ "$SESSION" =~ ^[0-9]+$ ]] || exit 0'

TMUX_STUB = """
printf '%s\\n' "$*" >> "$SM_LOG"
case "${1:-}" in
  display-message)
    if [ -n "${SM_DISPLAY_FAILS:-}" ]; then exit 1; fi
    printf '%s\\n' "${SM_CWD:-}"
    ;;
  list-sessions)
    if [ -n "${SM_LIST_FAILS:-}" ]; then exit 1; fi
    printf '%s' "${SM_SESSIONS:-}"
    ;;
  rename-session)
    :
    ;;
  *)
    exit 1
    ;;
esac
exit 0
"""

GIT_STUB = """
printf 'git %s\\n' "$*" >> "$SM_LOG"
if [ -z "${SM_GIT_ROOT:-}" ]; then exit 128; fi
printf '%s\\n' "$SM_GIT_ROOT"
exit 0
"""


class Run:
    """One invocation's result: the process, plus the parsed stub log."""

    def __init__(self, proc, log_text):
        self.proc = proc
        self.log = [ln for ln in log_text.splitlines() if ln.strip()]

    @property
    def renames(self):
        """`[(target, new_name), ...]` — every rename-session the script asked
        for. The COUNT is the measurement; a bare `not r.renames` would be
        satisfied by a harness that never recorded anything."""
        out = []
        for line in self.log:
            m = re.match(r"^rename-session -t (\S+) (.*)$", line)
            if m:
                out.append((m.group(1), m.group(2)))
        return out


def make_tree(tmp_path, slots=SYNTH_SLOTS, script_text=None):
    """A copy of the script with a slot table beside it, mirroring the deployed
    layout (~/.config/tmux/{autoname-session.sh,scratch-slots.sh})."""
    d = tmp_path / "tmuxdir"
    d.mkdir(exist_ok=True)
    target = d / "autoname-session.sh"
    target.write_text(SCRIPT.read_text() if script_text is None else script_text)
    target.chmod(0o755)
    (d / "scratch-slots.sh").write_text(slots)
    return target


def invoke(tmp_path, session, *, script=None, cwd="/w/synth-alpha",
           sessions=(), git_root=None, home="/home/synthuser",
           display_fails=False, list_fails=False, slots=SYNTH_SLOTS,
           script_text=None):
    """Run the script with a stub `tmux` + `git` first on PATH."""
    script = make_tree(tmp_path, slots=slots,
                       script_text=script_text) if script is None else script
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "calls.log"
    log.write_text("")
    write_exec(bindir / "tmux", TMUX_STUB)
    write_exec(bindir / "git", GIT_STUB)

    env = dict(os.environ)
    env.pop("TMUX", None)          # $TMUX overrides TMUX_TMPDIR; remove it
    env["TMUX_TMPDIR"] = str(tmp_path / "tmuxsock")
    env["PATH"] = f"{bindir}{os.pathsep}{os.environ['PATH']}"
    env["SM_LOG"] = str(log)
    env["SM_CWD"] = cwd
    env["SM_SESSIONS"] = "".join(f"{s}\n" for s in sessions)
    env["HOME"] = home
    if git_root:
        env["SM_GIT_ROOT"] = git_root
    else:
        env.pop("SM_GIT_ROOT", None)
    if display_fails:
        env["SM_DISPLAY_FAILS"] = "1"
    if list_fails:
        env["SM_LIST_FAILS"] = "1"

    proc = subprocess.run([str(script), session], env=env,
                          capture_output=True, text=True, timeout=30)
    return Run(proc, log.read_text())


# --------------------------------------------------------------------------- #
# THE HARNESS, before any verdict read off it
# --------------------------------------------------------------------------- #
def test_the_stub_is_what_the_script_actually_reaches(tmp_path):
    """POSITIVE CONTROL, both halves.

    (a) `tmux` resolves to the stub on the child's PATH, so nothing in this file
        can touch the operator's live server; and
    (b) a rename IS observable through the log — otherwise every `renames == []`
        below is a fact about the recorder, not about the script.
    """
    r = invoke(tmp_path, "3", cwd="/w/synth-alpha")
    bindir = tmp_path / "bin"
    resolved = shutil.which("tmux", path=f"{bindir}{os.pathsep}"
                                         f"{os.environ['PATH']}")
    assert resolved == str(bindir / "tmux"), (
        "the stub is not first on PATH — this suite would be driving the real "
        "tmux server")
    assert len(r.renames) == 1, "the recorder never observed a rename at all"


# --------------------------------------------------------------------------- #
# TIER 1 — the auto-number gate
# --------------------------------------------------------------------------- #
def test_an_auto_numbered_session_is_renamed_after_its_directory(tmp_path):
    r = invoke(tmp_path, "8", cwd="/w/synth-alpha")
    assert r.renames == [("=8", "synth-alpha")]
    assert r.proc.returncode == 0


def test_a_scratch_session_is_NEVER_touched(tmp_path):
    """🔴 The one that matters. `scratch7` ends in a digit and sits in a
    perfectly good repo directory — everything except the gate would let this
    through, which is why the mutation test below points at exactly this case.
    """
    r = invoke(tmp_path, "scratch7", cwd="/w/synth-alpha")
    assert r.renames == [], (
        "the hook renamed a SCRATCH session — that name is a hotkey target, a "
        "colour and a codename, not just a string")
    assert r.proc.returncode == 0


def test_a_deliberately_named_session_is_NEVER_touched(tmp_path):
    r = invoke(tmp_path, "reviews", cwd="/w/synth-alpha")
    assert r.renames == []


@pytest.mark.parametrize("session", ["", "12a", "a12", "8:1", "-8", "8.2", " 8"])
def test_only_a_purely_numeric_name_is_eligible(tmp_path, session):
    r = invoke(tmp_path, session, cwd="/w/synth-alpha")
    assert r.renames == []


def test_MUTATION_widening_the_gate_is_what_eats_a_scratch_name(tmp_path):
    """🔴 MUTATION + REACHABILITY, on the guard that owns the hazard.

    Widening `^[0-9]+$` to also admit letters is the single edit that turns this
    hook into something that renames `scratch7`. Two things are proven here, and
    the second is the one that is usually skipped:

      * the mutant DIES on the assertion that owns it — the mutant renames
        `scratch7`, so `test_a_scratch_session_is_NEVER_touched` goes red for
        THIS reason and not because some other guard's error killed the run; and
      * the gate is REACHED, not shadowed. The mutant's rename proves every
        later check (cwd, sanitiser, slot table, live sessions) ACCEPTS
        `scratch7` in exactly this fixture — so the unmutated `[]` is the gate's
        doing, not an accident of some earlier rejection.
    """
    # 🔴 The mutation targets the GATE LINE, not the pattern text. The header
    # comment quotes `^[0-9]+$` too, so a bare `.replace` on the pattern
    # rewrites the comment as well — and would then read as "mutation applied"
    # even against a tree where the gate had already been deleted. Measured:
    # that is exactly what happened on the first run of this sweep.
    src = SCRIPT.read_text()
    assert src.count(GATE_LINE) == 1, (
        f"expected exactly one gate line, found {src.count(GATE_LINE)} — the "
        "guard was renamed, duplicated or deleted, and this test can no longer "
        "say which")
    mutant = src.replace(GATE_LINE, GATE_LINE.replace("[0-9]", "[A-Za-z0-9]"))
    assert mutant != src

    r = invoke(tmp_path, "scratch7", cwd="/w/synth-alpha", script_text=mutant)
    assert r.renames == [("=scratch7", "synth-alpha")], (
        "the mutant did NOT rename scratch7 — the gate is shadowed by an "
        "earlier check and this suite is not testing what it claims to")


# --------------------------------------------------------------------------- #
# TIER 2 — where the name comes from
# --------------------------------------------------------------------------- #
def test_the_git_repo_root_beats_the_raw_directory(tmp_path):
    r = invoke(tmp_path, "8", cwd="/w/synth-bravo/nix/system",
               git_root="/w/synth-bravo")
    assert r.renames == [("=8", "synth-bravo")]


def test_a_cwd_outside_a_work_tree_falls_back_to_the_leaf(tmp_path):
    r = invoke(tmp_path, "8", cwd="/w/synth-charlie/deep/leafdir", git_root=None)
    assert r.renames == [("=8", "leafdir")]


@pytest.mark.parametrize("raw,expect", [
    ("/w/my repo.v2", "my-repo-v2"),
    ("/w/--edges--", "edges"),
    ("/w/Keep_Me-1", "Keep_Me-1"),
])
def test_the_name_is_reduced_to_a_tmux_safe_alphabet(tmp_path, raw, expect):
    """tmux forbids `.` and `:` in a session name, and the name is interpolated
    back into shell command lines by the other hooks."""
    r = invoke(tmp_path, "8", cwd=raw)
    assert r.renames == [("=8", expect)]
    assert "." not in expect and ":" not in expect


@pytest.mark.parametrize("kw", [
    {"cwd": "/home/synthuser"},          # $HOME — `synthuser` is not information
    {"cwd": "/"},
    {"cwd": ""},
    {"cwd": "/w/..."},                   # sanitises to nothing
    {"display_fails": True},             # tmux could not answer
])
def test_a_cwd_that_says_nothing_leaves_the_session_ALONE(tmp_path, kw):
    r = invoke(tmp_path, "8", **kw)
    assert r.renames == []
    assert r.proc.returncode == 0


def test_the_git_root_is_checked_against_HOME_too(tmp_path):
    """The HOME guard must survive the git-root path, not only the raw cwd —
    otherwise `git -C ~ rev-parse` in a dotfiles work tree renames a session to
    the operator's username."""
    r = invoke(tmp_path, "8", cwd="/home/synthuser/notes",
               git_root="/home/synthuser")
    assert r.renames == []


# --------------------------------------------------------------------------- #
# TIER 3 — collisions
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", ["scratch2", "Zephyr"])
def test_a_name_the_SLOT_TABLE_owns_is_deduped(tmp_path, name):
    """BOTH halves of a slot entry are reserved: the session name (renaming onto
    it would point a hotkey at the wrong window) and the codename (that is the
    word the HUD and the ledger use)."""
    r = invoke(tmp_path, "8", cwd=f"/w/{name}")
    assert r.renames == [("=8", f"{name}-2")]


def test_a_name_a_LIVE_session_holds_is_deduped(tmp_path):
    r = invoke(tmp_path, "8", cwd="/w/synth-delta",
               sessions=("synth-delta", "8"))
    assert r.renames == [("=8", "synth-delta-2")]


def test_dedupe_walks_past_consecutive_collisions(tmp_path):
    r = invoke(tmp_path, "8", cwd="/w/synth-echo",
               sessions=("synth-echo", "synth-echo-2", "synth-echo-3", "8"))
    assert r.renames == [("=8", "synth-echo-4")]


def test_ten_collisions_leaves_the_session_alone(tmp_path):
    taken = ["synth-fox"] + [f"synth-fox-{n}" for n in range(2, 11)]
    r = invoke(tmp_path, "8", cwd="/w/synth-fox", sessions=(*taken, "8"))
    assert r.renames == []


def test_the_session_being_renamed_does_not_collide_with_itself(tmp_path):
    """`8` is in `list-sessions` output while the hook runs. A dedupe that
    counted it would be a no-op on every real invocation."""
    r = invoke(tmp_path, "8", cwd="/w/synth-golf", sessions=("8", "scratch"))
    assert r.renames == [("=8", "synth-golf")]


def test_a_MISSING_slot_table_degrades_instead_of_crashing(tmp_path):
    """🔴 `set -u` + an UNSET array is a crash, not a no-op — and this is
    reachable: a host that has not switched yet, or any future edit that moves
    the deployed `scratch-slots.sh`. The mutant that empties the table
    (`SCRATCH_SLOTS=()`) does NOT cover it; that array is defined.

    Correct degradation is: rename anyway, having lost only the reservation.
    Refusing to name anything because a display nicety is missing would be the
    worse failure, and crashing would put a bash error in the terminal.
    """
    d = tmp_path / "noslots"
    d.mkdir()
    script = d / "autoname-session.sh"
    script.write_text(SCRIPT.read_text())
    script.chmod(0o755)
    assert not (d / "scratch-slots.sh").exists()
    assert not (d / "tmux-scratch-slots.sh").exists()

    r = invoke(tmp_path, "8", cwd="/w/synth-juliet", script=script)
    assert r.proc.returncode == 0
    assert r.proc.stderr == "", f"a bash error reached the terminal: {r.proc.stderr!r}"
    assert r.renames == [("=8", "synth-juliet")]


def test_a_failed_list_sessions_does_not_abort_the_rename(tmp_path):
    """Degrade, do not crash: tmux would refuse a duplicate rename anyway, so
    losing the live-session list costs a suffix, not the feature."""
    r = invoke(tmp_path, "8", cwd="/w/synth-hotel", list_fails=True)
    assert r.renames == [("=8", "synth-hotel")]


# --------------------------------------------------------------------------- #
# THE SEAM — the script and the CANONICAL slot table, not a copy of it
# --------------------------------------------------------------------------- #
def test_reserved_names_come_from_the_real_slot_table(tmp_path):
    """Read a codename OUT of `scripts/tmux-scratch-slots.sh` at runtime and
    check the script refuses it. Restating a codename here would pass against a
    private copy of the table — which is exactly the drift the table's own
    header forbids."""
    entries = re.findall(r'"([^":]+):([^":]+):(#[0-9a-fA-F]{6}):([^":]+)"',
                         REAL_SLOTS.read_text())
    assert entries, "the canonical slot table did not parse"
    codename = entries[0][3]

    tree = tmp_path / "repolike"
    tree.mkdir()
    script = tree / "autoname-session.sh"
    script.write_text(SCRIPT.read_text())
    script.chmod(0o755)
    # the in-repo NAME, which is the second of the two the script looks for
    (tree / "tmux-scratch-slots.sh").write_text(REAL_SLOTS.read_text())

    r = invoke(tmp_path, "8", cwd=f"/w/{codename}", script=script)
    assert r.renames == [("=8", f"{codename}-2")]


# --------------------------------------------------------------------------- #
# IDEMPOTENCE + THE HOOK CONTRACT (silence, exit 0)
# --------------------------------------------------------------------------- #
def test_running_it_again_on_the_renamed_session_is_a_no_op(tmp_path):
    first = invoke(tmp_path, "8", cwd="/w/synth-india")
    assert first.renames == [("=8", "synth-india")]
    second = invoke(tmp_path, "synth-india", cwd="/w/synth-india")
    assert second.renames == []


@pytest.mark.parametrize("session,kw", [
    ("8", {}),
    ("scratch7", {}),
    ("8", {"cwd": "/"}),
    ("8", {"display_fails": True}),
    ("", {}),
])
def test_it_exits_zero_and_says_nothing_on_every_path(tmp_path, session, kw):
    """It runs from a `session-created` hook. A non-zero exit or a stray line of
    output lands in the operator's terminal — or a view-mode popup — at the
    moment they open a new session."""
    r = invoke(tmp_path, session, **kw)
    assert r.proc.returncode == 0
    assert r.proc.stdout == ""
    assert r.proc.stderr == ""


# --------------------------------------------------------------------------- #
# THE HOOK WIRING — appended, not clobbered; and deployed at all
# --------------------------------------------------------------------------- #
_SET_HOOK_RE = re.compile(r"^\s*set-hook\s+(-[a-zA-Z]+)\s+(\S+)", re.M)


def _hooks_in_order():
    return _SET_HOOK_RE.findall(TMUX_CONF.read_text())


def test_only_the_FIRST_binding_of_a_hook_may_be_non_append():
    """🔴 A RELATIONSHIP guard over every hook in `.tmux.conf`, not a spelling
    check on the line this branch added.

    `set-hook -g <name>` REPLACES the hook; `-ga` appends. So a plain `-g` that
    appears after any other binding of the same hook silently deletes it. That
    is the exact way this branch could have killed activity telemetry: the
    `session-created` hook already carried `pipe-activity.sh init`, and a `-g`
    autoname line would have removed it with no error anywhere.
    """
    seen: dict[str, int] = {}
    for flags, hook in _hooks_in_order():
        seen[hook] = seen.get(hook, 0) + 1
        if seen[hook] > 1:
            assert "a" in flags.lstrip("-"), (
                f"`set-hook {flags} {hook}` is binding #{seen[hook]} for that "
                f"hook and does NOT append — it deletes every binding above it")


def test_the_pre_existing_session_created_binding_is_still_there():
    """The specific one this branch had to not break, pinned by its target."""
    hooks = [(f, h) for f, h in _hooks_in_order() if h == "session-created"]
    assert len(hooks) == 2, f"expected exactly two session-created bindings: {hooks}"
    assert hooks[0][0] == "-g" and hooks[1][0] == "-ga"
    text = TMUX_CONF.read_text()
    assert 'set-hook -g session-created' in text
    assert 'pipe-activity.sh init' in text


def test_the_hook_escapes_the_session_name_for_sh():
    """`run-shell` feeds its command to /bin/sh, so an unescaped
    `#{hook_session_name}` is shell syntax. `#{q:...}` is tmux's sh(1) escape."""
    line = [ln for ln in TMUX_CONF.read_text().splitlines()
            if "autoname-session.sh" in ln and ln.lstrip().startswith("set-hook")]
    assert len(line) == 1, line
    assert "#{q:hook_session_name}" in line[0]
    assert "-ga session-created" in line[0]


def test_the_script_is_EXECUTABLE():
    """🔴 MEASURED, not defensive. The first live run of this hook against an
    isolated tmux server did nothing at all: the file had landed mode 644, so
    `run-shell` got `Permission denied` — and `run-shell -b` swallows it, so the
    only symptom was a session that stayed called `0`. The `home.file` entry
    carries `executable = true`, which fixes the DEPLOYED copy and hides the
    repo mode; this pins the repo mode, which is what every other consumer
    (and this suite, which execs the file directly) actually sees.
    """
    assert os.access(SCRIPT, os.X_OK), (
        "scripts/tmux-autoname-session.sh is not executable — the hook will "
        "fail silently and the session will keep its auto-number")
    assert 'executable = true' in re.sub(
        r"\s+", " ",
        HOME_NIX.read_text().split('.config/tmux/autoname-session.sh')[1][:200])


def test_the_script_is_DEPLOYED_beside_the_slot_table_it_sources():
    """It resolves the slot table from its OWN directory, so a `home.file`
    entry that put it anywhere else would deploy a script that silently
    reserves no names at all. Both paths are asserted, not just the entry."""
    nix = HOME_NIX.read_text()
    assert '.config/tmux/autoname-session.sh' in nix
    assert '../scripts/tmux-autoname-session.sh' in nix
    assert '.config/tmux/scratch-slots.sh' in nix

    hook_path = re.search(r'run-shell -b "([^ "]*autoname-session\.sh)',
                          TMUX_CONF.read_text()).group(1)
    assert hook_path == "~/.config/tmux/autoname-session.sh"
    assert hook_path.split("/", 1)[1] in nix


def test_the_script_is_tracked_by_git():
    """🔴 CLAUDE.md: a new file that is not `git add`ed is silently omitted from
    the flake, the switch succeeds, and the file simply is not there.

    Deliberately NOT a skip in the sandbox — the two tiers check it two ways and
    both are assertions. In the nix build the flake source only carries TRACKED
    files, so the script EXISTING there is itself the proof; on the dev host,
    where an untracked file would still be sitting in the tree, `git ls-files`
    is the one that can tell the difference.
    """
    assert SCRIPT.exists(), (
        "scripts/tmux-autoname-session.sh is absent — in the nix sandbox that "
        "means it was never git added, so the flake omitted it")
    if shutil.which("git") and (REPO / ".git").exists():
        out = subprocess.run(
            ["git", "-C", str(REPO), "ls-files", "--error-unmatch",
             "scripts/tmux-autoname-session.sh"],
            capture_output=True, text=True)
        assert out.returncode == 0, (
            "scripts/tmux-autoname-session.sh is UNTRACKED — `home-manager "
            "switch` will succeed and the hook will point at nothing")
