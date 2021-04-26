#!/usr/bin/env bash

# Disable echo'ing commands before execution
set +x

NC='\033[0m' # No Color
BOLDNC="${NC}\033[1m"

printf "${BOLDNC}Upgrading Tilt${NC}\n"

# Upgrade Tilt
curl -fsSL https://raw.githubusercontent.com/tilt-dev/tilt/master/scripts/install.sh | bash >/dev/null

tilt version | xargs -I {} printf "${BOLDNC}Tilt upgraded to: ${NC}%s\n" "{}"

printf "${BOLDNC}Upgrading Nvim${NC}\n"

# Upgrade nvim
curl -OL https://github.com/neovim/neovim/releases/download/nightly/nvim-linux64.tar.gz >/dev/null
tar -xzf nvim-linux64.tar.gz >/dev/null
sudo cp nvim-linux64/bin/nvim /usr/local/bin >/dev/null
sudo cp -a nvim-linux64/share/. /usr/local/share >/dev/null
rm nvim-linux64.tar.gz >/dev/null

nvim --version | head -1 | xargs -I {} printf "${BOLDNC}Nvim upgraded to: ${NC}%s\n" "{}"

printf "${BOLDNC}Upgrading Golang${NC}\n"

curl -OL https://golang.org/dl/go1.16.3.linux-amd64.tar.gz >/dev/null
sudo rm -rf /usr/local/go >/dev/null
sudo tar -C /usr/local -xzf go1.16.3.linux-amd64.tar.gz >/dev/null
rm go1.16.3.linux-amd64.tar.gz >/dev/null

go version | xargs -I {} printf "${BOLDNC}Golang upgraded to: ${NC}%s\n" "{}"
