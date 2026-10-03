# Single source of truth for the repo-root and kubeconfig handles exported into
# EVERY agent shell — Claude Code's `zsh -c` (via programs/zsh envExtra) AND
# opencode's bash tool (via the generated ~/.config/opencode/plugin/env.js).
#
# WHY THIS FILE EXISTS: those two consumers used to define the same handles
# independently, and drifted. `scripts/opencode/env.js` hardcoded an absolute
# `/home/zach/workspace/homelab-talos` (so it was wrong on any other user/host),
# defined `KC_PROD` which zsh did not, and — unlike zsh — applied NO existence
# guard, so it exported handles pointing at files that may not exist. A handle
# that resolves to a nonexistent kubeconfig is the failure mode where a command
# silently runs against NO cluster while looking like it worked.
#
# Everything here is derived from `home`, never hardcoded. Both consumers
# existence-guard before exporting, so a host without a given checkout simply
# does not get that handle (no stale vars).
#
# 🔴 NO default KUBECONFIG on purpose: a merged/default one lets a bare
# `kubectl` hit prod. Pick a cluster explicitly per command.
{ home }:
{
  # Repo roots — guarded on the DIRECTORY existing (`-d`).
  repos = {
    DEVRC = "${home}/workspace/devrc";
    HOMELAB = "${home}/workspace/homelab-talos";
    DATAPACKET = "${home}/workspace/civit/datapacket-talos";
    CIVITAI = "${home}/workspace/civit/civitai";
    # ⚠ NAMED FOR THE REMOTE (`civitai/cli`), NOT FOR ITS DIRECTORY. Two full
    # clones of that remote exist on this host and this used to point at the
    # DORMANT one (`civit/civitai-cli`, 6 worktrees, working tree 3.5 months
    # stale) rather than the one every session works in (`civit/cli`, 71
    # worktrees). That was invisible because every handle here is
    # existence-guarded on `-d`: both directories exist, so the wrong one
    # exported cleanly. It stopped being cosmetic once `handoff_index.py` added
    # `CIVITAI_CLI` to `REPO_ENV_HANDLES` — the indexer never git-fetches, so
    # the handle decides which clone's refs the corpus is read from and a
    # dormant clone is a permanent, unbounded corpus lag.
    CIVITAI_CLI = "${home}/workspace/civit/cli";

    # naida-ai (`ZacxDev/naida-ai`). 🔴 THE PRIMARY IS THE WORKBENCH PATH, and
    # the laptop's copy of the same checkout sits ELSEWHERE — see `alternates`
    # below, which is why this entry may point at a directory that does not
    # exist on every host. Measured 2026-10-03 over ssh, both hosts: workbench
    # `~/workspace/naida-ai`, laptop `~/workspace/scratch/naida-ai` and NOT
    # `~/workspace/naida-ai`. Adding this handle was forced by the arc resolver:
    # `find-session.py --arc` and `handoff_search` reach only the
    # `handoff_index.REPO_ENV_HANDLES` repos, so every one of the 7
    # `claudedocs/handoff-*.md` on this checkout's mainline was invisible to
    # both — `--arc` reported "no repo handle holds it" (rc 5, nothing
    # measured) for a doc sitting on disk.
    NAIDA = "${home}/workspace/naida-ai";
  };

  # 🔴 EXTRA CANDIDATE PATHS per handle, tried in order AFTER the entry in
  # `repos`; the first existing path wins. `repos` stays ONE STRING per handle —
  # every parser in this repo (at least four test suites) pins the
  # `NAME = "${home}/…";` shape, and a list-valued entry there would be
  # silently unparsed — so a host-divergent checkout declares its primary here
  # and its other locations HERE, as a LIST (a shape `_NIX_ENTRY` deliberately
  # does not match).
  #
  # Consumers that resolve at RUNTIME (zsh envExtra, the generated env.js) walk
  # primary + alternates and export the first that exists. The ONE consumer
  # that cannot (the handoff-index-sync systemd unit, whose Environment is
  # static) exports the primary only — it is workbench-only (`serverMode`),
  # where the primary exists; elsewhere an absent primary is reported UNMEASURED,
  # never folded into a clean count.
  #
  # Every key here MUST be a handle declared in `repos` — pinned by
  # `test_absolute_handle_paths.py`, which is also where the shape is parsed.
  alternates = {
    NAIDA = [ "${home}/workspace/scratch/naida-ai" ];
  };

  # Kubeconfigs — guarded on the FILE existing (`-f`).
  kubeconfigs = {
    KC_HOMELAB = "${home}/workspace/homelab-talos/homelab-kubeconfig";
    KC_WORKBENCH = "${home}/workspace/homelab-talos/workbench-kubeconfig";
    KC_PROD = "${home}/workspace/homelab-talos/production-kubeconfig";
    KC_DPPROD = "${home}/workspace/civit/datapacket-talos/prod-kubeconfig";
    KC_NEBULA = "${home}/.kube/homelab-nebula.yaml";
  };
}
