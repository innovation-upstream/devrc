// options.js — persist the loopback port + bearer token + optional instance
// label into chrome.storage.local (read by service_worker.js). No network here;
// purely local config.
//
// The `label` is the human routing key for multi-instance targeting: when set,
// it is this profile's key for `browser --instance <label>`; when blank, the
// stable auto-generated instanceId (also in chrome.storage.local) is the key.
async function load() {
  const { port, token, label } = await chrome.storage.local.get(["port", "token", "label"]);
  document.getElementById("port").value = port || 8788;
  document.getElementById("token").value = token || "";
  document.getElementById("label").value = label || "";
}

document.getElementById("save").addEventListener("click", async () => {
  const port = parseInt(document.getElementById("port").value, 10) || 8788;
  const token = document.getElementById("token").value.trim();
  const label = document.getElementById("label").value.trim();
  await chrome.storage.local.set({ port, token, label });
  document.getElementById("status").textContent =
    "Saved. The bridge will connect within ~1 minute (or reload the extension).";
});

load();
