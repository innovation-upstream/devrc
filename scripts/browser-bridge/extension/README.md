# browser-bridge extension (MV3)

A standalone Manifest V3 extension that connects the user's live Brave session to
the local `browser-bridge` server so a Claude Code skill can drive the active
tab. This is a **sibling** to the activity collector's extension — do not confuse
or merge them.

## Files

| file | role |
|------|------|
| `manifest.json`     | MV3 manifest (permissions, background SW, options page) |
| `service_worker.js` | long-poll loop + chrome.* op executors (needs real Brave) |
| `protocol.js`       | pure op-set / validation / envelope / backoff logic (unit-tested) |
| `options.html/js`   | one-time setup: paste the bearer token + port into `chrome.storage.local` |

## Load it (and reload after every change)

1. Brave → `brave://extensions`
2. Toggle **Developer mode** (top-right).
3. **Load unpacked** → select this `extension/` directory.
4. Click the extension's **Options** (⋯ menu → Options), paste the token from
   `~/.config/browser-bridge/token`, set port `8788`, **Save**.

⚠ **Brave does not hot-reload unpacked extensions.** After editing any file here,
click the **reload** ↻ button on the extension's card in `brave://extensions`,
or the service worker keeps running the old code.

## Permissions (maximal — can be scoped later)

`scripting`, `tabs`, `activeTab`, `alarms`, `storage`, and
`host_permissions: ["<all_urls>"]`. `<all_urls>` + `scripting` is what lets the
worker run in whatever tab is active. If you only ever drive a known set of
sites, scope `host_permissions` to those origins and reload.

## Stable extension ID (optional)

Unpacked extensions get a per-path random ID. To pin a stable ID (e.g. so an
allowlist elsewhere can reference it), generate a keypair and add its public key
as a top-level `"key"` in `manifest.json`:

```bash
# generate a private key + derive the manifest "key" (base64 SPKI):
openssl genrsa 2048 | openssl pkcs8 -topk8 -nocrypt -out key.pem
openssl rsa -in key.pem -pubout -outform DER | base64 -w0   # → paste as manifest "key"
```

Left out of the committed manifest (MVP) — the bridge does not depend on the ID.

## Manual test checklist (what the unit tests can't cover)

The pure logic in `protocol.js` is unit-tested (`../tests/protocol.test.mjs`).
The chrome.* glue needs a real browser — verify by hand after loading:

- [ ] With the server running + token pasted, `browser health` reports
      `extension_connected:true` within ~1 min (or after a reload).
- [ ] `browser html` on a logged-in tab returns markup containing logged-in-only
      content (proves the live authenticated session).
- [ ] `browser eval 'document.title'` returns the active tab's title.
- [ ] `browser tabs` lists your open tabs.
- [ ] `browser nav https://example.com` navigates the active tab.
- [ ] `browser screenshot /tmp/shot.png` writes a real PNG of the visible tab.
- [ ] Stop the server → `browser health` fails / `extension_connected:false`
      after the stale window; restart → it reconnects on the next poll.
