# stt-voice — hold-to-talk voice input: i3 $mod+m press records the default
# mic, release stops + transcribes + opens the transcript TUI; the bar pill
# (scripts/i3status-stt) is the second trigger source and renders the tool's
# state file.
#
# 🔴 THIS IS A LOCAL-PATH PACKAGE, LIKE ITS SIBLING mention-review — the Go
# module lives in this repo at ./src, so there is no cross-repo drift window
# and no credential question. What IS carried over from that file is the
# version mechanism (and before it, clawgatectl.nix's lesson), because the
# failure it prevents is not about repositories:
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
# simply not installed and the failure is LOUD at use time (`stt-voice:
# command not found` — the hotkey and the pill both dead, visible immediately)
# rather than silent at build time. Failing the SWITCH is the worse outcome
# and is deliberately not what happens: ship.sh reports a failed switch as a
# SKIPPED host, which this repo's CLAUDE.md documents as the failure mode that
# silently stops all future delivery to that machine.
#
# 🔴 `doCheck = false`, AND IT IS NOT A CONVENIENCE. Same finding as
# mention-review's: nixpkgs' go module builder defaults `doCheck` TRUE and its
# check phase runs `buildGoDir test` over every test directory. On a package
# in `home.packages` that makes a red Go test fail a `home-manager switch` —
# the skipped-host failure above, triggered by a test. The tests are run by a
# SEPARATE gate leg (`scripts/run-go-tests.sh` / `checks.gotests`), exactly as
# the other languages' checks are separate from the deploy path.
#
# 🔴 THE RUNTIME TOOLS ARE PINNED BY STORE PATH, NOT INHERITED — the same
# reason mention-review pins `xdg-open`. The whole call chain starts in an i3
# `exec` with the session environment, and `~/.nix-profile` is blanked for
# ~30s during every home-manager switch, so a hotkey press landing in that
# window would find nothing by bare name. The wrapped PATH is exactly the set
# internal/effects execs (pw-record, pkill, xdotool, xclip, i3-msg, alacritty,
# notify-send); internal/effects' header is the ledger for it — add an effect,
# grow this list in the same commit.
{ pkgs, lib ? pkgs.lib }:

let
  srcDir = ./src;

  # The single source of truth for the version, and the file whose
  # `var buildVersion = "…"` line is BOTH the Go default and what this
  # derivation stamps back in.
  versionFile = ./src/cmd/stt-voice/version.go;

  # 🔴 EXACTLY ONE MATCHING LINE, OR NOTHING. (The doc comment in version.go
  # QUOTES the shape; builtins.match is anchored, so a `//` comment line
  # cannot match it.) Zero matches means the declaration was renamed or
  # reformatted; two or more means the pattern has become ambiguous and
  # picking either one would be a guess presented as a fact.
  versionPattern = "var buildVersion = \"([^\"]+)\".*";

  versionLines =
    if builtins.pathExists versionFile
    then
      builtins.filter
        (l: builtins.isString l && builtins.match versionPattern l != null)
        # builtins.split yields the separators as LISTS between the string
        # pieces, hence the isString filter above.
        (builtins.split "\n" (builtins.readFile versionFile))
    else [ ];

  parsedVersion =
    if builtins.length versionLines == 1
    then builtins.head (builtins.match versionPattern (builtins.head versionLines))
    else null;

  # `main.go` answers "is this tree complete enough to build at all";
  # `parsedVersion` answers "can this derivation state truthfully what it is
  # building". A package that cannot answer the second is not installed — it
  # is never labelled with a guess.
  available =
    builtins.pathExists ./src/cmd/stt-voice/main.go && parsedVersion != null;

  stt-voice = pkgs.buildGoModule {
    pname = "stt-voice";
    version = parsedVersion;

    src = lib.cleanSource srcDir;

    # 🔴 MEASURED by building with a deliberately wrong hash and taking the
    # "got:" value from the failure. NEVER hand-edited: change go.mod/go.sum
    # and this must be re-derived the same way.
    vendorHash = "sha256-7Sfx+N4aTBcPyPpSvgme/LmuUq5QANJ+mnDNw7qR4UQ=";

    subPackages = [ "cmd/stt-voice" ];

    # 🔴 SEE THE HEADER. A red Go test must not be able to fail a
    # `home-manager switch`. The tests run in their own gate leg.
    doCheck = false;

    # 🔴 THE STAMP IS AN IDENTITY — `parsedVersion` was read out of the very
    # `var buildVersion` line this overwrites. It must never be given
    # anything but `parsedVersion`.
    ldflags = [ "-s" "-w" "-X main.buildVersion=${parsedVersion}" ];

    nativeBuildInputs = [ pkgs.makeWrapper ];
    postInstall = ''
      wrapProgram $out/bin/stt-voice \
        --prefix PATH : ${lib.makeBinPath [
          pkgs.pipewire        # pw-record (the default mic)
          pkgs.procps          # pkill -RTMIN+20 i3status-rs
          pkgs.xdotool         # capture active window / type-as-input
          pkgs.xclip           # clipboard copy
          pkgs.i3              # i3-msg [id=N] focus
          pkgs.alacritty       # the TUI terminal
          pkgs.libnotify       # notify-send toasts
        ]}
    '';

    meta = with lib; {
      description = "Hold-to-talk voice input: mic → self-hosted ASR → transcript TUI";
      mainProgram = "stt-voice";
      license = licenses.mit;
      platforms = platforms.linux;
    };
  };
in
if available then stt-voice else null
