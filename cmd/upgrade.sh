#!/usr/bin/env sh

# Upgrade Tilt
curl -fsSL https://raw.githubusercontent.com/tilt-dev/tilt/master/scripts/install.sh | bash

# Upgrade nvim
curl -OL https://github.com/neovim/neovim/releases/download/nightly/nvim-linux64.tar.gz
tar -xzf nvim-linux64.tar.gz
sudo cp nvim-linux64/bin/nvim /usr/local/bin
sudo cp -a nvim-linux64/share/. /usr/local/share
rm nvim-linux64.tar.gz

