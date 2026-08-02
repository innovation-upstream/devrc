// guard_plugin_driver.mjs — exercise opencode's guard plugin THROUGH ITS REAL
// ENTRY POINT.
//
// 🔴 WHY THIS EXISTS. The bug this fixture was written for (guard.js resolving
// its python core via `import.meta.url`, which node resolves through the
// home-manager store symlink to a FLAT /nix/store path, so `../guard_core.py`
// became `/nix/guard_core.py`) was invisible to every existing test, because
// every existing test either read guard.js as TEXT or imported guard_core.py
// DIRECTLY in python. Neither ever ran the plugin. A test that does not execute
// the deployed artifact cannot see a deployment bug.
//
// So: import the module the way opencode does, call the hook opencode calls,
// and report the outcome as JSON on stdout.
//
//   usage: node guard_plugin_driver.mjs <path-to-guard.js> <command>
//   stdout: {"outcome":"allow"}
//         | {"outcome":"throw","message":"…"}
//         | {"outcome":"no-hook"}
//         | {"outcome":"import-error","message":"…"}
//
// The driver NEVER exits non-zero for a guard verdict — a throw from the hook is
// the guard working, not the driver failing. It exits non-zero only when it
// could not run at all, so the caller can tell those two apart instead of
// reading one exit code for both.

const [, , pluginPath, command] = process.argv;

if (!pluginPath || command === undefined) {
  console.error("usage: guard_plugin_driver.mjs <path-to-guard.js> <command>");
  process.exit(2);
}

const { pathToFileURL } = await import("node:url");

let mod;
try {
  // Import BY THE PATH GIVEN. When the caller passes a symlink (the deployed
  // ~/.config/opencode/plugin/guard.js), node resolves it to the real file and
  // `import.meta.url` inside guard.js becomes the STORE path — which is exactly
  // the condition under test. Do not `realpath` it here.
  mod = await import(pathToFileURL(pluginPath).href);
} catch (e) {
  console.log(
    JSON.stringify({ outcome: "import-error", message: String((e && e.message) || e) }),
  );
  process.exit(0);
}

const factory = mod.GuardPlugin;
if (typeof factory !== "function") {
  console.log(JSON.stringify({ outcome: "no-hook", message: "no GuardPlugin export" }));
  process.exit(0);
}

const plugin = await factory({});
const hook = plugin && plugin["tool.execute.before"];
if (typeof hook !== "function") {
  console.log(
    JSON.stringify({ outcome: "no-hook", message: "no tool.execute.before hook" }),
  );
  process.exit(0);
}

try {
  // The two arguments opencode passes: the tool identity, and the mutable
  // output whose `args.command` is the shell command about to run.
  await hook({ tool: "bash" }, { args: { command } });
  console.log(JSON.stringify({ outcome: "allow" }));
} catch (e) {
  console.log(JSON.stringify({ outcome: "throw", message: String((e && e.message) || e) }));
}
