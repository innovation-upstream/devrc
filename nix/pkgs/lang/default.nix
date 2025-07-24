{ pkgs ? import <nixpkgs> {} }:

with pkgs;
  (import ./golang.nix {pkgs=pkgs;})
  ++
  (import ./python.nix {pkgs=pkgs;})
  ++
  (import ./cue.nix {pkgs=pkgs;})
  ++
  (import ./rust.nix {pkgs=pkgs;})
  ++
  (import ./perl.nix {pkgs=pkgs;})
  ++
  (import ./lua.nix {pkgs=pkgs;})
  #++
  #(import ./dhall.nix {pkgs=pkgs;})
  ++
  (import ./solidity.nix {pkgs=pkgs;})
  ++
  (import ./nodejs.nix {pkgs=pkgs;})
  ++
  (import ./starlark.nix {pkgs=pkgs;})
  ++
  (import ./nix.nix {pkgs=pkgs;})
  #++
  #(import ./graphql.nix {pkgs=pkgs;})
