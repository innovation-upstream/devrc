# comic-flex.homelab.lan — the LAN lane of comic-flex

**Load this when:** a result envelope named this file in `site_notes` · you are
driving the comic-flex PWA over the LAN · a `js`/`eval` came back `null` here.

🔴 **The flows, selectors and traps live in one place:
`reference/sites/comics.zacx.dev.md`. Read that.** This host is the same app on
the same image; only the two facts below differ, and both change what a pass means.

1. 🔴 **No Authelia.** Plain `http://comic-flex.homelab.lan` is open on the LAN —
   `GET /ui/status` and `POST /api/next` both answer `200` with no login. This is
   the lane to drive. Reaching for the public host out of habit is what produced
   two "the feature is broken" reports that were really `302`s to Authelia.
2. 🔴 **A service worker NEVER registers here.** Plain HTTP that is not `localhost`
   is not a secure context, so this lane is **structurally blind** to every
   service-worker and stale-cache bug. A clean pass here is not evidence about
   caching. For a secure context without Authelia, `kubectl port-forward` and use
   `http://localhost:<port>`.

And the one that fakes a broken tool, repeated here because it is why sessions
stop: **`js` and `eval` return `null` on this app** — its CSP withholds
`unsafe-eval`. The bridge is fine. Control: `js '1+1'` on `example.com` returns
`2` in the same instance. Use `text` / `html` / `text --annotated` to read and the
trusted `click` op to drive; neither needs page scripting.
