{ pkgs ? import <nixpkgs> {} }:

let
  fuzzyclaw = pkgs.buildGoModule {
    pname = "tmux-fuzzyclaw";
    version = "2.0.0";

    src = pkgs.fetchFromGitHub {
      owner = "ZacxDev";
      repo = "tmux-fuzzyclaw";
      rev = "e46baeeab09fa5899778fd561054a5bfceb32b41";
      sha256 = "sha256-EX79PwI2J0ojdzIrarcTdyXg+kb0RVOte22iV2MTrvg=";
    };

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
