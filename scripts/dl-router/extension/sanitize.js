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
//
// The zero-width characters (ZWSP, ZWNJ, ZWJ, ZWNBSP) are in the same set for a
// second reason: they make two directory names that RENDER identically compare
// unequal, which defeats the NFC check below. U+FEFF additionally has to be here
// for cross-language parity -- JS `trim()` strips it and Python `str.strip()`
// does not, so merely allowing it guaranteed safety.py and this file disagreed
// on every name that ended with one.
const FORMAT_CONTROLS = new Set(
  [0x200e, 0x200f, 0x202a, 0x202b, 0x202c, 0x202d, 0x202e,
   0x2066, 0x2067, 0x2068, 0x2069, 0x061c,
   0x200b, 0x200c, 0x200d, 0xfeff].map((c) => String.fromCharCode(c)),
);

// Legal in a POSIX filename, but `:` is the separator in yt-dlp's
// `--paths TYPE:PATH` (see fetcher.py), so a directory name containing one is
// silently mis-parsed into a type selector plus a truncated path.
const HOSTILE_PUNCTUATION = new Set([":"]);

/**
 * C0 controls, DEL, and the C1 range (0x80-0x9f).
 *
 * C1 is here for parity, not paranoia: Python's `strip()`/`split()` treat
 * U+0085 (NEL) as whitespace and JS's `trim()`/`\s` do not, so any rule that
 * merely trimmed them diverged between the two implementations. Rejecting the
 * range outright makes both agree and loses nothing legitimate.
 */
function hasControl(s) {
  for (const ch of s) {
    const c = ch.codePointAt(0);
    if (c < 0x20 || c === 0x7f || (c >= 0x80 && c <= 0x9f)) return true;
  }
  return false;
}

function hasFormatControl(s) {
  for (const ch of s) if (FORMAT_CONTROLS.has(ch)) return true;
  return false;
}

function hasHostilePunctuation(s) {
  for (const ch of s) if (HOSTILE_PUNCTUATION.has(ch)) return true;
  return false;
}

/** True iff `name` is a single, safe path component. */
export function isSafeDirName(name) {
  if (typeof name !== "string") return false;
  if (name.length === 0 || name.length > MAX_DIR_NAME) return false;
  if (name === "." || name === "..") return false;
  if (name.includes("/") || name.includes("\\")) return false;
  if (hasHostilePunctuation(name)) return false;
  // NUL is < 0x20 so hasControl covers it; spaces are legitimate ("Jane Doe").
  if (hasControl(name)) return false;
  if (hasFormatControl(name)) return false;
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
    if (hasControl(ch) || FORMAT_CONTROLS.has(ch)
        || HOSTILE_PUNCTUATION.has(ch)) continue;
    cleaned += ch;
  }
  // ORDER MATTERS and must match safety.py exactly: collapse whitespace, then
  // strip dots at both ends, then strip whatever whitespace that exposed.
  cleaned = cleaned.split(/\s+/).filter(Boolean).join(" ");
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
 * the sidecar's /relocate expects.
 *
 * `libraryRoot` is REQUIRED and comes from the /dirs snapshot. This used to
 * take the last two components of whatever absolute path Chrome reported, with
 * nothing checking the download had landed under the library root at all: a
 * download saved anywhere else produced a plausible-looking "<dir>/<file>"
 * that named a DIFFERENT, real file inside the library, and /relocate would
 * move it. The library root is a live seeding target, so that file could be a
 * torrent payload.
 *
 * Returns null -- meaning "do not ask the sidecar to move anything" -- unless
 * the path is exactly `<root>/<one directory>/<one file>`. A file sitting
 * directly at the root, or nested deeper, or outside the root, all yield null.
 */
export function relPathFromAbsolute(absolute, libraryRoot) {
  if (typeof absolute !== "string" || !absolute) return null;
  if (typeof libraryRoot !== "string" || !libraryRoot) return null;
  const norm = (s) => s.replace(/\\/g, "/").replace(/\/+$/, "");
  const path = norm(absolute);
  const root = norm(libraryRoot);
  if (!root) return null;
  if (!path.startsWith(`${root}/`)) return null;
  const rest = path.slice(root.length + 1);
  const parts = rest.split("/");
  // Exactly one directory and one filename, and neither may be empty or a
  // traversal component.
  if (parts.length !== 2) return null;
  if (parts.some((p) => !p || p === "." || p === "..")) return null;
  return `${parts[0]}/${parts[1]}`;
}

// `_` is invalid per RFC 1123 but browsers and curl accept it, so it is
// allowed here too -- what matters is that BOTH implementations allow
// exactly the same set (see safety.py).
const HOST_CHARS = /^[A-Za-z0-9_-]+$/;
const IPV6_CHARS = /^[0-9A-Fa-f:.]+$/;
const DIGITS = /^[0-9]+$/;

/** Strict, parser-independent host rule. Mirrors safety.py exactly. */
function validHost(host) {
  if (!host || host.length > 253) return false;
  if (host.startsWith("[")) {
    if (!host.endsWith("]")) return false;
    const inner = host.slice(1, -1);
    return inner.length > 0 && IPV6_CHARS.test(inner);
  }
  if (host.includes("]")) return false;
  for (const label of host.split(".")) {
    if (!label || label.length > 63) return false;
    if (label.startsWith("-") || label.endsWith("-")) return false;
    if (!HOST_CHARS.test(label)) return false;
  }
  return true;
}

/**
 * True iff `url` is a plain http(s) URL safe to hand to yt-dlp.
 *
 * The authority is split and validated BY HAND rather than trusting `new URL`.
 * Differential fuzzing against safety.py found 50 inputs where the two
 * standard parsers disagreed about what a host is -- WHATWG collapses the
 * extra slashes in `http://////..` and calls the host `..`, while Python's
 * `urlsplit` accepts `http://%2f` and a bare ZWNBSP host. Neither is wrong for
 * its own spec, which is why neither can be the contract: only an explicit
 * shared rule makes the sidecar and the extension provably agree on what
 * yt-dlp will be handed.
 */
export function isHttpUrl(url) {
  if (typeof url !== "string" || !url || url.length > 4096) return false;
  if (url.startsWith("-")) return false;
  if (hasControl(url) || /\s/.test(url)) return false;
  if (hasFormatControl(url)) return false;

  const lowered = url.toLowerCase();
  let rest;
  if (lowered.startsWith("http://")) rest = url.slice("http://".length);
  else if (lowered.startsWith("https://")) rest = url.slice("https://".length);
  else return false;

  let end = rest.length;
  for (let i = 0; i < rest.length; i += 1) {
    const ch = rest[i];
    if (ch === "/" || ch === "?" || ch === "#") { end = i; break; }
  }
  let authority = rest.slice(0, end);
  const at = authority.lastIndexOf("@");
  if (at >= 0) authority = authority.slice(at + 1);

  let host = authority;
  let port = null;
  if (host.startsWith("[")) {
    const close = host.indexOf("]");
    if (close < 0) return false;
    const tail = host.slice(close + 1);
    host = host.slice(0, close + 1);
    if (tail) {
      if (!tail.startsWith(":")) return false;
      port = tail.slice(1);
    }
  } else if (host.includes(":")) {
    const colon = host.indexOf(":");
    port = host.slice(colon + 1);
    host = host.slice(0, colon);
  }

  if (port !== null) {
    if (!DIGITS.test(port)) return false;
    const n = Number(port);
    if (n < 1 || n > 65535) return false;
  }
  return validHost(host);
}
