# tmux-fuzzyclaw — built, like clawgatectl next door, from a LOCAL working tree
# of another repo (`${workspace}/tmux-fuzzyclaw`), with a hand-written `version`
# literal stamped into the binary at link time.
#
# 🔴 THAT SHAPE IS THE ONE THAT FAILED FOR clawgatectl ON 2026-08-14 — the nix
# literal overwrote a stale checkout's own, correct, version and produced a
# binary that lied about itself. clawgatectl.nix now reads its version out of
# the Go source it compiles. THIS PACKAGE CANNOT DO THE SAME, and the reason is
# a measurement, not an oversight (2026-08-18):
#
#   * cmd/version.go declares `var Version = "dev"` — a link-time placeholder,
#     not a version;
#   * the string "2.0.0" appears NOWHERE in that repo, only here;
#   * the repo carries no git tags to derive one from.
#
# There is no source of truth to read, and inventing one would be the same lie
# in a new place. So the literal stays, deliberately, and
# `scripts/tests/test_clawgatectl_version.py` pins that finding: it fails the
# day cmd/version.go grows a real version, which is the trigger to apply the
# same fix here. The STALENESS half is covered regardless — drift-check.sh
# reports whether this source repo is current on each host (rc 17).
{ pkgs, workspace }:

let
  fuzzyclaw = pkgs.buildGoModule {
    pname = "tmux-fuzzyclaw";
    version = "2.0.0";

    src = pkgs.lib.cleanSource (/. + "${workspace}/tmux-fuzzyclaw");

    vendorHash = null;

    subPackages = [ "." ];

    ldflags = [ "-s" "-w" "-X github.com/zachatrocern/tmux-fuzzyclaw/cmd.Version=2.0.0" ];

    postInstall = ''
      mv $out/bin/tmux-fuzzyclaw $out/bin/fuzzyclaw
    '';

    meta = with pkgs.lib; {
      description = "Fuzzy task dashboard for tmux + Claude Code";
      homepage = "https://github.com/ZacxDev/tmux-fuzzyclaw";
      license = licenses.mit;
    };
  };
in
[ fuzzyclaw ]
