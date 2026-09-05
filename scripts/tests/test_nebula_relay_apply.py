"""Harness for `nix/system/apply-nebula-relay.sh` — a script that runs as ROOT,
rewrites /etc/nixos/configuration.nix and activates the result.

🔴 NOTHING HERE TOUCHES THE REAL SYSTEM. Every test runs the script with

    NEBULA_CFG   -> a fixture configuration.nix inside tmp_path
    PATH         -> a shim directory FIRST, holding `id`, `ip`, `systemctl`,
                    `nixos-rebuild`, `nix-instantiate`

so `id -u` answers 0 without sudo, `ip` answers with the mesh address the host guard
expects, and `nixos-rebuild` records what it was asked to do instead of doing it.
There is no path from this file to `nixos-rebuild switch`, to
/nix/var/nix/profiles/system, or to /etc/nixos.

🔴 THE VERIFIER IS THE REAL ONE, and it reads the RUNNING PROCESS. So the shims
simulate a running nebula honestly: a real child process is spawned whose argv carries
`-config <rendered.yml>`, the `systemctl` shim reports its pid as MainPID, and the
`nixos-rebuild` shim RE-RENDERS that yml from the .nix file the script just patched —
which is what a rebuild-plus-restart actually does. That is why these tests can assert
"the relay is advertised by the running unit" rather than "the file was edited".

Every fake process is a subprocess.Popen child of this test and is stopped by its own
PID at teardown. No pattern ever reaches pkill.

WHAT EACH TEST IS FOR — the audit findings the script was fixed for:
  F-A  test/verify/switch/verify ordering and the three-state rollback message
  F-B  the anchor is scoped to services.nebula.networks.<NET>
  F-C  no predictable /tmp path is ever live
  F-D  a missing backup fails LOUDLY instead of silently skipping the rollback
  F-E  a symlinked $CFG is refused instead of being replaced by a regular file
  F-G  the verifier's FAIL text (with its egress-cost warning) reaches the operator
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

# 🔴 The shims are written at RUNTIME and then EXECED, so their shebang must exist in
# BOTH tiers — `/usr/bin/env` is absent from the nix build sandbox. `write_exec` owns
# that decision for the whole repo; `test_runtime_shebangs.py` enforces it, and caught
# this file writing its own `#!/usr/bin/env bash` before it ever reached the sandbox.
# Consequence: every shim body below is POSIX sh, not bash.
from testlib.mockbin import write_exec  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]

# TEST SEAM. `scripts/tests/mutants-nebula-relay.sh` copies nix/system/ into a
# `mktemp -d`, mutates the copy, and points this variable at it. Both scripts are taken
# from the SAME directory because apply-nebula-relay.sh resolves the verifier from its
# own location — one source, not two constants that would agree until the day it ran.
_SYSDIR = Path(os.environ.get("DEVRC_TEST_NEBULA_DIR",
                              str(REPO_ROOT / "nix" / "system"))).resolve()
APPLY = _SYSDIR / "apply-nebula-relay.sh"
CHECK = _SYSDIR / "check-nebula-relays.sh"

MESH_IP = "10.42.0.30"
RELAY = "10.42.0.2"

# The four lines the patch must insert, exactly.
INSERTED = (
    '      relay = {\n'
    '        use_relays = true;\n'
    '        relays = [ "10.42.0.2" ];\n'
    '      };\n'
)

# A configuration.nix with the shape the real one has: `services.nebula.networks.mesh`
# holding `    settings = {` immediately followed by `      punchy = {`, plus a SECOND
# network (`travel`) that has a settings block but NO punchy anchor. The second network
# is what makes F-B testable: NEBULA_NET=travel must not reach into the mesh block.
CONFIG_NIX = """\
{ config, pkgs, ... }:

{
  imports = [ ./hardware-configuration.nix ];

  services.nebula.networks.mesh = {
    enable = true;
    ca = "/etc/nebula/ca.crt";
    cert = "/etc/nebula/node.crt";
    key = "/etc/nebula/node.key";

    lighthouses = [ "10.42.0.1" "10.42.0.2" ];

    staticHostMap = {
      "10.42.0.1" = [ "192.168.50.94:4242" ];
      "10.42.0.2" = [ "198.51.100.7:4242" ];   # TEST-NET-3; this repo is PUBLIC
    };

    settings = {
      punchy = {
        punch = true;
        respond = true;
      };
    };

    firewall = {
      outbound = [
        { port = "any"; proto = "any"; host = "any"; }
      ];
      inbound = [
        { port = "any"; proto = "icmp"; host = "any"; }
      ];
    };
  };

  services.nebula.networks.travel = {
    enable = false;
    ca = "/etc/nebula/ca.crt";

    settings = {
      listen = {
        host = "0.0.0.0";
      };
    };
  };

  system.stateVersion = "24.05";
}
"""

# The same file with a relay list ALREADY present, in the shape apply-travel-prep.sh
# writes it: `relay = { ... };` BEFORE `punchy`, which breaks the anchor pair.
# A file where ANOTHER network carries the same anchor pair, EARLIER in the file. This
# is what makes the patch pass's own range guard reachable: without it the insertion
# lands in the first `settings = {` + `punchy = {` it meets, which is the wrong network.
CONFIG_NIX_TWO_ANCHORS = CONFIG_NIX.replace(
    "  services.nebula.networks.mesh = {",
    "  services.nebula.networks.travel = {\n"
    "    enable = false;\n"
    '    ca = "/etc/nebula/ca.crt";\n'
    "\n"
    "    settings = {\n"
    "      punchy = {\n"
    "        punch = false;\n"
    "        respond = false;\n"
    "      };\n"
    "    };\n"
    "  };\n"
    "\n"
    "  services.nebula.networks.mesh = {",
    1,
).replace(
    # drop the trailing `travel` network so the name stays unique
    "  services.nebula.networks.travel = {\n"
    "    enable = false;\n"
    '    ca = "/etc/nebula/ca.crt";\n'
    "\n"
    "    settings = {\n"
    "      listen = {\n"
    '        host = "0.0.0.0";\n'
    "      };\n"
    "    };\n"
    "  };\n\n",
    "",
    1,
)

CONFIG_NIX_WITH_RELAYS = CONFIG_NIX.replace(
    "    settings = {\n      punchy = {\n",
    "    settings = {\n"
    "      relay = {\n"
    "        use_relays = true;\n"
    '        relays = [ "10.42.0.9" ];\n'
    "      };\n"
    "      punchy = {\n",
    1,
)


# --------------------------------------------------------------------------- the rig
class Rig:
    """A sandboxed invocation of apply-nebula-relay.sh.

    Knobs are files under `self.state`, read by the shims at the moment they run, so a
    test can make the third `systemctl` call behave differently from the first.
    """

    def __init__(self, tmp_path: Path, config_text: str = CONFIG_NIX):
        self.root = tmp_path
        self.state = tmp_path / "state"
        self.bin = tmp_path / "bin"
        self.tmpdir = tmp_path / "tmp"
        self.etc = tmp_path / "etc"
        for d in (self.state, self.bin, self.tmpdir, self.etc):
            d.mkdir(parents=True, exist_ok=True)

        self.cfg = self.etc / "configuration.nix"
        self.cfg.write_text(config_text)
        self.original_cfg_text = config_text

        # The rendered YAML the fake nebula process points at. Starts in the state this
        # whole exercise exists to detect: `relays: []`.
        self.yml = self.state / "nebula.yml"
        self.write_yml(relays=[])

        self.procs: list[subprocess.Popen] = []
        self.set("mesh_ip", MESH_IP)
        self.set("active", "active")
        self.set("rebuild_test_rc", "0")
        self.set("rebuild_switch_rc", "0")
        self.set("instantiate_rc", "0")
        self.set("render", "1")          # does a rebuild re-render the yml?
        self.set("deferred_restart", "0")  # simulate "unit changed, process did not"
        self.set("restart_fixes", "1")     # does `systemctl restart` resolve it?
        self.set("break_config", "0")      # make the running -config unreadable
        self.set("delete_backup_on_rebuild", "0")
        (self.state / "rebuild.log").write_text("")
        (self.state / "systemctl.log").write_text("")

        self._write_shims()
        self.spawn_nebula(self.yml)

    # ---- state helpers
    def set(self, key: str, value: str) -> None:
        (self.state / key).write_text(value)

    def get(self, key: str) -> str:
        p = self.state / key
        return p.read_text() if p.exists() else ""

    def log(self, name: str) -> list[str]:
        text = (self.state / f"{name}.log").read_text()
        return [ln for ln in text.splitlines() if ln.strip()]

    def write_yml(self, relays: list[str], path: Path | None = None) -> Path:
        path = path or self.yml
        body = (
            "lighthouse:\n"
            "  am_lighthouse: false\n"
            "  hosts:\n"
            "  - 10.42.0.1\n"
            "  - 10.42.0.2\n"
            "relay:\n"
            "  am_relay: false\n"
        )
        if relays:
            body += "  relays:\n" + "".join(f"  - {r}\n" for r in relays)
        else:
            body += "  relays: []\n"
        body += "  use_relays: true\ntun:\n  dev: nebula.mesh\n"
        path.write_text(body)
        return path

    # ---- the fake running nebula
    def spawn_nebula(self, config_path: Path) -> None:
        """A real process whose /proc/<pid>/cmdline carries `-config <path>`.

        The verifier reads exactly that, so this is the honest way to fake "what nebula
        loaded" — a stubbed verifier would test the stub instead.
        """
        p = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(600)",
             "-config", str(config_path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.procs.append(p)
        self.set("pid", str(p.pid))
        self.set("unit_config", str(config_path))
        # Wait for /proc/<pid>/cmdline to be readable; the shim reports the pid the
        # instant the script asks, and a race here would look like `no-mainpid`.
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                if b"-config" in Path(f"/proc/{p.pid}/cmdline").read_bytes():
                    return
            except OSError:
                pass
            time.sleep(0.01)
        raise RuntimeError("fake nebula process never became readable")

    def teardown(self) -> None:
        for p in self.procs:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait(timeout=5)

    # ---- shims
    def _write_shims(self) -> None:
        S = str(self.state)
        py = sys.executable

        def sh(name: str, body: str) -> None:
            write_exec(self.bin / name, body)

        sh("id", '''
# `id -u` -> 0 so the root guard passes without sudo. The script calls it exactly once,
# with -u; anything else is a change worth failing on rather than guessing at.
if [ "$1" = "-u" ]; then echo 0; exit 0; fi
echo "id shim: unexpected args: $*" >&2
exit 64
''')

        sh("ip", f'''
# `ip -4 -o addr show <iface>` -> one line in real `-o` layout; $4 is the CIDR.
# POSIX sh has no ${{@: -1}}, so the last argument is taken by walking "$@".
for a in "$@"; do iface="$a"; done
echo "3: $iface    inet $(cat {S}/mesh_ip)/24 scope global $iface\\\\       valid_lft forever preferred_lft forever"
''')

        sh("systemctl", f'''
echo "$*" >> {S}/systemctl.log
case "$1" in
  cat)
    # Base unit then a drop-in, as the real `systemctl cat` prints them.
    echo "# /etc/systemd/system/$2"
    echo "[Service]"
    echo "ExecStart=/nix/store/deadbeef-nebula/bin/nebula -config $(cat {S}/unit_config)"
    exit 0 ;;
  is-active)
    a=$(cat {S}/active)
    if [ "$2" = "-q" ]; then [ "$a" = "active" ] && exit 0 || exit 3; fi
    echo "$a"; [ "$a" = "active" ] && exit 0 || exit 3 ;;
  show)
    cat {S}/pid; exit 0 ;;
  restart)
    # A restart puts the running process onto whatever the unit now points at --
    # unless the test is simulating a restart that does not help.
    [ "$(cat {S}/restart_fixes)" = "1" ] || exit 0
    old=$(cat {S}/pid)
    newcfg=$(cat {S}/unit_config)
    {py} -c 'import time; time.sleep(600)' -config "$newcfg" >/dev/null 2>&1 &
    echo $! > {S}/pid
    # Give /proc a moment, then retire the previous fake by its resolved PID only.
    for _ in $(seq 1 200); do
      [ -r "/proc/$(cat {S}/pid)/cmdline" ] && break
      sleep 0.01
    done
    [ -n "$old" ] && kill "$old" 2>/dev/null
    exit 0 ;;
esac
exit 0
''')

        sh("nixos-rebuild", f'''
echo "$1" >> {S}/rebuild.log
if [ "$(cat {S}/delete_backup_on_rebuild)" = "1" ]; then
  rm -f "$NEBULA_CFG".bak-nebula-relay-*
fi
case "$1" in
  test)   rc=$(cat {S}/rebuild_test_rc) ;;
  switch) rc=$(cat {S}/rebuild_switch_rc) ;;
  *)      rc=0 ;;
esac
if [ "$rc" != "0" ]; then
  echo "nixos-rebuild $1: simulated failure" >&2
  exit "$rc"
fi
# A successful rebuild renders the nebula config and restarts the unit onto it.
if [ "$(cat {S}/render)" = "1" ]; then
  {py} {S}/render.py "$NEBULA_CFG" "$(cat {S}/pid)"
fi
exit 0
''')

        sh("nix-instantiate", f'''
rc=$(cat {S}/instantiate_rc)
[ "$rc" = "0" ] || {{ echo "nix-instantiate: simulated parse failure" >&2; exit "$rc"; }}
exit 0
''')

        # The renderer: .nix -> rendered nebula yaml, and (unless a deferred restart is
        # being simulated) point the running process at the new file.
        (self.state / "render.py").write_text(f'''\
import os, re, subprocess, sys, time
STATE = {S!r}
cfg = open(sys.argv[1]).read()
m = re.search(r'relays = \\[ "([^"]+)" \\];', cfg)
relays = [m.group(1)] if m else []
body = ("lighthouse:\\n  am_lighthouse: false\\n  hosts:\\n  - 10.42.0.1\\n"
        "  - 10.42.0.2\\nrelay:\\n  am_relay: false\\n")
body += ("  relays:\\n" + "".join("  - %s\\n" % r for r in relays)) if relays else "  relays: []\\n"
body += "  use_relays: true\\ntun:\\n  dev: nebula.mesh\\n"
n = int(open(os.path.join(STATE, "gen")).read()) + 1 if os.path.exists(os.path.join(STATE, "gen")) else 1
open(os.path.join(STATE, "gen"), "w").write(str(n))
new = os.path.join(STATE, "nebula-gen%d.yml" % n)
open(new, "w").write(body)
open(os.path.join(STATE, "unit_config"), "w").write(new)
if open(os.path.join(STATE, "deferred_restart")).read().strip() == "1":
    sys.exit(0)          # unit points at the new file; the process does not. rc 2.
old = open(os.path.join(STATE, "pid")).read().strip()
p = subprocess.Popen([{py!r}, "-c", "import time; time.sleep(600)", "-config", new],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
open(os.path.join(STATE, "pid"), "w").write(str(p.pid))
for _ in range(500):
    try:
        if b"-config" in open("/proc/%d/cmdline" % p.pid, "rb").read():
            break
    except OSError:
        pass
    time.sleep(0.01)
if old:
    try:
        os.kill(int(old), 15)
    except (OSError, ValueError):
        pass
if open(os.path.join(STATE, "break_config")).read().strip() == "1":
    os.unlink(new)       # the process runs on; its -config path is now unreadable
''')

    # ---- running it
    def run(self, net: str = "mesh", cfg: Path | None = None,
            extra_env: dict | None = None, timeout: int = 120):
        env = dict(os.environ)
        env["PATH"] = f"{self.bin}:{env.get('PATH', '')}"
        env["TMPDIR"] = str(self.tmpdir)
        env["NEBULA_NET"] = net
        env["NEBULA_RELAY"] = RELAY
        env["NEBULA_EXPECT_MESH_IP"] = MESH_IP
        env["NEBULA_CFG"] = str(cfg or self.cfg)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["bash", str(APPLY)], env=env, capture_output=True, text=True,
            timeout=timeout, cwd=str(self.root),
        )


@pytest.fixture()
def rig(tmp_path):
    r = Rig(tmp_path)
    try:
        yield r
    finally:
        r.teardown()


# ------------------------------------------------------------------ the happy path
def test_happy_path_patches_activates_then_persists(rig):
    """test → verify → switch → verify, in that order, and the file ends up patched."""
    r = rig.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "=== DONE ===" in r.stdout

    # F-A: the ORDER is the fix. `test` first, `switch` only after a verify.
    assert rig.log("rebuild") == ["test", "switch"], r.stdout
    assert r.stdout.index("== nixos-rebuild test") < r.stdout.index("== verify (activated)")
    assert r.stdout.index("== verify (activated)") < r.stdout.index("== nixos-rebuild switch")
    assert r.stdout.index("== nixos-rebuild switch") < r.stdout.index("== verify (persisted)")

    text = rig.cfg.read_text()
    assert INSERTED in text
    assert text == rig.original_cfg_text.replace(
        "    settings = {\n      punchy = {\n",
        "    settings = {\n" + INSERTED + "      punchy = {\n", 1)

    backups = list(rig.etc.glob("configuration.nix.bak-nebula-relay-*"))
    assert len(backups) == 1
    assert backups[0].read_text() == rig.original_cfg_text
    # No temp file left beside the config.
    assert not list(rig.etc.glob("configuration.nix.new.*"))


def test_rerun_when_already_satisfied_writes_nothing(rig):
    rig.write_yml(relays=[RELAY])
    r = rig.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ALREADY SATISFIED" in r.stdout
    assert rig.cfg.read_text() == rig.original_cfg_text
    assert rig.log("rebuild") == []
    assert list(rig.etc.glob("configuration.nix.bak-nebula-relay-*")) == []


def test_wrong_host_aborts_before_any_write(rig):
    rig.set("mesh_ip", "10.42.0.77")
    r = rig.run()
    assert r.returncode == 1
    assert "WRONG HOST" in r.stderr
    assert rig.cfg.read_text() == rig.original_cfg_text
    assert rig.log("rebuild") == []


def test_config_already_carrying_a_relays_list_is_refused(tmp_path):
    r = Rig(tmp_path, config_text=CONFIG_NIX_WITH_RELAYS)
    try:
        res = r.run()
        assert res.returncode == 1
        assert "already contains a `relays = [` line" in res.stderr
        assert "will not merge lists" in res.stderr
        assert r.cfg.read_text() == r.original_cfg_text
        assert r.log("rebuild") == []
    finally:
        r.teardown()


# ------------------------------------------------------------------------ F-A cases
def test_rebuild_test_failure_persists_nothing(rig):
    """The F-A case. `test` fails ⇒ no profile generation, no bootloader, and the
    message says exactly that — never the old, false "never switched" claim."""
    rig.set("rebuild_test_rc", "1")
    r = rig.run()
    assert r.returncode != 0
    assert rig.log("rebuild") == ["test"], "switch must not be reached"
    assert rig.cfg.read_text() == rig.original_cfg_text, "the config must be restored"
    assert "ROLLED BACK" in r.stderr
    assert "NOT PERSISTED" in r.stderr
    assert "registers no profile generation" in r.stderr
    assert "THE PROFILE MAY HAVE MOVED" not in r.stderr


def test_verifier_failure_after_a_good_test_says_activated_not_persisted(rig):
    """`test` activates, but the relay is not visible ⇒ activated, nothing persisted."""
    rig.set("render", "0")      # rebuild "succeeds" but nebula never gains the relay
    r = rig.run()
    assert r.returncode != 0
    assert rig.log("rebuild") == ["test"]
    assert rig.cfg.read_text() == rig.original_cfg_text
    assert "ACTIVATED, NOT PERSISTED" in r.stderr
    assert "does not touch\n   the bootloader" in r.stderr or "bootloader" in r.stderr
    assert "THE PROFILE MAY HAVE MOVED" not in r.stderr


def test_switch_failure_after_a_good_test_does_not_claim_nothing_persisted(rig):
    """🔴 The exact claim F-A was about: after a `switch` attempt the script must NOT
    say nothing was persisted, because switch moves the profile BEFORE it activates."""
    rig.set("rebuild_switch_rc", "1")
    r = rig.run()
    assert r.returncode != 0
    assert rig.log("rebuild") == ["test", "switch"]
    assert rig.cfg.read_text() == rig.original_cfg_text
    assert "THE PROFILE MAY HAVE MOVED" in r.stderr
    assert "readlink /nix/var/nix/profiles/system" in r.stderr
    for false_claim in ("NEVER ACTIVATED", "nothing is running the change",
                        "ACTIVATED, NOT PERSISTED"):
        assert false_claim not in r.stderr, false_claim


def test_deferred_restart_is_retried(rig):
    """The narrow rc-2 retry (F-I): the unit points at a new config the process has not
    loaded. That, and only that, earns a restart.

    Two restarts is the CORRECT count here, not a bug: there are two verify passes
    (after `test` and after `switch`), each rebuild re-renders and defers, and each pass
    retries once. The "at most once" half is pinned by the next test.
    """
    rig.set("deferred_restart", "1")
    r = rig.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "the running process has not picked up the new config" in r.stdout
    restarts = [ln for ln in rig.log("systemctl") if ln.startswith("restart")]
    assert len(restarts) == 2, restarts


def test_the_retry_is_not_repeated_when_it_does_not_help(rig):
    """One retry only. If the restart does not resolve the disagreement, the run fails
    rather than restarting the mesh again."""
    rig.set("deferred_restart", "1")
    rig.set("restart_fixes", "0")
    r = rig.run()
    assert r.returncode != 0
    restarts = [ln for ln in rig.log("systemctl") if ln.startswith("restart")]
    assert len(restarts) == 1, restarts
    assert rig.log("rebuild") == ["test"], "switch must not be reached"
    assert rig.cfg.read_text() == rig.original_cfg_text
    assert "ACTIVATED, NOT PERSISTED" in r.stderr


def test_a_different_rc2_does_not_earn_a_restart(rig):
    """F-I. An UNREADABLE `-config` is also rc 2 (`REASON: no-config-arg`), and a
    restart does nothing for it — it just drops every mesh session. The old code
    retried on ANY rc 2.

    The unit stays ACTIVE here on purpose: an inactive unit is rejected by apply's own
    `is-active` check before the verifier ever runs, so that variant could not reach
    the branch under test at all.
    """
    rig.set("break_config", "1")
    r = rig.run()
    assert r.returncode != 0
    assert "REASON: no-config-arg" in r.stdout, r.stdout
    assert [ln for ln in rig.log("systemctl") if ln.startswith("restart")] == [], \
        "an unreadable -config must NOT earn a restart"
    assert "the running process has not picked up the new config" not in r.stdout


def test_an_inactive_unit_is_rejected_in_the_preflight(rig):
    """An inactive unit never reaches the retry branch at all: the PREFLIGHT verifier
    call returns rc 2 first and the script aborts before touching $CFG. That is why the
    F-I test above uses an unreadable `-config` instead of an inactive unit."""
    rig.set("active", "failed")
    r = rig.run()
    assert r.returncode != 0
    assert "the verifier could not read the current config (rc=2)" in r.stderr
    assert "REASON: unit-inactive" in r.stdout
    assert rig.cfg.read_text() == rig.original_cfg_text
    assert rig.log("rebuild") == []
    assert [ln for ln in rig.log("systemctl") if ln.startswith("restart")] == []


# ------------------------------------------------------------------------ F-D
def test_missing_backup_fails_loudly_and_says_the_config_is_still_patched(rig):
    rig.set("rebuild_test_rc", "1")
    rig.set("delete_backup_on_rebuild", "1")
    r = rig.run()
    assert r.returncode != 0
    assert "ROLLBACK FAILED — your config is still patched at" in r.stderr
    assert str(rig.cfg) in r.stderr
    assert "is GONE, so there is nothing to restore from" in r.stderr
    # The manual fix must be spelled out, and the claim must match reality:
    assert INSERTED.strip().splitlines()[0].strip() in r.stderr
    assert INSERTED in rig.cfg.read_text(), "the message says still patched; it must be"
    assert "ROLLED BACK" not in r.stderr


# ------------------------------------------------------------------------ F-E
def test_symlinked_config_is_refused_and_survives(rig):
    real = rig.root / "repo" / "configuration.nix"
    real.parent.mkdir()
    shutil.copy(rig.cfg, real)
    link = rig.root / "etc-link" / "configuration.nix"
    link.parent.mkdir()
    link.symlink_to(real)

    r = rig.run(cfg=link)
    # The finding first: the symlink was REPLACED by a regular file, silently.
    assert link.is_symlink(), "the symlink must still be a symlink"
    assert real.read_text() == rig.original_cfg_text
    assert r.returncode == 1
    assert "is a symlink" in r.stderr
    assert str(real) in r.stderr
    assert rig.log("rebuild") == []


def test_a_symlinked_directory_component_is_refused_too(rig):
    """`[ -L $CFG ]` only inspects the last component; the check is `readlink -f`."""
    realdir = rig.root / "repo2"
    realdir.mkdir()
    shutil.copy(rig.cfg, realdir / "configuration.nix")
    linkdir = rig.root / "etc-link2"
    linkdir.symlink_to(realdir)

    r = rig.run(cfg=linkdir / "configuration.nix")
    assert r.returncode == 1
    assert "is a symlink (or sits under one)" in r.stderr
    assert (realdir / "configuration.nix").read_text() == rig.original_cfg_text


# ------------------------------------------------------------------------ F-B
def test_net_without_the_anchor_does_not_reach_into_another_network(rig):
    """NEBULA_NET=travel reported success while patching MESH. It must abort now, and
    the mesh block must be byte-identical afterwards."""
    r = rig.run(net="travel")
    # The anchor guard's OWN error, not just "it aborted": the abort must come from the
    # scoped anchor count, and it must name the network that was asked for.
    assert "cannot locate exactly one nebula settings block to patch inside" in r.stderr, \
        r.stdout + r.stderr
    assert "services.nebula.networks.travel" in r.stderr
    assert "0 match(es) inside services.nebula.networks.travel" in r.stderr
    assert r.returncode == 1, r.stdout + r.stderr
    assert rig.cfg.read_text() == rig.original_cfg_text
    assert rig.log("rebuild") == []
    assert "DONE" not in r.stdout


def test_the_insertion_lands_in_the_named_network_not_the_first_anchor(tmp_path):
    """F-B, the WRITE half. Two networks carry the anchor pair and `travel` comes first.
    The four lines must land in `mesh`, which is the one that was named."""
    r = Rig(tmp_path, config_text=CONFIG_NIX_TWO_ANCHORS)
    try:
        res = r.run(net="mesh")
        assert res.returncode == 0, res.stdout + res.stderr
        text = r.cfg.read_text()
        assert text.count(INSERTED) == 1, text

        mesh_at = text.index("services.nebula.networks.mesh = {")
        travel_at = text.index("services.nebula.networks.travel = {")
        relay_at = text.index(INSERTED)
        assert travel_at < mesh_at, "fixture: travel must come first"
        assert relay_at > mesh_at, (
            "the relay block landed in the FIRST anchor (travel), not in mesh")
    finally:
        r.teardown()


def test_unknown_network_aborts(rig):
    r = rig.run(net="nosuchnet")
    # The locator's OWN message. Without this line the test passes even when the
    # `len(starts) != 1` guard is deleted, because the IndexError that follows aborts
    # with a message that happens to name the network too — a green for the wrong
    # reason, measured.
    assert "found 0 line(s) matching" in r.stderr, r.stdout + r.stderr
    assert "expected exactly 1" in r.stderr
    assert r.returncode == 1
    assert "services.nebula.networks.nosuchnet" in r.stderr
    assert rig.cfg.read_text() == rig.original_cfg_text
    assert rig.log("rebuild") == []


def test_the_located_block_is_the_named_network(rig):
    """The range printed in the preflight must be the mesh block, not the whole file."""
    r = rig.run()
    assert r.returncode == 0, r.stdout + r.stderr
    m = re.search(r"net block : services\.nebula\.networks\.mesh = lines (\d+)-(\d+)",
                  r.stdout)
    assert m, r.stdout
    start, end = int(m.group(1)), int(m.group(2))
    lines = rig.original_cfg_text.splitlines()
    assert lines[start - 1].strip() == "services.nebula.networks.mesh = {"
    assert lines[end - 1].strip() == "};"
    # It must NOT swallow the travel network that follows.
    travel = next(i for i, l in enumerate(lines, 1)
                  if "services.nebula.networks.travel" in l)
    assert end < travel, (start, end, travel)


# ------------------------------------------------------------------------ F-C
def test_no_predictable_tmp_path_is_live_while_the_verifier_runs(rig, tmp_path):
    """The old code ran the verifier with its stdout redirected to
    `/tmp/nebula-relay-pre.$$` — world-writable directory, predictable name, `>`
    follows symlinks, running as root.

    This observes the filesystem AT THE MOMENT the verifier runs (from inside a wrapper
    the script itself invokes), which is the only window in which that file existed.
    """
    # Wrap the real verifier so it snapshots /tmp before doing its job.
    snap = rig.state / "tmp-snapshot.txt"
    real_check = rig.root / "nixsys" / "check-nebula-relays.sh"
    real_check.parent.mkdir()
    shutil.copy(CHECK, real_check)
    wrapper_dir = rig.root / "nixsys"
    apply_copy = wrapper_dir / "apply-nebula-relay.sh"
    shutil.copy(APPLY, apply_copy)
    probe = write_exec(
        wrapper_dir / "probe-check.sh",
        f"echo '=== TMP' >> {snap}\n"
        f"ls -A /tmp >> {snap} 2>/dev/null\n"
        f"echo '=== TMPDIR' >> {snap}\n"
        f'ls -la "${{TMPDIR:-/tmp}}" >> {snap} 2>/dev/null\n'
        f'exec bash {real_check} "$@"\n'
    )

    # 🔴 Only entries this run CREATES count. A previous mutation-battery run leaves
    # `/tmp/nebula-relay-pre.<pid>` behind (the mutant has no cleanup), and without this
    # subtraction that residue fails every later run of this test — a stale artifact
    # reported as a live defect.
    before = set(os.listdir("/tmp"))
    # Point the copy of apply at the probe instead of the verifier next to it.
    txt = apply_copy.read_text().replace(
        'CHECK="${HERE}/check-nebula-relays.sh"',
        'CHECK="${HERE}/probe-check.sh"', 1)
    assert "probe-check.sh" in txt
    apply_copy.write_text(txt)

    env = dict(os.environ)
    env["PATH"] = f"{rig.bin}:{env.get('PATH', '')}"
    env["TMPDIR"] = str(rig.tmpdir)
    env.update(NEBULA_NET="mesh", NEBULA_RELAY=RELAY,
               NEBULA_EXPECT_MESH_IP=MESH_IP, NEBULA_CFG=str(rig.cfg))
    res = subprocess.run(["bash", str(apply_copy)], env=env, capture_output=True,
                         text=True, timeout=120, cwd=str(rig.root))
    assert res.returncode == 0, res.stdout + res.stderr

    observed = snap.read_text()
    assert observed.strip(), "the probe never ran — this test would pass vacuously"

    tmp_seen: set[str] = set()
    tmpdir_lines: list[str] = []
    section = None
    for line in observed.splitlines():
        if line == "=== TMP":
            section = "tmp"; continue
        if line == "=== TMPDIR":
            section = "tmpdir"; continue
        if section == "tmp":
            tmp_seen.add(line)
        elif section == "tmpdir":
            tmpdir_lines.append(line)

    # POSITIVE CONTROL for the probe itself: it must have observed a populated /tmp.
    # A probe wired to nothing would give an empty set and a reassuring zero below.
    assert len(tmp_seen) > 1, sorted(tmp_seen)

    created = tmp_seen - before
    offenders = [n for n in created if n.startswith("nebula-relay-pre")]
    assert not offenders, (
        "a predictable /tmp path was live while the verifier ran: %s" % offenders)

    # And the scratch it DOES use is a 0700 mktemp -d under TMPDIR.
    joined = "\n".join(tmpdir_lines)
    assert re.search(r"nebula-relay\.\w{8}", joined), joined[:2000]
    for line in tmpdir_lines:
        if "nebula-relay." in line and line.startswith("d"):
            assert line.startswith("drwx------"), line


def test_scratch_is_removed_on_success(rig):
    r = rig.run()
    assert r.returncode == 0
    assert list(rig.tmpdir.iterdir()) == [], list(rig.tmpdir.iterdir())


def test_scratch_is_removed_on_failure(rig):
    rig.set("rebuild_test_rc", "1")
    r = rig.run()
    assert r.returncode != 0
    assert list(rig.tmpdir.iterdir()) == [], list(rig.tmpdir.iterdir())
    assert not list(rig.etc.glob("configuration.nix.new.*"))


def test_an_invalid_nix_result_aborts_before_the_backup_and_leaves_no_temp(rig):
    """The temp lives NEXT TO $CFG so the final mv is atomic, which means a failure
    between creating it and moving it must not leave it there. This is the only path
    that fails while it still exists."""
    rig.set("instantiate_rc", "1")
    r = rig.run()
    assert not list(rig.etc.glob("configuration.nix.new.*")), "temp sibling leaked"
    assert "not valid Nix" in r.stderr
    assert r.returncode == 1
    assert rig.cfg.read_text() == rig.original_cfg_text
    assert list(rig.etc.glob("configuration.nix.bak-nebula-relay-*")) == [], \
        "the backup must not be taken before the parse check passes"
    assert rig.log("rebuild") == []


# ------------------------------------------------------------------------ F-G
def test_the_verifiers_fail_output_reaches_the_operator(rig):
    """The egress-cost paragraph exists to be read at the moment of choosing. The old
    code captured the verifier's rc-1 output and deleted it unread."""
    r = rig.run()
    assert r.returncode == 0, r.stdout + r.stderr
    # The substantive claim first: the cost warning itself reached stdout.
    assert "EGRESSES THE RELAY" in r.stdout
    assert "puts every relayed byte on that bill" in r.stdout
    assert "FAIL: relays is EMPTY" in r.stdout
    assert "the verifier's finding, in full" in r.stdout


def test_the_verifier_really_emits_that_warning_on_rc_1(rig):
    """The seam: the test above asserts apply PRINTS what the verifier said. This one
    asserts the verifier SAYS it. Neither claim implies the other."""
    env = dict(os.environ)
    env["PATH"] = f"{rig.bin}:{env.get('PATH', '')}"
    env["NEBULA_NET"] = "mesh"
    res = subprocess.run(["bash", str(CHECK), RELAY], env=env, capture_output=True,
                         text=True, timeout=60)
    assert res.returncode == 1, res.stdout + res.stderr
    assert "EGRESSES THE RELAY" in res.stdout
    assert "APPLYING THIS RESTARTS THE MESH" in res.stdout


# --------------------------------------------------------- the verifier's own controls
def test_check_script_self_test_passes():
    """F-H: the verifier's `--self-test` had no repo-gate coverage at all. It does now."""
    res = subprocess.run(["bash", str(CHECK), "--self-test"], capture_output=True,
                         text=True, timeout=120)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "terminator: later top-level am_relay is OUTSIDE -> ok" in res.stdout
    assert "FAILED" not in res.stdout


def test_reason_token_ledger_is_pinned_and_apply_branches_on_a_real_one():
    """🔴 A SEAM GUARD, not a component guard. apply-nebula-relay.sh narrows its retry
    by matching `REASON: unit-process-disagree` in the verifier's output. That is a
    relationship between two files, so it is pinned from BOTH sides: the full set of
    tokens the verifier can emit, and the one apply tests for being a member of it.
    It fails if the set GROWS (a new rc-2 reason nobody classified) or SHRINKS (a token
    apply still greps for that can no longer be produced).
    """
    check_src = CHECK.read_text()
    emitted = set(re.findall(r"^\s*cannot_determine ([a-z-]+)", check_src, re.M))
    assert emitted == {
        "unit-not-loaded", "unit-inactive", "no-mainpid", "no-config-arg",
        "unit-process-disagree", "self-test-failed", "parse-failed",
    }, sorted(emitted)

    apply_src = APPLY.read_text()
    grepped = set(re.findall(r"REASON: ([a-z-]+)", apply_src))
    assert grepped, "apply no longer branches on any REASON token"
    assert grepped <= emitted, sorted(grepped - emitted)
    assert "unit-process-disagree" in grepped


def test_apply_declares_every_tool_it_execs():
    """The preflight's job is to abort BEFORE the first write. A tool missing from its
    list fails half-way through instead."""
    src = APPLY.read_text()
    m = re.search(r"^for t in (.*?); do$", src, re.M | re.S)
    assert m, "the preflight tool loop moved"
    declared = set(m.group(1).replace("\\\n", " ").split())
    for tool in ("awk", "sed", "grep", "diff", "tr", "cut", "wc", "cp", "mv",
                 "date", "mktemp", "readlink", "systemctl", "ip",
                 "nixos-rebuild", "nix-instantiate", "python3"):
        assert tool in declared, tool


def test_both_scripts_warn_that_applying_restarts_the_mesh():
    for path in (APPLY, CHECK):
        src = path.read_text()
        assert "RESTARTS THE MESH" in src or "restarts the mesh" in src.lower(), path
        assert "drop" in src.lower(), path


def test_no_predictable_tmp_literal_survives_in_the_source():
    """A structural companion to the behavioural F-C test above: the old shape was a
    literal `/tmp/<name>.$$`. This is walkable by rewording, which is why it is the
    SECOND check and not the only one."""
    src = APPLY.read_text()
    assert not re.search(r">\s*/tmp/", src), src
    assert "mktemp -d" in src


def test_scripts_are_executable_and_pass_bash_n():
    for path in (APPLY, CHECK):
        assert os.stat(path).st_mode & stat.S_IXUSR, path
        res = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
        assert res.returncode == 0, res.stderr
