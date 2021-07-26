#!/usr/bin/env bash

# Disable echo'ing commands before execution
set +x

sudo apt-get update

NC='\033[0m' # No Color
BOLDNC="${NC}\033[1m"

printf "${BOLDNC}Upgrading Tilt${NC}\n"

# Upgrade Tilt
curl -fsSL https://raw.githubusercontent.com/tilt-dev/tilt/master/scripts/install.sh | bash >/dev/null

printf "${BOLDNC}Upgrading Nvim${NC}\n"

# Upgrade nvim
sudo apt-get install -y ninja-build gettext libtool libtool-bin autoconf \
  automake cmake g++ pkg-config unzip

git clone https://github.com/neovim/neovim.git $HOME/neovim
(
  cd $HOME/neovim && git pull &&
  make distclean &&
  make deps &&
  sudo make install &&
  make CMAKE_BUILD_TYPE=Release &&
  sudo mv ./build/bin/nvim /usr/local/bin/nvim
)

printf "${BOLDNC}Upgrading Golang${NC}\n"

curl -OL https://golang.org/dl/go1.16.4.linux-amd64.tar.gz >/dev/null
sudo rm -rf /usr/local/go >/dev/null
sudo tar -C /usr/local -xzf go1.16.4.linux-amd64.tar.gz >/dev/null
rm go1.16.4.linux-amd64.tar.gz >/dev/null

tilt version | xargs -I {} printf "${BOLDNC}Tilt upgraded to: ${NC}%s\n" "{}"
nvim --version | head -1 | xargs -I {} printf "${BOLDNC}Nvim upgraded to: ${NC}%s\n" "{}"
go version | xargs -I {} printf "${BOLDNC}Golang upgraded to: ${NC}%s\n" "{}"
