// options.js -- per-profile configuration (decision D2).
//
// Extension storage is per-profile, so ticking "enable" here scopes routing to
// THIS browser profile by construction. Every other profile keeps stock
// download behaviour with no code path through the router at all.
//
// The token comes from `dl-route token` (the sidecar's ~/.config/dl-router/token).
//
// THE PORT IS NOT A SETTING. `host_permissions` in manifest.json is a hard pin
// (`http://127.0.0.1:8791/*`) that an options page cannot change, so the port
// field used to be a footgun: editing it stored a value the extension was not
// permitted to reach, every fetch failed with a permissions error, and nothing
// in the UI said why. The field is now read-only and shows what the manifest
// actually allows -- one source of truth.
//
// Everything is exported and `mount()` takes its document + chrome, so the page
// is testable headlessly (it had zero coverage). The port rule itself lives in
// port.js so this file and the service worker cannot drift apart.

import { DEFAULT_PORT, manifestPort } from "./port.js";

export { DEFAULT_PORT, manifestPort };

/** Render the stored settings. The port always comes from the manifest. */
export async function load(doc, chromeApi) {
  const got = await chromeApi.storage.local.get(["token", "enabled"]);
  doc.getElementById("port").value = manifestPort(chromeApi);
  doc.getElementById("token").value = got.token || "";
  doc.getElementById("enabled").checked = Boolean(got.enabled);
}

/** Ask the sidecar who it is, and say so plainly. */
export async function probe(doc, chromeApi, fetchImpl) {
  const status = doc.getElementById("status");
  const port = manifestPort(chromeApi);
  const token = doc.getElementById("token").value.trim();
  if (!token) {
    status.textContent = "No token yet. Run `dl-route token` and paste it here.";
    return status.textContent;
  }
  try {
    const resp = await fetchImpl(`http://127.0.0.1:${port}/healthz`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (resp.status === 401) {
      status.textContent = "Sidecar reachable, token REJECTED.";
    } else if (!resp.ok) {
      status.textContent = `Sidecar returned HTTP ${resp.status}.`;
    } else {
      const body = await resp.json();
      if (body.configError) {
        status.textContent =
          `Sidecar up but its config is broken: ${body.configError}`;
      } else if (body.configured) {
        status.textContent =
          `Sidecar OK -- ${body.dirs} directories, ${body.aliases} aliases.`;
      } else {
        status.textContent =
          "Sidecar OK but library_root is not configured (see config.toml).";
      }
    }
  } catch (err) {
    status.textContent = `Sidecar unreachable on port ${port}: ${err}`;
  }
  return status.textContent;
}

/** Persist. The port is deliberately NOT stored -- the manifest owns it. */
export async function save(doc, chromeApi, fetchImpl) {
  const token = doc.getElementById("token").value.trim();
  const enabled = doc.getElementById("enabled").checked;
  await chromeApi.storage.local.set({ token, enabled });
  doc.getElementById("status").textContent = "Saved. Checking sidecar...";
  return probe(doc, chromeApi, fetchImpl);
}

export function mount(doc, chromeApi, fetchImpl = globalThis.fetch) {
  doc.getElementById("save").addEventListener(
    "click", () => { void save(doc, chromeApi, fetchImpl); });
  doc.getElementById("test").addEventListener(
    "click", () => { void probe(doc, chromeApi, fetchImpl); });
  return load(doc, chromeApi);
}

if (typeof document !== "undefined" && typeof chrome !== "undefined"
    && !globalThis.DL_ROUTER_NO_AUTOSTART) {
  void mount(document, chrome);
}
