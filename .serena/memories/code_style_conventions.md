# Code Style & Conventions

## Nix Files
- **File naming**: lowercase with hyphens (e.g., `session-variables.nix`)
- **Module structure**: Use `let...in` blocks for clarity
- **Imports**: Explicit package imports with `{pkgs=pkgs;}` pattern
- **Formatting**: Standard Nix formatting (2-space indentation)
- **Comments**: Use `#` for single-line comments
- **Attribute sets**: Clear hierarchical organization
- **Conditional logic**: Use `if...then...else` for platform-specific config (e.g., NixOS detection)

## Lua Files (Neovim)
- **File naming**: lowercase with underscores (e.g., `nvim_lsp.lua`)
- **Module organization**: 
  - `config/` for plugin configurations
  - `map/` for key mappings
  - `lua/` root for core functionality
- **Indentation**: Appears to use 2-space indentation
- **Plugin config pattern**: Separate files per plugin in `config/plugin/`
- **Mapping pattern**: Separate files per plugin in `map/plugin/`

## Shell Scripts
- **Shebang**: Always use `#!/usr/bin/env bash` for portability
- **Idempotency**: Installation scripts must be idempotent
- **Error handling**: Check for command existence before execution
- **Variables**: Use `${VARIABLE}` syntax with defaults (e.g., `${DEVRC_DIR:-$PWD}`)

## Vim Files (Legacy)
- **File naming**: lowercase with underscores (e.g., `completion_nvim.vim`)
- **Organization**: Similar to Lua - separate config and mapping files

## Directory Naming
- **Lowercase**: All directory names in lowercase
- **Descriptive**: Clear purpose indication (e.g., `lang/`, `tools/`, `programs/`)
- **Nix structure**: `default.nix` as main entry point for each module

## General Conventions
- **Modular design**: Each component (language, tool, program) in separate file
- **DRY principle**: Shared configuration through imports
- **Platform awareness**: Detect NixOS vs non-NixOS for conditional packages
- **Comments**: Explain complex logic and platform-specific decisions
- **Version pinning**: Use home.stateVersion for stability (currently "24.11")