#/usr/bin/env sh

if [ -z "$(command -v nix)" ]; then
  . ~/.nix-profile/etc/profile.d/nix.sh || true
fi

if [ -z "$(command -v nix)" ]; then
  . /etc/profile.d/nix.sh || true
fi

