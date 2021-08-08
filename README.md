# DEVRC

This repo assumes you are running Ubuntu 20 LTS or NixOS, but it may work on 
other versions/distros if you are lucky.

## Installation

Follow these steps to initialize a fresh environment capable of building and 
running any Innovation Upstream repo.

1. `mkdir $HOME/workspace && cd $HOME/workspace` (Optional, see step 4)
2. Clone this repo
3. Run `cmd/install.sh`

(Optional) If you cloned devrc into a different directory, you will need to set the 
`DEVRC_DIR` environment variable in `~/.devrc`.

```sh
$ export DEVRC_DIR="$HOME/workspace-2/devrc"
$ source $DEVRC_DIR/.zshrc
```

### Kubernetes

1. Run `cmd/dev_env_up.sh` to start the kubernetes cluster

**Configuring Multicluster**

If your project uses linkerd for its multicluster service mesh,

Copy dev certs (for authenticating cross-cluster communication)

- `mkdir $HOME/.dev_certs`
- `cp cmd/cluster/certs/* $HOME/.dev_certs`
- `$DEVRC_DIR/cmd/cluster/linkerd_install.sh`
Install linkerd CLI
- `$DEVRC_DIR/cmd/cluster/linkerd_up.sh`
Deploy linkered k8s resources into your `$DEV_CLUSER`
- `$DEVRC_DIR/cmd/cluster/add_gke_context.sh`
Add `$SHARED_DEV_CLUSTER` as a kubectl context

## Customization

If you need to add/modify your shell profile, you can do so by 
creating/modifying `~/.devrc`. (home-manager will tell zsh to source this file
if it exists)
