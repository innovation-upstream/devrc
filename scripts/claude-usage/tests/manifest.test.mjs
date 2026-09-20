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
import { execFileSync } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const EXT = resolve(HERE, "../extension");
const manifest = JSON.parse(readFileSync(join(EXT, "manifest.json"), "utf8"));

/** Every `from "./x.js"` / `from "../y.js"` in a file, resolved to a path
 * relative to the extension root.
 *
 * 🔴 STATIC IMPORTS ONLY, AND THAT IS A PRECONDITION THIS FILE ENFORCES
 * RATHER THAN ASSERTS IN PROSE. A `await import("./x.js")` inside a walked
 * module is INVISIBLE to this regex, so the closure would silently omit
 * `lib/x.js`, both directions of the equality below would pass, and the
 * widget would be dead on arrival again with this guard green -- which is
 * precisely the failure it was written for. A comment saying "we don't use
 * dynamic imports" is not a guard; `no_dynamic_imports_in_the_closure` below
 * is. (Measured against the regex: re-exports, multi-line imports,
 * side-effect imports and import attributes are all SEEN; a commented-out
 * import is a false positive, which fails loud and safe.) */
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
  // ONLY way severity.js enters the set is via widget.js importing it.
  const closure = importClosure(["lib/widget.js"]);
  assert.ok(closure.size >= 4, `walked only ${closure.size} file(s)`);
  assert.ok(closure.has("lib/severity.js"),
    "the walker must reach a SECOND-level import, not just the root");
  // A third assertion lived here -- that content_widget.js does not name
  // severity.js directly -- and it was VACUOUS: content_widget.js is a classic
  // content script whose static-import set the test below separately asserts
  // must be EMPTY, so it checked that an empty set lacks an element. True by
  // construction, incapable of failing, and its stated tripwire could never
  // fire. Removed rather than repaired: the two assertions above do the real
  // work, and a third layer that cannot fail reads as protection while
  // providing none.
});

test("🔴 no DYNAMIC import hides inside the walked closure", () => {
  // The precondition that makes the closure test meaningful. `import(expr)` is
  // unresolvable by static analysis, so if one appears inside a walked module
  // the closure silently under-reports and the guard above goes green over a
  // dead widget. Enforced, not asserted in a comment.
  //
  // The ONE legitimate dynamic import is content_widget.js's entry into
  // lib/widget.js -- the root of the walk, and not itself walked.
  for (const rel of importClosure(["lib/widget.js"])) {
    const src = readFileSync(join(EXT, rel), "utf8");
    const hit = src.match(/\bimport\s*\(/);
    assert.equal(hit, null,
      `${rel} contains a dynamic import, which the closure walker cannot `
      + `follow -- add it to the walk explicitly, or the WAR list is unpinned`);
  }
});

test("every path the manifest names exists AND is git-tracked", () => {
  // Two different hazards, and an earlier version of this test conflated them:
  // its comment named the `git add` trap (the flake ships only TRACKED files,
  // so an untracked file deploys as a 404) while its assertion called
  // existsSync, which sees an untracked file perfectly well and passes. A
  // comment claiming coverage the code does not provide is worse than no
  // comment, because it stops the next person looking.
  //
  // So both are checked. The nix sandbox tier would also catch the trackedness
  // half -- its source copy contains only tracked files -- but that is a
  // property of THAT TIER, not of this test, and the dev-host tier is where
  // most runs happen.
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

  // `git ls-files` lists tracked paths only, so an added-but-not-`git add`ed
  // file is absent from it while sitting right there on disk.
  //
  // 🔴 THIS RUNS IN TWO TIERS AND ONLY ONE OF THEM HAS A `.git`. The nix
  // sandbox tier builds from a `cp -r ${./.}` STORE COPY with no git
  // repository, so `git ls-files` there exits non-zero or returns nothing.
  // The first version of this asserted `tracked.size > 0` unconditionally and
  // went RED in CI while passing on every dev host — the exact two-tier trap
  // CLAUDE.md names, walked into by a test written to close a different one.
  //
  // The skip is NOT vacuous, and that distinction is the whole point: the
  // sandbox's source copy contains ONLY tracked files, so in that tier the
  // existence assertion above IS the trackedness assertion — a manifest entry
  // for an untracked file fails it there by construction. So each tier proves
  // the property by the means available to it, and neither is silently
  // skipping. What is NOT allowed is failing to notice which tier you are in.
  let tracked = null;
  try {
    tracked = new Set(
      execFileSync("git", ["ls-files", "--", "."],
        { cwd: EXT, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] })
        .split("\n").filter(Boolean).map((p) => p.replace(/\\/g, "/")),
    );
  } catch {
    tracked = null;                       // no git here — sandbox tier
  }

  if (tracked === null || tracked.size === 0) {
    // Prove we are actually in the no-git tier rather than in a checkout whose
    // git invocation merely failed: a real checkout has a .git entry.
    assert.equal(existsSync(join(EXT, "../../../.git")), false,
      "git ls-files produced nothing INSIDE a real checkout — that is a broken "
      + "instrument, not the sandbox tier, and this check must not be skipped");
    return;
  }

  const untracked = named.filter((p) => !tracked.has(p)).sort();
  assert.deepEqual(untracked, [],
    "manifest names a file that is NOT git-tracked — the flake ships only "
    + "tracked files, so the switch succeeds and the file is simply not there");
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
