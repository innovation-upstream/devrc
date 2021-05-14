# DEVRC

This repo assumes you are running Ubuntu 20 LTS but it may work on other versions/distros
if you are lucky.

## Installation

Follow these steps to initialize a fresh environment capable of building and running any Innovation
Upstream repo.  If you screw up entering your password and the script exits you can just run it
again as it is designed to be idemponent

1. `cd $HOME/workspace` (Optional, see step 4)
2. Clone this repo
3. Run `cmd/install.sh` and enter your password when prompted, select 'y' when prompted to change
your default shell to zsh. If a zsh session is opened you must `exit` to continue the installation
script
4. Modify your login script (usually this is `$HOME/.zshrc`) to source the devrc .zshrc:

```sh
$ source $HOME/workspace/devrc/.zshrc
```

If you cloned devrc into a different directory, you will need to set the `DEVRC_DIR` environment 
variable before sourcing devrc's zshrc so devrc knows where to look for itself.

```sh
$ export DEVRC_DIR="$HOME/workspace-2/devrc"
$ source $DEVRC_DIR/.zshrc
```

### Nvim

1. Modify your `init.vim` (usually this is `$HOME/.config/nvim/init.vim`) to source the devrc 
`init.vim`:

```vimscript
source $DEVRC_DIR/.config/nvim/init.vim
```

2. Ensure plugins are up to date
  `:PackerSync`
  `:TSUpdate` you can install more
  [supported languages](https://github.com/nvim-treesitter/nvim-treesitter#supported-languages) if
  necessary

### Kubernetes

1. Run `cmd/dev_env_up.sh` to start the kubernetes cluster

**Configuring Multicluster**

If your project uses linkerd for its multicluster service mesh,

- `mkdir $HOME/.dev_certs`
- `cp cmd/cluster/certs/* $HOME/.dev_certs`
- `$DEVRC_DIR/cmd/cluster/linkerd_install.sh`
- `$DEVRC_DIR/cmd/cluster/linkerd_up.sh`
- `$DEVRC_DIR/cmd/cluster/add_gke_context.sh`

## Customization

If you need to add/modify your shell profile, do so by copying `.devrc.default` into `.devrc` and 
modifying `.devrc`.

```sh
$ cp $DEVRC_DIR/.devrc.default $DEVRC_DIR/.devrc
```

`$DEVRC_DIR/.zshrc` will prefer to source `.devrc` if it exists.

