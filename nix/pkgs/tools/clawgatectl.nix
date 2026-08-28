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
# 🔴 THE VERSION IS READ OUT OF THE SOURCE. IT IS NOT WRITTEN HERE. Two repos,
# two delivery mechanisms, and only one of them is automatic — a hand-maintained
# `version = "x.y.z"` literal in THIS file is a claim about code that lives in
# the OTHER one, and nothing keeps the two together. Measured 2026-08-14:
#
#   17:44  homelab-infra 978c549c (#323) added `task status` + `task comment` to
#          clawgatectl and set the Go default buildVersion to "0.7.95".
#   17:48  devrc 1b790b3 (#483) bumped THIS FILE's literal 0.7.87 -> 0.7.95 and
#          shipped to both hosts. The changed ldflag forced a rebuild on both.
#
# The workbench's homelab-talos was current, so it got a genuine 0.7.95. The
# laptop's was frozen 24 commits back at 6e9055f7; its client.go correctly said
# "0.7.87", and the ldflag OVERWROTE that truth with "0.7.95". The laptop then
# carried a binary with no `task status` and no `task comment` wearing the label
# of one that had both: `clawgatectl task status <id> in_progress` printed help
# and exited 0 — a silent no-op, against a CLI whose write ritual the `clawgate`
# skill makes mandatory. No check anywhere could see it (drift-check.sh was
# green on that host throughout; it only knew about devrc).
#
# So `version` is now DERIVED from the very file being compiled — the Go default
# at cmd/clawgatectl/client.go — and the ldflag stamps that same derived value.
# Both the store path and the stamped buildVersion therefore describe the code
# that is actually in the tree. A stale checkout now reports its own real
# version, which is the point: client.go compares the server's /health version
# against buildVersion by EXACT EQUALITY and prints a one-line skew note when
# they differ, so a 0.7.87 binary talking to a 0.7.95 server says so. Had this
# been in place on 08-14 the laptop would have printed exactly that.
#
# 🔴 NO FINGERPRINT, SUFFIX OR "-dirty" MARKER MAY BE APPENDED TO buildVersion.
# That equality check is what makes the skew note meaningful; any suffix makes
# it fire on every command on every host forever, and a permanently-noisy
# warning is worse than none — it teaches the operator to ignore the one line
# that has to keep its meaning. Let the existing mechanism work.
#
# 🔴 AN UNPARSEABLE SOURCE MUST NOT FALL BACK TO A LITERAL — that is the lie
# again, in a new shape. It sets available = false instead, so the binary is
# simply not installed and the failure is LOUD at use time (`clawgatectl:
# command not found`) rather than silent at build time. Failing the switch is
# the worse outcome and is deliberately not what happens: ship.sh reports a
# failed switch as a SKIPPED host, which this repo's CLAUDE.md documents as the
# failure mode that silently stops all future delivery to that machine.
#
# If clawgatectl ever needs to build on a host without the checkout, the fix is
# to move it to its own public repo and swap this for fetchFromGitHub — the
# version extraction below is unchanged by that, since it reads the source tree
# whatever produced it.
{ pkgs, workspace }:

let
  # The whole Go module, not just cmd/clawgatectl: go.mod/go.sum live at its
  # root and buildGoModule needs them for the vendor derivation.
  srcDir = "${workspace}/homelab-talos/containers/clawgate";

  # 🔴 Guard on THIS COMMAND'S OWN SOURCE FILES, not on the repo and not on the
  # module. "Does the checkout exist" is the wrong question and was MEASURED
  # wrong on 2026-08-13: the laptop has ~/workspace/homelab-talos (so a
  # directory check passes) and it has go.mod (so a module check passes), but
  # its checkout sat at c417af30 — well behind the commit that added
  # cmd/clawgatectl. Under either weaker guard the derivation would be built and
  # `subPackages` would fail deep in the Go build, taking down that host's whole
  # home-manager switch — which ship.sh reports as a SKIPPED host, the failure
  # mode this repo's CLAUDE.md warns silently stops all future delivery.
  # Guarding on main.go also covers the empty-placeholder-directory case.
  mainFile = "${srcDir}/cmd/clawgatectl/main.go";

  # The single source of truth for the version, and the file whose `var
  # buildVersion = "…"` line is BOTH the Go default and what this derivation
  # stamps back in. Guarded by the same pathExists rule as main.go: a checkout
  # too old to have it is a host that does not get the binary, not a host whose
  # switch fails.
  versionFile = "${srcDir}/cmd/clawgatectl/client.go";

  # 🔴 EXACTLY ONE MATCHING LINE, OR NOTHING. Zero matches means the declaration
  # was renamed or reformatted; two or more means the pattern has become
  # ambiguous and picking either one would be a guess presented as a fact. Both
  # land on `null`, which switches the package off — see the header on why that
  # is preferable to both a literal fallback and a failed switch.
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

  # Both halves are required. main.go answers "is this checkout new enough to
  # build at all"; parsedVersion answers "can this derivation state truthfully
  # what it is building". A package that cannot answer the second is not
  # installed — it is never labelled with a guess.
  available = builtins.pathExists mainFile && parsedVersion != null;

  clawgatectl = pkgs.buildGoModule {
    pname = "clawgatectl";
    version = parsedVersion;

    src = pkgs.lib.cleanSource (/. + srcDir);

    # MEASURED by building with a deliberately wrong hash and taking the "got:"
    # value from the failure. Do not hand-edit: change go.mod/go.sum and this
    # must be re-derived the same way.
    vendorHash = "sha256-T2rgqEv5MEWXkOaAHNWpxEUY/Z60x7lGS/lyWWxNhPg=";

    # Only the CLI. The module also contains the clawgate SERVER, which pulls in
    # helm and client-go and has no business being built on a laptop.
    subPackages = [ "cmd/clawgatectl" ];

    # buildVersion is what the CLI compares the live server's /health version
    # against to print its one-line skew note, and what exit 7 quotes.
    #
    # 🔴 This stamp is now an IDENTITY — parsedVersion was read out of the very
    # `var buildVersion` line this overwrites — and that is exactly the property
    # that was missing. It is kept rather than deleted because it is what makes
    # the store path and the compiled-in string provably the same value, and
    # because the link-time override is the documented mechanism (client.go says
    # so). It must never be given anything but parsedVersion: the moment it can
    # differ, the binary can lie about itself again.
    ldflags = [ "-s" "-w" "-X main.buildVersion=${parsedVersion}" ];

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
