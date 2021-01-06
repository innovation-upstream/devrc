# DEVRC

This repo assumes you are running Ubuntu 16 LTS but it may work on other versions/distros

## Setup:

1. Run `cmd/install.sh`
2. Open nvim/vim and run `:PlugInstall`, `:CocInstall`

If you need to add/modify your shell profile, do so by copying `.devrc.default` into `.devrc`.

```shell
$ cp .devrc.default .devrc
```

`.zshrc` prefers to source `.devrc` if it exists.

