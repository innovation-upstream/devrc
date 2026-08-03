{
  description = "DEVRC - personal development environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, home-manager, ... }:
    let
      system = "x86_64-linux";
      # Explicit allowUnfree so unfree pkgs (elixir-ls, playwright browsers)
      # build without relying on an ambient NIXPKGS_ALLOW_UNFREE / --impure.
      pkgs = import nixpkgs {
        inherit system;
        config.allowUnfree = true;
      };
    in
    {
      # Pinned Playwright driver + browsers, built from THIS flake's locked
      # (allowUnfree) nixpkgs — the single source of truth shared by both
      # Playwright paths so they can never diverge on a channel bump:
      #   • scripts/playwright-nixos resolves `<repo>#playwright-driver.browsers`
      #     (+ `.version`) — NOT the ambient `nixpkgs#…` registry, which tracks
      #     the moving unstable channel.
      #   • nix/sessionVariables.nix (Recipe 2 / the Playwright MCP) exports the
      #     same `pkgs.playwright-driver.browsers`.
      packages.${system}.playwright-driver = pkgs.playwright-driver;

      homeConfigurations."zach" = home-manager.lib.homeManagerConfiguration {
        inherit pkgs;
        extraSpecialArgs = { isNixOS = true; };
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
      checks.${system} = {
        pytests =
        let
          pyEnv = pkgs.python312.withPackages (ps: with ps; [
            pytest
            requests
            psycopg2
            minio
            pyyaml
          ]);
        in
        pkgs.runCommandLocal "devrc-pytests"
          {
            # ripgrep: one repo-cos prescan test skipif's without it on PATH.
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
            # nodejs: MEASURED 2026-08-02 — scripts/initiatives/tests/{test_viewer,
            # test_streaming}.py extract the viewer's inline JS and run it under
            # `node`; without node on PATH they `pytest.skip("node not on PATH")`.
            # That was 123 of this check's 125 skips (`660 passed, 123 skipped` in
            # the initiatives suite vs `783 passed, 0 skipped` on a host with node).
            # Adding it recovers all 123. This does grow the pytest gate's closure
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
            # (1.18.4 at rev 9bc02893134c). Cost, stated as deliberately as the
            # nodejs and nix entries above: this check's closure grows by
            # opencode, and a nixpkgs bump that moves it invalidates the cache
            # AND turns the version assertion red. That red is the point — the
            # config header's "measured on v1.18.4 — do not re-derive" claims are
            # otherwise pinned to nothing.
            nativeBuildInputs = [ pyEnv pkgs.bash pkgs.ripgrep pkgs.git pkgs.util-linux pkgs.jq pkgs.gnugrep pkgs.curl pkgs.nodejs pkgs.nix pkgs.opencode ];
          }
          ''
            cp -r ${./.} src
            chmod -R u+w src
            # Some suites exec real repo scripts (e.g. scripts/collector/emit)
            # by path; their `#!/usr/bin/env bash` shebang can't resolve in the
            # sandbox (no /usr/bin/env). Rewrite shebangs to store paths so those
            # legitimately-hermetic tests can run. (Does NOT touch test logic.)
            patchShebangs src/scripts
            export HOME="$TMPDIR/home"
            mkdir -p "$HOME"
            cd src
            # The runner asserts a tool precondition, a pinned expected-skip set,
            # a global + per-directory collected-test floor, and parses pytest's
            # summary instead of reading its exit code. Read its header before
            # changing this line or the nativeBuildInputs above.
            bash scripts/run-tests.sh --set hermetic .
            touch "$out"
          '';

      # ---------------------------------------------------------------------
      # Node test gate — scripts/browser-bridge/tests/*.test.mjs (460 tests).
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
            bash scripts/run-node-tests.sh .
            touch "$out"
          '';
      };
    };
}
