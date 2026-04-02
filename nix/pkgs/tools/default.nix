{ pkgs, workspace }:

with pkgs; [
  docker-compose
  lazygit
  k9s
  nemo-with-extensions
]
++ (import ./tmux-fuzzyclaw.nix { inherit pkgs workspace; })
