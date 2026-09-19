// sw-mock.mjs -- a chrome.* mock for driving service_worker.js under node.
//
// Mirrors scripts/dl-router/tests' approach: each .test.mjs file is its own
// node process, sets `globalThis.chrome` BEFORE importing the worker, and
// sets CLAUDE_USAGE_NO_AUTOSTART to suppress listener registration and
// networking (trigger.test.mjs is the one suite that deliberately omits it --
// it pins the cold-start registration).

export function makeChromeMock(initialStorage = {}) {
  const storage = { ...initialStorage };
  const listeners = {};
  const record = (name) => ({
    addListener: (fn) => { listeners[name] = fn; },
  });
  const calls = {
    notifications: [],
    badgeText: [],
    badgeColor: [],
    badgeTitle: [],
    alarmsCreated: [],
    tabMessages: [],
  };
  const chrome = {
    storage: {
      local: {
        get: async (keys) => {
          const out = {};
          for (const k of [].concat(keys)) {
            if (k in storage) out[k] = JSON.parse(JSON.stringify(storage[k]));
          }
          return out;
        },
        set: async (patch) => {
          for (const [k, v] of Object.entries(patch)) storage[k] = JSON.parse(JSON.stringify(v));
        },
      },
    },
    runtime: {
      getURL: (p) => `chrome-extension://claude-usage-test/${p}`,
      sendMessage: async () => ({}),
      onMessage: record("runtime.onMessage"),
    },
    notifications: {
      create: async (opts) => { calls.notifications.push(opts); return "n1"; },
    },
    action: {
      setBadgeText: async (o) => { calls.badgeText.push(o.text); },
      setBadgeBackgroundColor: async (o) => { calls.badgeColor.push(o.color); },
      setTitle: async (o) => { calls.badgeTitle.push(o.title); },
    },
    tabs: {
      query: async () => [],
      get: async () => ({ id: 1, url: "https://claude.ai/new" }),
      sendMessage: async (tabId, msg) => { calls.tabMessages.push({ tabId, msg }); },
      onUpdated: record("tabs.onUpdated"),
      onActivated: record("tabs.onActivated"),
    },
    alarms: {
      create: (name, info) => { calls.alarmsCreated.push({ name, ...info }); },
      onAlarm: record("alarms.onAlarm"),
    },
  };
  return { chrome, storage, listeners, calls };
}

export const NO_AUTOSTART = () => {
  globalThis.CLAUDE_USAGE_NO_AUTOSTART = true;
};
