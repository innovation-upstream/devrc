// options.js — per-profile configuration (decision D2).
//
// Extension storage is per-profile, so ticking "enable" here scopes routing to
// THIS Brave profile by construction. Every other profile keeps stock download
// behaviour with no code path through the router at all.
//
// The token comes from `dl-route token` (the sidecar's ~/.config/dl-router/token).

const DEFAULT_PORT = 8791;

async function load() {
  const { port, token, enabled } = await chrome.storage.local.get(
    ["port", "token", "enabled"]);
  document.getElementById("port").value = port || DEFAULT_PORT;
  document.getElementById("token").value = token || "";
  document.getElementById("enabled").checked = Boolean(enabled);
}

async function probe() {
  const status = document.getElementById("status");
  const port = parseInt(document.getElementById("port").value, 10) || DEFAULT_PORT;
  const token = document.getElementById("token").value.trim();
  try {
    const resp = await fetch(`http://127.0.0.1:${port}/healthz`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (resp.status === 401) { status.textContent = "Sidecar reachable, token REJECTED."; return; }
    if (!resp.ok) { status.textContent = `Sidecar returned HTTP ${resp.status}.`; return; }
    const body = await resp.json();
    status.textContent = body.configured
      ? `Sidecar OK — ${body.dirs} directories, ${body.aliases} aliases.`
      : "Sidecar OK but library_root is not configured (see config.toml).";
  } catch (err) {
    status.textContent = `Sidecar unreachable: ${err}`;
  }
}

document.getElementById("save").addEventListener("click", async () => {
  const port = parseInt(document.getElementById("port").value, 10) || DEFAULT_PORT;
  const token = document.getElementById("token").value.trim();
  const enabled = document.getElementById("enabled").checked;
  await chrome.storage.local.set({ port, token, enabled });
  document.getElementById("status").textContent = "Saved. Checking sidecar…";
  await probe();
});

document.getElementById("test").addEventListener("click", probe);

load();
