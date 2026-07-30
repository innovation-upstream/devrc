// Sanitisation contract for the extension half.
//
// The hostile-input table below is the SAME one tests/test_security.py runs
// against safety.py. The two implementations must agree: the extension decides
// what goes into suggest({filename}); the sidecar decides what becomes a real
// directory. A divergence is a hole.
//
// Run: nix-shell -p nodejs --run "node --test scripts/dl-router/tests"
import test from "node:test";
import assert from "node:assert/strict";

import {
  MAX_DIR_NAME, MAX_FILE_NAME, baseName, isHttpUrl, isSafeDirName, joinDirFile,
  relPathFromAbsolute, sanitizeDirName, sanitizeFileName,
} from "../extension/sanitize.js";

const HOSTILE_DIR_NAMES = [
  "..", ".", "../..", "../../etc", "foo/bar", "foo\\bar", "/absolute",
  "/etc/passwd", "\\\\server\\share",
  "with\u0000nul", "with\nnewline", "with\rcarriage", "with\ttab",
  "with\u001bescape", "with\u007fdelete",
  "trailing ", " leading", "trailing.",
  "with\u202eRLO", "with\u200fRLM", "with\u2066FSI",
  "x".repeat(MAX_DIR_NAME + 1), "",
];

const SAFE_DIR_NAMES = [
  "Jane Doe", "john-smith", "Mary_Major", "acme-studio", "subject (2021)",
  "subject [remaster]", "José Álvarez", "x".repeat(MAX_DIR_NAME),
];

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
  for (const value of [null, undefined, 42, [], {}, true]) {
    assert.equal(isSafeDirName(value), false);
  }
});

test("decomposed unicode is rejected so lookalikes cannot coexist", () => {
  const decomposed = "José";   // e + combining acute
  const precomposed = "José";
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
  const cases = [
    ["../../etc/passwd", "passwd"],
    ["..\\..\\windows\\evil.exe", "evil.exe"],
    ["/absolute/path/clip.mp4", "clip.mp4"],
    ["clip\u0000.mp4", "clip.mp4"],
    ["clip\nname.mp4", "clipname.mp4"],
    ["...", "download"],
    ["..", "download"],
    [".", "download"],
    ["", "download"],
    [null, "download"],
    [42, "download"],
  ];
  for (const [raw, expected] of cases) {
    assert.equal(sanitizeFileName(raw), expected, `for ${JSON.stringify(raw)}`);
  }
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
  for (const url of [
    "http://example-site.test/v/1",
    "https://example-site.test/v/1?x=2#f",
    "https://user:pass@example-site.test/v",
  ]) assert.equal(isHttpUrl(url), true, url);
});

test("non-http or malformed URLs are refused", () => {
  for (const url of [
    "file:///etc/passwd", "javascript:alert(1)", "data:text/html,<script>",
    "ftp://example-site.test/x", "//example-site.test/x", "https://",
    "-oProxyCommand=id", "--config-location=/tmp/evil",
    "https://example-site.test/ x", "https://example-site.test/\nHeader: x",
    "https://example-site.test/\u0000", "https://x.test/" + "a".repeat(5000),
    "", null, 42,
  ]) assert.equal(isHttpUrl(url), false, String(url));
});
