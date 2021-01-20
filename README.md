# DEVRC

This repo assumes you are running Ubuntu 20 LTS but it may work on other versions/distros
if you are lucky.

## Setup

1. `cd $HOME`
2. Clone this repo
3. Copy the content into your home dir: `cp -a $HOME/devrc/. .`
4. Remove the now redundent directory: `rm -rf $HOME/devrc`
5. Run `cmd/install.sh` and enter your password when prompted, select 'n' when prompted to change your default shell to zsh
6. If you plan on using nvim, open it and run 
  `:PlugInstall`
  `:TSInstall go graphql typescript json vim bash` you can add more
  [supported languages](https://github.com/nvim-treesitter/nvim-treesitter#supported-languages) if
  necessary

If you need to add/modify your shell profile, do so by copying `.devrc.default` into `.devrc` and modifying `.devrc`.

```shell
$ cp .devrc.default .devrc
```

`.zshrc` willl prefer to source `.devrc` if it exists.
