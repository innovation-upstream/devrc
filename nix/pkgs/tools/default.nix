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
  # behaviours annotated "measured on v1.18.30 — do not re-derive" (last-match-
  # wins permission ordering, the hidden title/summary/compaction agents
  # inheriting the global permission block, the exact tool set). A few bullets
  # there — `ask` semantics under `opencode run` among them — are explicitly
  # CARRIED FORWARD at an older version instead, each saying so on its own line.
  # Those claims were pinned to a version nothing pinned.
  #
  # This entry pins them: at flake.lock's current nixpkgs rev, `pkgs.opencode`
  # is 1.18.30 (re-derived 2026-09-19).
  #
  # 🔴 THE REV AND THE STORE PATH ARE DELIBERATELY NOT SPELLED HERE. Only the
  # VERSION is, because only the version is guarded — `test_opencode_engine.py`'s
  # PINNED_VERSION scanner fails on a stale version literal in this file and can
  # see nothing else, so a hash written here would be an unguarded claim that
  # rots silently. Both are one command away, and neither can be stale that way:
  #     nix flake metadata ~/workspace/devrc --json \
  #       | jq -r .locks.nodes.nixpkgs.locked.rev          # the rev
  #     readlink -f "$(command -v opencode)"               # the store path
  # (say WHICH shell you ran the second one in — `nix develop` and the login
  # shell can carry different builds of the same tool.)
  #
  # It was 1.18.4 at rev 9bc02893134c when this pin was introduced, and 1.18.16
  # at rev 044bfe75bfe4; the 2026-08-13, 2026-08-19, 2026-08-29, 2026-09-08 and
  # 2026-09-19 bumps each re-derived the claims against the new binary rather than
  # re-spelling them — see PINNED_VERSION in
  # scripts/tests/test_opencode_engine.py. A nixpkgs bump
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
  #
  # 🔴 DONE, and do not reach for it again on a version-assertion red. Checked
  # on the workbench 2026-09-08: `nix profile remove opencode` answers "does not
  # match any packages in the profile" and the home-manager generation itself
  # provides bin/opencode. Every version red since 2026-08 has been a LOCK
  # MOVEMENT, which no profile removal and no switch can clear — the
  # discriminating check is in the assertion message of
  # test_engine_is_the_version_every_measurement_is_keyed_to.
  opencode
]
++ (import ./tmux-fuzzyclaw.nix { inherit pkgs workspace; })
# clawgatectl — machine client for the clawgate JSON API. Built from the
# homelab-talos working tree; the file itself explains why that is a local path
# and not fetchFromGitHub, and it yields [] on a host without that checkout
# rather than failing the switch.
++ (import ./clawgatectl.nix { inherit pkgs workspace; })
# mention-review — the nvim-octo replacement. Phase 2: it can also write.
#
# 🔴 ON PATH FOR TWO REASONS NOW. It is still run by hand
# (`mention-review <owner/repo> <number>`), and since the click path was flipped
# it is ALSO what an Alacritty `repo#N` hint spawns — see `REVIEW_EXE` in
# `scripts/mention-open.py` and the wrapper's `makeBinPath` in
# `nix/programs/alacritty/default.nix`, which a two-way ledger keeps in sync.
# ⚠ This entry is NOT what puts it on the click path's PATH — that wrapper pins
# its own closure precisely so a click landing during a switch still works.
#
# 🔴 THE NULL FILTER IS LOAD-BEARING, NOT DEFENSIVE. The derivation evaluates to
# `null` when it cannot read exactly one `var buildVersion` line out of the Go
# source — deliberately, so a binary is never labelled with a guessed version.
# Without this filter that null would land in `home.packages` and fail the
# SWITCH, which ship.sh reports as a SKIPPED host: the failure mode this repo's
# CLAUDE.md documents as silently stopping all future delivery to that machine.
# The whole point of the null is to be quieter than that.
++ (pkgs.lib.optional (pkgs.mention-review != null) pkgs.mention-review)
