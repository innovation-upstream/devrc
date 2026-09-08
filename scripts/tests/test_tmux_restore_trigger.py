"""What STARTS `tmux-session-restore.service` — pinned structurally.

WHY THIS FILE EXISTS
--------------------
🔴 THE UNIT DESTROYED THE WORKSPACE IT EXISTED TO RESTORE. Measured 2026-09-06:
43 `claude --resume` conversations, silently. The unit was started by a
`OnActiveSec=45s` timer; on a cold boot nothing else has started tmux by second
45, so `tmux-session-restore.py`'s own `tmux new-session -d` created the server
INSIDE the unit's cgroup. The sends were delivered SUCCESSFULLY into it. Then
ExecStart returned and `Type=oneshot` + `RemainAfterExit=no` +
`KillMode=control-group` tore the cgroup down, taking the server and every
claude process with it. The unit reported `Result=success`.

The discriminating evidence — against the rival "the panes were not ready and
the sends were discarded", which the empty scrollback cannot tell apart — is the
journal: `Started tmux child pane N launched by process <pid>` arrived in TWO
cohorts, 17 at the unit's own timestamp naming the unit's pid and 56 lines 24s
later naming a DIFFERENT pid. Two servers, not one unready one.

So the fix is a TRIGGER change: fire on a tmux server that this unit did not
create, and therefore cannot destroy. This file pins that trigger, because the
old one looked entirely reasonable and nothing in the suite objected to it.

WHAT EACH GROUP PINS
--------------------
  * `TestTheFixedDelayTimerIsGone` — the timer is not merely unused, it is not
    DECLARED. A timer left behind still fires.
  * `TestThePathUnitTriggersTheService` — a path unit exists, names the service,
    and is ENABLED. `nix_units`' own docstring records the measured shape of a
    unit that is declared and never enabled.
  * `TestTheWatchIsAnEventNotAState` — `PathExists=`/`PathExistsGlob=`/
    `DirectoryNotEmpty=` are STATES that systemd re-satisfies the moment the
    triggered unit terminates. MEASURED 2026-09-07 on this host: `PathExists=`
    ran the oneshot 5 times in 8 seconds and left BOTH units
    `Result=start-limit-hit`/`failed` — which under this unit's `OnFailure=`
    is a DND-bypassing toast on every boot, strictly worse than the bug.
  * `TestTheTriggerAndTheQueryNameOneServer` — a SEAM guard. The path unit
    watches a directory; `tmux` finds its socket via `$TMUX_TMPDIR`, whose
    compiled-in default is /tmp and NOT `%t`. If those two disagree the unit is
    triggered by one server and talks to another. This pins the RELATIONSHIP,
    not either side.
  * `TestTheUnitCannotOwnTheOperatorsServer` — an INVARIANT GUARD, labelled as
    one: the 2026-09-06 bug never violated it. `RemainAfterExit=yes` is
    measured to keep a unit-spawned tmux server alive and is the WRONG fix,
    because with `KillMode=control-group` the unit would then own the
    operator's server and `systemctl --user stop` would kill the workspace.

🔴 THIS FILE IS READ-ONLY. It opens `nix/home.nix` and asserts; it runs no
systemd command and starts nothing. The systemd behaviour quoted above was
measured out-of-band with transient units on scratch paths; the numbers are
recorded here as the REASON for each assertion, not re-measured by it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from testlib.nix_units import (  # noqa: E402
    declares,
    directive,
    section,
    unit_source,
)

HOME_NIX = REPO / "nix" / "home.nix"

SERVICE = "systemd.user.services.tmux-session-restore"
TIMER = "systemd.user.timers.tmux-session-restore"
PATH_UNIT = "systemd.user.paths.tmux-session-restore"

# 🔴 THE THREE STATE-TYPE PATH DIRECTIVES, AS A LEDGER RATHER THAN ONE NAME.
# systemd.path(5): "When a service unit triggered by a path unit terminates
# (regardless whether it exited successfully or failed), monitored paths are
# checked immediately again, and the service accordingly restarted instantly."
# Every directive here describes a CONDITION THAT PERSISTS, so a tmux socket
# that goes on existing re-satisfies it forever. `PathChanged=`/`PathModified=`
# describe an EVENT and do not. Adding a fourth state-type directive to systemd
# would need a line here; that is the point of enumerating them.
STATE_TYPE_PATH_DIRECTIVES = ("PathExists", "PathExistsGlob", "DirectoryNotEmpty")


@pytest.fixture(scope="module")
def src() -> str:
    return HOME_NIX.read_text()


@pytest.fixture(scope="module")
def service_block(src: str) -> str:
    return unit_source(SERVICE, src)


@pytest.fixture(scope="module")
def path_block(src: str) -> str:
    """🔴 THE `[Path]` SECTION, NOT THE WHOLE UNIT — `Unit` is both a section
    name and a directive name here, and only narrowing tells them apart. See
    `nix_units.section`."""
    body = section("Path", unit_source(PATH_UNIT, src))
    assert body is not None, (
        f"{PATH_UNIT} declares no `Path = {{ … }}` section, so it watches "
        "nothing at all"
    )
    return body


@pytest.fixture(scope="module")
def path_unit_block(src: str) -> str:
    """The WHOLE path unit — for the `[Install]` half."""
    return unit_source(PATH_UNIT, src)


def _unquote(value: str | None) -> str | None:
    """Strip one layer of nix string quoting from a directive value."""
    if value is None:
        return None
    return value.strip().strip('"')


# --- the service still exists at all -------------------------------------------

def test_the_service_is_still_declared(src: str):
    """The floor under every other assertion here.

    Without this, deleting the service outright would make most of this file
    vacuously green — `unit_source` raises, but a reader scanning for
    "is the restore still wired?" deserves the direct answer.
    """
    assert declares(SERVICE, src), (
        f"{SERVICE} is not a live declaration in nix/home.nix — every trigger "
        "assertion in this file is about a unit that no longer exists"
    )


# --- the fixed-delay timer is gone ---------------------------------------------

class TestTheFixedDelayTimerIsGone:
    """🔴 A DURATION WAS NEVER THE VARIABLE.

    The `OnActiveSec=45s` timer is what put the restore on the wrong side of
    "has anything started tmux yet". Retiring it is the change; a timer left
    declared still fires, so this asserts absence of the DECLARATION, not that
    something stopped referencing it.
    """

    def test_the_timer_unit_is_not_declared(self, src: str):
        assert not declares(TIMER, src), (
            f"{TIMER} is declared again. A fixed delay cannot know whether a "
            "tmux server exists; on a cold boot it fires before one does and "
            "the restore script then creates a server inside this unit's "
            "cgroup, which systemd kills on ExecStart return (43 conversations "
            "lost, 2026-09-06)."
        )

    def test_the_service_carries_no_fixed_delay_directive(self, service_block: str):
        """⚠ AN INVARIANT GUARD, NOT REGRESSION COVERAGE — GREEN AT THE BASE REF.

        The 2026-09-06 bug put the delay on the TIMER, never on the service, so
        this pins a property the defect never violated. It is belt to the
        timer's braces: the delay must not reappear ON the service either (a
        `Timer` block moved inline). Enumerated rather than pattern-matched,
        because each name is a distinct way to reintroduce "wait a while and
        hope" as the trigger.
        """
        for name in ("OnActiveSec", "OnBootSec", "OnStartupSec", "OnUnitActiveSec"):
            assert directive(name, service_block) is None, (
                f"{name} is set on the restore SERVICE. The trigger is the tmux "
                "socket appearing, not an elapsed duration."
            )


# --- the path unit triggers the service ----------------------------------------

class TestThePathUnitTriggersTheService:
    def test_the_path_unit_is_declared(self, src: str):
        assert declares(PATH_UNIT, src), (
            f"{PATH_UNIT} is not declared, so nothing starts the restore "
            "service at all — the timer is gone and no replacement trigger "
            "exists."
        )

    def test_it_names_the_restore_service(self, path_block: str):
        """`Unit=` defaults to the same basename, so this is not strictly
        required to FUNCTION — but a path unit that silently triggers a
        different service is exactly the failure the default hides. Assert the
        wiring is explicit and correct."""
        assert _unquote(directive("Unit", path_block)) == "tmux-session-restore.service", (
            "the path unit does not explicitly trigger "
            "tmux-session-restore.service"
        )

    def test_it_is_ENABLED_not_merely_declared(self, path_unit_block: str):
        """🔴 DECLARED IS NOT ENABLED, and the difference is silent.

        `nix_units`' own docstring records the measured shape: a timer whose
        `WantedBy = [ "timers.target" ]` had been changed to `After =`, leaving
        it declared, never enabled, never firing, with the guard green. A path
        unit with no `Install.WantedBy` is never started, so it never watches,
        so the restore never runs — and nothing else in this file would notice.
        """
        install = section("Install", path_unit_block)
        assert install is not None, (
            "the path unit has no [Install] section — it will never be started "
            "by the user manager, so it will never watch anything and the "
            "restore will never fire"
        )
        wanted = directive("WantedBy", install)
        assert wanted is not None, (
            "the path unit has no WantedBy — it will never be started by the "
            "user manager, so it will never watch anything and the restore "
            "will never fire"
        )
        assert "default.target" in wanted, (
            f"the path unit's WantedBy is {wanted!r}; it must be pulled in by "
            "the user manager's default target so it is armed before any "
            "terminal starts tmux"
        )


# --- the watch is an event, not a state ----------------------------------------

class TestTheWatchIsAnEventNotAState:
    """🔴 THE MEASURED BUSY LOOP. See STATE_TYPE_PATH_DIRECTIVES above."""

    @pytest.mark.parametrize("name", STATE_TYPE_PATH_DIRECTIVES)
    def test_no_state_type_directive_is_used(self, path_block: str, name: str):
        assert directive(name, path_block) is None, (
            f"{name}= is a STATE, and systemd re-checks it the instant the "
            "triggered unit terminates. MEASURED 2026-09-07 with a transient "
            "unit on a scratch path: PathExists= on a file that goes on "
            "existing ran the oneshot 5 times in 8 seconds and left BOTH the "
            "service and the path unit Result=start-limit-hit / failed. This "
            "service carries OnFailure=notify-failure@%n, whose toast bypasses "
            "DND — so this shape is a nightly alarm on every boot, strictly "
            "worse than the bug it was meant to fix. Use PathChanged=."
        )

    def test_it_watches_via_PathChanged(self, path_block: str):
        watched = _unquote(directive("PathChanged", path_block))
        assert watched is not None, (
            "the path unit sets no PathChanged=. Some event-type watch must "
            "exist or the unit watches nothing and the restore never fires."
        )
        assert watched.endswith("/default"), (
            f"PathChanged={watched!r} does not name tmux's default socket. "
            "tmux's socket is <TMUX_TMPDIR>/tmux-<uid>/default; watching the "
            "DIRECTORY instead would fire on every unrelated socket created in "
            "it, and this host's runtime dir demonstrably collects other "
            "agents' probe sockets (measured: a sibling create does not fire a "
            "watch on the specific name, and must not start firing one)."
        )


# --- the trigger and the query must name ONE server ----------------------------

class TestTheTriggerAndTheQueryNameOneServer:
    """🔴 A SEAM GUARD: it pins a RELATIONSHIP, not either component.

    The path unit watches a socket under some directory. The restore script
    shells out to `tmux`, which resolves its socket from `$TMUX_TMPDIR` — whose
    COMPILED-IN DEFAULT IS /tmp, not `%t`. Each side can be individually
    correct while together they describe two different tmux servers: the unit
    fires on a socket appearing in one place and then asks about a server in
    another. No test scoped to one unit can see that.
    """

    def test_the_service_pins_TMUX_TMPDIR_at_all(self, service_block: str):
        env = directive("Environment", service_block)
        assert env is not None and "TMUX_TMPDIR=" in env, (
            "the restore service does not pin TMUX_TMPDIR. Without it the unit "
            "depends on an inherited manager value that appears in no "
            "/etc/nixos file, no environment.d and no home-manager output — "
            "undeclared runtime state, i.e. a fact about this boot rather than "
            "about the configuration."
        )

    def test_the_watched_socket_lives_under_the_pinned_TMUX_TMPDIR(
        self, service_block: str, path_block: str
    ):
        env = directive("Environment", service_block) or ""
        m = re.search(r'"TMUX_TMPDIR=([^"]*)"', env)
        assert m, f"could not read a TMUX_TMPDIR value out of {env!r}"
        tmpdir = m.group(1)

        watched = _unquote(directive("PathChanged", path_block))
        assert watched is not None, "the path unit sets no PathChanged="

        # tmux's socket is <TMUX_TMPDIR>/tmux-<uid>/default. Compare the
        # PARENT-OF-PARENT of the watched path against the pinned TMUX_TMPDIR:
        # that is the relationship, and it holds whatever specifier either side
        # is spelled with.
        socket_root = str(Path(watched).parent.parent)
        assert socket_root == tmpdir, (
            f"the path unit watches {watched!r}, whose socket root is "
            f"{socket_root!r}, but the service pins TMUX_TMPDIR={tmpdir!r}. "
            "The unit would be triggered by a socket in one directory and then "
            "query a server in another — the trigger and the query must name "
            "ONE server."
        )

    def test_the_socket_directory_is_named_for_the_UID(self, path_block: str):
        """tmux namespaces its socket directory by uid (`tmux-1000`). A watch
        that hardcodes one uid is wrong on any other account; `%U` is the
        specifier that expands to it (MEASURED to expand in a real path unit:
        the resulting Paths= read /run/user/1000/…-1000/default)."""
        watched = _unquote(directive("PathChanged", path_block)) or ""
        socket_dir = Path(watched).parent.name
        assert socket_dir == "tmux-%U", (
            f"the watched socket directory is {socket_dir!r}. tmux names it "
            "tmux-<uid>; use the %U specifier rather than a hardcoded uid."
        )


# --- the service must be skipped, not FAILED, when there is no socket ----------

class TestTheServiceIsGuardedByTheSameSocket:
    """🔴 `PathChanged=` FIRES ON DELETION TOO — MEASURED.

    In the 2026-09-07 experiment the watch fired once when the socket was
    created and AGAIN when it was removed. Socket removal is the operator's
    tmux server exiting, i.e. a routine event. Without a guard on the service,
    every `tmux kill-server` would start a restore into a box with no server.

    `ConditionPathExists=` is the guard, and it is safe under this unit's
    `OnFailure=`: measured with both controls, a failing condition left the
    unit `Result=success ActiveState=inactive`, ExecStart did NOT run, and the
    OnFailure handler fired ZERO times — against ONE firing for a genuine
    `exit 1` positive control.
    """

    def test_the_service_is_condition_guarded(self, service_block: str):
        cond = _unquote(directive("ConditionPathExists", service_block))
        assert cond is not None, (
            "the restore service has no ConditionPathExists. PathChanged= "
            "fires on socket DELETION as well as creation (measured), so "
            "without this every tmux server shutdown starts a restore into a "
            "box with no server."
        )

    def test_the_condition_is_the_same_path_the_watch_names(
        self, service_block: str, path_block: str
    ):
        """The relationship again: guarding on a DIFFERENT path than the one
        watched would let the unit be triggered by one socket and gated on
        another."""
        cond = _unquote(directive("ConditionPathExists", service_block))
        watched = _unquote(directive("PathChanged", path_block))
        assert cond == watched, (
            f"the service is gated on {cond!r} but the path unit watches "
            f"{watched!r}. These must be the same socket, or the guard is "
            "answering about a server the trigger did not observe."
        )


# --- invariant guard: the unit must never own the operator's server ------------

class TestTheUnitCannotOwnTheOperatorsServer:
    """🔴 LABELLED AN INVARIANT GUARD, NOT REGRESSION COVERAGE.

    The 2026-09-06 bug never violated this — `RemainAfterExit` was absent then
    and is absent now, so this is GREEN at the base ref and pins a decision
    rather than catching a regression. It is here because `RemainAfterExit=yes`
    is the obvious-looking fix, is MEASURED to work (with it, a oneshot's tmux
    server survives ExecStart returning), and is wrong: with the default
    `KillMode=control-group` the unit would then OWN whatever server it
    started, and `systemctl --user stop tmux-session-restore` would kill the
    operator's entire workspace. The trigger change removes the need for it,
    because the server pre-exists in another cgroup.
    """

    def test_RemainAfterExit_is_not_enabled(self, service_block: str):
        value = directive("RemainAfterExit", service_block)
        assert value is None or _unquote(value) in ("false", "no"), (
            f"RemainAfterExit={value!r} on the restore service. With "
            "KillMode=control-group this unit would own any tmux server it "
            "started, and `systemctl --user stop tmux-session-restore` would "
            "then kill the operator's whole workspace. It is measured to work "
            "and it is the wrong fix; the trigger change is what removes the "
            "need for it."
        )

    def test_the_service_is_still_a_oneshot(self, service_block: str):
        """Pins the shape the RemainAfterExit argument is ABOUT. If the unit
        stopped being a oneshot the reasoning above would need redoing rather
        than silently continuing to be asserted."""
        assert _unquote(directive("Type", service_block)) == "oneshot", (
            "the restore service is no longer Type=oneshot; the cgroup-"
            "ownership argument this class encodes was written about a oneshot "
            "and must be re-derived"
        )
