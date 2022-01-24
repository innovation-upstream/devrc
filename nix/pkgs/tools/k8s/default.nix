{ pkgs ? import <nixpkgs> {} }:

with pkgs;
let
  tilt = buildGoModule rec {
    pname = "tilt";
    version = "0.23.4";

    src = fetchFromGitHub {
      owner = "tilt-dev";
      repo = pname;
      rev = "v${version}";
      sha256 = "sha256-SWofXsbkuirPvqgU639W8IQklafLKbThoZUzOzfYwdQ=";
    };

    vendorSha256 = null;

    subPackages = [ "cmd/tilt" ];

    ldflags = [ "-X main.version=${version}" ];
  };
in
  [
    kube3d
    kubectl
    kubernetes-helm
    tilt
  ]
