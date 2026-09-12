{
  description = "DEVRC - personal development environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    # ------------------------------------------------------------------------
    # SECOND, DELIBERATELY-FROZEN nixpkgs — the ONLY thing taken from it is
    # `playwright-driver` at 1.57.0. Do NOT `nix flake update` this input; it is
    # a version pin, not a channel, and moving it is the whole failure mode it
    # exists to prevent.
    #
    # WHY A SECOND INPUT AT ALL: Playwright's version→browser-build mapping is
    # strict 1:1. One nixpkgs revision can only ever offer ONE playwright-driver
    # version, so a single input structurally cannot serve two projects pinned
    # to different playwright releases — which is now the normal case here:
    #   • civitai/civitai            pins ^1.57.0  → chromium-1200
    #   • civitai-developer-docs     pins ^1.61.1  → chromium-1228
    # MEASURED 2026-08-06 (the failure reproduced, then fixed, in one session):
    # the 1.61.1 bundle ships chromium-1228 / chromium_headless_shell-1228 only,
    # so the civitai `component` vitest project COLLECTED 130 files and EXECUTED
    # 0 of them — `Test Files (130)` / `Tests no tests` / 180ms / exit 1. A zero
    # that reads exactly like a broken suite rather than a missing browser
    # build. With the 1.57 bundle selected: 130 passed (130) / 1304 passed
    # (1304) / exit 0, on an unchanged repo.
    #
    # WHY THIS EXACT REV (d61c78f4921b, nixpkgs 2026-03-09): it is inside the
    # window where nixpkgs' playwright-driver was 1.57.0 — bumped in to 1.57.0 at
    # 145b67bd0bd4 (2026-01-03) and out to 1.58.2 at 9cded172058d (2026-03-15).
    # Verified by evaluation, not by reading the log:
    #   nix eval --raw github:NixOS/nixpkgs/d61c78f4921b…#playwright-driver.version
    #     → 1.57.0
    # and the realised bundle contains chromium-1200 / chromium_headless_shell-1200.
    # Chosen near the END of that window so the closure is as close to the
    # current channel as the pin allows: `--dry-run` reports 1 derivation built
    # (the fixed-output browser fetch) + 228 paths substituted from
    # cache.nixos.org, i.e. no stdenv rebuild.
    #
    # NOT `follows = "nixpkgs"` — that would defeat the pin entirely. The
    # duplicated nixpkgs eval is the price of the second driver version; it is
    # paid only when something actually asks for the 1.57 output.
    # ------------------------------------------------------------------------
    nixpkgs-playwright-1_57.url = "github:NixOS/nixpkgs/d61c78f4921b1622127584d67b2e9afddf588c92";

    # ------------------------------------------------------------------------
    # `cairn` — the subsystem-store client, consumed as a PACKAGE instead of
    # being forked into `scripts/cairn`. The extracted OSS repo is now the one
    # copy of the reader; devrc keeps only what the OSS repo deliberately does
    # not have (`scripts/cairn-who`, the writer, and the `scripts/lib/` modules
    # those two still import).
    #
    # 🔴 THE PIN IS `flake.lock`, NOT THIS URL. The packaged client's VERSION IS
    # ITS GIT REVISION — cairn's flake derives it from `self.shortRev` precisely
    # so an artefact cannot be mislabelled — which is worth nothing if the input
    # resolves to whatever the branch holds at build time. Move it with
    # `nix flake lock --update-input cairn` and record what changed;
    # `scripts/tests/test_cairn_flake_pin.py` fails if the lock entry goes away.
    #
    # 🔴 DELIBERATELY *NOT* `inputs.nixpkgs.follows = "nixpkgs"`, and the reason
    # is upstream's rather than ours. cairn's own flake pins `pkgs.python312`
    # because `server/Dockerfile` is `python:3.12-slim` and its CI pins 3.12; a
    # bare `pkgs.python3` there once followed nixpkgs to 3.14 and shipped an
    # interpreter NOTHING in that repo had ever run its suite under (that suite
    # already emits a 3.14 tar-extraction DeprecationWarning, so the gap was
    # behaviourally live, not theoretical). Following would rebuild the client
    # against devrc's `nixpkgs-unstable` — a nixpkgs cairn's CI has never tested
    # — so the thing deployed would stop being the thing that was tested. Same
    # SHAPE of argument as the frozen playwright input above, different
    # mechanism: that one is a version freeze, this one is a refusal to override
    # someone else's freeze. The cost is a second nixpkgs in the lock, evaluated
    # only when something asks for the cairn output.
    # ------------------------------------------------------------------------
    cairn.url = "github:ZacxDev/cairn";
  };

  outputs = { self, nixpkgs, home-manager, nixpkgs-playwright-1_57, cairn, ... }:
    let
      system = "x86_64-linux";
      # Explicit allowUnfree so unfree pkgs (elixir-ls, playwright browsers)
      # build without relying on an ambient NIXPKGS_ALLOW_UNFREE / --impure.
      # ---------------------------------------------------------------------
      # 🔴 THE OVERLAY EXISTS SO ONE PACKAGE IS SPELLED `pkgs.nvim-octo`, AND
      # THAT SPELLING IS LOAD-BEARING RATHER THAN COSMETIC.
      #
      # `nvim-octo` is consumed in two places: `nix/pkgs/tools/default.nix`
      # (so the operator has it on PATH) and — the one that matters — the hint
      # wrapper's `lib.makeBinPath` in `nix/programs/alacritty/default.nix`.
      # That list is pinned against `mention-open.py`'s own syntax tree by
      # `test_mention_open.py::test_the_alacritty_wrapper_PATH_covers_every_
      # executable_the_handler_spawns`, which reads the block TEXTUALLY and
      # matches `pkgs\.[A-Za-z0-9_-]+`. A local `let`-bound derivation is not
      # spelled that way, so it would be INVISIBLE to the reader: the wrapper
      # would look like it pins nothing for `nvim-octo`, and the seam that test
      # exists to hold — "everything the handler spawns is on the wrapper's
      # PATH" — would quietly stop covering the review TUI.
      #
      # So the package is put into the package set instead of being threaded as
      # an argument. `callPackage` is not used: the derivation takes `pkgs`
      # whole (it reaches `pkgs.vimPlugins`, `pkgs.neovim` and
      # `pkgs.writeShellApplication`), so it is called with `final` directly —
      # which also means a later overlay can still override it.
      # ---------------------------------------------------------------------
      nvimOctoOverlay = final: _prev: {
        nvim-octo = import ./nix/pkgs/tools/nvim-octo { pkgs = final; };
      };

      pkgs = import nixpkgs {
        inherit system;
        config.allowUnfree = true;
        overlays = [ nvimOctoOverlay ];
      };
      # Same allowUnfree treatment for the frozen 1.57 nixpkgs — the browser
      # bundle is unfree there too, and an --impure fallback would make the
      # 1.57 path behave differently from the default one.
      pkgs157 = import nixpkgs-playwright-1_57 {
        inherit system;
        config.allowUnfree = true;
      };

      # ---------------------------------------------------------------------
      # THE GATE'S TOOLCHAIN — ONE list, TWO consumers.
      #
      # `scripts/run-tests.sh` asserts a REQUIRED_TOOLS precondition and exits 3
      # when a binary is missing, because the suites `skipif` on these and a
      # missing one would take the run GREEN while testing less. That
      # precondition is correct and stays. What it lacked was a discoverable way
      # to SATISFY it: the FATAL named `flake.nix checks.pytests
      # nativeBuildInputs`, which is a derivation you cannot stand in, so a
      # contributor's only route was to reverse-engineer the list into an ad-hoc
      # `nix-shell -p ...`. Getting that list wrong reads as a red gate that
      # looks like a code failure.
      #
      # So the same list now also backs `devShells.default`, and the FATAL names
      # `nix develop` (see run-tests.sh's GUARD 1). Hoisted to ONE binding
      # rather than copied: two hand-maintained copies is how the shell that is
      # supposed to satisfy the precondition drifts out of satisfying it, and
      # that drift would resurface as exactly the message this fixes.
      # `scripts/tests/test_devshell_satisfies_required_tools.py` pins the
      # relationship in the other direction -- every REQUIRED_TOOLS entry must
      # be reachable from this list.
      # ---------------------------------------------------------------------
      # 🔴 NO COMMENTS INSIDE THE LIST BELOW. GUARD 1c
      # (test_guard1c_dep_list_matches_the_flake_interpreter) parses this list
      # TEXTUALLY, so a comment's words are read as package names and it fails
      # naming every word. Put the prose here instead.
      #
      # pytest-xdist: scripts/run-tests.sh runs each target `-n 4 --dist
      # loadfile`. Measured on the whole gate: 1194s serial -> 576s (2.07x),
      # with the dominant target (scripts/tests, 57% of the run) going
      # 677s -> 223s. Read run-tests.sh's PARALLELISM header before changing the
      # job count or the dist mode — `loadfile` and the nested-run serialisation
      # are both load-bearing, not tuning.
      gatePyEnv = pkgs.python312.withPackages (ps: with ps; [
        pytest
        pytest-xdist
        requests
        psycopg2
        minio
        pyyaml
      ]);
      # Read the per-entry justifications on checks.pytests' nativeBuildInputs
      # below before adding or removing anything here.
      # age: scripts/tests/test_analyze_service_index_backup.py resolves `age` and
      # `age-keygen` at IMPORT and raises rather than skipping — deliberately, and
      # for the same reason test_analyze_service_index_commit.py does: a skipped
      # backup test reports safety it never measured, which for a disaster-recovery
      # feature is quiet exactly when it is wrong. That suite runs the real
      # encrypt/decrypt round trip (it generates its own throwaway identity; the
      # operator's key is never touched), so without age here the whole file is an
      # import error inside the sandbox rather than a green-with-skips.
      gateTools = [
        gatePyEnv pkgs.bash pkgs.ripgrep pkgs.git pkgs.util-linux pkgs.jq
        pkgs.gnugrep pkgs.curl pkgs.nodejs pkgs.nix pkgs.opencode pkgs.logrotate
        pkgs.rsync pkgs.zsh pkgs.age pkgs.dash
        # 🔴 cairn — THE PINNED CLIENT, AND IT IS NOT A TEST-ONLY CONVENIENCE.
        # devrc deleted its five forked reader modules and resolves them from
        # this package at runtime (`scripts/lib/cairn_pin.py`: `which cairn` ->
        # realpath -> `libexec/cairn/lib`). So every suite that imports the
        # writer or the reader needs it on PATH, `run-tests.sh` asserts it in
        # REQUIRED_TOOLS, and putting it HERE — in the shared list — is what
        # makes `nix develop` and both check tiers satisfy that precondition
        # from one place.
        #
        # 🔴 IT IS THE SAME PACKAGE `homeConfigurations.zach` DEPLOYS, resolved
        # from the same `flake.lock` entry, so the gate exercises the exact
        # client the hosts run rather than a second build of it. That is also
        # the cost, stated as plainly as the nodejs/nix/opencode entries below
        # are: a `nix flake lock --update-input cairn` now invalidates this
        # check's build cache AND can turn it red — which is the point, because
        # every other cairn guard in this repo reads text and stays green while
        # the pinned client is broken.
        #
        # ⚠ `cairn` is a PRIVATE flake input, absent from cache.nixos.org, so on
        # a cold CI store this leg may pay a real build. Unmeasured here; the
        # same caveat is recorded on `checks.cairn-client-runs` below.
        cairn.packages.${system}.cairn
        # 🔴 tmux, because two of tmux-reply-agent's guards CANNOT be written
        # against a stub. A stub tmux always exits 0, so it models neither the
        # session PREFIX-MATCH (`-t scratch2:` opening a window in `scratch20`)
        # nor the `-c <missing path>` fallback to the home directory -- both
        # measured live, both shipped, both invisible to a stubbed suite. Those
        # tests drive a REAL tmux on a private `-L` socket, and this entry is what
        # stops them SKIPPING in the check sandbox, where a skip is an error.
        pkgs.tmux
        # 🔴 fzf, for the SAME reason and it was learned the same way. The
        # mention picker's ranking (`--tiebreak=end`, the whole point of
        # replacing rofi) is a property of fzf's ALGORITHM, so the four tests
        # that pin it run the REAL binary — two through `--filter`, two through
        # a pty. MEASURED 2026-09-09: without this entry the dev-host tier ran
        # them and the check sandbox SKIPPED all four, and `run-tests.sh` failed
        # the derivation on the unpinned skips. That is the guard working: the
        # authoritative tier would otherwise have carried zero coverage of the
        # reason this change exists. It is also a PRODUCTION dependency now —
        # `nix/programs/alacritty/default.nix` puts `pkgs.fzf` on the hint
        # wrapper's PATH — so the gate environment carrying it is not a
        # test-only convenience.
        pkgs.fzf
      ];
    in
    {
      # ---------------------------------------------------------------------
      # Playwright driver + browsers — a REGISTRY of versions, not a single
      # value, because Playwright's version→chromium-build mapping is 1:1 and
      # this host drives projects pinned to different Playwright releases.
      #
      # NAMING IS LOAD-BEARING — scripts/playwright-nixos derives the attr name
      # from the project's own installed Playwright version by convention:
      #     <major>.<minor>.<patch>  →  playwright-driver-<major>_<minor>
      # so `1.57.0` → `playwright-driver-1_57`. Adding another version means
      # adding an input + one line here with that exact spelling; the wrapper
      # needs no edit. `--list` enumerates whatever is present.
      #
      # `playwright-driver` (unsuffixed) is the DEFAULT and stays on the current
      # channel's version (1.61.1 today). It is what:
      #   • nix/sessionVariables.nix exports GLOBALLY as
      #     PLAYWRIGHT_BROWSERS_PATH (interactive shells + the Playwright MCP),
      #     via pkgs.playwright-driver.browsers in nix/home.nix — same locked
      #     nixpkgs, so the two can never diverge on a channel bump;
      #   • scripts/playwright-nixos falls back to when a project pins a version
      #     with no matching output here, or when no project can be detected.
      # Every non-default entry is therefore strictly OPT-IN: nothing that works
      # on 1.61.1 today changes because a 1.57 bundle now exists alongside it.
      #
      # Both consumers resolve `<repo>#playwright-driver…`, NOT the ambient
      # `nixpkgs#…` registry — the registry tracks the moving unstable channel
      # and would silently drift out of sync with the HM export.
      # ---------------------------------------------------------------------
      packages.${system} = {
        playwright-driver = pkgs.playwright-driver;             # 1.61.1 → chromium-1228
        playwright-driver-1_57 = pkgs157.playwright-driver;      # 1.57.0 → chromium-1200
      };

      homeConfigurations."zach" = home-manager.lib.homeManagerConfiguration {
        inherit pkgs;
        # 🔴 `cairnPackage` IS THREADED, NOT LOOKED UP. `nix/home.nix` cannot
        # reach a flake input on its own — it is a home-manager module, and its
        # only channel from here is `extraSpecialArgs`. It takes the argument
        # WITHOUT a default on purpose, so a thread that gets cut is an
        # evaluation error rather than a `~/.local/bin/cairn` symlink quietly
        # pointing at `/bin/cairn`. The name differs from the input's
        # (`cairn` -> `cairnPackage`) so "is the input wired" and "is the
        # package wired" are not the same substring to a grep or a guard.
        extraSpecialArgs = {
          isNixOS = true;
          cairnPackage = cairn.packages.${system}.cairn;
        };
        modules = [
          ./nix/home.nix
          {
            home.username = "zach";
            home.homeDirectory = "/home/zach";
          }
        ];
      };

      # ---------------------------------------------------------------------
      # Test gate. `nix flake check` (and any future CI / `ship.sh --check`)
      # runs the HERMETIC Python suite in the nix sandbox: pinned python312,
      # NO network, NO /home. Every third-party call in the gated suites
      # (psycopg2 / requests / minio HTTP) is mocked, so nothing reaches a live
      # DB or the network — see scripts/run-tests.sh for the exact dir list.
      #
      # Deps below cover the modules-under-test's import-time requirements
      # (requests/psycopg2/minio/pyyaml); the tests themselves mock the I/O.
      # ---------------------------------------------------------------------
      # ---------------------------------------------------------------------
      # `nix develop` — the environment `scripts/run-tests.sh` demands, by name.
      #
      # Built from the SAME `gateTools` list as checks.pytests below, so the
      # shell a contributor is told to enter cannot drift out of satisfying the
      # precondition that told them to enter it.
      #
      # This is a shell, NOT a second gate: `nix flake check` remains the
      # hermetic authority (no network, no /home). The shell just makes the
      # authoritative runner runnable by hand.
      # ---------------------------------------------------------------------
      devShells.${system}.default = pkgs.mkShell {
        name = "devrc-gate";
        packages = gateTools;
        shellHook = ''
          # 🔴 Marks a SANCTIONED gate environment. run-tests.sh GUARD 1 uses it to
          # tell a REPO defect from a CALLER defect: a REQUIRED_TOOLS entry missing
          # while this is set means the repo asked for something `gateTools` does
          # not supply (or the entry is a typo) — that BLOCKS. Missing while it is
          # unset means the caller is simply not in the gate env — that degrades.
          # Set in BOTH tiers (here and checks.pytests) or the sandbox would
          # misclassify its own repo defects as environment faults.
          export DEVRC_GATE_ENV=1
          # 🔴 LOCALE_ARCHIVE IS THE LOAD-BEARING HALF, AND IT IS SET IN BOTH
          # TIERS FOR THE SAME REASON DEVRC_GATE_ENV IS. A test whose subject is
          # COLLATION must see the same locales in each, or the gating tier
          # silently degrades to C and stops observing the bug: measured, with
          # both `comm` calls in seed.sh unpinned, the sandbox reported ONE
          # failure where the dev host reported TWO.
          #
          # ⚠ TWO CORRECTIONS ON THE RECORD, because the first attempt to
          # correct this was itself wrong.
          #
          # An earlier version added `pkgs.glibc.bin` to gateTools, on the
          # stated grounds that a collation test "cannot run" without the
          # `locale` binary. MEASURED FALSE: with LOCALE_ARCHIVE set and no
          # `locale` binary at all, `LC_ALL=en_US.UTF-8 sort` collates
          # correctly. Only a test's *probe* wanted it; the test now detects the
          # locale by exercising the capability, so the entry was dropped.
          #
          # 🔴 The commit that dropped it then justified the removal by saying
          # the entry had been "shadowing the system getconf/ldd/iconv for every
          # human in `nix develop`". ALSO MEASURED FALSE — and after the
          # removal: `nix develop` still resolves `locale` and `getconf` from a
          # STORE glibc-bin, because `mkShell`'s stdenv cc-wrapper supplies it
          # regardless (a bare `mkShell { packages = []; }` resolves them too).
          # What the removal actually changes is the SANDBOX, which is
          # `runCommandLocal`/stdenvNoCC and has no cc-wrapper — the opposite
          # tier from the one that claim credited. The entry is still right to
          # drop, for the first reason and not the second.
          export LOCALE_ARCHIVE=${pkgs.glibcLocales}/lib/locale/locale-archive
          echo "devrc: gate toolchain ready — bash scripts/run-tests.sh ." >&2
        '';
      };

      checks.${system} = {
        pytests =
        pkgs.runCommandLocal "devrc-pytests"
          {
            # ripgrep: repo-cos (its only consumer) retired 2026-09-07. Kept because
            # gateTools and run-tests.sh's REQUIRED_TOOLS are pinned two-way, so
            # dropping it is a separate, deliberate change with its own re-measure.
            # git: verify-agent-work tests drive real temp git repos in-sandbox.
            # util-linux: the browser-agent wrapper uses `setsid` for its
            # process-group timeout kill (test_browser_agent.py exercises it).
            # jq + gnugrep: task-spec-drafter's behavioral tests
            # (test_severity_and_gate_skip.py) source drafter.sh and exercise the
            # real safety_gate/severity_tag/build_summary via bash+jq+grep; without
            # these on PATH the suite pytest-skips and the gate goes green without
            # ever running the invariant (e.g. "TASK-with-risk STILL escalates").
            # curl: the `browser` CLI shells out to curl, and two suites drive the
            # REAL script (test_server.py, and test_browser_cli_args.py against a
            # stub LOOPBACK server — still hermetic, no outbound network).
            #
            # MEASURED, not assumed: `nix build .#checks.x86_64-linux.pytests` at
            # 69f5334 (before this line) exited 1 with
            #   5 failed, 258 passed, 41 skipped
            # — four `AssertionError: browser: curl not found on PATH` in
            # test_server.py (those callers are NOT skip-guarded), plus one
            # `token file not found/readable` from the --help test. So the gate was
            # RED, not "green while silently skipping": 41 test_server.py tests
            # skipped AND five failed. Adding curl here (with the --help/token fix
            # in the CLI) is what takes it green and makes those 41 actually run.
            #
            # nodejs: MEASURED 2026-08-02 against the initiatives viewer suite, which
            # extracted the viewer's inline JS and ran it under `node`; without node on
            # PATH 123 of this check's 125 skips came from there. That suite RETIRED with
            # the initiatives board (2026-09-07), so the measurement no longer describes
            # the closure — but node is STILL required, by
            # scripts/tests/test_opencode_session_env_plugin.py and
            # scripts/tests/test_skill_mjs_parses.py, which skip without it. Re-measure
            # before quoting a skip count. This does grow the pytest gate's closure
            # by a node toolchain — accepted deliberately: 123 silently-unrun tests
            # cost more than a cache invalidation on a nodejs bump. The `nodetests`
            # check below stays separate for its OWN reasons (distinct failure
            # signal, parallel execution), which this does not change.
            #
            # nix: MEASURED 2026-08-02 — scripts/tests/test_opencode_config.py's
            # `nix_eval()` shells out to `nix-instantiate --eval` to pin the
            # GENERATED handle values from nix/agent-handles.nix, and calls
            # `pytest.fail()` (NOT skip) when the binary is absent, deliberately:
            # "a skip here is how a wrong kubeconfig path ships". Without nix on
            # PATH that fired for all 10 `test_handles_resolve_to_the_exact_
            # expected_paths[...]` cases — `10 failed, 455 passed` in that file,
            # reproduced on a dev host by stripping PATH down to python alone.
            #
            # 🔴 This was INVISIBLE until #289. Those 10 fail ONLY in the sandbox
            # (every dev host has nix) and were hidden behind the gate being red
            # for an unrelated reason. Its sibling defect is the exact complement:
            # session_insight's test_patterns_cover_bash_guard fails only on a
            # host where ~/.claude/hooks/bash-guard.py is DEPLOYED, and skips
            # here. A two-tier suite needs BOTH tiers read — see claude/RULES.md.
            #
            # Cost, stated as deliberately as the nodejs one above: this grows the
            # pytest gate's closure by the nix package, and a nixpkgs bump that
            # moves `nix` now invalidates this check's build cache (the same trade
            # already accepted for nodejs). Bought: 10 tests that structurally
            # cannot run without it, pinning that the handles every agent shell
            # exports resolve to the exact expected paths. The alternative —
            # moving them to DEVHOST_TARGETS — would keep the closure small but
            # weaken the pin to "runs only where someone remembers to run it",
            # which is the failure mode this whole area keeps hitting.
            # `--eval` is a PURE evaluation: agent-handles.nix is `{ home }: {…}`
            # with no nixpkgs import and no fetch, so it needs no daemon, no
            # network and no store realisation.
            #
            # 🔴 run-tests.sh now ASSERTS every one of these is on PATH
            # (`REQUIRED_TOOLS`) and fails naming the missing binary. Deleting one
            # from this list no longer silently skips tests — it fails the gate.
            # opencode: MEASURED 2026-08-02 — scripts/tests/test_opencode_engine.py
            # runs the REAL `opencode debug agent <name> --pure` against a
            # throwaway OPENCODE_CONFIG_DIR seeded from scripts/opencode/, and
            # compares the engine's authoritative flat permission array against
            # the Python resolver model that every permission assertion in
            # test_opencode_config.py rests on. Without the binary those 19 tests
            # `pytest.fail()` (NOT skip) — the same deliberate choice as
            # nix-instantiate above, for the same reason.
            #
            # HERMETIC, verified before adding: `debug agent` is READ-ONLY (it
            # executes no tool), needs no network (OPENCODE_DISABLE_MODELS_FETCH
            # is baked into the derivation), materialises no node_modules, takes
            # ~1 s per call, and was reproduced byte-for-byte under an env
            # carrying only PATH/HOME/TMPDIR — which is what makes it sandbox-
            # gateable rather than a dev-host-only check.
            #
            # 🔴 This ALSO makes the sandbox the tier that pins the VERSION.
            # `pkgs.opencode` here and in nix/pkgs/tools/default.nix resolve from
            # the same flake.lock, so CI tests the exact binary the hosts deploy
            # (1.18.29 — derive the rev with `nix flake metadata --json | jq -r
            # .locks.nodes.nixpkgs.locked.rev`; a rev spelled here would be an
            # unguarded claim, since the version scanner sees only the version).
            # Cost, stated as deliberately as the
            # nodejs and nix entries above: this check's closure grows by
            # opencode, and a nixpkgs bump that moves it invalidates the cache
            # AND turns the version assertion red. That red is the point — the
            # config header's "measured on v1.18.29 — do not re-derive" claims are
            # otherwise pinned to nothing.
            #
            # logrotate: scripts/tests/test_claude_log_rotate.py drives the REAL
            # binary against a temp directory — it asserts that an oversized log
            # is rotated AND truncated in place (copytruncate), that a small one
            # is left alone, that the generation cap holds, and 🔴 that the nine
            # hand-made `.bak` config copies in ~/.claude are never touched. That
            # last one is a data-loss fence, so it must not be a skipif: those
            # tests FAIL naming the missing binary, the same deliberate choice as
            # nix-instantiate and opencode above. Hermetic — logrotate only ever
            # sees a pytest tmp_path, its own generated config and its own state
            # file; no network, no ambient /var/lib/logrotate.status.
            #
            # rsync: MEASURED 2026-08-16 — scripts/tests/test_subsystem_store_api.py
            # drives the REAL scripts/subsystem-store-api/seed.sh, whose one
            # copy step is `rsync -a --delete "$STORE"/ "$STAGE"/`. Without the
            # binary those tests fail with `rsync: command not found` (rc 127),
            # which is a FAILURE and not a skip, deliberately: the property they
            # pin is that seeding never writes to the local store, which is the
            # AUTHORITATIVE copy of client-confidential content — the daily MinIO
            # bundle and the served pod are both lagging derivatives, so a
            # corrupted source replicates outward rather than being repairable
            # from either — though a bundle already in the bucket still holds
            # what was reachable when it was written, so "lagging" is not
            # "useless": check what is in the bucket, with restore-verify.py,
            # before concluding anything is unrecoverable.
            #
            # 🔴 This block is the TWIN of the one in scripts/run-tests.sh — the
            # same argument in different words, and NOTHING ENFORCES THAT THEY
            # AGREE. They have drifted apart three times: on "the only copy", on
            # the bucket qualifier above, and on "check the retention window"
            # (which is false — see backup.py). Each time one was corrected and
            # the other was not, and each time the correcting commit believed it
            # had reconciled them. Change both in the same commit, or delete one
            # and point at the other. The same
            # nix-shell-stripped-PATH method as the `nix` entry above reproduced
            # it. Present on the workbench (`~/.nix-profile/bin/rsync`), so the
            # pre-push tier is unaffected.
            #
            # zsh: scripts/tests/test_run3.py. The rule `scripts/run3` enforces
            # is a zsh-vs-bash DIFFERENCE — MULTIOS duplicates stdout onto a
            # pipe, so `cmd 2>&1 >/dev/null | c` hands the consumer stdout where
            # bash hands it nothing — so a tier without zsh is structurally
            # blind to the entire class: both zsh tests would skip and the gate
            # would go green having exercised only the shell in which the defect
            # cannot occur. Hermetic enough to gate: `zsh -c` on a literal
            # string, no network, no writes. ⚠ It DOES source `~/.zshenv` — in
            # the sandbox HOME holds none, and on the workbench devrc's does not
            # touch MULTIOS, which is exactly why the trap is live there. That
            # is a property the control test ASSERTS rather than assumes.
            # Present on both hosts as the login shell, so the pre-push tier is
            # unaffected.
            # `gateTools` is defined in the outer `let` and is SHARED verbatim
            # with devShells.default — see the block that defines it. Adding a
            # tool there is what makes `nix develop` able to run this same
            # runner by hand; adding it only here would put the gate and the
            # shell back out of sync.
            nativeBuildInputs = gateTools;
          }
          ''
            cp -r ${./.} src
            chmod -R u+w src
            # Some suites exec real repo scripts (e.g. scripts/collector/emit)
            # by path; their `#!/usr/bin/env bash` shebang can't resolve in the
            # sandbox (no /usr/bin/env). Rewrite shebangs to store paths so those
            # legitimately-hermetic tests can run. (Does NOT touch test logic.)
            patchShebangs src/scripts
            # Same marker the devShell sets — see its shellHook for why.
            export DEVRC_GATE_ENV=1
            # Same reason as the devShell's copy — see its shellHook, which
            # also records why `pkgs.glibc.bin` is NOT here. Without this the
            # sandbox has only `C` and a collation test cannot be made to fail.
            export LOCALE_ARCHIVE=${pkgs.glibcLocales}/lib/locale/locale-archive
            export HOME="$TMPDIR/home"
            mkdir -p "$HOME"
            cd src
            # The runner asserts a tool precondition, a pinned expected-skip set,
            # per-target collected-test floors (and a global floor derived as
            # their sum), and parses pytest's summary instead of reading its
            # exit code. Read its header before changing this line or the
            # nativeBuildInputs above.
            #
            # 🔴 The rc is captured and branched on EXPLICITLY rather than left
            # to `touch "$out"` being harmless. MEASURED 2026-08-11 with a
            # throwaway `runCommandLocal` of exactly this shape — a body that
            # runs `bash -c 'echo "RESULT: FAIL"; exit 1'` and then `touch
            # "$out"` — the build DOES fail (`builder failed with exit code 1`),
            # because nixpkgs' generic builder evals `buildCommand` under
            # `set -e`. So this is not a live bug being fixed; it is a dependency
            # on stdenv's implicit `set -e` being made explicit, in the one file
            # where a trailing command after a test runner would be invisible.
            rc=0
            bash scripts/run-tests.sh --set hermetic . || rc=$?
            if [ "$rc" -ne 0 ]; then
              echo "checks.pytests: run-tests.sh exited $rc — failing the derivation." >&2
              exit "$rc"
            fi
            touch "$out"
          '';

      # ---------------------------------------------------------------------
      # Node test gate — every scripts/**/*.test.mjs suite (997 tests, measured
      # 2026-08-03 in this sandbox across three suites: browser-bridge 468,
      # dl-router 508, collector/browser-ext 21).
      #
      # 🔴 It covered ONLY browser-bridge until 2026-08-03, because the runner
      # hard-coded `FILES=(scripts/browser-bridge/tests/*.test.mjs)`. dl-router's
      # 508 tests and browser-ext's 21 were gated by NOTHING from the day each
      # suite was written — the #276/#298/#306 shape in a fourth costume. The
      # runner now DISCOVERS suites and pins them both ways; read its header
      # before changing this.
      #
      # Deliberately a SEPARATE check from `pytests`, not folded into it:
      #  • distinct toolchain (nodejs vs python312) — keeping them apart means
      #    the pytest gate's closure doesn't grow a node dependency, and a node
      #    toolchain bump can't invalidate the python gate's build cache;
      #  • distinct failure signal — `nix flake check` names the failing check,
      #    so "nodetests failed" vs "pytests failed" is legible without reading
      #    the log;
      #  • they run in parallel under `nix flake check`, so the wall-clock cost
      #    of adding this is ~0 next to the (much slower) python gate.
      #
      # Until this existed the .mjs suite gated NOTHING — flake.nix had zero
      # references to node, so every "460 pass" claim was a MANUAL run and a
      # .mjs regression could merge freely.
      #
      # HERMETIC (audited 2026-08-02): the suite has no `createServer`/`.listen()`,
      # no `spawn`/`execFile`, and every `fetch` is a stub assigned onto
      # `globalThis.fetch`. Its only I/O is reads of repo-relative files and
      # writes under `tmpdir()`. Nothing needs the network the sandbox denies —
      # so nothing is excluded, and the runner's test-count floor is what proves
      # that stays true.
      #
      # nodejs is pinned from THIS flake's locked nixpkgs, NOT whatever node
      # happens to be on the dev host's PATH. MEASURED 2026-08-02: the sandbox
      # runs v24.18.0 while the workbench's PATH node is v26.5.0 — the runner
      # echoes `node --version` on every run so the gate's toolchain is never a
      # guess, and that divergence is the proof the pin is real.
      # ---------------------------------------------------------------------
        nodetests =
        pkgs.runCommandLocal "devrc-nodetests"
          {
            nativeBuildInputs = [ pkgs.nodejs pkgs.bash pkgs.git pkgs.gnugrep pkgs.gawk pkgs.coreutils ];
          }
          ''
            cp -r ${./.} src
            chmod -R u+w src
            export HOME="$TMPDIR/home"
            mkdir -p "$HOME"
            cd src
            # The runner globs the .test.mjs files itself, asserts a file-count
            # and a TEST-COUNT floor, and refuses to degrade to `node --test <dir>`
            # (which silently yields a bogus `# tests 1 / # fail 1`). Read its
            # header before changing this line.
            #
            # rc captured explicitly — same reason as checks.pytests above.
            rc=0
            bash scripts/run-node-tests.sh . || rc=$?
            if [ "$rc" -ne 0 ]; then
              echo "checks.nodetests: run-node-tests.sh exited $rc — failing the derivation." >&2
              exit "$rc"
            fi
            touch "$out"
          '';

        # 🔴 WHETHER THIS IS A GATE OR ONLY AN OUTPUT IS DECIDED IN ANOTHER
        # REPO, SO VERIFY IT — DO NOT TRUST THIS COMMENT'S TENSE.
        # `devrc-ci-pipeline.yaml` lives in the infra repo. It hardcoded exactly
        # two legs (`LEG` ∈ {pytests, nodetests}, built as
        # `.#checks.x86_64-linux.${LEG}`) with no `nix flake check` and no loop,
        # so a third output was never built by CI and this check could not fail
        # a PR. An earlier revision of this block stated that as a flat fact —
        # and it was falsified by a change in a repository no gate here can see,
        # which is exactly why it is now written as something to check:
        #
        #     gh pr checks <any devrc PR>     # is a `cairn-client` leg listed?
        #
        # A `tekton/devrc-cairn-client-runs` context in that list means the leg
        # is wired. ⚠ Wired is not REQUIRED: it was deliberately left out of
        # branch protection so a brand-new leg could not block every merge on
        # its first bad day, so it can be present and still block nothing.
        # 🔴 Until you have checked, assume it gates NOTHING and do not weaken
        # or delete this check on the strength of it being one — that is the
        # defect class this check exists to catch, committed by the check itself.
        # The wiring change is `ZacxDev/homelab-infra#786`.
        #
        # Run it on demand: `nix build .#checks.x86_64-linux.cairn-client-runs`
        # (~1.4 s here; the cairn package is already in the home-manager
        # closure). ⚠ "It adds no build" holds for THIS host and is NOT
        # established for CI: `cairn` is a private flake input, absent from
        # `cache.nixos.org` and from the pytests/nodetests closures, so on a cold
        # `/nix` PVC — and on exactly the pin-bump PR this check exists for — the
        # leg may pay a real build. Unmeasured; do not quote it as a cost.
        #
        # 🔴 THE ONLY CHECK THAT *EXECUTES* THE PINNED CLIENT. Every other cairn
        # guard in this repo reads `flake.nix` / `flake.lock` / `nix/home.nix` /
        # `nix/sessionVariables.nix` as TEXT, so all of them stay green while the
        # pinned client is thoroughly broken: they assert the WIRING, never that
        # anything runs. A `nix flake lock --update-input cairn` to a revision
        # where `packages.cairn` still builds but a VERB regressed would leave
        # this repo's gate fully green and surface at the operator, mid-task.
        # cairn's own `checks.client-resolves-its-lib` would catch some of that,
        # and it lives in cairn's flake — which this repo's gate does not run.
        #
        # 🔴 IT ASSERTS OUTPUT CONTENT, NOT EXIT CODES, and the two verbs are
        # asserted DIFFERENTLY on purpose:
        #   `validate` — a fixture cache with exactly ONE parsable entry must
        #     produce "1 of 1 entry file(s) parse". That string is the gate: a
        #     client whose validate prints nothing fails here, which is this
        #     check's whole reason to exist.
        #   `doctor`   — asserted only to PRODUCE A REPORT. Its exit code is
        #     deliberately NOT asserted: in a sandbox with no pod, no token and
        #     no network, a non-zero doctor verdict is the CORRECT answer, and
        #     demanding zero would either pin a wrong expectation or push the
        #     check into faking an environment. Same reasoning cairn's own
        #     packaging check records.
        cairn-client-runs =
        pkgs.runCommandLocal "devrc-cairn-client-runs"
          {
            nativeBuildInputs = [
              cairn.packages.${system}.cairn
              pkgs.coreutils
              pkgs.gnugrep
            ];
          }
          ''
            export HOME="$TMPDIR/home"
            mkdir -p "$HOME"
            F="$TMPDIR/cache"
            mkdir -p "$F/demo"

            # A stamp is REQUIRED, not decoration: the reader refuses a store
            # that cannot date itself rather than serving it, so without this
            # the run below fails as `store-unreachable` and would "fail" for a
            # reason that says nothing about the client's verbs.
            printf 'synced=1700000000\nrevision=fixture\nentries=1\ncoverage=ALL\n' \
              > "$F/.sync-stamp"

            cat > "$F/demo/widget.md" <<'ENTRY'
            ---
            service: widget
            scope: demo
            sensitivity: public
            created_by: handoff
            ---
            # widget

            ## What it is
            A fixture entry, and the ONLY one — so "1 of 1" below is a real count.

            ## Pointers
            - `nowhere/real.py` — a pointer.

            ## Nuance / work-history
            - 2026-01-01: a bullet.
            ENTRY
            # The heredoc is indented to match this file; strip that leading
            # whitespace or the front matter is not at column 0 and the entry
            # parses as prose. (Measured: an indented `---` is not front matter.)
            sed -i 's/^            //' "$F/demo/widget.md"

            # --- validate: the verb this gate exists to police -------------
            rc=0
            cairn --cache "$F" validate --scope demo --no-sync > val.txt 2>&1 || rc=$?
            if ! grep -q '1 of 1 entry file(s) parse' val.txt; then
              echo "checks.cairn-client-runs: the pinned client's \`validate\` did not report" >&2
              echo "  parsing the one fixture entry. rc=$rc, output follows:" >&2
              sed 's/^/    /' val.txt >&2
              exit 1
            fi

            # --- doctor: drives the deep import closure --------------------
            # `--help` would NOT do: the hazard packaging introduces is the
            # sibling-import mechanism, and only a verb that reaches the deep
            # modules exercises it.
            cairn --cache "$F" doctor --no-sync > doc.txt 2>&1 || true
            if [ ! -s doc.txt ]; then
              echo "checks.cairn-client-runs: \`doctor\` produced NO output at all." >&2
              echo "  Its exit code is not asserted (no pod in a sandbox), but a" >&2
              echo "  client that cannot even report is not a working client." >&2
              exit 1
            fi

            touch "$out"
          '';
      };
    };
}
