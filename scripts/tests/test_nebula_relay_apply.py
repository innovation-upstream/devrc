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
  F-H  the verifier is invoked through an explicit interpreter, and an rc that is
       NOT one of its own {0,1,2} is reported as "it did not run" rather than as a
       fact about the config -- structural + behavioural, because the structural
       half is the only one visible on the dev-host tier
"""
from __future__ import annotations

import os
import re
import shlex
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
            extra_env: dict | None = None, timeout: int = 120,
            apply: Path | None = None):
        """`apply` runs a COPY of the script from another directory. The script derives
        CHECK from its own location (`HERE`), so a copy sited next to a stub verifier
        exercises the real rc-handling against a chosen exit code -- which is the only
        way to reach codes the real verifier cannot produce."""
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
            ["bash", str(apply or APPLY)], env=env, capture_output=True, text=True,
            timeout=timeout, cwd=str(self.root),
        )

    def apply_beside_stub_verifier(self, rc: int, out: str = "stub verifier output"):
        """A copy of the real apply script in a fresh dir, next to a verifier stub that
        exits `rc`. Returns the path to the copy.

        🔴 The stub goes through `write_exec` like every other shim in this file, NOT a
        hand-written shebang. A `#!/usr/bin/env bash` stub execs fine on the dev host
        and ENOENTs in the sandbox — which would make this test fabricate rc 126 from
        its own stub rather than from the code under test, and pass for the wrong
        reason on one tier while failing on the other. That is the very fault F-H
        exists to pin, so writing it here would be circular."""
        d = self.root / f"sysstub{rc}"
        d.mkdir(exist_ok=True)
        shutil.copy2(APPLY, d / APPLY.name)
        write_exec(d / CHECK.name, f"printf '%s\\n' {shlex.quote(out)}\nexit {rc}\n")
        return d / APPLY.name

    def apply_beside_sequenced_verifier(self, first_rc: int, then_rc: int):
        """Like the above, but the stub answers `first_rc` on its FIRST call and
        `then_rc` on every later one.

        This is the only way to reach the POST-REBUILD verify site. A stub that fails
        at the preflight aborts there and never gets past it — so a single-code stub
        cannot exercise the second call site at all, and the two sites classify their
        rc independently. The post-rebuild one is the consequential half: it runs after
        `nixos-rebuild test` has activated, so what it dies with is what the EXIT trap
        rolls back and what the operator is told the rollback was for."""
        d = self.root / f"sysseq{first_rc}_{then_rc}"
        d.mkdir(exist_ok=True)
        shutil.copy2(APPLY, d / APPLY.name)
        counter = d / "calls"
        write_exec(d / CHECK.name, (
            f'n=$(cat {counter} 2>/dev/null || echo 0)\n'
            f'n=$((n+1)); echo "$n" > {counter}\n'
            f'if [ "$n" = "1" ]; then\n'
            f'  echo "relay not advertised (stub call 1)"; exit {first_rc}\n'
            f'fi\n'
            f'echo "stub call $n"; exit {then_rc}\n'
        ))
        return d / APPLY.name


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
    """The preflight's job is to abort BEFORE the first write.

    ⚠ READ WHAT THIS ACTUALLY CHECKS. It asserts a hard-coded list is a SUBSET of what
    the script declares — it inspects one side of the relationship its name implies. It
    therefore CANNOT catch a tool the script execs but does not declare, which is the
    failure the preflight exists to prevent.

    MEASURED 2026-09-08: `head`, `id` and `rm` are exec'd and undeclared right now, and
    this test is green. (`$BASH` is a fourth omission but a CORRECT one — the
    interpreter is by definition already running, so it needs no `command -v`.)

    Closing it means deriving the exec'd set from the source, which is a real piece of
    work and not this test's current claim. Until then the docstring says what the body
    does, rather than what the name suggests — a guard that reads as coverage while
    providing none is worse than no guard, because it stops anyone looking."""
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


def test_the_verifier_is_never_execed_via_its_own_shebang():
    """F-H, structural half. The verifier MUST be invoked through an explicit
    interpreter, never by executing it and letting its `#!/usr/bin/env bash` shebang
    dispatch -- that makes /usr/bin/env a runtime dependency of this script, and the
    nix build sandbox has no /usr at all.

    WHY IT EXISTED, AND WHY THAT REASON NO LONGER HOLDS. It was written when the only
    other coverage was behavioural-via-the-real-environment: the dev host HAS
    /usr/bin/env, so reverting the fix left the whole file green there and only the
    sandbox tier went red. A source assertion was then the one thing failing on both.

    ⚠ `test_the_verifier_runs_with_its_shebang_BROKEN` below took that job over, and
    took it over BETTER -- it manufactures the missing interpreter itself, so it is
    tier-independent AND spelling-independent. RE-MEASURED at this tree with the helper
    reverted to `run_check() { "$CHECK" "$@"; }`: **2 failed, 37 passed** on the dev
    host, and both failures are these two tests. So the sentence this docstring used to
    carry -- "only the sandbox tier goes red" -- is now FALSE, and it was the whole
    stated justification for keeping this half.

    What this test still earns: it fails FAST (no subprocess, no fixture) and names the
    intended shape in its message, so a revert gets a readable diagnosis instead of "the
    happy path exited 1". That is a real but MODEST reason -- recorded as such rather
    than left reading like the load-bearing guard, which it is not.

    ⚠ THIS IS THE CHEAP HALF AND IT IS SPELLING-BOUND. It asserts the helper exists.
    It does NOT prove the absence of a second, direct call site: an audit measured that
    `"${CHECK}" "$RELAY"`, `$CHECK "$RELAY"`, a line-split call and several other
    spellings all evade a source regex while reintroducing the exact dependency. The
    real guard is `test_the_verifier_runs_with_its_shebang_BROKEN`, which is
    behavioural and cannot be reworded around. This one is kept because it names the
    intended shape and fails fast with a readable message."""
    src = APPLY.read_text()
    assert re.search(r'^run_check\(\)\s*\{\s*"\$BASH"\s+"\$CHECK"', src, re.M), (
        "the run_check helper is gone or no longer invokes the verifier through "
        '"$BASH" -- it must not be executed directly, or /usr/bin/env becomes a '
        "runtime dependency and the sandbox tier goes red")


def test_the_verifier_runs_with_its_shebang_BROKEN(rig):
    """F-H, the LOAD-BEARING half — behavioural, and spelling-independent.

    Runs the whole happy path against a verifier whose shebang points at an
    interpreter that does not exist AND whose exec bit is cleared. If apply invokes it
    through `$BASH` the file is merely READ and none of that matters. If ANY call site
    execs it directly — however it is spelled — the kernel refuses and the run dies.

    🔴 This is what makes the guard un-walkable. Its structural sibling above pins one
    spelling; an audit demonstrated that swapping a single call site to `"${CHECK}"`
    reintroduces the /usr/bin/env dependency while leaving the dev-host suite at 38
    passed. This test fails on that mutant, on every other spelling, and on both tiers
    — because it manufactures the missing-interpreter condition itself instead of
    waiting for a sandbox that happens to lack /usr/bin/env."""
    d = rig.root / "brokenshebang"
    shutil.copytree(_SYSDIR, d)
    chk = d / CHECK.name
    original = chk.read_text()
    assert original.startswith("#!"), "the verifier lost its shebang; this test assumes one"
    chk.write_text("#!/nonexistent/interpreter\n" + original.split("\n", 1)[1])
    chk.chmod(0o644)          # not executable either — belt and braces
    r = rig.run(apply=d / APPLY.name)
    assert r.returncode == 0, (
        "the run died with the verifier's shebang broken, so something still EXECS it "
        "rather than reading it through an explicit interpreter:\n" + r.stdout + r.stderr)
    assert "=== DONE ===" in r.stdout, r.stdout


def test_the_verifiers_exit_codes_are_a_closed_set():
    """F-H's load-bearing precondition. `verifier_answered` in apply-nebula-relay.sh
    treats {0,1,2} as verdicts and EVERYTHING else as "it did not run". If the verifier
    ever grows an `exit 3`, that new verdict would be misreported as an exec fault --
    silently, and in the direction that reads as reassuring ("nothing was determined")
    when something WAS.

    So the two must move together. This fails the moment they diverge."""
    codes = set(re.findall(r'^\s*(?:\|\|\s*)?exit\s+([0-9]+)', CHECK.read_text(), re.M))
    codes |= set(re.findall(r'\|\|\s*exit\s+([0-9]+)', CHECK.read_text()))
    assert codes <= {"0", "1", "2"}, (
        f"check-nebula-relays.sh can now exit {sorted(codes)}, but apply-nebula-relay.sh's "
        "`verifier_answered` still treats only 0/1/2 as answers -- a new code would be "
        "reported as 'the verifier did NOT RUN'. Update both.")
    m = re.search(r'verifier_answered\(\)\s*\{\s*case\s+"\$1"\s+in\s+([0-9|]+)\)',
                  APPLY.read_text())
    assert m, "verifier_answered() moved or was reworded"
    assert set(m.group(1).split("|")) == {"0", "1", "2"}, m.group(1)


@pytest.mark.parametrize("rc,label", [(126, "a directory, or unreadable"),
                                      (127, "vanished"),
                                      (137, "SIGKILL/OOM")])
def test_a_verifier_that_did_not_RUN_is_not_reported_as_a_config_fault(rig, rc, label):
    """F-H, behavioural half. rc outside {0,1,2} is not a verdict -- the verifier never
    reached one. Reporting it as "could not read the current config" asserts something
    about $CFG that is false, and is precisely how the /usr/bin/env fault (rc 126) read
    as a config problem instead of an exec one."""
    r = rig.run(apply=rig.apply_beside_stub_verifier(rc))
    combined = r.stdout + r.stderr
    assert r.returncode != 0, combined
    assert "did NOT RUN" in combined, (
        f"{label}: an rc outside the verifier's own {{0,1,2}} must be reported as "
        f"'did NOT RUN', not as a verdict:\n{combined}")
    assert f"rc={rc}" in combined, (
        f"the abort must name the actual rc ({rc}) it could not interpret:\n{combined}")
    assert "could not read the current config" not in combined, (
        f"{label}: an exec/signal fault is still being blamed on $CFG:\n{combined}")
    # The advice is CONDITIONAL on whether anything was written. Nothing has been at the
    # preflight, so "re-run" is correct here — and must not be the other branch's text.
    assert "Nothing has been written yet" in combined, (
        "nothing has been written at the preflight, so the advice MUST say 'Nothing has "
        "been written yet':\n" + combined)
    assert "DO NOT simply re-run" not in combined, (
        "the post-write branch's advice ('DO NOT simply re-run') reached the preflight, "
        "where nothing has been written:\n" + combined)


@pytest.mark.parametrize("rc", [126, 137])
def test_a_POST_REBUILD_verifier_that_did_not_RUN_does_not_blame_the_mesh(rig, rc):
    """F-H, the consequential half — and the site the PREFLIGHT tests cannot reach.

    The preflight answers 1 (not advertised), so the script patches, runs
    `nixos-rebuild test`, and only THEN gets a non-verifier rc. That `die` is what the
    EXIT trap rolls back, so blaming it on the mesh undoes a change that worked and
    sends the operator to debug a mesh that is very possibly fine.

    🔴 This test exists because a mutation sweep found the gap: widening
    `verifier_answered` to accept 126 was caught only by the STRUCTURAL closed-set
    test, because every behavioural case aborted at the preflight and never executed
    `verifier_answered` at all. A guard that is never reached is not a guard."""
    r = rig.run(apply=rig.apply_beside_sequenced_verifier(first_rc=1, then_rc=rc))
    combined = r.stdout + r.stderr
    assert r.returncode != 0, combined
    assert "did NOT RUN" in combined, (
        "an rc outside the verifier's own {0,1,2} must be reported as 'did NOT RUN', "
        "not as a verdict about the relay:\n" + combined)
    assert f"rc={rc}" in combined, (
        f"the abort must name the actual rc ({rc}) it could not interpret:\n{combined}")
    assert "does not see" not in combined, (
        f"an exec/signal fault at the post-rebuild verify is still reported as the "
        f"relay not being advertised:\n{combined}")
    # 🔴 THE OTHER BRANCH. $CFG has been patched and activated by now, so telling the
    # operator to re-run is actively harmful: the trap rolls the file back while the
    # RUNNING unit still advertises the relay, and a re-run's preflight then asks that
    # unit, prints "ALREADY SATISFIED" and exits 0 over a config that no longer carries
    # the change. An unconditional "idempotent, re-run it" said exactly that, and also
    # contradicted the trap paragraph printed two lines below it.
    assert "DO NOT simply re-run" in combined, (
        "$CFG has been patched by now, so the advice MUST say 'DO NOT simply re-run' "
        "rather than inviting one:\n" + combined)
    assert "Nothing has been written yet" not in combined, (
        "the preflight branch's advice ('Nothing has been written yet') reached a site "
        "where $CFG HAS been written:\n" + combined)


def test_an_INHERITED_PATCHED_cannot_change_the_preflight_advice(rig):
    """The advice branches on $PATCHED, so $PATCHED must come from THIS run.

    The flags used to be declared beside the trap, far below the preflight, so the only
    way to read one early was `${PATCHED:-0}` -- which falls back to an INHERITED
    environment variable. This script's own header documents running it as
    `sudo env "PATH=$PATH" bash ...`, which preserves the caller's environment, so an
    exported PATCHED=1 made the preflight claim work had been done when none had.

    Fail-safe in direction (it over-warns rather than under-warns), but it is a message
    about what the machine's state IS, and getting that from the caller's environment is
    wrong regardless of which way it errs."""
    r = rig.run(apply=rig.apply_beside_stub_verifier(126), extra_env={"PATCHED": "1"})
    combined = r.stdout + r.stderr
    assert r.returncode != 0, combined
    assert "Nothing has been written yet" in combined, (
        "an inherited PATCHED=1 reached the preflight's advice:\n" + combined)
    assert "DO NOT simply re-run" not in combined, combined


def test_a_REAL_verifier_refusal_still_blames_the_config(rig):
    """The other side of the split, so the fix cannot be "call everything an exec
    fault". rc 2 IS one of the verifier's own verdicts -- it really could not read the
    config -- and must keep saying so."""
    r = rig.run(apply=rig.apply_beside_stub_verifier(2))
    combined = r.stdout + r.stderr
    assert r.returncode != 0, combined
    assert "could not read the current config" in combined, (
        "rc 2 is a real verifier refusal and must keep saying so:\n" + combined)
    assert "did NOT RUN" not in combined, (
        "rc 2 IS one of the verifier's own verdicts and must NOT be reported as "
        "'did NOT RUN':\n" + combined)


def test_scripts_are_executable_and_pass_bash_n():
    for path in (APPLY, CHECK):
        assert os.stat(path).st_mode & stat.S_IXUSR, path
        res = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
        assert res.returncode == 0, res.stderr
