{ pkgs, ... }:

let
  nvim-fzf = pkgs.vimUtils.buildVimPlugin {
    name = "nvim-fzf";
    src = builtins.fetchGit {
      url = "https://github.com/vijaymarupudi/nvim-fzf.git";
      ref = "master";
    };
  };
  fzf-lua = pkgs.vimUtils.buildVimPlugin {
    name = "fzf-lua";
    src = builtins.fetchGit {
      url = "https://github.com/ibhagwan/fzf-lua.git";
      ref = "main";
    };
  };
in
{
  nvim-fzf=nvim-fzf;
  fzf-lua=fzf-lua;
}

