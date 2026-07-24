# Source this to activate the DSPy eval venv on NixOS.
# pip wheels with native extensions (tokenizers, pyarrow) need libstdc++/zlib
# from the nix store on LD_LIBRARY_PATH — NixOS has no global /usr/lib.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
export LD_LIBRARY_PATH="/nix/store/8lahnh9pn3lrrnhax5nk7ibvjcbjmnkm-gcc-15.2.0-lib/lib:/nix/store/b2swxfi8srrbsafvh9iyyhd26mz9giwf-zlib-1.3.2/lib:${LD_LIBRARY_PATH:-}"
. "$HERE/.venv/bin/activate"
