"""`nix/system/preflight-tmp-churn-host.sh` — the /tmp-churn preflight and applier.

This file had ZERO tests while its sibling `apply-tmp-churn-retention.sh` had a
355-line harness, and a round-1 audit of PR #1370 found three 🔴 and nine 🟡 in it
— every one of them reproducible in under a minute. So the harness comes first and
each finding gets a fixture case with its own assertion text.

WHY THE WHOLE FILE IS DRIVABLE HERE, unlike its sibling (whose tests can only reach
the extracted python heredoc): `TMP_CHURN_CFG` now means what it means in the
sibling — edit a fixture, touch nothing on the system — so the script drops its root
requirement and never runs `nixos-rebuild` in that mode. Everything else it shells
out to (`systemd-tmpfiles`, `nixos-rebuild`, `nix-instantiate`, `systemctl`,
`hostname`, `findmnt`) is stubbed on PATH. No namespaces, no root, no skips.

🔴 THE `systemd-tmpfiles` STUB IS STATEFUL ON PURPOSE. The script reads the live
tmpfiles config TWICE — once before it touches anything, once to verify — and the
central defect this module exists to pin is that the OLD code compared the second
read's COUNT against a number from the repo, never against the first read. A stub
that returned one fixed answer could not tell a working rebuild from an inert one.
So call 1 returns `live-before`, call 2+ returns `live-after`, and
`test_the_live_stub_is_actually_reached` is the positive control that the script
calls it at all — a zero from an unreached stub is indistinguishable from a pass.
"""

from __future__ import annotations

import os
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

# scripts/ — for the cross-suite helpers in testlib/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from testlib import mockbin  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
RETENTION = REPO / "nix" / "system" / "apply-tmp-churn-retention.sh"

# 🔴 THE RED-BEFORE HARNESS, and the only reason these two overrides exist.
# `claude/RULES.md`: "a test you have not watched FAIL proves nothing". Every guard
# below is a regression test for a defect that was live at d5421b4d, so the matrix
# has to be reproducible by someone who was not here:
#
#   git show d5421b4d:nix/system/preflight-tmp-churn-host.sh > /tmp/base.sh
#   PREFLIGHT_SCRIPT_UNDER_TEST=/tmp/base.sh PREFLIGHT_RUN_PREFIX='unshare -r' \
#     python -m pytest scripts/tests/test_preflight_tmp_churn_host.py
#
# The prefix is needed because the OLD script demanded root even under
# TMP_CHURN_CFG (finding 6) — without it every case would go red on the root check
# instead of on the defect it is about, which is red for the wrong reason and proves
# nothing. Nothing in CI sets either variable; unset, this is the shipped file.
SCRIPT = Path(
    os.environ.get("PREFLIGHT_SCRIPT_UNDER_TEST")
    or REPO / "nix" / "system" / "preflight-tmp-churn-host.sh"
)
RUN_PREFIX = shlex.split(os.environ.get("PREFLIGHT_RUN_PREFIX", ""))

# The rule set the shipped retention script emits. Read from the script itself so
# there is no second copy here to drift — the same discipline the sibling suite uses.
def ledger() -> list[str]:
    out = subprocess.run(
        ["bash", str(RETENTION), "--emit-rules"],
        capture_output=True, text=True, check=True,
    ).stdout
    rules = [ln for ln in out.splitlines() if ln and not ln.startswith("#")]
    assert rules, "the retention script emitted no rules — the harness has nothing to pin"
    return rules


CONFIG_WITH_ANCHOR = """{ config, pkgs, ... }:
{
  networking.hostName = "fixture";
  systemd.tmpfiles.rules = [
    "d /run/example 0755 root root -"
  ];
}
"""

CONFIG_WITHOUT_ANCHOR = """{ config, pkgs, ... }:
{
  networking.hostName = "fixture";
  services.openssh.enable = true;
}
"""


class Host:
    """A fixture NixOS host: a config directory, a stub PATH, and a live tmpfiles
    config that can differ before and after the (stubbed) activation."""

    def __init__(self, tmp_path: Path):
        self.root = tmp_path
        self.cfgdir = tmp_path / "etc-nixos"
        self.cfgdir.mkdir()
        self.cfg = self.cfgdir / "configuration.nix"
        self.stub = tmp_path / "stub"
        self.stub.mkdir()
        self.tmpdir = tmp_path / "tmpdir"
        self.tmpdir.mkdir()
        self.state = tmp_path / "state"
        self.state.mkdir()
        self.live_before = self.state / "live-before"
        self.live_after = self.state / "live-after"
        self.live_before.write_text("")
        self.live_after.write_text("")
        self.calls = self.state / "cat-config-calls"
        self.rebuild_ran = self.state / "rebuild-ran"
        self.retention = str(RETENTION)
        self._write_stubs()

    # -- stubs ---------------------------------------------------------------
    def _stub(self, name: str, body: str) -> None:
        """🔴 The shebang is owned by `testlib.mockbin.write_exec` (POSIX sh), never
        written here: the nix build sandbox has no env(1) at the path an
        env-based shebang names, so a stub that supplies its own execs fine on the
        dev host and ENOENTs in the authoritative tier. Every body below is
        therefore POSIX sh, not bash. `scripts/tests/test_runtime_shebangs.py` is
        the guard, and it caught this file's first draft on its first gate run —
        which is exactly the two-tier hazard, found by the tier that can see it."""
        mockbin.write_exec(self.stub / name, body)

    def _write_stubs(self) -> None:
        # Stateful: first --cat-config is the BEFORE state, every later one is AFTER.
        self._stub(
            "systemd-tmpfiles",
            f'n=0\n'
            f'if [ -f "{self.calls}" ]; then n=$(cat "{self.calls}"); fi\n'
            f'n=$((n+1)); echo "$n" > "{self.calls}"\n'
            f'if [ "$n" -le 1 ]; then cat "{self.live_before}"; '
            f'else cat "{self.live_after}"; fi\n'
            "exit 0\n",
        )
        self._stub(
            "nixos-rebuild",
            f'echo "STUB nixos-rebuild $*" ; date +%s%N >> "{self.rebuild_ran}" ; exit 0\n',
        )
        self._stub("nix-instantiate", "exit 0\n")
        self._stub("systemctl", "printf 'HEAD\\nNEXT  timer row\\n'; exit 0\n")
        self._stub("hostname", "echo fixture-host\n")
        self._stub("findmnt", "echo /dev/fixture\n")

    def add_stub(self, name: str, body: str) -> None:
        self._stub(name, body)

    # -- state setters -------------------------------------------------------
    def set_live(self, before: list[str], after: list[str] | None = None) -> None:
        self.live_before.write_text("".join(f"{r}\n" for r in before))
        self.live_after.write_text(
            "".join(f"{r}\n" for r in (before if after is None else after))
        )

    def write_config(self, text: str, mode: int = 0o644) -> None:
        self.cfg.write_text(text)
        self.cfg.chmod(mode)

    def symlink_config(self, text: str, mode: int = 0o644) -> Path:
        """The layout of anyone keeping /etc/nixos in a git repo — which devrc is."""
        real = self.root / "repo" / "configuration.nix"
        real.parent.mkdir(exist_ok=True)
        real.write_text(text)
        real.chmod(mode)
        if self.cfg.exists() or self.cfg.is_symlink():
            self.cfg.unlink()
        self.cfg.symlink_to(real)
        return real

    # -- driver --------------------------------------------------------------
    def run(self, *args: str, env_extra: dict[str, str] | None = None,
            retention: str | None = None) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PATH"] = f"{self.stub}:{env['PATH']}"
        env["TMPDIR"] = str(self.tmpdir)
        env["TMP_CHURN_CFG"] = str(self.cfg)
        env["TMP_CHURN_RETENTION"] = self.retention if retention is None else retention
        env.pop("TMP_CHURN_FLAKE_ACK", None)
        if env_extra:
            for k, v in env_extra.items():
                if v is None:
                    env.pop(k, None)
                else:
                    env[k] = v
        cmd = [*RUN_PREFIX, "bash", str(SCRIPT), *args]
        if RUN_PREFIX:
            # `unshare -r` does not forward the caller's environment through `env`
            # reliably for the shell it spawns; go through env(1) explicitly.
            cmd = [*RUN_PREFIX, "env", *(f"{k}={v}" for k, v in env.items()),
                   "bash", str(SCRIPT), *args]
        return subprocess.run(cmd, capture_output=True, text=True, env=env)


@pytest.fixture
def host(tmp_path: Path) -> Host:
    return Host(tmp_path)


def out(p: subprocess.CompletedProcess) -> str:
    return p.stdout + p.stderr


# ── harness self-checks (validate the instrument before reading its verdict) ──


def test_the_live_stub_is_actually_reached(host: Host):
    """A zero from a stub that was never called looks exactly like a pass. Pin that
    the script really shells out to `systemd-tmpfiles --cat-config`, twice: once
    before it touches anything and once to verify."""
    host.write_config(CONFIG_WITH_ANCHOR)
    host.set_live([], ledger())

    p = host.run("--apply")

    assert host.calls.exists(), "the script never ran systemd-tmpfiles at all"
    assert host.calls.read_text().strip() == "2", (
        f"expected exactly 2 --cat-config reads (before + verify), got "
        f"{host.calls.read_text().strip()}"
    )
    assert p.returncode == 0, out(p)


def test_the_harness_can_produce_a_red(host: Host):
    """Negative control for every PASS assertion below: the same fixture with an
    activation that changes nothing must FAIL."""
    host.write_config(CONFIG_WITH_ANCHOR)
    host.set_live([], [])  # after == before: nothing became live

    p = host.run("--apply")

    assert p.returncode == 1, out(p)
    assert "RESULT: 🔴 FAIL" in out(p)


# ── 🔴 1 — READ-ONLY mode must never execute an unvalidated retention script ──

OLD_RETENTION = """# A pre-2026-09-04 copy (06597151): no argument handling at all, so ANY argument
# falls through to the live /etc/nixos edit. Measured: that revision contains zero
# occurrences of '--emit-rules'. This stand-in records that it ran instead.
set -eu
echo "I AM EDITING THE CONFIG NOW" > "$OLD_RETENTION_SENTINEL"
"""

# 🔴 A guard that greps for the STRING `--emit-rules` anywhere in the file passes on
# this: the flag is named in a comment and the code still falls through to the edit.
MENTIONS_ONLY_RETENTION = """# TODO: add a --emit-rules read-only surface like the workbench copy has.
set -eu
echo "I AM EDITING THE CONFIG NOW" > "$OLD_RETENTION_SENTINEL"
"""


@pytest.mark.parametrize(
    "body,label",
    [
        pytest.param(OLD_RETENTION, "no mention at all", id="no-mention"),
        pytest.param(MENTIONS_ONLY_RETENTION, "mentioned in a comment", id="comment-only"),
    ],
)
def test_a_retention_script_without_an_emit_rules_ARM_is_never_executed(
    host: Host, body: str, label: str
):
    """🔴 BLOCKER 1. The no-argument invocation is documented as "READ-ONLY report,
    changes nothing", and it ran `bash "$RETENTION" --emit-rules` unconditionally
    with `2>/dev/null`. On a host whose checkout predates d8fe0bce that call performs
    the FULL config edit — inserting the superseded files-only `m:7d` ruleset and the
    withdrawn dead `homelab-talos-prs-*` rule — with every diagnostic swallowed by
    the command substitution. This is the LSOF_BIN shape from #1366: a root
    execution of a path derived from a $SUDO_USER lookup, unvalidated.

    The probe is on the case ARM, not on a mention of the flag, so the second
    parametrisation — a script that merely NAMES `--emit-rules` in a comment — must
    also be refused."""
    sentinel = host.state / "old-retention-ran"
    old = mockbin.write_exec(host.root / "old-retention.sh", body)
    host.write_config(CONFIG_WITH_ANCHOR)

    p = host.run(retention=str(old), env_extra={"OLD_RETENTION_SENTINEL": str(sentinel)})

    assert not sentinel.exists(), (
        f"the read-only invocation EXECUTED a retention script with {label} — "
        "that execution is a root edit of the NixOS config"
    )
    assert "🔴 PRESENT BUT TOO OLD" in out(p), out(p)
    # The read-only run now classifies as unapplied-no-script and refuses (3) rather
    # than reporting a count it obtained by editing the config to get it.
    assert p.returncode == 3, out(p)


def test_an_unsafe_retention_script_blocks_apply_rather_than_running_it(host: Host):
    sentinel = host.state / "old-retention-ran"
    old = mockbin.write_exec(host.root / "old-retention.sh", OLD_RETENTION)
    host.write_config(CONFIG_WITH_ANCHOR)

    p = host.run("--apply", retention=str(old),
                 env_extra={"OLD_RETENTION_SENTINEL": str(sentinel)})

    assert not sentinel.exists(), "--apply executed the too-old retention script"
    assert p.returncode == 3, out(p)
    assert "CANNOT APPLY" in out(p)
    assert not host.rebuild_ran.exists(), "nixos-rebuild ran despite the refusal"


def test_the_shipped_retention_script_IS_accepted(host: Host):
    """Positive control for the probe: it must not refuse everything."""
    host.write_config(CONFIG_WITH_ANCHOR)
    p = host.run()
    assert "(present, has --emit-rules)" in out(p), out(p)
    assert "UNAPPLIED, and the anchor is present" in out(p)


def test_a_retention_script_emitting_something_other_than_rules_is_refused(host: Host):
    """Structure is not behaviour: a file can carry the case arm and still print
    something that is not a tmpfiles rule set."""
    bogus = mockbin.write_exec(
        host.root / "bogus.sh",
        "case \"${1:-}\" in\n  --emit-rules)\n"
        "    echo 'INFO: nothing to do'\n    exit 0 ;;\nesac\n",
    )
    host.write_config(CONFIG_WITH_ANCHOR)

    p = host.run(retention=str(bogus))

    assert "not a /tmp rule" in out(p), out(p)


# ── 🔴 2 — PASS must mean THIS edit went live, not "the count matched" ────────


def test_seven_unrelated_live_rules_and_an_inert_activation_do_not_pass(host: Host):
    """🔴 BLOCKER 2. `after_live -eq expected` compares a count over the WHOLE live
    tmpfiles config against a number from the repo ledger. With seven UNRELATED
    `mM:7d` rules already live — from an imported module, an /etc/tmpfiles.d
    drop-in, or a flake host where `configuration.nix` is a leftover nothing
    evaluates — and an activation that changes nothing, the old code printed
    `RESULT: PASS` and exited 0 over a completely inert edit."""
    unrelated = [f"e /tmp/unrelated-{i} - - - mM:7d" for i in range(1, 8)]
    assert len(unrelated) == len(ledger()), (
        "this fixture is only the audit's scenario if the decoy count EQUALS the "
        "ledger's — otherwise a plain count check would have caught it anyway"
    )
    host.write_config(CONFIG_WITH_ANCHOR)
    host.set_live(unrelated, unrelated)

    p = host.run("--apply")

    assert p.returncode == 1, out(p)
    assert "RESULT: 🔴 FAIL" in out(p)
    assert "NOT LIVE (matched as strings" in out(p), out(p)
    assert "The live mM:7d count moved by 0, not 7" in out(p), out(p)


def test_the_right_COUNT_of_the_wrong_rules_does_not_pass(host: Host):
    """The identity half, isolated from the delta half: the count rises by exactly
    the right amount, but the rules that became live are not ours."""
    host.write_config(CONFIG_WITH_ANCHOR)
    decoys = [f"e /tmp/decoy-{i} - - - mM:7d" for i in range(len(ledger()))]
    host.set_live([], decoys)

    p = host.run("--apply")

    assert p.returncode == 1, out(p)
    assert "NOT LIVE (matched as strings" in out(p), out(p)
    # The delta is RIGHT here — only the identity check can see this one.
    assert "count moved by" not in out(p), (
        "the delta check fired too; this fixture is meant to isolate identity"
    )


def test_a_rule_that_is_already_live_from_elsewhere_is_not_a_false_failure(host: Host):
    """The delta is `|want| - already`, measured BEFORE the edit. One of our rules
    arriving from another module must not make a correct run report FAIL."""
    rules = ledger()
    host.write_config(CONFIG_WITH_ANCHOR)
    host.set_live([rules[0]], rules)

    p = host.run("--apply")

    assert p.returncode == 0, out(p)
    assert "1 of them are ALREADY live" in out(p), out(p)
    assert f"the live count must rise by {len(rules) - 1}" in out(p), out(p)
    assert "RESULT: PASS" in out(p)


def test_an_activation_that_drops_an_UNRELATED_rule_does_not_pass(host: Host):
    """The delta half, isolated from the identity half — and the case that made the
    delta check earn its place. A mutation sweep found the delta assertion SURVIVED
    every other fixture in this module: the identity check killed them all first. So
    here every rule we asked for IS live and matched by string, and the run must
    still fail, because the live count did not move the way it had to — the
    activation quietly took an unrelated `mM:7d` rule away with it. That is the
    exact class of collateral the sibling's deleted eviction feature caused twice in
    three audit rounds, and a per-rule identity check cannot see it."""
    rules = ledger()
    bystander = "e /tmp/someone-elses-rule - - - mM:7d"
    host.write_config(CONFIG_WITH_ANCHOR)
    host.set_live([bystander], rules)  # ours arrive; the bystander vanishes

    p = host.run("--apply")

    assert p.returncode == 1, out(p)
    assert "NOT LIVE (matched as strings" not in out(p), (
        "this fixture is meant to isolate the DELTA check; identity fired too"
    )
    assert f"count moved by {len(rules) - 1}, not {len(rules)}" in out(p), out(p)


def test_a_working_activation_passes(host: Host):
    host.write_config(CONFIG_WITH_ANCHOR)
    host.set_live([], ledger())

    p = host.run("--apply")

    assert p.returncode == 0, out(p)
    assert "RESULT: PASS" in out(p)
    assert f"all {len(ledger())} required rule(s) matched by STRING" in out(p)


# ── 🔴 3 — a symlinked config must leave a restorable backup ──────────────────


def test_a_symlinked_config_gets_a_real_backup_not_a_second_link(host: Host):
    """🔴 BLOCKER 3. `cp -a` implies `-d`. On a symlinked `configuration.nix` — the
    standard layout for anyone keeping their NixOS config in a git repo, which is
    what this repository is — the backup was a SECOND SYMLINK to the file about to
    be edited. The edit then wrote through the link, so the "backup" read back the
    edited content and the restore was a no-op."""
    real = host.symlink_config(CONFIG_WITHOUT_ANCHOR)
    original = real.read_text()
    host.set_live([], ledger())

    p = host.run("--init")

    backups = sorted(host.cfgdir.glob("configuration.nix.bak-2*"))
    assert backups, f"no backup was taken: {out(p)}"
    bak = backups[-1]
    assert not bak.is_symlink(), "the backup is a symlink — cp -a preserved the link"
    assert bak.read_text() == original, (
        "the backup does not hold the ORIGINAL content; it followed the edit"
    )
    assert "mM:7d" in real.read_text(), "the edit did not reach the symlink target"


def test_apply_takes_its_own_dereferenced_backup_before_delegating(host: Host):
    """The delegate `apply-tmp-churn-retention.sh` backs up with a plain `cp -a`, so
    on a symlinked config ITS backup is a second link to the file it then rewrites
    and the rollback its footer documents is a no-op. That file is out of scope for
    this change, so the preflight takes a dereferenced copy of its own first."""
    real = host.symlink_config(CONFIG_WITH_ANCHOR)
    original = real.read_text()
    host.set_live([], ledger())

    p = host.run("--apply")

    assert p.returncode == 0, out(p)
    ours = sorted(host.cfgdir.glob("configuration.nix.bak-preflight-*"))
    assert ours, f"no pre-delegate backup was taken: {out(p)}"
    assert not ours[-1].is_symlink()
    assert ours[-1].read_text() == original
    assert "mM:7d" in real.read_text(), "the delegate's edit did not reach the target"


def test_apply_does_not_double_up_the_backup_on_a_regular_file(host: Host):
    """Positive control in the other direction: on a regular file the delegate's own
    backup is already correct, so a second copy would be noise."""
    host.write_config(CONFIG_WITH_ANCHOR)
    host.set_live([], ledger())

    p = host.run("--apply")

    assert p.returncode == 0, out(p)
    assert not list(host.cfgdir.glob("configuration.nix.bak-preflight-*"))


def test_a_failed_parse_on_a_symlinked_config_restores_the_original(host: Host):
    """The end state the auditor measured on the old code: config left edited and
    broken, and no recoverable copy anywhere."""
    real = host.symlink_config(CONFIG_WITHOUT_ANCHOR)
    original = real.read_text()
    host.set_live([], ledger())
    host.add_stub("nix-instantiate", "echo 'error: syntax error, unexpected }' >&2; exit 1\n")

    p = host.run("--init")

    assert p.returncode == 1, out(p)
    assert "DOES NOT PARSE" in out(p)
    assert real.read_text() == original, (
        "the symlink target was left EDITED after a failed parse — the restore "
        "wrote into the backup's own target instead"
    )


# ── 🟡 4 — the restore's own failure must be reported, not silently fatal ─────


def test_a_restore_that_itself_fails_says_so_and_names_the_backup(host: Host):
    """The old comment claimed "the restore is itself re-parsed". Under bare
    `set -e` a failing restore exited immediately, so neither the re-parse nor the
    `inspect BY HAND` message it promised could ever run.

    The fixture makes the restore fail for real: the nix-instantiate stub reports a
    syntax error AND chmods the backup unreadable on its way out."""
    host.write_config(CONFIG_WITHOUT_ANCHOR)
    host.set_live([], ledger())
    host.add_stub(
        "nix-instantiate",
        f'chmod 000 "{host.cfgdir}"/configuration.nix.bak-2* 2>/dev/null || true\n'
        "echo 'error: syntax error' >&2\nexit 1\n",
    )

    p = host.run("--init")

    assert p.returncode == 1, out(p)
    assert "THE RESTORE ITSELF FAILED" in out(p), out(p)
    assert "put it back BY HAND" in out(p) or "BY HAND" in out(p), out(p)
    # 🔴 And it must not have TRUNCATED the config on the way. `cat "$backup" >
    # "$CFG"` opens the destination for writing FIRST, so an unreadable backup
    # takes $CFG to zero bytes — strictly worse than the unparseable file it was
    # undoing. This assertion is why the restore stages through a temp file.
    assert host.cfg.stat().st_size > 0, (
        "the failed restore truncated the config to zero bytes"
    )
    assert "systemd.tmpfiles.rules" in host.cfg.read_text(), (
        "the config is neither the edit nor the original — the restore destroyed it"
    )
    # restore the mode so pytest's tmp_path cleanup can proceed
    for b in host.cfgdir.glob("configuration.nix.bak-2*"):
        b.chmod(0o644)


def test_nix_instantiate_stderr_is_shown_so_127_is_not_read_as_a_syntax_error(host: Host):
    host.write_config(CONFIG_WITHOUT_ANCHOR)
    host.set_live([], ledger())
    host.add_stub("nix-instantiate", "echo 'DISTINCTIVE-PARSER-DIAGNOSTIC' >&2; exit 127\n")

    p = host.run("--init")

    assert "DISTINCTIVE-PARSER-DIAGNOSTIC" in out(p), (
        "the parse failure's stderr was discarded, so a MISSING nix-instantiate "
        "(exit 127) is indistinguishable from a syntax error"
    )


# ── 🟡 5 — branch on $#, not on ${1:-} ───────────────────────────────────────


@pytest.mark.parametrize("args", [("--init", "--dry-run"), ("--apply", "--whatever")])
def test_more_than_one_argument_is_refused_not_read_as_the_first(host: Host, args):
    """🟡 `case "${1:-}"` silently ignores every argument after the first, so
    `--init --dry-run` — which reads like a preview — performed the full apply and
    switch. apply-tmp-churn-retention.sh refuses this explicitly ("BRANCH ON $#,
    NOT ON ${1:-}"); this script edits the same file."""
    host.write_config(CONFIG_WITHOUT_ANCHOR)
    before = host.cfg.read_text()

    p = host.run(*args)

    assert p.returncode == 64, out(p)
    assert "exactly one argument, got 2" in out(p), out(p)
    assert host.cfg.read_text() == before, "the config was edited on the refusal path"
    assert not host.rebuild_ran.exists()


def test_a_single_empty_argument_is_refused(host: Host):
    host.write_config(CONFIG_WITH_ANCHOR)
    p = host.run("")
    assert p.returncode == 64, out(p)
    assert "empty argument" in out(p)


# ── 🟡 6 — TMP_CHURN_CFG is the no-system-change contract ────────────────────


def test_test_mode_never_runs_nixos_rebuild(host: Host):
    """🟡 TMP_CHURN_CFG is the sibling's no-system-change fixture surface, and the
    sibling refuses even to OFFER a rebuild in it: a rebuild would build the REAL
    system from an /etc/nixos the run never touched, and the operator would watch it
    scroll past and conclude the fixture's rules had been applied. This script
    honoured the variable for reads and for the edit, then ran the switch anyway."""
    host.write_config(CONFIG_WITH_ANCHOR)
    host.set_live([], ledger())

    p = host.run("--apply")

    assert not host.rebuild_ran.exists(), (
        "nixos-rebuild ran under TMP_CHURN_CFG — that is a real system change from "
        "a run the header calls 'no system change'"
    )
    assert "TEST MODE — NOT running nixos-rebuild" in out(p), out(p)


def test_test_mode_needs_no_root(host: Host):
    """The corollary: if it changes nothing on the system it has no business
    demanding root. This whole module runs as an ordinary user, so every other test
    here is also evidence for this one — but pin the message, since a re-added EUID
    check would make the rest of the file unreachable rather than red."""
    host.write_config(CONFIG_WITH_ANCHOR)
    p = host.run()
    assert os.geteuid() != 0, "this control is vacuous when the suite runs as root"
    assert "no root needed" in out(p), out(p)
    assert p.returncode == 0


def test_test_mode_exports_the_fixture_to_the_retention_child(host: Host):
    """Unexported, the child falls back to /etc/nixos/configuration.nix and a "test"
    run edits the real system."""
    host.write_config(CONFIG_WITH_ANCHOR)
    host.set_live([], ledger())

    p = host.run("--apply")

    assert "TEST MODE — editing fixture" in out(p), (
        "the retention child did not see TMP_CHURN_CFG"
    )
    assert "mM:7d" in host.cfg.read_text()


# ── 🟡 7 — an unknown $SUDO_USER must diagnose, not die silently ──────────────


def test_an_unresolvable_sudo_user_gets_a_diagnostic_not_a_silent_exit_2(host: Host):
    """`getent passwd <unknown>` exits 2; under `set -e` + `pipefail` the whole run
    died there with zero bytes of output. Measured: rc 2, nothing printed."""
    host.write_config(CONFIG_WITH_ANCHOR)

    p = host.run(env_extra={"SUDO_USER": "nosuchuser-preflight-fixture",
                            "TMP_CHURN_RETENTION": None})

    assert p.returncode == 3, f"rc={p.returncode}; {out(p)}"
    assert "could not resolve a home directory" in out(p), out(p)
    assert "TMP_CHURN_RETENTION" in out(p), "the message does not say how to proceed"


def test_an_empty_home_field_does_not_become_an_absolute_path(host: Host):
    """The other half: an empty field 6 yields `/workspace/devrc/...`, an absolute
    path this script would hand to `bash` — as root, in the real invocation."""
    host.write_config(CONFIG_WITH_ANCHOR)
    host.add_stub("getent", 'printf "empty::0:0::"; printf ":/bin/sh\\n"; exit 0\n')

    p = host.run(env_extra={"SUDO_USER": "empty", "TMP_CHURN_RETENTION": None})

    assert p.returncode == 3, out(p)
    assert "could not resolve a home directory" in out(p), out(p)
    assert "/workspace/devrc" not in out(p).replace(str(REPO), ""), (
        "an empty home field was still turned into an absolute retention path"
    )


# ── 🟡 8 — applied-not-rebuilt must not FAIL over a successful rebuild ────────


def test_applied_not_rebuilt_verifies_against_the_config_not_the_ledger(host: Host):
    """🟡 The state `applied-not-rebuilt` is decided from $CFG alone, BEFORE the
    retention script is consulted. With an unusable retention script the old code
    got `expected=0` and reported 🔴 FAIL on a fully successful operation — one line
    after printing `rules LIVE now: 7`.

    In that state the ledger is not needed: the honest claim is about the rules the
    CONFIG carries."""
    rules = ledger()
    cfg = CONFIG_WITH_ANCHOR.replace(
        '    "d /run/example 0755 root root -"\n',
        '    "d /run/example 0755 root root -"\n'
        + "".join(f'    "{r}"\n' for r in rules),
    )
    host.write_config(cfg)
    host.set_live([], rules)  # nothing live before; the rebuild makes them live
    missing = host.root / "no-such-retention.sh"

    p = host.run("--apply", retention=str(missing))

    assert p.returncode == 0, out(p)
    assert "needs a rebuild only" in out(p)
    assert f"the {len(rules)} rule(s) already in" in out(p), out(p)
    assert "RESULT: PASS" in out(p)


def test_the_applying_banner_is_not_printed_on_the_rebuild_only_path(host: Host):
    """🟢 `=== applying (this EDITS $CFG) ===` was printed on a path that edits
    nothing."""
    rules = ledger()
    cfg = CONFIG_WITH_ANCHOR.replace(
        '    "d /run/example 0755 root root -"\n',
        "".join(f'    "{r}"\n' for r in rules),
    )
    host.write_config(cfg)
    host.set_live([], rules)

    p = host.run("--apply")

    assert p.returncode == 0, out(p)
    assert "this EDITS" not in out(p), (
        "the apply banner claims an edit on the rebuild-only path"
    )


# ── 🟡 9 — a flake means $CFG may not be the evaluated file ───────────────────


def test_a_flake_in_the_config_dir_is_reported(host: Host):
    host.write_config(CONFIG_WITH_ANCHOR)
    (host.cfgdir / "flake.nix").write_text("{ outputs = _: {}; }\n")

    p = host.run()

    assert "nixos-rebuild PREFERS a flake" in out(p), out(p)


def test_a_flake_in_the_config_dir_blocks_init(host: Host):
    """🟡 Nothing established $CFG as the file the host evaluates. `nixos-rebuild`
    auto-prefers a flake, so on a flake host the whole edit can land in a leftover —
    which is exactly the state that made the old count-based verification pass over
    an inert change."""
    host.write_config(CONFIG_WITHOUT_ANCHOR)
    (host.cfgdir / "flake.nix").write_text("{ outputs = _: {}; }\n")
    before = host.cfg.read_text()
    host.set_live([], ledger())

    p = host.run("--init")

    assert p.returncode == 3, out(p)
    assert "TMP_CHURN_FLAKE_ACK=1" in out(p), out(p)
    assert host.cfg.read_text() == before, "the config was edited despite the refusal"


def test_the_flake_refusal_can_be_acknowledged(host: Host):
    """Positive control: the gate must be passable by an operator who checked, or it
    is a permanently-red gate people learn to work around."""
    host.write_config(CONFIG_WITHOUT_ANCHOR)
    (host.cfgdir / "flake.nix").write_text("{ outputs = _: {}; }\n")
    host.set_live([], ledger())

    p = host.run("--init", env_extra={"TMP_CHURN_FLAKE_ACK": "1"})

    assert p.returncode == 0, out(p)
    assert "mM:7d" in host.cfg.read_text()


# ── 🟡 10 — the duplicate check is module-scoped, not file-scoped ─────────────


def test_init_refuses_when_a_SIBLING_module_mentions_tmpfiles(host: Host):
    """🟡 Precondition 1 grepped ONE file while `systemd.tmpfiles.rules` is a
    `listOf str` MERGED ACROSS IMPORTED MODULES. Two modules defining it is not a
    Nix error — it is a concatenation — so a host whose rules live in an imported
    module passed the check and got a silently duplicated rule set appended."""
    host.write_config(CONFIG_WITHOUT_ANCHOR)
    (host.cfgdir / "tmp-retention.nix").write_text(
        "{ ... }:\n{\n  systemd.tmpfiles.rules = [ \"e /tmp/x - - - mM:7d\" ];\n}\n"
    )
    before = host.cfg.read_text()
    host.set_live([], ledger())

    p = host.run("--init")

    assert p.returncode == 3, out(p)
    assert "listOf MERGED across modules" in out(p), out(p)
    assert "tmp-retention.nix" in out(p), out(p)
    assert host.cfg.read_text() == before


def test_init_still_refuses_a_SAME_file_mention(host: Host):
    """The narrower case the old check did cover — it must not regress."""
    host.write_config(
        CONFIG_WITHOUT_ANCHOR.replace(
            "  services.openssh.enable = true;\n",
            "  systemd.tmpfiles.settings.x = {};\n",
        )
    )
    host.set_live([], ledger())

    p = host.run("--init")

    assert p.returncode == 3, out(p)
    assert "already appears in" in out(p), out(p)


def test_init_proceeds_when_no_nix_file_mentions_tmpfiles(host: Host):
    """Positive control: the widened check must not refuse a clean host."""
    host.write_config(CONFIG_WITHOUT_ANCHOR)
    (host.cfgdir / "unrelated.nix").write_text("{ ... }: { services.nginx.enable = true; }\n")
    host.set_live([], ledger())

    p = host.run("--init")

    assert p.returncode == 0, out(p)
    assert "mM:7d" in host.cfg.read_text()


# ── 🟡 12 — a successful --init must not change the config's mode ─────────────


@pytest.mark.parametrize("mode", [0o644, 0o600])
def test_init_preserves_the_configs_mode(host: Host, mode: int):
    """🟡 `cp -a "$new" "$CFG"` put the mktemp's 0600 onto the destination, so a
    0644 config became root-only after a SUCCESSFUL run. Only success downgraded it
    — the restore path replays the original mode — so nothing ever reported it.

    Both modes are measured: 0600 is the case the operator's laptop happened to be
    in, where the defect changed nothing, and it cannot distinguish a fix from the
    bug. 0644 is the one that can."""
    host.write_config(CONFIG_WITHOUT_ANCHOR, mode=mode)
    host.set_live([], ledger())

    p = host.run("--init")

    assert p.returncode == 0, out(p)
    got = stat.S_IMODE(host.cfg.stat().st_mode)
    assert got == mode, f"mode went {oct(mode)} -> {oct(got)} on a successful run"


def test_init_preserves_the_mode_through_a_symlink(host: Host):
    real = host.symlink_config(CONFIG_WITHOUT_ANCHOR, mode=0o644)
    host.set_live([], ledger())

    p = host.run("--init")

    assert p.returncode == 0, out(p)
    assert stat.S_IMODE(real.stat().st_mode) == 0o644


# ── 🟢 nits ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "term", ["--init", "--apply", "--help", "TMP_CHURN_CFG", "TMP_CHURN_RETENTION",
             "TMP_CHURN_FLAKE_ACK", "READ-ONLY report"]
)
def test_help_documents_every_mode_and_variable(host: Host, term: str):
    """`--help` printed a slice of the file header by LINE NUMBER and never
    mentioned `--init` at all — the most invasive flag. Measured on the old file:
    `--help | grep -c -- --init` -> 0."""
    p = host.run("--help")
    assert p.returncode == 0, out(p)
    assert term in p.stdout, f"--help never mentions {term}"


def test_a_failing_systemctl_does_not_turn_PASS_into_exit_1(host: Host):
    """🟢 The `systemctl list-timers … | sed -n 2p` was the last command before
    `exit 0`, unguarded: with a failing systemctl the script printed `RESULT: PASS`
    and then exited 1."""
    host.write_config(CONFIG_WITH_ANCHOR)
    host.set_live([], ledger())
    host.add_stub("systemctl", "echo 'Failed to connect to bus' >&2; exit 1\n")

    p = host.run("--apply")

    assert "RESULT: PASS" in out(p)
    assert p.returncode == 0, (
        f"printed PASS and exited {p.returncode} — the exit status came from "
        "systemctl, not from the verification"
    )


def test_a_successful_init_leaves_no_temp_files_behind(host: Host):
    """🟢 `new=$(mktemp)` had no EXIT trap, so it leaked a `/tmp/tmp.*` whenever the
    run died between the mktemp and the `rm -f` two lines later — the very prefix
    this tool exists to reap. TMPDIR is redirected into the fixture so the assertion
    is about THIS run and cannot be confused by other processes.

    ⚠ INVARIANT GUARD, NOT A REGRESSION TEST, for the success path: measured against
    d5421b4d this case is GREEN, because there the `rm -f "$new"` sits on the same
    line as the copy and the success path always reaches it. It is the failure path
    below, and the `$block.err` file that did not exist at all before, that the trap
    is actually needed for. Labelled rather than counted."""
    host.write_config(CONFIG_WITHOUT_ANCHOR)
    host.set_live([], ledger())

    p = host.run("--init")

    assert p.returncode == 0, out(p)
    leftovers = sorted(x.name for x in host.tmpdir.iterdir())
    assert leftovers == [], f"temp files left behind: {leftovers}"


def test_a_failed_init_also_leaves_no_temp_files_behind(host: Host):
    """⚠ Also an INVARIANT GUARD against d5421b4d: measured green there too, because
    the old code's `rm -f "$new"` sits before the parse check on the same line. What
    it does cover at HEAD is `$block.err`, a file the old code did not have."""
    host.write_config(CONFIG_WITHOUT_ANCHOR)
    host.set_live([], ledger())
    host.add_stub("nix-instantiate", "echo boom >&2; exit 1\n")

    p = host.run("--init")

    assert p.returncode == 1, out(p)
    leftovers = sorted(x.name for x in host.tmpdir.iterdir())
    assert leftovers == [], f"temp files left behind on the failure path: {leftovers}"


# ── the states that were already right, pinned so a rework cannot lose them ──


def test_already_applied_and_live_does_nothing(host: Host):
    rules = ledger()
    cfg = CONFIG_WITH_ANCHOR.replace(
        '    "d /run/example 0755 root root -"\n',
        "".join(f'    "{r}"\n' for r in rules),
    )
    host.write_config(cfg)
    host.set_live(rules, rules)

    p = host.run("--apply")

    assert p.returncode == 0, out(p)
    assert "ALREADY APPLIED AND LIVE" in out(p)
    assert not host.rebuild_ran.exists()


def test_no_anchor_without_init_refuses_with_exit_3(host: Host):
    host.write_config(CONFIG_WITHOUT_ANCHOR)
    before = host.cfg.read_text()

    p = host.run("--apply")

    assert p.returncode == 3, out(p)
    assert "Re-run with --init" in out(p)
    assert host.cfg.read_text() == before


def test_a_config_not_closing_with_a_bare_brace_is_refused(host: Host):
    host.write_config(CONFIG_WITHOUT_ANCHOR.rstrip("\n") + "  # trailing comment\n")
    before = host.cfg.read_text()
    host.set_live([], ledger())

    p = host.run("--init")

    assert p.returncode == 3, out(p)
    assert "closes with a bare '}'" in out(p), out(p)
    assert host.cfg.read_text() == before


def test_an_unrecognised_argument_is_refused_with_64(host: Host):
    host.write_config(CONFIG_WITH_ANCHOR)
    p = host.run("--emit-rules")
    assert p.returncode == 64, out(p)
    assert "unrecognised argument" in out(p)


def test_the_module_is_pointed_at_the_shipped_script_by_default():
    """The override above must not be able to silently retarget a CI run."""
    if os.environ.get("PREFLIGHT_SCRIPT_UNDER_TEST"):
        pytest.skip("explicitly retargeted for a red-before baseline run")
    assert SCRIPT == REPO / "nix" / "system" / "preflight-tmp-churn-host.sh"
    assert RUN_PREFIX == []


def test_the_script_is_syntactically_valid():
    """Cheap, and it is the one check that covers every branch no fixture reaches."""
    p = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


def test_bash_is_available():
    """Positive control for the module: everything above shells out."""
    assert shutil.which("bash")
