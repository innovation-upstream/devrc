# Codebase Structure

```
devrc/
├── cmd/
│   └── install.sh          # Main installation script
├── nix/
│   ├── home.nix            # Main home-manager configuration entry point
│   ├── sessionVariables.nix # Environment variables configuration
│   ├── overlays.nix        # Nix package overlays (currently disabled)
│   ├── bin/                # Nix setup scripts
│   │   ├── channels.sh
│   │   ├── init-home-manager.sh
│   │   └── source-nix.sh
│   ├── pkgs/               # Package definitions
│   │   ├── default.nix     # Main package list (core utilities)
│   │   ├── lang/           # Language-specific packages
│   │   │   ├── default.nix # Language package aggregator
│   │   │   ├── golang.nix
│   │   │   ├── python.nix
│   │   │   ├── rust.nix
│   │   │   ├── nodejs.nix
│   │   │   ├── lua.nix
│   │   │   ├── csharp.nix
│   │   │   ├── perl.nix
│   │   │   ├── solidity.nix
│   │   │   ├── cue.nix
│   │   │   ├── nix.nix
│   │   │   ├── yaml.nix
│   │   │   ├── starlark.nix
│   │   │   ├── elixir.nix
│   │   │   ├── bash.nix
│   │   │   ├── html.nix
│   │   │   └── graphql.nix
│   │   └── tools/          # Development tools
│   │       ├── default.nix
│   │       ├── docker.nix
│   │       └── lazygit.nix
│   └── programs/           # Program configurations
│       ├── default.nix     # Program aggregator
│       ├── neovim/
│       │   ├── default.nix
│       │   └── plugins.nix
│       ├── zsh/
│       │   └── default.nix
│       ├── bash/
│       │   └── default.nix
│       ├── tmux/
│       │   └── default.nix
│       ├── git/
│       │   └── default.nix
│       ├── fzf/
│       │   └── default.nix
│       ├── direnv/
│       │   └── default.nix
│       ├── alacritty/
│       │   └── default.nix
│       ├── k9s/
│       │   └── default.nix
│       └── ranger/
│           └── default.nix
├── .config/                # Application configurations
│   ├── nvim/               # Neovim configuration
│   │   ├── init.lua        # Lua initialization
│   │   ├── init.vim        # Vim initialization
│   │   ├── lua/
│   │   │   ├── plugins.lua
│   │   │   ├── nvim_lsp.lua
│   │   │   ├── helpers.lua
│   │   │   ├── config/     # Plugin configurations
│   │   │   │   ├── native.lua
│   │   │   │   └── plugin/
│   │   │   │       ├── gruvbox.lua
│   │   │   │       ├── cmp.lua
│   │   │   │       ├── treesitter.lua
│   │   │   │       ├── symbols-outline.lua
│   │   │   │       ├── lazygit.lua
│   │   │   │       └── harpoon.lua
│   │   │   └── map/        # Key mappings
│   │   │       ├── native.lua
│   │   │       └── plugin/
│   │   │           ├── fzf.lua
│   │   │           ├── lazygit.lua
│   │   │           ├── harpoon.lua
│   │   │           ├── tig.lua
│   │   │           ├── qdr.lua
│   │   │           ├── ranger.lua
│   │   │           ├── claudecode.lua
│   │   │           └── spectre.lua
│   │   ├── config/         # Legacy vim configs
│   │   │   ├── native.vim
│   │   │   └── plugin/
│   │   └── map/            # Legacy vim mappings
│   │       └── plugin/
│   └── lazygit/
│       └── config.yml
├── .zshrc                  # Zsh shell configuration
├── .tmux.conf              # Tmux configuration
├── .gitignore
├── window.lua              # Window management script (new)
└── README.md               # Installation and usage documentation
```

## Key Organizational Principles
1. **Nix-centric**: All package and program management through Nix expressions
2. **Separation of concerns**: Packages, programs, and configurations are isolated
3. **Language modularity**: Each language has its own Nix module
4. **Tool modularity**: Each tool/program has its own configuration module
5. **Dual config format**: Both Lua and Vim configs for Neovim (migrating to Lua)