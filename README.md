# DEVRC

This repo assumes you are running Ubuntu 20 LTS but it may work on other versions/distros

## Setup

1. `cd $HOME`
2. Clone this repo
3. Copy the content into your home dir: `cp -a $HOME/devrc/. .`
4. Remove the not redundent directory: `rm -rf $HOME/devrc`
5. Run `cmd/install.sh` and enter your password when prompted, select 'n' when prompted to change your default shell to zsh.
6. Open nvim and run `:PlugInstall`, `:CocInstall`

If you need to add/modify your shell profile, do so by copying `.devrc.default` into `.devrc` and modifying `.devrc`.

```shell
$ cp .devrc.default .devrc
```

`.zshrc` prefers to source `.devrc` if it exists.
