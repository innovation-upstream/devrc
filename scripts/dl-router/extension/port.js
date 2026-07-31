// port.js -- the ONE place the sidecar port is decided.
//
// `host_permissions` in manifest.json is a hard pin (`http://127.0.0.1:8791/*`)
// that no page can change, so the port is not a setting: a stored value the
// extension is not permitted to fetch just fails every request with no UI
// explanation. Reading it back OUT of the manifest keeps one source of truth.
//
// This module exists because the identical regex was living in two files
// (service_worker.js and options.js). Two copies of a rule is how the
// safety.py/sanitize.js divergence started.

export const DEFAULT_PORT = 8791;

const LOOPBACK_PERMISSION = /^https?:\/\/127\.0\.0\.1:(\d+)\//;

/**
 * The port the manifest permits, i.e. the only port this extension can reach.
 * Falls back to the default for any manifest shape that does not name one.
 */
export function manifestPort(chromeApi) {
  try {
    const perms = chromeApi.runtime.getManifest()?.host_permissions || [];
    for (const perm of perms) {
      const m = LOOPBACK_PERMISSION.exec(String(perm));
      if (m) {
        const port = Number(m[1]);
        if (Number.isInteger(port) && port >= 1 && port <= 65535) return port;
      }
    }
  } catch { /* fall through */ }
  return DEFAULT_PORT;
}
