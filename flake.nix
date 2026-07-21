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
      checks.${system}.pytests =
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
            nativeBuildInputs = [ pyEnv pkgs.bash pkgs.ripgrep ];
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
            bash scripts/run-tests.sh --set hermetic .
            touch "$out"
          '';
    };
}
