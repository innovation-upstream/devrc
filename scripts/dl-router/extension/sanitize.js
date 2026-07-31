// sanitize.js -- name validation for the download path.
//
// This is the browser-side twin of ../safety.py and the two MUST agree: the
// extension decides what string goes into suggest({filename}) and the sidecar
// decides what string becomes a real directory. A disagreement is a hole.
// tests/sanitize.test.mjs and tests/test_security.py assert the same table of
// hostile inputs against both.
//
// The threat is concrete: the directory name is derived from page markup, and
// the filename comes from the server's Content-Disposition. Chrome refuses to
// let onDeterminingFilename escape the download root, but "refuses" means "drops
// the suggestion and uses the default" -- we want a deterministic, validated
// answer instead of relying on the browser's rejection.
//
// Every non-ASCII character below is written as an escape ON PURPOSE. That rule
// covers EVERY file under extension/ and tests/source_hygiene.test.mjs enforces
// it: one raw control character anywhere in a source file makes git classify the
// whole module as binary, and `gh pr diff` then prints "Binary files differ"
// instead of the code -- which is exactly how an 11 KB module carrying the
// suggest() ladder shipped unreviewed once already.

export const MAX_DIR_NAME = 120;
export const MAX_FILE_NAME = 200;

// Bidi/format controls: LRM, RLM, LRE, RLE, PDF, LRO, RLO, LRI, RLI, FSI, PDI,
// ALM. With an RLO, "subject<RLO>gnp.exe" renders as "subject exe.png"; there is
// no legitimate use for one in a directory name.
const BIDI = new Set(
  [0x200e, 0x200f, 0x202a, 0x202b, 0x202c, 0x202d, 0x202e,
   0x2066, 0x2067, 0x2068, 0x2069, 0x061c].map((c) => String.fromCharCode(c)),
);

function hasControl(s) {
  for (const ch of s) {
    const c = ch.codePointAt(0);
    if (c < 0x20 || c === 0x7f) return true;
  }
  return false;
}

function hasBidi(s) {
  for (const ch of s) if (BIDI.has(ch)) return true;
  return false;
}

/** True iff `name` is a single, safe path component. */
export function isSafeDirName(name) {
  if (typeof name !== "string") return false;
  if (name.length === 0 || name.length > MAX_DIR_NAME) return false;
  if (name === "." || name === "..") return false;
  if (name.includes("/") || name.includes("\\")) return false;
  // NUL is < 0x20 so hasControl covers it; spaces are legitimate ("Jane Doe").
  if (hasControl(name)) return false;
  if (hasBidi(name)) return false;
  if (name !== name.trim() || name.endsWith(".")) return false;
  if (name.normalize("NFC") !== name) return false;
  return true;
}

/**
 * Validate a directory name, optionally against the known-directory set.
 * Returns the name, or null when it cannot be used (caller falls back).
 *
 * `known` is a Set. On the download path it is ALWAYS supplied: the extension
 * may only file into a directory that already exists (or one just created via
 * the sidecar's /mkdir, which refreshes the set).
 */
export function sanitizeDirName(name, known) {
  if (!isSafeDirName(name)) return null;
  if (known && !known.has(name)) return null;
  return name;
}

/** Reduce any string to one safe filename component. Never throws. */
export function sanitizeFileName(name, fallback = "download") {
  if (typeof name !== "string" || name.length === 0) return fallback;
  // Strip any directory part under BOTH separators: a server-supplied
  // "..\\..\\evil.exe" must not survive as a relative path.
  let base = name.replace(/\\/g, "/");
  base = base.slice(base.lastIndexOf("/") + 1);
  base = base.normalize("NFC");
  let cleaned = "";
  for (const ch of base) {
    const c = ch.codePointAt(0);
    if (c < 0x20 || c === 0x7f || BIDI.has(ch)) continue;
    cleaned += ch;
  }
  cleaned = cleaned.split(/\s+/).filter(Boolean).join(" ").trim();
  cleaned = cleaned.replace(/^\.+/, "").replace(/\.+$/, "").trim();
  if (!cleaned || cleaned === "." || cleaned === "..") return fallback;
  if (cleaned.length <= MAX_FILE_NAME) return cleaned;
  const dot = cleaned.lastIndexOf(".");
  const ext = dot > 0 ? cleaned.slice(dot + 1) : "";
  if (ext && ext.length <= 12) {
    const keep = MAX_FILE_NAME - ext.length - 1;
    return keep > 0 ? `${cleaned.slice(0, keep)}.${ext}`
      : cleaned.slice(0, MAX_FILE_NAME);
  }
  return cleaned.slice(0, MAX_FILE_NAME);
}

/** The exact relative path handed to suggest(). Throws on an unusable dir. */
export function joinDirFile(dir, file) {
  if (!isSafeDirName(dir)) throw new Error(`unsafe directory: ${dir}`);
  return `${dir}/${sanitizeFileName(file)}`;
}

/** The final path component of an absolute or relative path. */
export function baseName(path) {
  if (typeof path !== "string" || !path) return "";
  const norm = path.replace(/\\/g, "/");
  return norm.slice(norm.lastIndexOf("/") + 1);
}

/**
 * `<dir>/<file>` for a completed download's ABSOLUTE path -- the relative path
 * the sidecar's /relocate expects. Returns null when the path has no directory
 * component (the file landed at the download root).
 */
export function relPathFromAbsolute(absolute) {
  if (typeof absolute !== "string" || !absolute) return null;
  const parts = absolute.replace(/\\/g, "/").split("/").filter(Boolean);
  if (parts.length < 2) return null;
  return `${parts[parts.length - 2]}/${parts[parts.length - 1]}`;
}

/** True iff `url` is a plain http(s) URL safe to hand to yt-dlp. */
export function isHttpUrl(url) {
  if (typeof url !== "string" || !url || url.length > 4096) return false;
  if (url.startsWith("-")) return false;
  if (hasControl(url) || /\s/.test(url)) return false;
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return false;
  return Boolean(parsed.host);
}
