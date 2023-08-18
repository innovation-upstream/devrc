#!/usr/bin/env bash

# This script must remain idempotent

echo "prefix = $HOME/.npm-packages" > $HOME/.npmrc
echo 'export $PATH=$PATH:$HOME/.npm-packages/bin' >> $HOME/.devenvrc

npm install -g solidity-language-server @tailwindcss/language-server @volar/vue-language-server @cucumber/language-server

