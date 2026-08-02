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
    CIVITAI_CLI = "${home}/workspace/civit/civitai-cli";
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
