{ pkgs ? import <nixpkgs> {} }:

with pkgs;
let
  tilt = buildGoModule rec {
    pname = "tilt";
    version = "0.22.2";

    src = builtins.fetchGit {
      url = "https://github.com/tilt-dev/tilt.git";
      ref = "master";
    };

    vendorSha256 = null;

    subPackages = [ "cmd/tilt" ];

    buildFlagsArray = [ "-ldflags=-X main.version=${version}" ];
  };
in
  [
    kube3d
    kubectl
    kubernetes-helm
    tilt
  ]

