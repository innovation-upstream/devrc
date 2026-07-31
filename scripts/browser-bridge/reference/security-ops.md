# Security contract, telemetry, first-time setup, and the change gate

**Load this when:** you are about to MODIFY browser-bridge (server / extension / CLI)
— the live-verify gate below is MANDATORY · the user asks whether/why this is safe, or
what it records · `browser health` says `extension_connected:false` and the extension
has never been paired on this profile · you are setting up a second Brave profile.

Core: `~/workspace/devrc/scripts/browser-bridge/SKILL.md`.
Full architecture + security model: `~/workspace/devrc/scripts/browser-bridge/README.md`.

## What this is

`browser-bridge` lets you operate the user's **real, logged-in Brave** browser.
Commands go: the `browser` CLI → a loopback rendezvous server (`127.0.0.1:8788`,
bearer-token auth) → a standalone MV3 extension in the live Brave session →
executed against the **active tab** → result back to you.

This is authorized personal automation on the user's own workbench. It is a
**sibling** to the activity-collector browser extension (telemetry) — different
subsystem, do not conflate.

## Security contract (why it's safe)

- **Loopback only** (`127.0.0.1:8788`) — never bound to an external interface.
- **Bearer token** on every request — auto-created `0600` at
  `~/.config/browser-bridge/token` on first server start. The `browser` CLI
  reads it; a web page can't. Defeats DNS-rebinding.
- **Host-header allowlist** — only `127.0.0.1`/`localhost`/`::1`.
- **No `cookies` permission, on purpose** (README → *Security model*) — which is why
  there is no cookie op; see the core's authenticated-request pattern.
- **CDP is own-tab-scoped, typed, and always detached** — details in
  `~/workspace/devrc/scripts/browser-bridge/reference/frames-cdp.md` § *CDP security model*.
- **`upload` is data-exfil-capable** (any readable file's CONTENTS could be posted to
  the site), so **every upload is AUDIT-LOGGED** (op + target domain + path) and it is
  **OPERATOR-ONLY** — not in the autonomous agent's default op set (`op_not_allowed:upload`;
  re-enable only via an explicit `BROWSER_AGENT_ALLOWED_OPS` opt-in). The result carries
  the basename only. Chrome reads the file BY PATH itself (same host), so **no bytes
  cross the bridge**; the CLI validates the path (readable regular file) and resolves it
  to ABSOLUTE **before** dispatch.
- **`screenshot` writes owner-only files** — a screenshot is a pixel-perfect image of an
  **authenticated** view, so temp captures are mode-0600 and **auto-pruned after 24h**
  (prefix-scoped, best-effort). Copy one to an explicit `path` if you need to keep it.
  The payload is validated (strict base64 + PNG signature) before any write, so a failed
  capture errors instead of leaving a 0-byte `.png`.

## Telemetry (metadata-only)

Every handled command emits one **best-effort** activity event
(`source=browser-bridge`, `kind=cmd`) into the personal telemetry pipeline
(`activity.events`), so browser-skill usage is queryable / visible to
`adoption-scan`. It records **only** metadata — op, instance key, outcome,
latency, and the active tab's **bare domain** — **never** the eval source, page
HTML, screenshot bytes, full URLs, or any page content. It runs off the critical
path and can never delay or break a command. Nothing you need to do; noted so you
know browser usage is being counted. Details: `README.md` → Telemetry.

## 🔴 Changing the bridge: live-verify against real Brave is the ONLY gate

If you MODIFY browser-bridge (server / extension / CLI), a green test suite and a
clean security audit are **prerequisites, NOT verification** — CI cannot drive a
real Brave, so it never exercises the actual MV3 / CDP / i3 behaviour. Across the
build-out, driving each change against the live browser caught ~11 defects that
passing tests and audits BOTH missed (Chrome-side focus being inert on i3;
`chrome.scripting` unable to eval a string → CDP `Runtime.evaluate`;
`captureVisibleTab` needing foreground → CDP `Page.captureScreenshot`; OOPIFs
needing `Target.setAutoAttach`; the reload-vs-restart trap). So the mandatory loop
for any browser change is **build → audit → fix → merge → ship → live-verify on
real Brave** — reproduce the exact path and LOOK at the actual result (exit 0 is
not verification). Operate changes via a feature branch + `/audit-pr`-style review
given it's a live-cookie surface.

## One-time setup (hand these steps to the user)

1. `home-manager switch --flake ~/workspace/devrc --impure` (starts the service).
2. Brave → `brave://extensions` → Developer mode → **Load unpacked** →
   `scripts/browser-bridge/extension/`.
3. Extension **Options** → paste the token from `~/.config/browser-bridge/token`,
   port `8788`, optionally set a unique **label** (required only if a second
   profile also connects), **Save**.
4. `browser health` → `extension_connected:true`.

For a second profile, repeat in that profile and give it a **different** label,
then target with `browser --instance <label> <op>`.

> After updating the extension you MUST reload it — and reload ↻ is unreliable;
> see `~/workspace/devrc/scripts/browser-bridge/reference/errors.md`. Brave may prompt to re-confirm the `debugger` permission.
