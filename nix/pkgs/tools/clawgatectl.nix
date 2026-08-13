# clawgatectl — the machine client for clawgate's JSON API.
#
# 🔴 SOURCE REFERENCE STRATEGY: LOCAL PATH, not fetchFromGitHub. Deliberate.
#
# The code lives in a DIFFERENT and PRIVATE repo (ZacxDev/homelab-infra, checked
# out here as ~/workspace/homelab-talos), under containers/clawgate. Fetching it
# would mean giving the nix builder a GitHub credential — a netrc/access-token
# in the store or an impure env var — for a binary whose only consumer is the
# two machines that already have the checkout. So this follows the precedent
# already set by tmux-fuzzyclaw.nix next door: point at the working tree.
#
# What that BUYS: no credentials anywhere, and `git pull` in homelab-talos plus
# a `home-manager switch` is the whole upgrade path.
#
# What it COSTS, stated plainly:
#   • NOT reproducible on a fresh host that lacks ~/workspace/homelab-talos.
#     Handled below by the pathExists guard: such a host simply does not get the
#     binary, instead of failing the whole switch. `clawgatectl: command not
#     found` on a new machine means "clone homelab-talos, switch again".
#   • The build reads a LIVE, routinely-dirty working tree. The binary therefore
#     reflects whatever is in that tree at switch time, NOT what is committed on
#     trunk. If you need to know which source a deployed clawgatectl came from,
#     the answer is `git -C ~/workspace/homelab-talos status`, not the git log.
#   • An uncommitted edit in that tree changes the src hash, so the next switch
#     rebuilds. That is a cost, not a bug.
#
# If clawgatectl ever needs to build on a host without the checkout, the fix is
# to move it to its own public repo and swap this for fetchFromGitHub — the rest
# of this derivation is unchanged by that.
{ pkgs, workspace }:

let
  version = "0.7.87";

  # The whole Go module, not just cmd/clawgatectl: go.mod/go.sum live at its
  # root and buildGoModule needs them for the vendor derivation.
  srcDir = "${workspace}/homelab-talos/containers/clawgate";

  # Guard on go.mod specifically rather than the directory: a bare directory
  # check would pass for an empty placeholder dir and fail deep inside the Go
  # build with a much worse error.
  available = builtins.pathExists "${srcDir}/go.mod";

  clawgatectl = pkgs.buildGoModule {
    pname = "clawgatectl";
    inherit version;

    src = pkgs.lib.cleanSource (/. + srcDir);

    # MEASURED by building with a deliberately wrong hash and taking the "got:"
    # value from the failure. Do not hand-edit: change go.mod/go.sum and this
    # must be re-derived the same way.
    vendorHash = "sha256-vJk2z5piIye+YwhsiyHqhUgXzHd6bHsr1kWH7PqUX7k=";

    # Only the CLI. The module also contains the clawgate SERVER, which pulls in
    # helm and client-go and has no business being built on a laptop.
    subPackages = [ "cmd/clawgatectl" ];

    # buildVersion is what the CLI compares the live server's /health version
    # against to print its one-line skew note, and what exit 7 quotes. Stamping
    # it from `version` keeps the two from drifting apart silently.
    ldflags = [ "-s" "-w" "-X main.buildVersion=${version}" ];

    meta = with pkgs.lib; {
      description = "Machine client for the clawgate JSON API";
      mainProgram = "clawgatectl";
      # No `license`: the source is a private repo, and asserting
      # licenses.unfree here would put the package behind allowUnfree for no
      # benefit — nothing outside these two machines ever evaluates it.
      platforms = platforms.linux;
    };
  };
in
pkgs.lib.optionals available [ clawgatectl ]
