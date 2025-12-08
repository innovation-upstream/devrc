# Suggested Commands

## Installation & Setup
```bash
# Initial installation
mkdir $HOME/workspace && cd $HOME/workspace
git clone <devrc-repo>
cd devrc
./cmd/install.sh
home-manager switch

# Custom installation directory
export DEVRC_DIR="$HOME/custom-path/devrc"
source $DEVRC_DIR/.zshrc
```

## Development Commands

### Home-Manager (Main workflow)
```bash
# Apply configuration changes
home-manager switch

# Apply with custom TMPDIR (recommended)
TMPDIR=/var/tmp home-manager switch

# Check configuration
home-manager build
```

### Git Commands
```bash
git status
git add <file>
git commit -m "message"
git push
git log --oneline
tig  # Interactive git UI
```

### Search & Navigation
```bash
# Search file contents
rg <pattern>             # ripgrep
grep <pattern> <files>   # GNU grep

# Find files
fd <pattern>             # Modern find alternative
find <path> -name <pattern>

# Fuzzy finder (fzf) - typically used in shell integration
# Ctrl+R for history search
# Ctrl+T for file search
```

### File Operations
```bash
ls -la          # List files
cd <path>       # Change directory
bat <file>      # Better cat with syntax highlighting
cat <file>      # Standard file viewing
```

### System Monitoring
```bash
gotop           # Terminal-based activity monitor
docker ps       # Docker container status
k9s             # Kubernetes cluster management
```

### Editor
```bash
nvim <file>     # Open file in Neovim (default editor)
vim <file>      # Also available
```

### Directory Environment
```bash
direnv allow    # Allow .envrc in current directory
direnv deny     # Deny .envrc in current directory
```

## Nix-Specific Commands
```bash
# Source Nix environment
source $DEVRC_DIR/nix/bin/source-nix.sh

# Update channels
$DEVRC_DIR/nix/bin/channels.sh

# Initialize home-manager
$DEVRC_DIR/nix/bin/init-home-manager.sh

# Nix package management
nix-env -q      # List installed packages
nix-env -i <pkg> # Install package (prefer home-manager)
nix-collect-garbage -d # Clean old generations
```

## File Manager
```bash
ranger          # Terminal-based file manager
```

## Customization
Edit `~/.devrc` for custom shell configuration (sourced by zsh if exists)