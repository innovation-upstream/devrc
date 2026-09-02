#!/usr/bin/env bash
# Rebuild the handoff-doc commit index and the all-doc-names list.
# --all is load-bearing: these clones sit on unpredictable branches and run behind;
# HEAD-only lost 33% of commits in the originating measurement.
set -u
# Output directory. Override with CHAIN_WORKDIR; defaults to the cwd.
S="${CHAIN_WORKDIR:-$PWD}"
[ -n "$S" ] && [ -d "$S" ] || { echo "CHAIN_WORKDIR is empty or not a directory: '$S'" >&2; exit 1; }
: > "$S/docs.idx"
: > "$S/allnames.txt"
n=0
while IFS= read -r g; do
  r="$(dirname "$g")"
  b="$(basename "$r")"
  n=$((n+1))
  git -C "$r" log --all --since=2026-08-15 \
      --pretty=format:"C%x09%h%x09%ad" --date=short \
      --diff-filter=AM --name-only -- 'claudedocs/*handoff*' 2>/dev/null \
    | awk -v repo="$b" 'NF{print repo "\t" $0}' >> "$S/docs.idx"
  # every handoff doc name this repo has ever carried, any branch
  git -C "$r" log --all --pretty=format: --name-only --diff-filter=AM -- 'claudedocs/*handoff*' 2>/dev/null \
    | awk 'NF' >> "$S/allnames.txt"
  # plus what is on disk right now
  ls "$r"/claudedocs/ 2>/dev/null | awk '/[Hh][Aa][Nn][Dd][Oo][Ff][Ff]/{print "claudedocs/" $0}' >> "$S/allnames.txt"
done < <(find "$HOME/workspace" -maxdepth 4 -type d -name .git 2>/dev/null)
sort -u "$S/allnames.txt" -o "$S/allnames.txt"
echo "repos scanned: $n"
echo "commit-index lines: $(wc -l < "$S/docs.idx")"
echo "distinct doc names: $(wc -l < "$S/allnames.txt")"
