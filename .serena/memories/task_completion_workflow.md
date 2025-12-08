# Task Completion Workflow

## After Making Changes

### 1. Apply Configuration
```bash
# Always rebuild home-manager after Nix changes
TMPDIR=/var/tmp home-manager switch

# Or without TMPDIR if needed
home-manager switch
```

### 2. Verification Steps
- **Nix changes**: Verify packages are installed and programs configured
- **Neovim changes**: Restart nvim and verify plugins/configs load
- **Shell changes**: Source config or restart shell
  ```bash
  source ~/.zshrc  # For zsh changes
  source $DEVRC_DIR/.zshrc  # For devrc changes
  ```

### 3. Git Workflow
```bash
# Check status
git status

# Add files individually (never git add -A)
git add <specific-file>

# Commit with descriptive message
git commit -m "descriptive message"

# Push changes
git push
```

## No Automated Testing
- This repository does not have automated tests
- Manual verification is required after changes
- Test by applying configuration and checking functionality

## No Linting/Formatting Commands
- No automated linting for Nix files
- No automated formatting enforced
- Follow existing code style conventions manually
- Nix will validate syntax during `home-manager switch`

## Common Post-Change Checks
- **Package additions**: Verify with `which <command>` or `<command> --version`
- **Neovim plugins**: Check `:PackerStatus` in nvim
- **Shell functions**: Test new functions in terminal
- **Environment variables**: `echo $VARIABLE_NAME`
- **Program configs**: Open and test the program (nvim, tmux, git, etc.)

## Rollback Strategy
```bash
# List home-manager generations
home-manager generations

# Rollback to previous generation
home-manager rollback

# Or switch to specific generation
/nix/store/<generation-path>/activate
```

## Deployment Target
- **Primary**: Local development machine (Ubuntu 20 LTS or NixOS)
- **No CI/CD**: This is personal configuration, not deployed to servers
- **No build artifacts**: Configuration is declarative and applied via home-manager