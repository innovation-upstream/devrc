# clawgate — the browser extension: delivery, reload, and verifying a build

Read when: you changed the clawgate browser extension, or you need to know **which build is actually
loaded** in Zach's Brave.

## 🔴 The extension does NOT ship via Flux — merging to `trunk` deploys NOTHING
Brave loads it unpacked from a **flat copy** at `~/clawgate-extension` **on the workbench** (that is
where Zach's Brave runs — `localhost:8972` civitai-manager answers there, not on the desktop).

Delivery is `scripts/sync-clawgate-extension.sh` (`--check` = drift only, rc 1 on drift), and
**Brave does not hot-reload unpacked extensions** — a sync without a `brave://extensions` ↻ leaves
the OLD build running. After adding a command, also check `brave://extensions/shortcuts`: Brave
routinely leaves newly-added hotkeys unbound on an in-place reload.

## 🔴 A missing `.synced-from` stamp means someone hand-copied it out of band
That is exactly how a build that never passed CI ran live for ~11 hours while `trunk` looked clean —
copied straight from a working tree, so `clawgate-ci` never saw it. Check the stamp before trusting
any claim about which build is loaded:

```bash
ssh 192.168.50.250 'cat ~/clawgate-extension/.synced-from; grep "\"version\"" ~/clawgate-extension/manifest.json'
ssh 192.168.50.250 '~/workspace/homelab-talos/scripts/sync-clawgate-extension.sh --check'
```

## ⚠ Serving a scratch test page TO that Brave
The workbench firewall `allowedTCPPorts` is a short allowlist
(80/443/6443/7844/8110/8180/25565/58012), so an ad-hoc port is unreachable over the LAN — serve it on
the workbench and open **`http://localhost:<port>`**, never open a firewall port for a throwaway.

Verifying the picker's privacy guard by hand has a trap that makes the obvious test vacuous →
`/home/zach/workspace/devrc/claude/skills/clawgate/reference/element-references.md`.

## CI scope
`clawgate-ci` (Tekton — see the `tekton` skill) runs `go build`/`vet`/`test -race` + extension
coverage + hook bats. It does **NOT** run Playwright, so the browser layer is UNGATED by CI. Run
`make e2e` locally and **count** the results: `tasks.spec.ts` `test.skip`s the whole file without
Docker, so a "green" run can mean 17 tests never executed.
