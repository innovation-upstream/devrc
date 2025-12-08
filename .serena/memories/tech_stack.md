# Tech Stack

## Core Technologies
- **Nix**: Declarative package management and environment configuration
- **home-manager**: User environment management for Nix
- **Shell**: zsh (primary), bash (secondary)

## Language Support
The repository configures development environments for:
- **Go** (golang)
- **Python**
- **Rust**
- **Node.js**
- **Lua**
- **C#** (csharp)
- **Perl**
- **Solidity**
- **Cue**
- **Nix**
- **YAML**
- **Starlark**

(Note: Dhall and GraphQL support exist but are commented out)

## Tools & Programs
- **Editor**: Neovim (default editor) with extensive Lua configuration
- **Version Control**: Git, tig
- **Terminal Multiplexer**: tmux
- **File Manager**: ranger
- **Fuzzy Finder**: fzf
- **Environment Manager**: direnv (with nix-direnv)
- **Terminal Emulator**: alacritty
- **Kubernetes**: k9s
- **Git UI**: lazygit
- **Search**: ripgrep, fd, gnugrep
- **Monitoring**: gotop
- **Container**: Docker
- **Git Hooks**: lefthook
- **Utilities**: bat, wget, gcc, coreutils, gnused

## Configuration Format
- Nix expressions (`.nix` files)
- Lua for Neovim configuration
- Vim for legacy Neovim config
- Shell scripts for installation
- YAML for tool configurations (lazygit, k9s)