# kubeclaw agent-devpod hardening playbook (0.7.x chart values)

Read when: locking down a kubeclaw agent devpod **on the homelab cluster** (initiatives,
task-drafter, …). Harden via first-class chart **values** (0.7.x), **NOT postRenderers**.

🔴 **This is a playbook, not a description of what agents already have — and the netpol half does
not apply to clawgate-provisioned agents at all.** Measured 2026-08-12: the chart defaults are
`networkPolicy.enabled: false` and `tls.verify: false` (→ `NODE_TLS_REJECT_UNAUTHORIZED=0`), and no
live `devpod-*` namespace on workbench has a NetworkPolicy. The **container** securityContext IS
applied by default (`allowPrivilegeEscalation: false` + seccomp `RuntimeDefault`, confirmed on a
live pod); only **`podSecurityContext`** is `{}`. And because the FQDN allowlist can only be
expressed as a **CiliumNetworkPolicy**, it is unavailable on **workbench — which has 0 Cilium CRDs**
and is exactly where clawgate provisions agents. Homelab has Cilium; workbench does not.

- **`securityContext`** — `allowPrivilegeEscalation:false` + seccomp `RuntimeDefault` always. Add
  `capabilities.drop:[ALL]` **ONLY if the image bakes its runtime deps** (no apt/dpkg at init) —
  cap-drop breaks apt-at-init, which needs `CHOWN` + `DAC_OVERRIDE`. Non-root is still deferred
  (the chart hardcodes `/root/.openclaw|.ssh|.kube`).
- **`tls.verify:true`** (→ `NODE_TLS_REJECT_UNAUTHORIZED=1`) when the egress targets present valid
  CAs — this overrides the chart's historical hardcoded `0`.
- **`networkPolicy`** (Cilium; `cilium:true`) egress **default-deny + a PER-AGENT FQDN allowlist**.
  Needs `allowKubeDns:true` (so `toFQDNs`/`rules.dns` resolves) + `allowKubeApiserver:true`.
  ⚠ kubeclaw **0.7.1 fails-loud on an empty allowlist**; **0.7.0 silently bricks egress** to DNS +
  API only.
- **Per-agent egress must include**: the **model host** (`openrouter.ai` + `*.openrouter.ai`); any
  **APIs** the agent calls (clickup, etc.); **GitHub** (`github.com` + `*.githubusercontent.com`
  over https, **`:22` if it SSH-clones**, `api.github.com` + `codeload.github.com`); the
  **`infraTools` install sources** IF enabled (`dl.k8s.io` **plus `cdn.dl.k8s.io`**, which the
  release redirect lands on, and bare **`fluxcd.io`** — the installer fetches `https://fluxcd.io/install.sh`,
  and a `matchPattern: *.fluxcd.io` does NOT match the apex; GitHub releases —
  **NOT** `*.debian.org` unless the image apt-installs at init); plus kube-dns + apiserver.
- 🔴 **`tools.web.search.enabled:false` for any restricted-egress agent.**
  `openclaw doctor --fix` auto-enables a brave/perplexity plugin that npm-fetches
  `registry.npmjs.org` and **HANGS the gateway** under a locked egress — this is the whole
  2026.6.11 rollback class. Turn it off unless web search is load-bearing; if it is, allowlist
  `registry.npmjs.org` + `api.search.brave.com` instead.
- **Base-image rebuild**: the shared `openclaw-image` rebuilds via **`--legacy-peer-deps`** on the
  nested matrix-bot-sdk install (npm arborist `edgesOut` bug on node:22-slim's npm 10.9.8) — see
  openclaw-image PR #3.
