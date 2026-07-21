# Driving Playwright on NixOS

**Problem (gametape #6):** Playwright's bundled Chromium (`~/.cache/ms-playwright/chromium-*/…/chrome`)
is a generic-linux dynamically-linked ELF. NixOS has no global `/lib` `ld-linux`,
so `stub-ld` refuses it — the browser dies at spawn with `exitCode=127` (and, with
an FHS/patchelf half-fix in play, the classic `GLIBC_ABI_GNU2_TLS not found`). Result:
every headless-Chromium e2e / OAuth-tester / run-page click-path was undrivable, forcing
DB-baseline *proxies* for verification.

**Fix:** use the browsers nixpkgs already patched for NixOS
(`playwright-driver.browsers`) via `PLAYWRIGHT_BROWSERS_PATH`, and skip Playwright's
host-requirements probe + its (broken) auto-download.

## One pinned source of truth

Both paths resolve the browser bundle from **this repo's flake** (the
`flake.lock`-pinned, `allowUnfree` nixpkgs), never the ambient `nixpkgs#…`
registry (which tracks the moving unstable channel and would silently drift):

- `flake.nix` exposes `packages.<system>.playwright-driver = pkgs.playwright-driver`.
- `scripts/playwright-nixos` builds `<repo>#playwright-driver.browsers` (+ reads
  `.version`) — self-locating via `readlink -f "$0"` so it uses the flake it ships in.
- `nix/sessionVariables.nix` exports the same `pkgs.playwright-driver.browsers`.

So the wrapper, interactive shells, and the MCP all get the **identical** Chromium
build; a channel bump can't split them.

## The version-match gotcha

Playwright pins **one exact Chromium build per release**. The npm `playwright` /
`@playwright/test` / `playwright-core` version in a project **must equal** the nixpkgs
`playwright-driver` version, or Playwright looks for a build number the nix bundle
doesn't contain and refuses to launch.

- Get the version to pin to: `scripts/playwright-nixos --version` (currently **1.61.1**).
- Pin npm to it: `npm install playwright@$(scripts/playwright-nixos --version)`.
- The wrapper WARNS if it detects a mismatched local `node_modules` install.
- It also WARNS on **MCP build skew**: it compares the Chromium build the Playwright
  MCP already fetched (`~/.cache/ms-playwright/chromium-<N>`) against the build the
  pinned bundle provides; a mismatch means the MCP (Recipe 2) would fail to launch even
  though the wrapper works — bump the flake's `playwright-driver` (or the MCP's
  `playwright-core`) so the two build numbers agree.

## Recipe 1 — the wrapper (switch-free; for agents / CI / one-offs)

`scripts/playwright-nixos` resolves the nix browser bundle on demand (cached after the
first realise) and exports the right env, needing **no `home-manager switch`**:

```sh
# run a script / test with Chromium wired up:
scripts/playwright-nixos node e2e.mjs
scripts/playwright-nixos npx playwright test

# or export into the current shell:
eval "$(scripts/playwright-nixos --env)"

# introspection:
scripts/playwright-nixos --version   # nixpkgs driver version to pin npm to
scripts/playwright-nixos --env       # the three export lines
```

This is the path the **Bash tool** must use: it runs *non-interactive* `zsh -c`, which
sources `.zshenv` only — NOT the `profile.d` file that carries `home.sessionVariables`
(Recipe 2). So inside a Claude session, always go through the wrapper.

The three env vars it sets:

| var | value |
| --- | --- |
| `PLAYWRIGHT_BROWSERS_PATH` | `…-playwright-browsers` (nix store) |
| `PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS` | `true` |
| `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD` | `1` (so `npm install` won't re-fetch the broken bundle) |

## Recipe 2 — global (interactive shells + the Playwright MCP)

`nix/sessionVariables.nix` now exports `PLAYWRIGHT_BROWSERS_PATH` +
`PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS` from `pkgs.playwright-driver.browsers`
(the arg was already plumbed into the file but silently dropped — it's now used).
After a `home-manager switch` / `ship.sh`, **interactive** shells and any process they
launch — including the **Playwright MCP** (`mcp__playwright__browser_*`, whose cached
Chromium build 1228 already matches driver 1.61.1) — launch Chromium natively with no
per-command wrapper. Non-interactive `zsh -c` still won't see it → use Recipe 1 there.

## How this composes with verification

- **`/verify-agent`** is the *mechanical* gate — build / typecheck / test / vet /
  git-completeness only. It does **not** drive a browser. Unchanged.
- **The `verify` skill's e2e layer** (and `/ux-audit`, the `ux-audit-loops`
  naida/vetr harnesses) is what this unblocks: real headless-Chromium click-path /
  OAuth / run-page checks can now run in-session instead of DB-baseline proxies.
- Drive them via **Recipe 1** inside a session, or the **Playwright MCP** after a
  switch (Recipe 2).

## Known limitation

Because the browser bundle is now a `home.sessionVariables` value + a flake package,
**every `home-manager switch` realises the ~hundreds-of-MB nixpkgs browser bundle** as
a build-time dependency (first switch per host; then cached). On a network-constrained
host (e.g. the laptop on a slow link) that first switch can be slow or fail to fetch —
it's a one-time cost per host, but worth knowing.

## Verified

`scripts/playwright-nixos node launch.mjs` on the workbench: headless Chromium
launched (`browserVersion=149.0.7827.55`), rendered `page.setContent(...)`
(title + `#x` text read back), screenshotted, and `goto('https://example.com')`
returned **200 / "Example Domain"**. The same script against the default
`~/.cache/ms-playwright` bundle fails with `exitCode=127`. Version-mismatch warning
fires when local npm playwright ≠ driver version.
