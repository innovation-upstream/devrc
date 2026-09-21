"""Tests for the LAPTOP's host-AirVPN stack: status writer + roaming killswitch.

The laptop now runs its OWN host AirVPN tunnel (default-off, roaming mode) and
its own `i3status-airvpn` pill. Three pieces:

  scripts/airvpn-status-poll        the 60s cache writer (loads bar-status-poll
                                    by path and runs ITS run_source for ONE
                                    source — no second copy of the writer)
  scripts/airvpn-updown `roaming`   the killswitch with a DERIVED LAN
  nix/system/apply-airvpn-laptop.sh the staged, operator-run apply

🔴 THE CONTRACT, in one line: the laptop's pill must render the LAPTOP's
tunnel with the same grammar the workbench's renders — no second copy of a
verdict function, and never a hardcoded LAN on a machine that roams.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from testlib import mockbin

SCRIPTS = Path(__file__).resolve().parents[1]
NIX = SCRIPTS.parent / "nix"
UPDOWN = SCRIPTS / "airvpn-updown"
WRAPPER = SCRIPTS / "airvpn-status-poll"
APPLY = NIX / "system" / "apply-airvpn-laptop.sh"


def _load(name: str, alias: str):
    sys.dont_write_bytecode = True
    path = SCRIPTS / name
    loader = importlib.machinery.SourceFileLoader(alias, str(path))
    spec = importlib.util.spec_from_loader(alias, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# scripts/airvpn-updown — the ROAMING mode, driven with mocked binaries
# ---------------------------------------------------------------------------
#
# The nft ruleset arrives on STDIN of `nft -f -`, so a stub that tees stdin IS
# the assertion surface. Routes are recorded by the `ip` stub.

def _write_stub(bin_dir: Path, name: str, code: str) -> str:
    return str(mockbin.write_exec(bin_dir / name, code))


def _run_updown(tmp_path: Path, mode: str | None, *,
                gw="192.168.4.1", phys="wlan0", lan="192.168.4.0/24",
                fwmark="0xca6c"):
    """Run airvpn-updown `up` with stubbed ip/wg/nft. Returns (nft_ruleset,
    routes) — the ruleset `nft -f -` would load and the routes `ip route
    replace` would install."""
    mockbin.write_exec(tmp_path / "nft", f"""
case "$1 $2" in
  *"-f -"*) cat > {str(tmp_path / "nft-ruleset.txt")} ;;
esac
""")
    mockbin.write_exec(tmp_path / "ip", f"""
case "$1 $2 $3 $4" in
  "-o route show default") echo "default via {gw} dev {phys}" ;;
esac
case "$1 $2" in
  "-o -4") echo "{lan} dev {phys} scope link" ;;
esac
if [ "$1 $2" = "route replace" ]; then
  printf '%s\\n' "$3 $4 $5 $6 $7" >> {str(tmp_path / "ip-routes.txt")}
fi
""")
    mockbin.write_exec(tmp_path / "wg", f"""
if [ "$1" = "show" ] && [ "$3" = "endpoints" ]; then echo "peer  203.0.113.7:1637";
elif [ "$1" = "show" ]; then echo "{fwmark}"; fi
""")
    mockbin.write_exec(tmp_path / "logger", ":")

    argv = [str(UPDOWN), "up", "airvpn"] + ([mode] if mode else [])
    # the stubs FIRST, then the HOST dirs holding the real tools the script
    # itself needs (bash for its shebang, grep/awk/sort for the derivations).
    # Assembled from `which`, never a hardcoded store/system path — this suite
    # also runs in the nix build sandbox, where neither /run/current-system
    # nor /usr/bin has them. Only ip/wg/nft/logger are stubbed; everything
    # else runs real.
    dirs: list[str] = [str(tmp_path)]
    for tool in ("bash", "grep", "awk", "head", "sort", "basename", "id",
                 "cat", "sed"):
        w = shutil.which(tool)
        if w:
            d = os.path.dirname(w)
            if d not in dirs:
                dirs.append(d)
    env = dict(os.environ, PATH=":".join(dirs))
    proc = subprocess.run(argv, capture_output=True, text=True, env=env,
                          timeout=30)
    assert proc.returncode == 0, proc.stderr
    rules = (tmp_path / "nft-ruleset.txt").read_text()
    rf = tmp_path / "ip-routes.txt"
    routes = [ln.split() for ln in rf.read_text().splitlines() if ln.strip()] \
        if rf.exists() else []
    return rules, routes


def test_workbench_mode_is_unchanged_hardcoded_LAN():
    """The regression guard for the WORKBENCH: no mode arg -> the hardcoded
    home-LAN ruleset, byte-shape as before. The roaming port must not have
    moved the workbench's behaviour."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        rules, routes = _run_updown(tmp, None)
        assert "ip daddr 192.168.50.0/24 accept" in rules
        assert "oifname !=" in rules                       # the uplink guard
        joined = {" ".join(r) for r in routes}
        assert any(r.startswith("192.168.50.1") for r in joined), joined


def test_roaming_mode_derives_the_LAN_from_the_uplink():
    """🔴 THE POINT OF ROAMING: no hardcoded 192.168.50.0/24 anywhere in the
    ruleset or routes — the allowed LAN is the one the uplink actually has."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        rules, routes = _run_updown(tmp, "roaming")
        assert "192.168.50.0/24" not in rules and "192.168.50.1" not in rules
        assert "ip daddr 192.168.4.0/24 accept" in rules, rules
        joined = {" ".join(r) for r in routes}
        assert not any(r.startswith("192.168.50.") for r in joined), joined
        # the endpoint bypass still routes via the original gateway
        assert any(r.startswith("203.0.113.7") and "192.168.4.1" in r
                   for r in joined), joined
        # the lighthouse bypass survives in both modes
        assert any(r.startswith("5.161.118.55") for r in joined), joined


def test_roaming_without_a_derivable_subnet_installs_NO_LAN_rule():
    """🔴 Fail-closed degradation: an uplink with no connected /24 (rare) must
    NOT fall back to the hardcoded home LAN — it drops the LAN rule entirely;
    the gateway rule (which allows router DNS/DHCP) still arms."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # an /32 scope-link only — nothing derivable
        rules, _ = _run_updown(tmp, "roaming", lan="10.9.8.7/32")
        assert "192.168.50.0/24" not in rules
        assert "ip daddr 10.9.8.7/32" not in rules, rules


def test_the_apply_script_wires_the_ROAMING_hooks():
    """The laptop conf must carry the mode argument, or the killswitch silently
    polices a LAN that does not exist and allows the home LAN that does not."""
    text = APPLY.read_text()
    assert "up %i roaming" in text and "down %i roaming" in text
    # and it must REWRITE non-roaming hooks, not just skip (idempotency path)
    assert "airvpn-updown up %i roaming" in text
    assert "roaming" in text.split("sed -i")[1].split("\n")[0], \
        "the rewrite sed must target the mode argument"


# ---------------------------------------------------------------------------
# scripts/airvpn-status-poll — the laptop's cache writer
# ---------------------------------------------------------------------------

def test_the_writer_runs_the_POLLERS_OWN_source_function(tmp_path, monkeypatch):
    """🔴 NO SECOND COPY OF THE WRITER. The wrapper must load bar-status-poll
    by path and call its run_source — pinned by monkeypatching the loaded
    module's run_source and observing the call."""
    fake = _load("bar-status-poll", "_t_fake_poll")
    seen = {}

    def fake_run_source(name, fn):
        seen["name"], seen["fn"] = name, fn
        return {"count": 0, "state": "ok", "ts": 123}

    monkeypatch.setattr(fake, "run_source", fake_run_source)
    wr = _load("airvpn-status-poll", "_t_wr")
    monkeypatch.setattr(wr, "load_poller", lambda: fake)
    rc = wr.main()
    assert rc == 0
    assert seen["name"] == "airvpn"
    assert seen["fn"] is fake.fetch_airvpn, \
        "the writer must call the POLLER's fetch_airvpn, not a copy"


def test_the_writer_survives_a_poller_crash_and_exits_zero(monkeypatch, capsys):
    """A crashed writer must not take the timer down repeating: the cache ages
    past MAX_CACHE_AGE_SECS and the pill renders `?` — the honest shape."""
    wr = _load("airvpn-status-poll", "_t_wr2")

    class Boom:
        # fetch_airvpn exists (the wrapper resolves it before run_source);
        # run_source itself is the thing that crashes.
        fetch_airvpn = lambda *a, **k: {}

        def run_source(self, *_a, **_k):
            raise RuntimeError("kaboom")

    monkeypatch.setattr(wr, "load_poller", lambda: Boom())
    rc = wr.main()
    assert rc == 0
    err = capsys.readouterr().err
    assert "RuntimeError" in err, "the failure must be visible in stderr"


# ---------------------------------------------------------------------------
# the nix deployment: the laptop gets the REAL block, and the writer unit
# ---------------------------------------------------------------------------

def _nix_graphical() -> str:
    return (NIX / "graphical.nix").read_text()


def test_the_laptop_runs_the_REAL_airvpn_block_and_the_workbench_keeps_its():
    nix = _nix_graphical()
    # the block def exists once and rides BOTH host blocks lists
    assert nix.count("airvpnBlock = {") == 1
    blocks = nix[nix.index("  blocks ="):nix.index("lib.mkIf isNixOS")]
    assert "lib.optionals (!isLaptop) [ runawaysBlock ]" in blocks, \
        "runaways must stay workbench-only, WITHOUT airvpn"
    assert "lib.optional isLaptop airvpnBlock" in blocks, \
        "the laptop must run its own real airvpnBlock"
    # and its scripts deploy on BOTH hosts (no mkIf on the airvpn entries)
    for name in ("i3status-airvpn", "airvpn-menu", "airvpn-detail",
                 "data/airvpn-servers.json"):
        m = 'home.file.".config/i3status-rust/scripts/%s"' % name
        i = nix.index(m)
        assert "mkIf" not in nix[i:i + 120], \
            "%s is still host-gated; the laptop needs the real stack" % name


def test_the_laptop_writer_unit_is_islaptop_gated_and_names_its_inputs():
    nix = _nix_graphical()
    assert "systemd.user.services.airvpn-status-poll = lib.mkIf isLaptop" in nix
    assert "systemd.user.timers.airvpn-status-poll = lib.mkIf isLaptop" in nix
    assert "systemd.user.timers.bar-status-poll = lib.mkIf (!isLaptop)" in nix, \
        "the two writers must stay mutually exclusive per host"
    # the writer loads the poller as a SIBLING — both must be reachable from
    # the working tree it runs from (the poller is already tree-executed on
    # the workbench; the laptop resolves the same absolute path).
    src = (SCRIPTS / "airvpn-status-poll").read_text()
    assert "bar-status-poll" in src and "run_source" in src
    assert "airvpn" in src


def test_the_writer_and_the_relay_never_fight_over_airvpn_json():
    """🔴 The laptop runs BOTH bar-remote-pull's install_poller_cache AND the
    airvpn writer into the same directory. The pull must still never write
    airvpn.json — the writer owns that file. (The structural half is pinned in
    test_bar_remote_snapshot::test_a_HOST_LOCAL_source_is_not_carried; this is
    the laptop-side statement of the same contract.)"""
    loader = importlib.machinery.SourceFileLoader(
        "_t_snap", str(SCRIPTS / "bar-remote-snapshot"))
    spec = importlib.util.spec_from_loader("_t_snap", loader)
    snap = importlib.util.module_from_spec(spec)
    loader.exec_module(snap)
    wanted = snap.native_cache_filenames()
    assert "airvpn.json" not in wanted
    assert "i3status-airvpn" in snap.RELAY_BLOCKS, \
        "the wb rollup must still carry the WORKBENCH tunnel's alarms"
