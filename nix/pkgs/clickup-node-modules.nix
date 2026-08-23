# node_modules for the `clickup` skill (claude/skills/clickup), built from its
# COMMITTED package-lock.json.
#
# WHY THIS EXISTS
# ---------------
# `claude/skills/clickup/lib/markdown.mjs` imports `unified`, `remark-parse` and
# `remark-stringify` — load-bearing for `lib/mentions.mjs`, `lib/format.mjs` and
# `api/doc-comments.mjs`, i.e. every comment/doc command. The skill used to be a
# standalone directory with its own flake and a pnpm store, which is why it only
# ever worked on the host somebody had run an install on. Deployed through
# home-manager it has to bring its dependency tree with it.
#
# node_modules is deliberately NOT committed. This derivation materialises it
# from the lock file (51 packages — the 3 direct deps plus the micromark
# ecosystem) and home.nix symlinks the result to
# `~/.claude/skills/clickup/node_modules`, where node's upward resolution from
# `lib/markdown.mjs` finds it.
#
# 🔴 The `src` is a two-file directory, not the skill tree. `npm ci` needs only
# package.json + package-lock.json, and narrowing the input means editing
# query.mjs does not rebuild (or re-fetch) the dependency tree.
#
# UPDATING: change package.json/package-lock.json, then set npmDepsHash to
# lib.fakeHash, build, and copy the `got:` hash out of the mismatch. Never guess
# it — a stale hash fetches the OLD tree and the skill silently runs on it.
{ lib, runCommandLocal, buildNpmPackage }:

buildNpmPackage {
  pname = "clickup-skill-node-modules";
  version = "1.0.0";

  src = runCommandLocal "clickup-skill-npm-manifest" { } ''
    mkdir -p "$out"
    cp ${../../claude/skills/clickup/package.json} "$out/package.json"
    cp ${../../claude/skills/clickup/package-lock.json} "$out/package-lock.json"
  '';

  # Obtained by building with `lib.fakeHash` and copying the `got:` line
  # (2026-08-13, nixpkgs 9bc02893134c, node 24.18.0).
  npmDepsHash = "sha256-OAL2UogbWC4/1nc19MvrqWWvu9YAcWfgaiDf99yS5/M=";

  # There is nothing to build: the skill is plain .mjs run by node directly, and
  # its three dependencies ship as ESM. `npm ci` materialising node_modules IS
  # the whole job.
  dontNpmBuild = true;

  # The default install phase runs `npm pack`/`npm install -g`-style packaging,
  # which would bury the tree under $out/lib/node_modules/clickup/. home.nix
  # wants ONE stable path to symlink, so publish node_modules at the top level.
  installPhase = ''
    runHook preInstall
    mkdir -p "$out"
    cp -r node_modules "$out/node_modules"
    runHook postInstall
  '';

  meta = {
    description = "Runtime dependencies for the clickup Claude Code skill";
    platforms = lib.platforms.all;
  };
}
