# Guidelines & Design Patterns

## Design Patterns

### 1. Modular Nix Architecture
- **Pattern**: Each component (language, tool, program) has its own module
- **Structure**: `default.nix` aggregates submodules
- **Example**: `nix/pkgs/lang/default.nix` imports all language-specific modules
- **Benefit**: Easy to add/remove components without affecting others

### 2. Configuration Separation
- **Pattern**: Separate Nix declarations from application configs
- **Nix**: Package installation and program enablement in `nix/`
- **App configs**: Actual configuration files in `.config/` or root
- **Example**: `nix/programs/neovim/default.nix` enables neovim, `.config/nvim/` contains config

### 3. Dual Configuration Format (Neovim)
- **Legacy**: Vim script in `.config/nvim/init.vim` and `config/` directory
- **Modern**: Lua in `.config/nvim/init.lua` and `lua/` directory
- **Migration**: Project is transitioning from Vim to Lua
- **Guideline**: Prefer Lua for new configurations

### 4. Platform Detection
- **Pattern**: Use `builtins.pathExists /etc/NIXOS` to detect NixOS
- **Usage**: Conditional package installation (e.g., autorandr only on NixOS)
- **Example**: See `nix/home.nix` lines 18-22

### 5. Environment Customization Hook
- **Pattern**: Check for `~/.devrc` file for user-specific overrides
- **Purpose**: Allow customization without modifying repository
- **Usage**: Zsh sources `~/.devrc` if it exists

## Key Guidelines

### Installation
- **Idempotency**: `cmd/install.sh` must be safe to run multiple times
- **Prerequisite checks**: Check if commands exist before installing
- **User permissions**: Add user to docker group, use non-root where possible

### Package Management
- **Prefer declarative**: Add packages to Nix files, not `nix-env -i`
- **Language packages**: Add to appropriate file in `nix/pkgs/lang/`
- **Tools**: Add to `nix/pkgs/tools/default.nix` or create dedicated module
- **Programs**: Add to `nix/programs/` if it needs configuration

### Configuration Changes
- **Test locally**: Always test with `home-manager switch` before committing
- **Incremental changes**: Small, focused changes are better
- **Comment complex logic**: Especially platform-specific or non-obvious decisions

### Directory Conventions
- **DEVRC_DIR**: Defaults to `$HOME/workspace/devrc` but can be customized
- **Sourcing**: Always source Nix environment from `nix/bin/source-nix.sh`
- **Paths**: Use `$DEVRC_DIR` variable for portability

### Neovim Plugin Management
- **Packer**: Uses packer-nvim for plugin management
- **Nix integration**: Base plugins in `nix/programs/neovim/default.nix`
- **Lua config**: Plugin-specific config in `.config/nvim/lua/config/plugin/`
- **Mappings**: Plugin keybindings in `.config/nvim/lua/map/plugin/`

## Recent Changes Context
Based on recent commits:
- Added claudecode, ranger, tig plugins
- Migrated to new nvim LSP API
- Fixed git deprecations
- Updated Python packages
- Added C# language server
- Added YAML LSP support

## Common Mistakes to Avoid
- Don't use `git add -A` (add files individually)
- Don't modify files without testing home-manager rebuild
- Don't add system-wide packages when user packages suffice
- Don't hardcode paths (use `$DEVRC_DIR` or relative paths)
- Don't skip `home-manager switch` after Nix changes