# mention-review — a single-purpose Go TUI for reading one GitHub pull request.
#
# WHAT IT IS FOR: the same job `nvim-octo` does today. A clicked GitHub `repo#N`
# mention in the terminal opens a review surface instead of a browser tab.
#
# 🔴 PHASE 2. IT CAN COMMENT, APPROVE, REQUEST CHANGES, SUBMIT A REVIEW AND
# MERGE — four of those five behind a y/N confirmation naming the repo, the
# number, the merge method and the AUTHENTICATED LOGIN (§10.2, cli/cli#14370).
#
# 🔴 `nvim-octo` IS STILL THE PACKAGE THE CLICK PATH USES.
# `scripts/mention-open.py`'s `REVIEW_EXE` still says `nvim-octo`, deliberately:
# the retirement is a separate, later, revertable step, and the proposal is
# explicit that it ships "only after the operator has used the new TUI for a
# real review". Until then this binary is on PATH and invoked by hand:
#     mention-review <owner/repo> <number>
# Flipping the click path over is ONE line in `mention-open.py`, and flipping it
# back is the same line.
#
# ---------------------------------------------------------------------------
# 🔴 SOURCE REFERENCE STRATEGY: A LOCAL PATH INSIDE THIS REPO.
#
# Unlike `clawgatectl.nix` next door — which points at a working tree of a
# DIFFERENT, private repo and therefore has to guard on `pathExists` — the Go
# module here lives in this repo, at ./src. There is no cross-repo drift window
# and no credential question. What IS carried over from that file is the
# version mechanism, because the failure it prevents is not about repositories.
#
# 🔴 THE VERSION IS READ OUT OF THE SOURCE BEING COMPILED. IT IS NOT WRITTEN
# HERE. `clawgatectl.nix` exists in its current form because a hand-maintained
# `version = "x.y.z"` literal was a claim about code that nothing kept in step,
# and on 2026-08-14 it stamped `0.7.95` onto a binary built from `0.7.87`
# source — producing a CLI that printed help and exited 0 for a subcommand it
# did not have. Both halves live in this repo, so the window is smaller; the
# mechanism is free and the failure mode is identical, so it is kept.
#
# 🔴 AN UNPARSEABLE SOURCE MUST NOT FALL BACK TO A LITERAL — that is the same
# lie in a new shape. It sets `available = false` instead, so the binary is
# simply not installed and the failure is LOUD at use time
# (`mention-review: command not found`) rather than silent at build time.
# Failing the SWITCH is the worse outcome and is deliberately not what happens:
# `ship.sh` reports a failed switch as a SKIPPED host, which this repo's
# CLAUDE.md documents as the failure mode that silently stops all future
# delivery to that machine.
#
# 🔴 `doCheck = false`, AND IT IS NOT A CONVENIENCE. Verified in the pinned
# nixpkgs rather than assumed: `pkgs/build-support/go/module.nix` defaults
# `doCheck` to TRUE and its check phase runs `buildGoDir test` over every test
# directory. On a package in `home.packages` that makes a red Go test fail a
# `home-manager switch` — i.e. the skipped-host failure above, triggered by a
# test. The tests are run by a SEPARATE gate leg (`scripts/run-go-tests.sh` and
# `checks.gotests`), exactly as `pytests`/`nodetests` are separate from the
# deploy path.
{ pkgs, lib ? pkgs.lib }:

let
  srcDir = ./src;

  # The single source of truth for the version, and the file whose
  # `var buildVersion = "…"` line is BOTH the Go default and what this
  # derivation stamps back in.
  versionFile = ./src/cmd/mention-review/version.go;

  # 🔴 EXACTLY ONE MATCHING LINE, OR NOTHING. Zero matches means the
  # declaration was renamed or reformatted; two or more means the pattern has
  # become ambiguous and picking either one would be a guess presented as a
  # fact. Both land on `null`, which switches the package off — see the header
  # for why that is preferable to both a literal fallback and a failed switch.
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
  # building". A package that cannot answer the second is not installed — it is
  # never labelled with a guess.
  available =
    builtins.pathExists ./src/cmd/mention-review/main.go && parsedVersion != null;

  mention-review = pkgs.buildGoModule {
    pname = "mention-review";
    version = parsedVersion;

    src = lib.cleanSource srcDir;

    # 🔴 MEASURED by building with a deliberately wrong hash and taking the
    # "got:" value from the failure. NEVER hand-edited: change go.mod/go.sum and
    # this must be re-derived the same way.
    vendorHash = "sha256-gtKRWZmVWKjm5Xro0FKRQpVwkQZyYLmdfl3DumxnPgc=";

    subPackages = [ "cmd/mention-review" ];

    # 🔴 SEE THE HEADER. A red Go test must not be able to fail a
    # `home-manager switch`. The tests run in their own gate leg.
    doCheck = false;

    # 🔴 THE STAMP IS AN IDENTITY — `parsedVersion` was read out of the very
    # `var buildVersion` line this overwrites. That is the property that makes
    # the store path and the compiled-in string provably the same value. It
    # must never be given anything but `parsedVersion`: the moment it can
    # differ, the binary can lie about itself again.
    ldflags = [ "-s" "-w" "-X main.buildVersion=${parsedVersion}" ];

    # 🔴 `xdg-open` IS PINNED BY STORE PATH, NOT INHERITED — the same reason
    # `nvim-octo` pins `gh`. The whole call chain starts in an Alacritty hint
    # spawned with the DISPLAY MANAGER's environment, and `~/.nix-profile` is
    # blanked for ~30 s during every `home-manager switch`, so a click landing
    # in that window would find nothing by bare name.
    #
    # ⚠ `pkgs.gh` IS DELIBERATELY ABSENT, AND THAT IS A CLAIM WORTH CHECKING
    # AGAIN IF AUTH CHANGES. `go-gh`'s token precedence is: GH_TOKEN /
    # GITHUB_TOKEN, then `~/.config/gh/hosts.yml`, then — and only then — a
    # SUBPROCESS `gh auth token --secure-storage`. MEASURED on this host:
    # `hosts.yml` carries plaintext `oauth_token` entries and the session bus
    # exposes no `org.freedesktop.secrets`, so rung 2 fires and rung 3 never
    # runs. If it ever does, `TokenForHost` returns ("", "default") SILENTLY
    # with no error — which this binary renders as the `NO TOKEN` card rather
    # than as an empty PR view, so the failure is legible rather than
    # mysterious. Adding `pkgs.gh` here would pull a large closure to cover a
    # rung that is not reached; the honest card is the cheaper mitigation.
    nativeBuildInputs = [ pkgs.makeWrapper ];
    postInstall = ''
      wrapProgram $out/bin/mention-review \
        --prefix PATH : ${lib.makeBinPath [ pkgs.xdg-utils ]}
    '';

    meta = with lib; {
      description = "Single-purpose TUI for reading one GitHub pull request";
      mainProgram = "mention-review";
      license = licenses.mit;
      platforms = platforms.linux;
    };
  };
in
if available then mention-review else null
