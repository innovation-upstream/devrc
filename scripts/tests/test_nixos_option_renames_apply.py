"""Tests for `nix/system/apply-nixos-option-renames-2026-09-09.sh`.

🔴 WHY THIS FILE EXISTS. The script EDITS `/etc/nixos/configuration.nix` and
then runs `nixos-rebuild switch`. Four of its five edits are RENAMES whose whole
claim is "byte-identical output", and the fifth adds a file to `/etc`. A wrong
occurrence, a swallowed comment or a silently-skipped edit is invisible in the
rebuild's own output, so nothing but a test holds the shape.

HERMETIC. Every test drives the script against a FIXTURE config via `NIXOPTS_CFG`
and `NIXOPTS_PORTALS_CONF`, with a shim directory first on PATH holding
`nixos-rebuild` and `dig` (both record/serve instead of doing). There is no path
from this file to a real `nixos-rebuild`, to `/etc/nixos`, or to `/etc/xdg`.

🔴 EVERY ADDRESS IN THE FIXTURE IS FROM TEST-NET-3 (203.0.113.0/24, RFC 5737) OR
RFC 1918. This repo is PUBLIC and `test_no_public_ips.py` rejects a routable
literal in a tracked file; the live config this patch targets contains a real
public resolver address, so the fixture is a STRUCTURAL likeness of it rather
than a copy. That is also why the script itself never spells an address: it
CAPTURES the resolver list out of the live config and re-emits it verbatim.

    run:  pytest scripts/tests/test_nixos_option_renames_apply.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "nix" / "system" / "apply-nixos-option-renames-2026-09-09.sh"
sys.path.insert(0, str(REPO / "scripts"))
from testlib.mockbin import write_exec  # noqa: E402

BASH = shutil.which("bash") or "/bin/bash"

#: A structural likeness of the live workbench config: the same nesting, the same
#: neighbouring COMMENTED-OUT lines, the same already-correct `${pkgs.xrandr}`
#: interpolation. Every one of those neighbours is a trap the script has to not
#: fall into, and two of them caught a real defect while it was being written.
FIXTURE = """{ config, pkgs, ... }:
{
xdg.portal = {
  enable = true;
  extraPortals = [ pkgs.xdg-desktop-portal-gtk ];
};
services.flatpak.enable = true;

  services.dnsmasq = {
    enable = true;
    alwaysKeepRunning = true;
    #servers = ["/lan/203.0.113.5" "203.0.113.1"];
    # docker.io is pinned FIRST to the public resolver on purpose: the LAN router
    # serves a stale record with an absurd TTL.
    # Remove this line once the router's stale entry is cleared.
    servers = ["/docker.io/203.0.113.1" "192.168.50.1" "203.0.113.1"];  # router first
    settings = {
      address = [
        "/workbench.lan/192.168.50.250"
      ];
      cache-size = 10000;
    };
  };

  services.xserver = {
    displayManager = {
      sessionCommands = ''
        ${pkgs.xrandr}/bin/xrandr --output DP-0 --mode 3440x1440
      '';
    };
    windowManager.i3 = {
      enable = true;
      extraPackages = with pkgs; [
        dmenu
        rofi
        xorg.xrandr
        xorg.xkill
        i3lock
     ];
    };
  };

  services.gnome.tracker.enable = true;
  services.gnome.tracker-miners.enable = true;

# an old experiment, left commented out. NONE of this is a definition:
#  xdg.portal = {
#    enable = true;
#    extraPortals = with pkgs; [
#      xdg-desktop-portal-termfilechooser
#    ];
#    config = {
#      common.default = [ "*" ];
#    };
#  };
#xdg.portal.config.common.default = "*";
}
"""

PORTALS_OK = "[preferred]\ndefault=*\n"


@pytest.fixture
def rig(tmp_path):
    """A fixture config, a shim PATH, and a runner."""

    class Rig:
        pass

    r = Rig()
    r.dir = tmp_path
    r.cfg = tmp_path / "configuration.nix"
    r.portals = tmp_path / "portals.conf"
    r.bin = tmp_path / "bin"
    r.bin.mkdir()
    r.calls = tmp_path / "rebuild-calls"
    r.channels = tmp_path / "channels"
    (r.channels / "nixos").mkdir(parents=True)

    # Records the invocation instead of performing it, and writes the portals.conf
    # a real activation would write — the script's verify step then has something
    # true to read. `$PORTALS_BODY` lets a test make activation produce the WRONG
    # content, which is the negative control for that check.
    # 🔴 `${VAR+x}` (no colon) throughout, so a test can inject an EMPTY value —
    # `${VAR:-default}` would silently substitute the default for "" and make
    # test_verify_goes_red_when_portals_conf_was_never_created vacuous.
    write_exec(r.bin / "nixos-rebuild", f'''
echo "nixos-rebuild $*" >> "{r.calls}"
echo "SHIM: nixos-rebuild $*"
if [ -n "${{FAKE_WARNINGS+x}}" ]; then
  printf '%s' "$FAKE_WARNINGS"
else
  echo "evaluation warning: 'wineWowPackages' is deprecated as it is no longer preferred by upstream. Use wineWow64Packages instead"
fi
if [ -z "${{NO_PORTALS:-}}" ]; then
  if [ -n "${{PORTALS_BODY+x}}" ]; then
    printf '%s' "$PORTALS_BODY" > "{r.portals}"
  else
    printf '[preferred]\\ndefault=*\\n' > "{r.portals}"
  fi
fi
exit ${{REBUILD_RC:-0}}
''')
    write_exec(r.bin / "dig", '''
case "$*" in
  *+short*workbench.lan*) echo "192.168.50.250" ;;
  *registry-1.docker.io*) printf 'registry-1.docker.io.\\t%s\\tIN\\tA\\t203.0.113.7\\n' "${FAKE_TTL:-33}" ;;
esac
''')

    def run(cfg_text=FIXTURE, args=(), env=None, expect=None):
        if cfg_text is not None:
            r.cfg.write_text(cfg_text)
        e = dict(os.environ)
        e["PATH"] = f"{r.bin}:{e.get('PATH', '')}"
        e["NIXOPTS_CFG"] = str(r.cfg)
        e["NIXOPTS_PORTALS_CONF"] = str(r.portals)
        e["NIXOPTS_ROOT_CHANNELS"] = str(r.channels)
        e.update(env or {})
        # 🔴 bash is resolved from the AMBIENT PATH, not the child's — one test
        # deliberately hands the script a PATH with nothing on it, and looking
        # bash up in that would fail in the launcher instead of in the script,
        # turning a real check into a FileNotFoundError.
        p = subprocess.run([BASH, str(SCRIPT), *args], capture_output=True,
                           text=True, env=e, timeout=120)
        if expect is not None:
            assert p.returncode == expect, (
                f"rc={p.returncode} expected {expect}\n"
                f"--- stdout ---\n{p.stdout}\n--- stderr ---\n{p.stderr}")
        return p

    r.run = run
    return r


# --------------------------------------------------------------------------
# the five edits
# --------------------------------------------------------------------------
def test_it_applies_all_five_edits(rig):
    rig.run(expect=0)
    out = rig.cfg.read_text()

    # 1. dnsmasq moved INTO settings, and the top-level key is gone.
    assert "\n    servers = [" not in out
    assert "      server = [" in out
    # 2 & 3. the GNOME renames.
    assert "services.gnome.tinysparql.enable = true;" in out
    assert "services.gnome.localsearch.enable = true;" in out
    assert "services.gnome.tracker.enable" not in out
    assert "services.gnome.tracker-miners.enable" not in out
    # 4. the i3 package-list entry — and ONLY it.
    assert "\n        xrandr\n" in out
    assert "xorg.xrandr" not in out
    # 🔴 `xorg.xkill` is a SIBLING attribute in the same list that nixpkgs has
    # NOT deprecated. It exists in the fixture as the negative half of edit 4:
    # a mutant that swaps `xorg.` out globally is otherwise EQUIVALENT here and
    # SURVIVED a fully green sweep before this line was added (measured).
    assert "        xorg.xkill\n" in out
    # 5. the portal default, LIVE (not the commented-out experiment).
    assert '\n  config.common.default = "*";\n' in out


def test_the_resolver_list_is_moved_verbatim(rig):
    """The whole point of the dnsmasq edit: the VALUE must not change.

    The script never spells an address; it captures the bracketed list and
    re-emits it. Anything that reformats, reorders or drops an entry here would
    silently unpin docker.io, which is a real outage (TLS failures on ~half of
    all pulls) that the script's own verify section exists to catch.
    """
    before = rig.cfg  # written by run()
    rig.run(expect=0)
    out = before.read_text()
    assert '["/docker.io/203.0.113.1" "192.168.50.1" "203.0.113.1"];  # router first' in out
    # exactly once — not duplicated into both places
    assert out.count('"/docker.io/203.0.113.1"') == 1


def test_the_docker_io_comment_travels_with_the_line(rig):
    """The 3-line rationale must end up INSIDE settings, above `server`, at the
    settings indent — not orphaned where the old key used to be."""
    rig.run(expect=0)
    out = rig.cfg.read_text()
    i_comment = out.index("      # docker.io is pinned FIRST")
    i_server = out.index("      server = [")
    i_settings = out.index("    settings = {")
    assert i_settings < i_comment < i_server, out


def test_the_commented_out_servers_line_is_not_swallowed(rig):
    """🔴 The comment-carrying walk-back stops at `#servers = [...]`.

    That line is COMMENTED-OUT CODE sitting directly above the rationale block.
    Re-indenting it into the `settings` attrset would move a dead definition to
    a place where uncommenting it means something different. The `# ` (with the
    space) in the walk-back pattern is what stops it.
    """
    rig.run(expect=0)
    out = rig.cfg.read_text()
    assert '    #servers = ["/lan/203.0.113.5" "203.0.113.1"];\n    settings = {' in out


def test_the_already_correct_pkgs_xrandr_interpolation_is_untouched(rig):
    """The config uses `${pkgs.xrandr}` in sessionCommands and it is ALREADY
    right. Only the `xorg.xrandr` package-list entry may move."""
    rig.run(expect=0)
    out = rig.cfg.read_text()
    assert "${pkgs.xrandr}/bin/xrandr --output DP-0 --mode 3440x1440" in out
    assert out.count("/bin/xrandr") == 1
    assert out.count("xorg.") == 1, "an unrelated xorg.* attribute was rewritten"


def test_the_commented_out_portal_experiment_does_not_read_as_applied(rig):
    """🔴 REGRESSION. MEASURED 2026-09-09 on the first fixture run against the
    REAL config: a plain `'config.common.default = "*";' in src` idempotency
    check matched the commented-out `#xdg.portal.config.common.default = "*";`
    left over from an old termfilechooser experiment, so the script reported
    "default already set" and skipped patch B — the one edit that actually does
    something. A commented line is not a definition.
    """
    rig.run(expect=0)
    out = rig.cfg.read_text()
    assert '\n  config.common.default = "*";\n' in out, "patch B was skipped"
    # the dead line is still dead, and still there
    assert '\n#xdg.portal.config.common.default = "*";\n' in out


def test_nothing_outside_the_five_edits_moves(rig):
    """The script's own byte-delta guard, driven from outside.

    Line-for-line: only lines the five edits own may leave or arrive. Seven
    leave — the four rewritten definitions plus the three rationale comments,
    which "leave" because they are re-indented into `settings`. Twelve arrive —
    those three at the new indent, the `server` line, the two renamed GNOME
    lines, the bare `xrandr` entry, the `config.common` line and its four
    comment lines.
    """
    before = FIXTURE.splitlines()
    rig.run(expect=0)
    after = rig.cfg.read_text().splitlines()
    left = [l for l in before if l not in after]
    arrived = [l for l in after if l not in before]

    for owned in (
        '    servers = ["/docker.io/203.0.113.1" "192.168.50.1" "203.0.113.1"];  # router first',
        "        xorg.xrandr",
        "  services.gnome.tracker.enable = true;",
        "  services.gnome.tracker-miners.enable = true;",
    ):
        assert owned in left, owned
    assert len(left) == 7, left
    assert len(arrived) == 12, arrived
    # nothing unrelated arrived
    allowed = ("#", "server =", "config.common.default", "xrandr",
               "services.gnome.tinysparql.enable", "services.gnome.localsearch.enable")
    for a in arrived:
        assert a.strip().startswith(allowed), a


# --------------------------------------------------------------------------
# idempotency
# --------------------------------------------------------------------------
def test_a_second_run_changes_nothing(rig):
    rig.run(expect=0)
    once = rig.cfg.read_text()
    p = rig.run(cfg_text=None, expect=0)
    assert rig.cfg.read_text() == once
    for marker in ("already migrated", "already renamed", "already set"):
        assert marker in p.stdout, p.stdout
    assert "already fully applied" in p.stdout


def test_dry_run_writes_nothing_and_needs_no_root(rig):
    p = rig.run(args=("--dry-run",), expect=0)
    assert rig.cfg.read_text() == FIXTURE
    assert not rig.calls.exists(), "a --dry-run invoked nixos-rebuild"
    assert "NOTHING WRITTEN" in p.stdout


def test_an_unknown_argument_is_refused_before_any_edit(rig):
    p = rig.run(args=("--yolo",), expect=64)
    assert rig.cfg.read_text() == FIXTURE
    assert "unrecognised argument" in p.stderr


# --------------------------------------------------------------------------
# 🔴 REFUSAL ON DRIFT — the script must never guess which occurrence to edit.
# Each case mutates the fixture into a state the patch was NOT written against
# and requires rc=2 AND an untouched file.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name,mutate,expect_msg", [
    (
        "two servers lines",
        lambda s: s.replace(
            '    servers = ["/docker.io/203.0.113.1"',
            '    servers = ["/docker.io/203.0.113.9"];\n    servers = ["/docker.io/203.0.113.1"', 1),
        "found 2",
    ),
    (
        "settings does not follow servers",
        lambda s: s.replace("  # router first\n    settings = {",
                            "  # router first\n    # an interloping comment\n    settings = {"),
        "is not `settings = {`",
    ),
    (
        "two xorg.xrandr entries",
        lambda s: s.replace("        xorg.xrandr\n", "        xorg.xrandr\n        xorg.xrandr\n"),
        "found 2",
    ),
    (
        "the gnome option is simply absent",
        lambda s: s.replace("  services.gnome.tracker.enable = true;\n", ""),
        "services.gnome.tracker.enable",
    ),
    (
        "two xdg.portal blocks",
        lambda s: s.replace("xdg.portal = {\n  enable = true;\n",
                            "xdg.portal = {\n};\nxdg.portal = {\n  enable = true;\n", 1),
        "xdg.portal = {",
    ),
    (
        "no dnsmasq servers key at all, in either spelling",
        lambda s: s.replace('    servers = ["/docker.io/203.0.113.1" "192.168.50.1" "203.0.113.1"];  # router first\n', ""),
        "cannot tell which state",
    ),
])
def test_it_refuses_on_drift(rig, name, mutate, expect_msg):
    drifted = mutate(FIXTURE)
    assert drifted != FIXTURE, f"the {name} mutation is a no-op — the test is vacuous"
    p = rig.run(cfg_text=drifted, expect=2)
    assert rig.cfg.read_text() == drifted, f"{name}: the file was modified anyway"
    assert not rig.calls.exists(), f"{name}: nixos-rebuild ran after a refusal"
    assert "REFUSING" in p.stderr, p.stderr
    assert expect_msg in p.stderr, p.stderr


def test_a_refusal_is_distinguishable_from_a_bad_argument(rig):
    """rc 2 = drift, rc 64 = usage. Collapsing them would let a typo read as
    'the host has drifted, apply by hand'."""
    assert rig.run(cfg_text=FIXTURE.replace("        xorg.xrandr\n",
                                            "        xorg.xrandr\n        xorg.xrandr\n"),
                   expect=2)
    assert rig.run(args=("--nope",), expect=64)


# --------------------------------------------------------------------------
# the verify section — it must be able to go RED
# 🔴 Every check below asserts a NON-DEFAULT outcome. A verify block that only
# ever prints "ok" is indistinguishable from one wired to nothing.
# --------------------------------------------------------------------------
def test_verify_passes_on_a_good_activation(rig):
    p = rig.run(expect=0)
    assert "ok   1 evaluation warning remains and it is wineWowPackages" in p.stdout
    assert "ok   workbench.lan -> 192.168.50.250" in p.stdout
    assert "ok   registry-1.docker.io TTL=33s" in p.stdout
    assert "contains default=*" in p.stdout
    # `bad()` is the only thing that prints two spaces then FAIL.
    assert "  FAIL" not in p.stdout


def test_verify_goes_red_when_the_docker_io_pin_did_not_survive(rig):
    """🔴 THE ONE THAT MATTERS. A 487-day TTL means the LAN router's stale record
    is being served again — half of all `docker pull`s then fail TLS."""
    p = rig.run(env={"FAKE_TTL": "42048498"}, expect=1)
    assert "  FAIL registry-1.docker.io TTL=42048498s" in p.stdout
    assert "nixos-rebuild switch --rollback" in p.stdout


def test_verify_goes_red_on_an_unparseable_ttl(rig):
    """A garbage TTL must not crash the script under `set -e` — the arithmetic
    comparison `[[ $ttl -lt 1000 ]]` is a hard error on a non-numeric value, and
    a crash here skips every remaining check while looking like a failure of
    something else."""
    p = rig.run(env={"FAKE_TTL": "notanumber"}, expect=1)
    assert "could not parse a TTL" in p.stdout
    assert "VERIFY" in p.stdout


def test_verify_goes_red_when_a_warning_other_than_wine_survives(rig):
    p = rig.run(env={"FAKE_WARNINGS":
                     "evaluation warning: The option `services.dnsmasq.servers' has been renamed.\n"},
                expect=1)
    assert "  FAIL" in p.stdout
    assert "NOT wineWowPackages" in p.stdout


def test_verify_goes_red_when_more_than_one_warning_survives(rig):
    p = rig.run(env={"FAKE_WARNINGS":
                     "evaluation warning: one\nevaluation warning: two\n"}, expect=1)
    assert "  FAIL expected exactly 1 evaluation warning, got 2" in p.stdout


def test_verify_goes_red_when_zero_warnings_are_reported(rig):
    """🔴 A ZERO IS NOT A PASS HERE. Six warnings became one, not none — the
    wineWowPackages one is deliberately left. Zero means the log was not read
    (wrong stream, wrong file, a pattern that cannot match), which is exactly
    the shape of a check wired to nothing."""
    p = rig.run(env={"FAKE_WARNINGS": ""}, expect=1)
    assert "  FAIL expected exactly 1 evaluation warning, got 0" in p.stdout


def test_verify_goes_red_when_portals_conf_lacks_the_default(rig):
    p = rig.run(env={"PORTALS_BODY": "[preferred]\ndefault=gtk;\n"}, expect=1)
    assert "has no 'default=*'" in p.stdout


def test_verify_goes_red_when_portals_conf_was_never_created(rig):
    """Patch B's ONLY observable effect is this file existing."""
    p = rig.run(env={"NO_PORTALS": "1"}, expect=1)
    assert "was not created — patch B did not take effect" in p.stdout


def test_a_failed_rebuild_names_the_backup_and_does_not_verify(rig):
    p = rig.run(env={"REBUILD_RC": "1"}, expect=1)
    assert "nixos-rebuild FAILED" in p.stderr
    assert "cp -a" in p.stderr and ".bak-" in p.stderr
    assert "VERIFY" not in p.stdout


def test_the_backup_is_the_pre_edit_content(rig):
    rig.run(expect=0)
    baks = sorted(rig.dir.glob("configuration.nix.bak-*"))
    assert len(baks) == 1, baks
    assert baks[0].read_text() == FIXTURE


# --------------------------------------------------------------------------
# 🔴 the two measured hazards
# --------------------------------------------------------------------------
def test_it_preflights_python3_before_touching_anything(rig, tmp_path):
    """HAZARD 2. `/run/current-system/sw/bin` has no python3 on this host, so a
    plain `sudo` (env_reset) leaves root without one. Half-applying is the
    failure mode this preflight exists to prevent: it must fire BEFORE the
    backup and BEFORE the edit."""
    # PATH is the shim dir ALONE — no python3, and no coreutils either. The
    # preflight branch is builtins-only precisely so it still speaks in this
    # environment; if it ever reaches for `cat` again, this goes red in the nix
    # sandbox tier where /run/current-system/sw/bin does not exist at all.
    p = rig.run(env={"PATH": str(rig.bin)}, expect=1)
    assert "no python3 on PATH" in p.stderr
    assert 'sudo env "PATH=$PATH" bash' in p.stderr
    assert rig.cfg.read_text() == FIXTURE
    assert not list(rig.dir.glob("configuration.nix.bak-*")), "it took a backup anyway"
    assert not rig.calls.exists()


def test_the_nix_path_pin_is_spelled_and_is_a_SET_not_an_UNSET():
    """HAZARD 1, pinned as a property of the SOURCE.

    It cannot be driven end-to-end here — TEST MODE deliberately skips the pin,
    because a fixture run makes no claim about this machine's channels — so this
    is a structural guard and is labelled as one.

    What it holds: the script must EXPORT a NIX_PATH whose `nixpkgs=` entry is
    root's channel. `unset NIX_PATH` is NOT equivalent and must not creep back
    in: NixOS's `nix.nixPath` default is what supplies the `nixpkgs=` alias, and
    root's channel directory is named `nixos`, so with NIX_PATH unset `<nixpkgs>`
    falls back to nix's built-in default whose first component is
    `$HOME/.nix-defexpr/channels` — under `sudo -E` that is the user's OLDER
    unstable channel again, which is the entire hazard.
    """
    text = SCRIPT.read_text()
    assert 'export NIX_PATH="nixpkgs=${ROOT_CHANNELS}/nixos:' in text
    assert "unset NIX_PATH" not in text


def test_the_set_e_rc_capture_uses_the_only_correct_form():
    """🔴 Both directions of the `set -e` trap, pinned in the source.

    `cmd; rc=$?` is DEAD CODE under `set -e` — the shell dies before the
    assignment. `cmd || rc=$?` without a preceding `rc=0` crashes under `set -u`
    on the SUCCESS path, because nothing ever assigns rc. Only `rc=0` followed by
    `cmd || rc=$?` is correct, and the script must contain no other shape.
    """
    lines = SCRIPT.read_text().splitlines()
    # Comment lines are excluded — the script's own WHY block SPELLS the two
    # wrong forms in order to name them, and counting those as captures would
    # make this guard fail on the very prose that documents it.
    captures = [i for i, l in enumerate(lines)
                if "rc=$?" in l and not l.lstrip().startswith("#")]
    assert captures, "no rc capture found — this guard is pinned to nothing"
    for i in captures:
        assert "||" in lines[i], f"line {i+1} captures rc without `||`: {lines[i]}"
        prior = [l.strip() for l in lines[max(0, i - 6):i]]
        assert "rc=0" in prior, (
            f"line {i+1} has no `rc=0` above it; under `set -u` the success path "
            f"reads an unset rc: {lines[i]}")


def test_wine_is_deliberately_out_of_scope():
    """🔴 The sixth warning stays, and the script must say WHY in prose a future
    maintainer will read before "finishing the job".

    `wineWowPackages` -> `wineWow64Packages` is NOT a rename: the first is a real
    32+64-bit pair, the second a single 64-bit binary using upstream's WoW64
    thunking. Different mechanism. Pinned as a whole normalised sentence rather
    than a keyword, because a guard on WORDS is walkable by rewording.
    """
    # Normalised: the leading `# ` of each comment line stripped, then all
    # whitespace collapsed, so a REWRAP of the paragraph cannot walk this guard.
    text = " ".join(" ".join(l.lstrip().lstrip("#") for l in
                             SCRIPT.read_text().splitlines()).split())
    assert "WHY WARNING 6 STAYS: wineWowPackages IS NOT A RENAME" in text
    assert 'DO NOT "FINISH THE JOB" BY ADDING IT.' in text
    assert ("`wineWowPackages` is a genuine 32-bit + 64-bit pair; "
            "`wineWow64Packages` is a SINGLE 64-bit binary relying on upstream's "
            "new WoW64 thunking layer to run 32-bit code.") in text
    assert "One warning is the correct steady state here." in text

    # 🔴 AND IT MUST NOT ACTUALLY TOUCH IT. The only runtime mention allowed is
    # the verify check that asserts the surviving warning IS this one.
    body = SCRIPT.read_text()
    editor = body.split("<<'PYBLOCK'", 1)[1].split("\nPYBLOCK\n", 1)[0]
    assert "wineWow" not in editor, (
        "the python editor — the only thing that writes configuration.nix — "
        "mentions wineWow. It must not touch it at all.")

    runtime = body.split("set -euo pipefail", 1)[1]
    # The new spelling must never appear outside the WHY block: writing it into
    # the config is exactly the change this script refuses to make.
    assert "wineWow64Packages" not in runtime, runtime
    # Every runtime mention is read-only — a grep of the rebuild log, or the
    # message reporting what that grep found.
    mentions = [l.strip() for l in runtime.splitlines()
                if "wineWow" in l and not l.strip().startswith("#")]
    assert mentions, "the verify step no longer names the surviving warning"
    for m in mentions:
        assert m.startswith(("if printf", "ok ", "bad ")), m
