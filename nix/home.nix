{ config, pkgs, lib, ... }:

let
  packages = import ./pkgs {pkgs=pkgs;};
  fzfDefaultCommand = "fd --type file --follow --hidden --exclude .git --exclude node_modules --exclude www --exclude public";
  neovim = import ./programs/neovim {pkgs=pkgs;};
in
{
  # Let Home Manager install and manage itself.
  programs.home-manager.enable = true;

  programs.neovim = {
    enable = true;
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
      nvim-treesitter
      ale
      nvim-compe
      vim-polyglot
      nvim-fzf
      fzf-lua
      vim-obsession
      vim-cue
    ];
  };

  programs.zsh = {
    enable = true;
    autocd = true;
    dotDir = ".config/zsh";
    enableCompletion = true;
    initExtra = builtins.readFile ../.zshrc;
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
    };
  };

  programs.fzf = {
    enable = true;
    enableZshIntegration = true;
  };

  programs.bash = {
    enable = true;
    initExtra = ''
      . "$HOME/workspace/devrc/nix/bin/source-nix.sh"
      [ "$(command -v zsh)" ] && zsh
    '';
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

  home.stateVersion = "21.05";

  home.packages = packages;

  home.sessionVariables = {
    EDITOR = "nvim";
    FZF_DEFAULT_COMMAND = fzfDefaultCommand;
    FZF_ALT_C_COMMAND = "fdfind --type d . --color=never";
    FZF_CTRL_T_COMMAND = fzfDefaultCommand;
  };
}
