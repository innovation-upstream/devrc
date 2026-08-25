/**
 * `awaiting` — tasks whose newest comment is NOT the token owner's.
 *
 * 🔴 WHY THIS PREDICATE, and not a better one. Measured against the live API
 * (read-only) before this was written:
 *
 *   * `resolved` is NOT readable on a task comment. The union of every field
 *     across a sample of 84 comments on 29 tasks was
 *     ['assignee','comment','comment_text','date','group_assignee','id',
 *      'reactions','reply_count','user'] — `resolved` never appears, not even
 *     as `false`, even though `resolve-comment` can WRITE it. So "unresolved
 *     comments" is not computable from this API. Do not try.
 *   * `assignee` was null on all 84 sampled comments, so comment-level
 *     assignment cannot narrow anything either.
 *   * The `inbox-*` commands — ClickUp's own "@mentioned me / unread" surface —
 *     need a JWT this host's accounts.json does not have.
 *
 * What is left, and all that is left: the newest comment on a task was written
 * by somebody who is not the token owner.
 *
 * 🔴 The blind spot that comes with it: ClickUp has no bot identity. Every
 * comment posted through the `pk_` token comes back authored as the token
 * owner, whoever actually typed it. "The owner answered" and "a machine
 * answered on the owner's behalf" are the SAME observable. The formatter prints
 * that in the command's own output — not only in this comment — because a
 * caveat that lives only in docs is a caveat nobody reads at the moment it
 * matters.
 *
 * Everything here is pure except `findAwaiting`, whose two effects (fetching
 * comments, sleeping) are injected, so the cap and pacing are testable without
 * a network.
 *
 * 🔴 THIS PREDICATE IS IMPLEMENTED TWICE, AND THAT IS DELIBERATE.
 * `scripts/check-clickup-addressed/` derives the same fact across a seam
 * (`recent-comments.py::latest_reply_ts_by` -> `check-addressed.py::
 * _reply_answers_the_comment`), and it already shells out to this CLI — so
 * "have ccua call `query.mjs awaiting`" looks obvious and is a REGRESSION. The
 * decisive reason is one line below (`selectAwaiting` skips a task the owner
 * answered last, which is exactly the population ccua's suppression note is
 * built from); the rest, plus the three measured cross-language divergences,
 * are in `reference/awaiting-vs-ccua.md`. The two implementations are pinned to
 * ONE shared table — `test/awaiting-contract.fixtures.json`, read by
 * `test/awaiting-contract.test.mjs` and by the Python half — so neither can
 * drift silently. Read that file before changing anything here.
 */

export const MS_PER_DAY = 86400000;

/**
 * Fan-out and pacing defaults.
 *
 * The token's limit is 100 requests/minute. An unpaced loop over an assigned
 * board bursts far past that (~190/min measured), so fetches go out in small
 * batches with a sleep sized to hold the average rate at `ratePerMin`, which is
 * set below the ceiling for headroom — `getMyTasks` itself spends requests on
 * the same budget.
 *
 * ⚠️ The budget is counted in FETCHES, not HTTP requests: `getComments`
 * paginates at 25 comments per request, so a heavily-commented task costs more
 * than one. `ratePerMin` is therefore a ceiling on fetches and only an
 * approximation of the request rate — the headroom below 100 is what absorbs
 * that, and it is why the number reported is labelled a fetch count.
 *
 * `maxTasks` is a HARD cap on the fan-out: one request per task, so an
 * unbounded board is an unbounded scan. Truncation is reported, never silent.
 */
export const AWAITING_DEFAULTS = Object.freeze({
  maxTasks: 60,
  batchSize: 5,
  ratePerMin: 80,
  minAgeDays: 0,
});

/** Sleep, the default injectable effect. */
export function sleep(ms) {
  if (!(ms > 0)) return Promise.resolve();
  return new Promise((r) => setTimeout(r, ms));
}

/**
 * How long to pause after a batch of `batchSize` requests to hold an average of
 * `ratePerMin` requests per minute, given the batch itself took `elapsedMs`.
 * Never negative: a batch slower than its own budget pauses not at all.
 */
export function batchPauseMs(batchSize, ratePerMin, elapsedMs = 0) {
  if (!(ratePerMin > 0)) {
    throw new RangeError(`ratePerMin must be > 0, got ${ratePerMin}`);
  }
  const budgetMs = (batchSize * 60000) / ratePerMin;
  return Math.max(0, Math.ceil(budgetMs - elapsedMs));
}

/** Split `items` into consecutive chunks of at most `size`. */
export function chunk(items, size) {
  if (!Number.isInteger(size) || size < 1) {
    throw new RangeError(`chunk size must be a positive integer, got ${size}`);
  }
  const out = [];
  for (let i = 0; i < items.length; i += size) {
    out.push(items.slice(i, i + size));
  }
  return out;
}

/**
 * Apply the fan-out cap.
 *
 * Returns the slice that will be examined ALONGSIDE what was left out, so the
 * caller can say "20 of 51" rather than printing a number that looks like the
 * whole board.
 */
export function capFanOut(tasks, maxTasks) {
  if (!Number.isInteger(maxTasks) || maxTasks < 1) {
    throw new RangeError(`--max must be a positive integer, got ${maxTasks}`);
  }
  const examined = tasks.slice(0, maxTasks);
  return {
    examined,
    total: tasks.length,
    skipped: tasks.length - examined.length,
    truncated: tasks.length > maxTasks,
  };
}

/** A comment's timestamp in epoch ms, or null when it has none/unparseable. */
export function commentTimeMs(comment) {
  const raw = comment?.date;
  if (raw === undefined || raw === null || raw === '') return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

/** A comment's author id as a string, or null when the API gave none. */
export function commentAuthorId(comment) {
  const id = comment?.user?.id;
  if (id === undefined || id === null || id === '') return null;
  return String(id);
}

/** A human label for a comment's author. */
export function commentAuthorName(comment) {
  const user = comment?.user;
  if (user?.username) return user.username;
  if (user?.email) return user.email;
  const id = commentAuthorId(comment);
  return id ? `user ${id}` : 'unknown';
}

/**
 * The newest comment in a list, by timestamp. Undated comments sort oldest, so
 * a dated comment always wins over one the API gave no date.
 */
export function newestComment(comments) {
  if (!Array.isArray(comments) || comments.length === 0) return null;
  let best = null;
  let bestAt = -Infinity;
  for (const c of comments) {
    const at = commentTimeMs(c) ?? -Infinity;
    if (best === null || at > bestAt) {
      best = c;
      bestAt = at;
    }
  }
  return best;
}

/**
 * Is this task awaiting the token owner?
 *
 * True when the newest comment was authored by someone else. An author the API
 * did not identify counts as SOMEONE ELSE: a false positive costs one glance,
 * a false negative hides the thing the command exists to find. The row carries
 * `unknown` as the author so the reader can see which case they are looking at.
 */
export function isAwaiting(comments, ownerUserId) {
  const newest = newestComment(comments);
  if (!newest) return false;
  const author = commentAuthorId(newest);
  if (author === null) return true;
  return author !== String(ownerUserId);
}

/**
 * Build the awaiting rows from already-fetched comments.
 *
 * @param {object[]} tasks              tasks whose comments were fetched
 * @param {Map<string,object[]>} commentsByTaskId
 * @param {string|number} ownerUserId   the TOKEN owner (never the --assignee)
 * @param {number} minAgeDays           keep rows whose newest comment is at
 *                                      least this old
 * @param {number} now                  epoch ms, injected for determinism
 * @returns {{rows: object[], examined: number, withComments: number, matched: number}}
 */
export function selectAwaiting({
  tasks,
  commentsByTaskId,
  ownerUserId,
  minAgeDays = AWAITING_DEFAULTS.minAgeDays,
  now = Date.now(),
}) {
  const minAgeMs = minAgeDays * MS_PER_DAY;
  const rows = [];
  let withComments = 0;

  for (const task of tasks) {
    const comments = commentsByTaskId.get(task.id) || [];
    if (comments.length > 0) withComments++;
    if (!isAwaiting(comments, ownerUserId)) continue;

    const newest = newestComment(comments);
    const lastCommentAtMs = commentTimeMs(newest);
    const ageMs = lastCommentAtMs === null ? null : now - lastCommentAtMs;
    if (ageMs !== null && ageMs < minAgeMs) continue;
    if (ageMs === null && minAgeMs > 0) continue; // cannot prove it is old enough

    rows.push({
      id: task.id,
      name: task.name,
      url: task.url,
      list: task.list?.name || null,
      status: task.status?.status || null,
      lastCommentAtMs,
      ageMs,
      lastCommentBy: commentAuthorName(newest),
      lastCommentById: commentAuthorId(newest),
      commentCount: comments.length,
    });
  }

  // Oldest first: the thing that has been waiting longest is the thing to read.
  rows.sort((a, b) => (a.lastCommentAtMs ?? -Infinity) - (b.lastCommentAtMs ?? -Infinity));

  return {
    rows,
    examined: tasks.length,
    withComments,
    matched: rows.length,
  };
}

/**
 * Fetch comments for (at most `maxTasks`) tasks, paced, and select the awaiting
 * ones.
 *
 * A task whose comments could not be fetched is counted as UNREADABLE and
 * reported separately — never folded into the examined count, because a scan
 * that silently dropped failures reads as broader coverage than it had.
 *
 * @param {object}   p.tasks          tasks from getMyTasks()
 * @param {string}   p.ownerUserId    the token owner's user id
 * @param {function} p.fetchComments  async (taskId) => comment[]
 * @param {function} [p.sleepFn]      injected sleep
 * @param {function} [p.clock]        injected () => epoch ms
 */
export async function findAwaiting({
  tasks,
  ownerUserId,
  fetchComments,
  sleepFn = sleep,
  clock = Date.now,
  maxTasks = AWAITING_DEFAULTS.maxTasks,
  batchSize = AWAITING_DEFAULTS.batchSize,
  ratePerMin = AWAITING_DEFAULTS.ratePerMin,
  minAgeDays = AWAITING_DEFAULTS.minAgeDays,
  onProgress = null,
}) {
  const cap = capFanOut(tasks, maxTasks);
  const commentsByTaskId = new Map();
  const unreadable = [];
  // FETCHES, not HTTP requests — one per task, each of which may paginate.
  let commentFetches = 0;

  const batches = chunk(cap.examined, batchSize);
  for (let i = 0; i < batches.length; i++) {
    const batch = batches[i];
    const startedAt = clock();
    const results = await Promise.all(
      batch.map(async (task) => {
        try {
          return { id: task.id, comments: await fetchComments(task.id) };
        } catch (err) {
          return { id: task.id, error: err?.message || String(err) };
        }
      })
    );
    commentFetches += batch.length;
    for (const r of results) {
      if (r.error) unreadable.push({ id: r.id, error: r.error });
      else commentsByTaskId.set(r.id, r.comments || []);
    }
    if (onProgress) onProgress({ done: commentFetches, total: cap.examined.length });
    if (i < batches.length - 1) {
      await sleepFn(batchPauseMs(batch.length, ratePerMin, clock() - startedAt));
    }
  }

  const readable = cap.examined.filter((t) => commentsByTaskId.has(t.id));
  const selection = selectAwaiting({
    tasks: readable,
    commentsByTaskId,
    ownerUserId,
    minAgeDays,
    now: clock(),
  });

  return {
    rows: selection.rows,
    matched: selection.matched,
    examined: selection.examined,
    withComments: selection.withComments,
    unreadable,
    assigned: cap.total,
    skipped: cap.skipped,
    truncated: cap.truncated,
    maxTasks,
    minAgeDays,
    commentFetches,
  };
}

/** "5d 3h" / "4h 10m" / "12m" — coarse, because the point is "how stale". */
export function formatAge(ms) {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return 'unknown';
  if (ms < 0) return '0m';
  const minutes = Math.floor(ms / 60000);
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  const mins = minutes % 60;
  if (days > 0) return hours > 0 ? `${days}d ${hours}h` : `${days}d`;
  if (hours > 0) return mins > 0 ? `${hours}h ${mins}m` : `${hours}h`;
  return `${mins}m`;
}

function formatWhen(ms) {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return 'unknown date';
  return new Date(ms).toLocaleString('en-US', {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

/**
 * Render a findAwaiting() result.
 *
 * 🔴 Three things this ALWAYS prints, whatever the outcome:
 *   1. tasks EXAMINED beside tasks MATCHED — a bare "0 awaiting" from a scan
 *      that walked nothing is the failure mode, not the all-clear;
 *   2. truncation, explicitly, when the cap cut the scan short;
 *   3. the predicate and its blind spot, in the command's own output.
 */
export function formatAwaiting(result, { scopeLabel = 'you', ownerLabel = 'the API token owner' } = {}) {
  const lines = [];

  for (const row of result.rows) {
    lines.push(`[${row.status || '?'}] ${row.name}`);
    const bits = [
      `List: ${row.list || '?'}`,
      `Waiting: ${formatAge(row.ageMs)}`,
      `Last comment: ${row.lastCommentBy} on ${formatWhen(row.lastCommentAtMs)}`,
    ];
    lines.push(`  ${bits.join(' | ')}`);
    if (row.url) lines.push(`  ${row.url}`);
    lines.push('');
  }

  const window = result.minAgeDays > 0 ? `, quiet for at least ${result.minAgeDays}d` : '';
  lines.push(
    `Awaiting a reply from ${scopeLabel}: ${result.matched} matched of ` +
      `${result.examined} task(s) examined (${result.withComments} had any comment` +
      `${window}).`
  );
  lines.push(
    `Assigned tasks found: ${result.assigned}; comment fetches issued: ${result.commentFetches} ` +
      '(one per task; getComments paginates, so a busy task costs more than one request).'
  );

  if (result.truncated) {
    lines.push(
      `🔴 TRUNCATED: --max ${result.maxTasks} capped the scan — ${result.skipped} of ` +
        `${result.assigned} assigned task(s) were NOT examined. This is not a clean sweep; ` +
        `raise --max to cover them.`
    );
  }
  if (result.unreadable.length > 0) {
    lines.push(
      `🔴 UNREADABLE: comments could not be fetched for ${result.unreadable.length} task(s) ` +
        `(${result.unreadable.map((u) => u.id).join(', ')}) — they are excluded from the ` +
        `examined count, not counted as answered.`
    );
  }

  lines.push('');
  lines.push(`Predicate: the NEWEST comment on the task was not authored by ${ownerLabel}.`);
  lines.push(
    'Blind spot: ClickUp has no bot identity — every comment posted with this API token ' +
      'comes back authored as the token owner, so an automated write-back is ' +
      'indistinguishable from you answering. A task answered by a machine on your behalf ' +
      'will NOT appear here. Comment-level `resolved` is not readable through the API, so ' +
      '"unresolved comments" is not what this measures.'
  );

  return lines.join('\n');
}
