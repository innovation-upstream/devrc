// options.js — persist the loopback port + bearer token into chrome.storage.local
// (read by service_worker.js). No network here; purely local config.
async function load() {
  const { port, token } = await chrome.storage.local.get(["port", "token"]);
  document.getElementById("port").value = port || 8788;
  document.getElementById("token").value = token || "";
}

document.getElementById("save").addEventListener("click", async () => {
  const port = parseInt(document.getElementById("port").value, 10) || 8788;
  const token = document.getElementById("token").value.trim();
  await chrome.storage.local.set({ port, token });
  document.getElementById("status").textContent =
    "Saved. The bridge will connect within ~1 minute (or reload the extension).";
});

load();
