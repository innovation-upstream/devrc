// Sanitisation contract for the extension half.
//
// The hostile-input table is NOT written here. It lives in
// tests/fixtures/name_cases.json and tests/test_security.py drives safety.py
// from the SAME file. The two implementations must agree: the extension
// decides what goes into suggest({filename}); the sidecar decides what becomes
// a real directory. A divergence is a hole -- and while the tables were two
// hand-copied literal lists, a differential fuzz found 991 inputs the two
// disagreed on that neither list covered.
//
// Run (the glob must be quoted -- `node --test <dir>` fails):
//   nix-shell -p nodejs --run "node --test 'scripts/dl-router/tests/*.test.mjs'"
import test from "node:test";
import assert from "node:assert/strict";

import {
  MAX_DIR_NAME, MAX_FILE_NAME, baseName, isHttpUrl, isSafeDirName, joinDirFile,
  relPathFromAbsolute, sanitizeDirName, sanitizeFileName,
} from "../extension/sanitize.js";
import { loadNameCases } from "./fixtures.mjs";

const CASES = loadNameCases();
const HOSTILE_DIR_NAMES = CASES.hostileDirNames;
const SAFE_DIR_NAMES = CASES.safeDirNames;

test("the shared fixture actually loaded", () => {
  // A silently empty table would make every case below vacuously pass.
  assert.ok(HOSTILE_DIR_NAMES.length >= 30, "hostile table too small");
  assert.ok(SAFE_DIR_NAMES.length >= 8, "safe table too small");
  assert.ok(CASES.fileNames.length >= 15);
  assert.ok(CASES.httpUrlsAccepted.length >= 5);
  assert.ok(CASES.httpUrlsRejected.length >= 20);
  // The {repeat, count} form must have been expanded, not left as an object.
  assert.ok(HOSTILE_DIR_NAMES.some(
    (n) => typeof n === "string" && n.length === MAX_DIR_NAME + 1));
  assert.ok(SAFE_DIR_NAMES.some(
    (n) => typeof n === "string" && n.length === MAX_DIR_NAME));
});

test("hostile directory names are rejected", () => {
  for (const name of HOSTILE_DIR_NAMES) {
    assert.equal(isSafeDirName(name), false, `accepted ${JSON.stringify(name)}`);
    assert.equal(sanitizeDirName(name), null);
  }
});

test("legitimate directory names are accepted", () => {
  for (const name of SAFE_DIR_NAMES) {
    assert.equal(isSafeDirName(name), true, `rejected ${JSON.stringify(name)}`);
    assert.equal(sanitizeDirName(name), name);
  }
});

test("non-strings are rejected", () => {
  for (const value of [...CASES.nonStringNames, undefined]) {
    assert.equal(isSafeDirName(value), false);
  }
});

test("decomposed unicode is rejected so lookalikes cannot coexist", () => {
  const decomposed = "Jose\u0301";     // e + COMBINING ACUTE ACCENT
  const precomposed = "Jos\u00e9";     // LATIN SMALL LETTER E WITH ACUTE
  assert.notEqual(decomposed, precomposed, "the two forms must differ");
  assert.equal(isSafeDirName(decomposed), false);
  assert.equal(isSafeDirName(precomposed), true);
});

test("the known-directory allowlist is enforced", () => {
  const known = new Set(["Jane Doe", "other"]);
  assert.equal(sanitizeDirName("Jane Doe", known), "Jane Doe");
  assert.equal(sanitizeDirName("Not Known", known), null);
  // No allowlist supplied -> only the syntactic rules apply (the /mkdir path).
  assert.equal(sanitizeDirName("Brand New"), "Brand New");
});

test("filenames are reduced to one safe component", () => {
  for (const [raw, expected] of CASES.fileNames) {
    assert.equal(sanitizeFileName(raw), expected, `for ${JSON.stringify(raw)}`);
  }
});

test("zero-width and C1 characters are stripped from filenames", () => {
  // Regression for the cross-language divergence: JS `trim()` strips U+FEFF
  // and Python `strip()` does not, while Python treats U+0085 as whitespace
  // and JS does not. Both are dropped outright on both sides now.
  for (const raw of ["clip\ufeff.mp4", "clip\u0085.mp4", "clip\u200b.mp4"]) {
    assert.equal(sanitizeFileName(raw), "clip.mp4", JSON.stringify(raw));
  }
});

test("a colon is stripped because yt-dlp reads it as a --paths selector", () => {
  assert.ok(!sanitizeFileName("season:one.mp4").includes(":"));
});

test("filename bidi characters are stripped", () => {
  assert.ok(!sanitizeFileName("clip\u202egnp.exe").includes("\u202e"));
});

test("filenames never contain a separator", () => {
  for (const raw of ["a/b/c.mp4", "a\\b\\c.mp4", "..%2f..%2fetc"]) {
    const out = sanitizeFileName(raw);
    assert.ok(!out.includes("/"), out);
    assert.ok(!out.includes("\\"), out);
  }
});

test("long filenames are truncated but keep their extension", () => {
  const out = sanitizeFileName("a".repeat(400) + ".mp4");
  assert.ok(out.length <= MAX_FILE_NAME);
  assert.ok(out.endsWith(".mp4"));
});

test("joinDirFile refuses a traversing directory", () => {
  assert.throws(() => joinDirFile("../..", "x.mp4"));
  assert.throws(() => joinDirFile("a/b", "x.mp4"));
  assert.equal(joinDirFile("Jane Doe", "clip.mp4"), "Jane Doe/clip.mp4");
});

test("joinDirFile output is always exactly two components", () => {
  for (const raw of ["../../etc/passwd", "a/b.mp4", "..\\..\\evil.exe"]) {
    const out = joinDirFile("Jane Doe", raw);
    assert.equal(out.split("/").length, 2, out);
    assert.ok(out.startsWith("Jane Doe/"));
  }
});

test("baseName takes the last component under either separator", () => {
  assert.equal(baseName("/a/b/c.mp4"), "c.mp4");
  assert.equal(baseName("a\\b\\c.mp4"), "c.mp4");
  assert.equal(baseName("c.mp4"), "c.mp4");
  assert.equal(baseName(""), "");
});

test("relPathFromAbsolute returns dir/file", () => {
  assert.equal(relPathFromAbsolute("/home/u/lib/Jane Doe/clip.mp4"),
    "Jane Doe/clip.mp4");
  assert.equal(relPathFromAbsolute("clip.mp4"), null);
  assert.equal(relPathFromAbsolute(""), null);
  assert.equal(relPathFromAbsolute(null), null);
});

test("http(s) URLs are accepted", () => {
  for (const url of CASES.httpUrlsAccepted) {
    assert.equal(isHttpUrl(url), true, String(url));
  }
});

test("non-http or malformed URLs are refused", () => {
  for (const url of CASES.httpUrlsRejected) {
    assert.equal(isHttpUrl(url), false, String(url).slice(0, 60));
  }
});

test("the host rule does not delegate to new URL", () => {
  // WHATWG collapses the extra slashes in `http://////..` and calls the host
  // `..`; Python's urlsplit accepts `http://%2f`. Neither parser can be the
  // contract, so both implementations validate the authority by hand.
  assert.equal(isHttpUrl("http://////.."), false);
  assert.equal(isHttpUrl("http://%2f"), false);
  assert.equal(isHttpUrl("https:///80"), false);
  assert.equal(isHttpUrl("https://example-site.test:99999/v"), false);
});
