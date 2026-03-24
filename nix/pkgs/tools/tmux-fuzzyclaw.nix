{ pkgs ? import <nixpkgs> {} }:

let
  home = builtins.getEnv "HOME";
  fuzzyclaw = pkgs.buildGoModule {
    pname = "tmux-fuzzyclaw";
    version = "2.0.0";

    src = pkgs.lib.cleanSource (builtins.path {
      path = "${home}/workspace/tmux-fuzzyclaw";
      name = "tmux-fuzzyclaw-src";
    });

    vendorHash = null;

    subPackages = [ "." ];

    ldflags = [ "-s" "-w" "-X github.com/zachatrocern/tmux-fuzzyclaw/cmd.Version=2.0.0" ];

    postInstall = ''
      mv $out/bin/tmux-fuzzyclaw $out/bin/fuzzyclaw
    '';

    meta = with pkgs.lib; {
      description = "Fuzzy task dashboard for tmux + Claude Code";
      homepage = "https://github.com/ZacxDev/tmux-fuzzyclaw";
      license = licenses.mit;
    };
  };
in
[
  fuzzyclaw
]
