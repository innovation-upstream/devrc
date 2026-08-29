{ pkgs, workspace }:

with pkgs; [
  docker-compose
  lazygit
  k9s
  nemo-with-extensions

  # 🔴 opencode — DECLARATIVE ON PURPOSE, and version-pinned by flake.lock.
  #
  # It used to be an IMPERATIVE `nix profile install nixpkgs#opencode` on both
  # hosts, which drifted: MEASURED 2026-08-02, laptop 1.18.4 / workbench 1.18.9,
  # each movable independently by a `nix profile upgrade` that nothing records.
  # scripts/opencode/opencode.jsonc documents a large set of load-bearing
  # behaviours annotated "measured on v1.18.21 — do not re-derive" (last-match-
  # wins permission ordering, the hidden title/summary/compaction agents
  # inheriting the global permission block, the exact tool set). A few bullets
  # there — `ask` semantics under `opencode run` among them — are explicitly
  # CARRIED FORWARD at an older version instead, each saying so on its own line.
  # Those claims were pinned to a version nothing pinned.
  #
  # This entry pins them: MEASURED at flake.lock's nixpkgs rev c27cdad491a9,
  # `pkgs.opencode` is 1.18.21 — store path
  # /nix/store/iqc8xfx692ym3pds6ky0vhqzscg6kgxd-opencode-1.18.21. (It was
  # 1.18.4 at rev 9bc02893134c when this pin was introduced, 1.18.16 at rev 044bfe75bfe4, 1.18.18 at rev 5c680dac9f02;
  # the 2026-08-13, 2026-08-19 and
  # 2026-08-29 bumps each re-derived the claims
  # against the new binary rather than re-spelling them — see PINNED_VERSION in
  # scripts/tests/test_opencode_engine.py.) A nixpkgs bump
  # that moves it now shows up as a flake.lock diff AND fails
  # scripts/tests/test_opencode_engine.py's version assertion, which is the
  # prompt to re-derive the header's measurements rather than let them rot.
  #
  # 🔴 PREREQUISITE, once per host, BEFORE the first switch that carries this:
  #     nix profile remove opencode
  # MEASURED (reproduced in a throwaway profile): the imperative entry and
  # home-manager-path are both priority 5 in the SAME profile, so both providing
  # bin/opencode is a HARD `nix profile` file collision — the switch FAILS with
  # "files in this package conflict with other packages". It is not silent
  # shadowing, so a missed prereq is loud, not wrong.
  opencode
]
++ (import ./tmux-fuzzyclaw.nix { inherit pkgs workspace; })
# clawgatectl — machine client for the clawgate JSON API. Built from the
# homelab-talos working tree; the file itself explains why that is a local path
# and not fetchFromGitHub, and it yields [] on a host without that checkout
# rather than failing the switch.
++ (import ./clawgatectl.nix { inherit pkgs workspace; })
