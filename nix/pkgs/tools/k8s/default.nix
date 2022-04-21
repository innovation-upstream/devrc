{ pkgs ? import <nixpkgs> {} }:

with pkgs;
let
  tilt = buildGoModule rec {
    pname = "tilt";
    version = "0.27.2";

    src = fetchFromGitHub {
      owner = "tilt-dev";
      repo = pname;
      rev = "v${version}";
      sha256 = "sha256-dvY5kiLJ3psoQxG12E4qOjgF9GdXpjRKU3HlbPvwWBU==";
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
