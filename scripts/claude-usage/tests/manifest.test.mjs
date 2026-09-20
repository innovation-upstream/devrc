// manifest.test.mjs -- the SEAM between manifest.json and the code it loads.
//
// 🔴 WHY THIS FILE EXISTS. Every module in this extension was hermetically
// tested, the whole node tier was green in BOTH tiers, a mutation battery was
// 13/13, and the in-page widget was nevertheless DEAD ON ARRIVAL: a fix commit
// added lib/severity.js, lib/widget.js imported it, and manifest.json's
// `web_accessible_resources` was not updated. The page-context dynamic import
// then rejects with "Failed to fetch dynamically imported module", the content
// script's own catch swallows it, and NOTHING mounts and nothing logs.
//
// No test could see it, and that is structural rather than an oversight:
//   * node resolves `./severity.js` off the FILESYSTEM, where every import
//     works whether or not the manifest exposes it. Every unit test passes.
//   * an earlier hand-check asserted "every file the manifest NAMES exists",
//     which is the wrong DIRECTION -- it cannot see a file the code NEEDS and
//     the manifest fails to name.
//
// So this file asserts a RELATIONSHIP, not a component: the web-accessible set
// must equal the transitive import closure of what the page actually loads.
// It fails when the set GROWS as well as when it SHRINKS, because an
// over-broad WAR entry exposes extension internals to claude.ai's own scripts.
//
// The template is scripts/dl-router/tests/picker_overlay.test.mjs, which pins
// the same property for the same reason.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const EXT = resolve(HERE, "../extension");
const manifest = JSON.parse(readFileSync(join(EXT, "manifest.json"), "utf8"));

/** Every `from "./x.js"` / `from "../y.js"` in a file, resolved to a path
 * relative to the extension root. Static imports only -- this extension uses
 * no dynamic imports inside its modules, and the ONE dynamic import (the
 * content script's entry into lib/widget.js) is the root we start from. */
function staticImports(relFile) {
  const src = readFileSync(join(EXT, relFile), "utf8");
  const dir = dirname(relFile);
  const out = new Set();
  for (const m of src.matchAll(/(?:from|import)\s+["'](\.\.?\/[^"']+)["']/g)) {
    out.add(join(dir, m[1]).replace(/\\/g, "/"));
  }
  return out;
}

/** Transitive closure of static imports from a set of roots, roots included. */
function importClosure(roots) {
  const seen = new Set();
  const queue = [...roots];
  while (queue.length) {
    const f = queue.pop();
    if (seen.has(f)) continue;
    seen.add(f);
    for (const dep of staticImports(f)) if (!seen.has(dep)) queue.push(dep);
  }
  return seen;
}

const warResources = new Set(
  (manifest.web_accessible_resources || []).flatMap((w) => w.resources || []),
);

test("🔴 the web-accessible set IS the page-loaded import closure -- no more, no less", () => {
  // content_widget.js dynamically imports lib/widget.js from PAGE context, so
  // widget.js and everything it pulls in must be web-accessible.
  const needed = importClosure(["lib/widget.js"]);

  const missing = [...needed].filter((f) => !warResources.has(f)).sort();
  assert.deepEqual(missing, [],
    "a module the widget imports is NOT web-accessible -- the dynamic import "
    + "rejects in the browser and the widget silently never mounts");

  const extra = [...warResources].filter((f) => !needed.has(f)).sort();
  assert.deepEqual(extra, [],
    "a web-accessible resource nothing loads -- exposes extension internals "
    + "to claude.ai's own scripts for no reason");
});

test("the closure walker actually walks -- positive control", () => {
  // A zero from an unproven walker is indistinguishable from a walker wired to
  // nothing. This asserts the instrument CAN see a transitive dependency: the
  // ONLY way severity.js enters the set is via widget.js importing it, since
  // content_widget.js does not name it.
  const closure = importClosure(["lib/widget.js"]);
  assert.ok(closure.size >= 4, `walked only ${closure.size} file(s)`);
  assert.ok(closure.has("lib/severity.js"),
    "the walker must reach a SECOND-level import, not just the root");
  assert.ok(!staticImports("content_widget.js").has("lib/severity.js"),
    "severity.js is reached transitively, not named directly -- if this ever "
    + "becomes false the test above stops proving transitivity");
});

test("every path the manifest names actually exists on disk", () => {
  // The other direction, and the one a hand-check already covered. Kept
  // because it is cheap and the flake ships only TRACKED files: a manifest
  // entry pointing at a file that was never `git add`ed deploys as a 404.
  const named = [
    manifest.background.service_worker,
    ...manifest.content_scripts.flatMap((c) => c.js),
    ...warResources,
    ...Object.values(manifest.icons),
    ...Object.values(manifest.action.default_icon || {}),
    manifest.action.default_popup,
  ];
  const absent = named.filter((p) => !existsSync(join(EXT, p))).sort();
  assert.deepEqual(absent, [], "manifest names a file that is not on disk");
});

test("the content scripts are registered in dependency-free order", () => {
  // Both are classic (non-module) content scripts in one `js` array; neither
  // may rely on the other's globals, since MV3 gives no ordering guarantee
  // beyond array order and either can be absent if injection is interrupted.
  const cs = manifest.content_scripts.find((c) => c.js.includes("content_widget.js"));
  assert.ok(cs, "the widget content script is registered");
  assert.equal(cs.run_at, "document_idle");
  assert.deepEqual(cs.matches, ["https://claude.ai/*"]);
  for (const f of cs.js) {
    assert.deepEqual([...staticImports(f)], [],
      `${f} is a CLASSIC content script and must have no static imports`);
  }
});

test("the icon set is complete on both surfaces", () => {
  // "add an icon" is half this PR's purpose; relying on Chrome's icons->action
  // fallback leaves the toolbar rendering an unverified default.
  for (const size of ["16", "48", "128"]) {
    assert.ok(manifest.icons[size], `icons is missing ${size}`);
    assert.ok(manifest.action.default_icon[size], `action.default_icon is missing ${size}`);
  }
});
