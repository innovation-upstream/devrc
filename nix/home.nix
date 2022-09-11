{ config, pkgs, lib, ... }:

let
  packages = import ./pkgs {pkgs=pkgs;};
  fzfDefaultCommand = "fd --type file --follow --hidden --exclude .git --exclude node_modules --exclude www --exclude public";
  neovim = import ./programs/neovim {pkgs=pkgs;};
  isNixOS = builtins.pathExists /etc/NIXOS;
in
{
  nixpkgs.overlays = [
    (import (builtins.fetchTarball {
      url = https://github.com/nix-community/neovim-nightly-overlay/archive/master.tar.gz;
    }))
  ];

  # Let Home Manager install and manage itself.
  programs.home-manager.enable = true;

  programs.neovim = {
    enable = true;
    package = pkgs.neovim-nightly;
    vimAlias = true;
    extraConfig = builtins.readFile ../.config/nvim/init.vim;
    plugins = with pkgs.vimPlugins; with neovim; [
      packer-nvim
      vim-fugitive
      undotree
      gruvbox
      incsearch-vim
      vim-signify
      nvim-lspconfig
      completion-nvim
      completion-buffers
      completion-treesitter
      nvim-compe
      vim-obsession
      vim-cue
      nvim-treesitter
    ];
    extraLuaPackages = with pkgs.lua51Packages; [
      lyaml
    ];
  };

  programs.zsh = {
    enable = true;
    autocd = true;
    dotDir = ".config/zsh";
    enableCompletion = true;
    initExtra = let
      zshRc = builtins.readFile ../.zshrc;
      hasDevRc = builtins.pathExists "${config.home.homeDirectory}/.devrc";
    in
      if hasDevRc then zshRc + builtins.readFile "${config.home.homeDirectory}/.devrc" else zshRc;
    oh-my-zsh = {
      enable = true;

      theme = "robbyrussell";
      plugins = [
        "git"
      ];
    };
    shellAliases = {
      k = "kubectl";
      g = "git";
      d = "docker";
      dc = "docker-compose";
      b = "bazel";
      ba = "cat /sys/class/power_supply/BAT1/capacity";
    };
  };

  programs.fzf = {
    enable = true;
    enableZshIntegration = true;
  };

  programs.bash = {
    enable = true;
    initExtra = let 
      cmd = ''
      . "$HOME/workspace/devrc/nix/bin/source-nix.sh"
      [ "$(command -v zsh)" ] && zsh
    '';
      hasDevBashRc = builtins.pathExists "${config.home.homeDirectory}/bashrc.devrc";
    in
      if hasDevBashRc then cmd + builtins.readFile "${config.home.homeDirectory}/bashrc.devrc" else cmd;
  };

  programs.tmux = {
    enable = true;
    prefix = "C-a";
    keyMode = "vi";
    baseIndex = 1;
    extraConfig = builtins.readFile ../.tmux.conf;
    plugins = with pkgs.tmuxPlugins; [
      {
        plugin = dracula;
        extraConfig = ''
          set -g @dracula-plugins "cpu-usage ram-usage"
        '';
      }
      {
        plugin = resurrect;
        extraConfig = "set -g @resurrect-strategy-nvim 'session'";
      }
      {
        plugin = continuum;
        extraConfig = ''
          set -g @continuum-restore 'on'
          set -g @continuum-save-interval '5'
        '';
      }
    ];
  };

  programs.git = {
    enable = true;
    aliases = {
      c = "commit";
      co = "checkout";
    };
  };

  programs.direnv.enable = true;
  programs.direnv.nix-direnv.enable = true;

  home.stateVersion = "21.11";

  home.packages = if isNixOS then packages ++ [pkgs.autorandr] else packages;

  home.sessionVariables = {
    EDITOR = "nvim";
    FZF_DEFAULT_COMMAND = fzfDefaultCommand;
    FZF_ALT_C_COMMAND = "fdfind --type d . --color=never";
    FZF_CTRL_T_COMMAND = fzfDefaultCommand;
  };
}
