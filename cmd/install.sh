#!/usr/bin/env bash
# This script must remain idempotent

sudo apt update
sudo apt-get update

# Create workspace dir
[ -d $HOME/workspace ] || mkdir workspace

# Install ripgrep
sudo apt-get install ripgrep

source $HOME/.devrc.default

# Install nvm and configure default node/npm version to lts
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.37.2/install.sh | bash
# Refresh shell so nvm is in $PATH
su - ${USER}
nvm install --lts

# Docker Install
# Prereqs
sudo apt install -y apt-transport-https ca-certificates curl software-properties-common build-essential
# Add Docker GPG Key to apt
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo apt-key add -
# Add Docker repo to apt
sudo add-apt-repository "deb [arch=amd64] https://download.docker.com/linux/ubuntu focal stable"
sudo apt update
# Install Docker CE
sudo apt install -y docker-ce
# Add user to docker group
sudo usermod -aG docker ${USER}

# Install docker compose
sudo curl -L "https://github.com/docker/compose/releases/download/1.28.0/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# Install helm 3
curl https://raw.githubusercontent.com/helm/helm/master/scripts/get-helm-3 | bash
helm repo add stable https://charts.helm.sh/stable

# Install tilt
curl -fsSL https://raw.githubusercontent.com/tilt-dev/tilt/master/scripts/install.sh | bash

# Install Bazelisk
npm i -g @bazel/bazelisk

# Install Firebase tools
curl -sL https://firebase.tools | bash

# Install oh-my-zsh
sh -c "$(curl -fsSL https://raw.github.com/ohmyzsh/ohmyzsh/master/tools/install.sh)"

# Install fzf (use git installation instead of apt so we get keybindings/completion)
git clone --depth 1 https://github.com/junegunn/fzf.git $HOME/.fzf
$HOME/.fzf/install --key-bindings --no-update-rc --completion

# Install nvim
curl -OL https://github.com/neovim/neovim/releases/download/nightly/nvim-linux64.tar.gz
tar -xzf nvim-linux64.tar.gz
sudo cp nvim-linux64/bin/nvim /usr/local/bin
sudo cp -a nvim-linux64/share/. /usr/local/share
rm nvim-linux64.tar.gz

# Install some pkgs for nvim lsp/plugins
npm install -g typescript typescript-language-server vscode-json-languageserver neovim eslint_d

# Install pynvim for nvim deoplete(completion) plugin
pip3 install --user pynvim

# Install vim-plug
sh -c 'curl -fLo "${XDG_DATA_HOME:-$HOME/.local/share}"/nvim/site/autoload/plug.vim --create-dirs \
https://raw.githubusercontent.com/junegunn/vim-plug/master/plug.vim'

# Install k3d
curl -s https://raw.githubusercontent.com/rancher/k3d/main/install.sh | bash
# Create and switch to new cluster TODO: Make this wait for dockerd to start
# (currently there is a race condition between this line being executed and dockerd having enough time to start)
k3d cluster create dev-cluster --volume $HOME/workspace:/home/$USER/workspace --no-image-volume --volume "${HOME}/.k3d/registries.yaml:/etc/rancher/k3s/registries.yaml"

# Alias python to run python3 binary
sudo update-alternatives --install /usr/bin/python python /usr/bin/python3 1

# Install golang
curl -OL https://golang.org/dl/go1.15.7.linux-amd64.tar.gz
sudo tar -C /usr/local -xzf go1.15.7.linux-amd64.tar.gz

# Revert .zshrc (it gets overwritten when we install zsh)
git checkout master .zshrc

# Set user default shell to zsh
# chsh -s /usr/bin/zsh
# Refresh session
# su - ${USER}

# Install zsh, fd
sudo DEBIAN_FRONTEND=noninteractive apt install -y zsh fd-find python3-pip

# Install goimports
go get golang.org/x/tools/cmd/goimports
