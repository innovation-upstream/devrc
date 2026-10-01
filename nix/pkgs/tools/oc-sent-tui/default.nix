# oc-sent-tui — the popup TUI behind tmux's Alt+S: "what did I send in the
# CURRENT opencode session", resolved from the focused pane's cwd.
#
# 🔴 THIS IS A LOCAL-PATH PACKAGE, LIKE ITS SIBLINGS mention-review AND
# stt-voice — the Go module lives in this repo at ./src, so there is no
# cross-repo drift window and no credential question. What IS carried over
# is the version mechanism, because the failure it prevents is not about
# repositories:
#
# 🔴 THE VERSION IS READ OUT OF THE SOURCE BEING COMPILED. IT IS NOT WRITTEN
# HERE. `clawgatectl.nix` exists in its current form because a hand-maintained
# `version = "x.y.z"` literal was a claim about code that nothing kept in
# step, and on 2026-08-14 it stamped `0.7.95` onto a binary built from
# `0.7.87` source — producing a CLI that printed help and exited 0 for a
# subcommand it did not have.
#
# 🔴 AN UNPARSEABLE SOURCE MUST NOT FALL BACK TO A LITERAL — that is the same
# lie in a new shape. It sets `available = false` instead, so the binary is
# simply not installed and the failure is LOUD at use time (`oc-sent-tui:
# command not found` — the Alt+S popup dead, visible immediately) rather than
# silent at build time. Failing the SWITCH is the worse outcome and is
# deliberately not what happens: ship.sh reports a failed switch as a
# SKIPPED host, which this repo's CLAUDE.md documents as silently stopping
# all future delivery to that machine.
#
# 🔴 `doCheck = false`, AND IT IS NOT A CONVENIENCE. Same finding as its two
# siblings: nixpkgs' go module builder defaults `doCheck` TRUE and its check
# phase runs `buildGoDir test` over every test directory. On a package in
# `home.packages` that makes a red Go test fail a `home-manager switch` —
# the skipped-host failure above, triggered by a test. The tests are run by
# a SEPARATE gate leg (`scripts/run-go-tests.sh` / `checks.gotests`).
#
# 🔴 RUNTIME DEPENDENCY BY BARE NAME: the TUI execs `oc-sent` (the python
# CLI — one reader of the sqlite store, see internal/sent's header), which
# resolves through `~/.local/bin` (home.sessionPath), NOT the nix profile —
# so unlike stt-voice's wrapped PATH, a switch-time profile blanking cannot
# break it. There is deliberately NO wrapProgram: wrapping would pin a
# SECOND copy of oc-sent's resolution and drift from the one the keybind's
# old `| less` pipeline used.
{ pkgs, lib ? pkgs.lib }:

let
  srcDir = ./src;

  # The single source of truth for the version, and the file whose
  # `var buildVersion = "…"` line is BOTH the Go default and what this
  # derivation stamps back in.
  versionFile = ./src/cmd/oc-sent-tui/version.go;

  # 🔴 EXACTLY ONE MATCHING LINE, OR NOTHING. Zero matches means the
  # declaration was renamed or reformatted; two or more means the pattern has
  # become ambiguous and picking either one would be a guess presented as a
  # fact. Both land on `null`, which switches the package off — see the
  # header for why that is preferable to both a literal fallback and a
  # failed switch. cmd/oc-sent-tui/main_test.go pins the file's shape.
  versionPattern = "var buildVersion = \"([^\"]+)\".*";

  versionLines =
    if builtins.pathExists versionFile
    then
      builtins.filter
        (l: builtins.isString l && builtins.match versionPattern l != null)
        (builtins.split "\n" (builtins.readFile versionFile))
    else [ ];

  parsedVersion =
    if builtins.length versionLines == 1
    then builtins.head (builtins.match versionPattern (builtins.head versionLines))
    else null;

  available =
    builtins.pathExists ./src/cmd/oc-sent-tui/main.go && parsedVersion != null;

  oc-sent-tui = pkgs.buildGoModule {
    pname = "oc-sent-tui";
    version = parsedVersion;

    src = lib.cleanSource srcDir;

    # 🔴 MEASURED by building with a deliberately wrong hash and taking the
    # "got:" value from the failure. NEVER hand-edited: change go.mod/go.sum
    # and this must be re-derived the same way.
    vendorHash = "sha256-6h2J2IoegBul0VVWTQXd6DwiwZp7Ph3sE0l/Ms1lnmw=";

    subPackages = [ "cmd/oc-sent-tui" ];

    # 🔴 SEE THE HEADER. A red Go test must not be able to fail a
    # `home-manager switch`. The tests run in their own gate leg.
    doCheck = false;

    # 🔴 THE STAMP IS AN IDENTITY — `parsedVersion` was read out of the very
    # `var buildVersion` line this overwrites. It must never be given
    # anything but `parsedVersion`.
    ldflags = [ "-s" "-w" "-X main.buildVersion=${parsedVersion}" ];

    meta = with lib; {
      description = "Popup TUI for the messages you sent in the current opencode session";
      mainProgram = "oc-sent-tui";
      license = licenses.mit;
      platforms = platforms.linux;
    };
  };
in
if available then oc-sent-tui else null