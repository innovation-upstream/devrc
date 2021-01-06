# Reasonable zsh defaults
export ZSH="/home/$USER/.oh-my-zsh"
ZSH_THEME="robbyrussell"
plugins=(git)
source $ZSH/oh-my-zsh.sh

# Custom Dev Env Init
if test -f $HOME/.devrc; then
  source $HOME/.devrc
else
  BLUE='\033[1;34m'
  NC='\033[0m' # No Color
  printf "${BLUE}No \$HOME/.devrc file found! ${NC}Create one to configure your shell.\n"
  source $HOME/.devrc.default
fi

