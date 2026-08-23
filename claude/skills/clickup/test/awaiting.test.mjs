#!/usr/bin/env node

/**
 * `awaiting` gate (hermetic — no credentials, no network).
 *
 * The command's whole value is a NUMBER a human will act on, so what is pinned
 * here is not "it runs" but:
 *   * the predicate matches a foreign last comment and NOT the owner's;
 *   * the fan-out cap truncates, and SAYS it truncated — the failure mode is a
 *     confident "0 awaiting" from a scan that walked a fraction of the board;
 *   * a task whose comments could not be read is reported, never folded into
 *     the examined count;
 *   * the output carries its own blind spot (no bot identity).
 *
 * 🔴 Fixture values are pairwise distinct — distinct ids, distinct authors,
 * distinct timestamps, distinct list/status strings — because a fixture built
 * from repeated or default values collapses distinct implementations into
 * identical output, and a mutant that swaps two fields then survives a green
 * suite.
 *
 * Every id, name and author below is SYNTHETIC. This repo is public.
 *
 * Usage:
 *   node test/awaiting.test.mjs
 *   node --test test/awaiting.test.mjs
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  AWAITING_DEFAULTS,
  MS_PER_DAY,
  batchPauseMs,
  capFanOut,
  chunk,
  commentAuthorId,
  commentAuthorName,
  commentTimeMs,
  findAwaiting,
  formatAge,
  formatAwaiting,
  isAwaiting,
  newestComment,
  selectAwaiting,
} from '../lib/awaiting.mjs';
import {
  bundlesOf,
  mergeInboxPages,
  nextInboxCursor,
  fetchAllInboxNotifications,
} from '../api/inbox.mjs';

// ── Fixtures ───────────────────────────────────────────────────────────────
//
// One fixed "now" so every age is arithmetic, not wall-clock.
const NOW = 1_760_000_000_000; // epoch ms, arbitrary and fixed
const OWNER_ID = '111';

const t = (n) => String(NOW - n * MS_PER_DAY); // "n days ago", as the API's ms STRING

/** A comment. Every field distinct per call site. */
function comment({ id, userId, username, date, text }) {
  return {
    id,
    date,
    comment_text: text,
    user: { id: userId, username, email: `${username}@example.invalid` },
  };
}

/** A task. */
function task({ id, name, list, status }) {
  return {
    id,
    name,
    url: `https://app.clickup.test/t/${id}`,
    list: { id: `9${id}`, name: list },
    status: { status },
  };
}

const TASK_FOREIGN = task({ id: 'aaa1', name: 'Wire the widget', list: 'Platform', status: 'in progress' });
const TASK_OWNER = task({ id: 'bbb2', name: 'Retire the gadget', list: 'Backlog', status: 'open' });
const TASK_SILENT = task({ id: 'ccc3', name: 'Nobody said anything', list: 'Icebox', status: 'blocked' });
const TASK_OLD_FOREIGN = task({ id: 'ddd4', name: 'Stale question', list: 'Support', status: 'review' });

const COMMENTS = new Map([
  // Newest is FOREIGN (author 222) → awaiting. Deliberately NOT last in the
  // array, so an implementation that reads comments[length-1] instead of the
  // newest is caught.
  [TASK_FOREIGN.id, [
    comment({ id: 'c10', userId: '222', username: 'dana', date: t(3), text: 'any update?' }),
    comment({ id: 'c11', userId: OWNER_ID, username: 'owner', date: t(9), text: 'looking' }),
  ]],
  // Newest is the OWNER's → not awaiting.
  [TASK_OWNER.id, [
    comment({ id: 'c20', userId: '333', username: 'ravi', date: t(12), text: 'ping' }),
    comment({ id: 'c21', userId: OWNER_ID, username: 'owner', date: t(1), text: 'done' }),
  ]],
  // No comments at all → not awaiting.
  [TASK_SILENT.id, []],
  // Foreign and 20 days old → awaiting, and the OLDEST, so it sorts first.
  [TASK_OLD_FOREIGN.id, [
    comment({ id: 'c30', userId: '444', username: 'mira', date: t(20), text: 'still waiting' }),
  ]],
]);

const ALL_TASKS = [TASK_FOREIGN, TASK_OWNER, TASK_SILENT, TASK_OLD_FOREIGN];

const fetchFixtureComments = async (taskId) => {
  if (!COMMENTS.has(taskId)) throw new Error(`no fixture for ${taskId}`);
  return COMMENTS.get(taskId);
};

// ── The predicate ──────────────────────────────────────────────────────────

test('a task whose NEWEST comment is foreign is awaiting', () => {
  assert.equal(isAwaiting(COMMENTS.get(TASK_FOREIGN.id), OWNER_ID), true);
});

test("a task whose NEWEST comment is the token owner's is NOT awaiting", () => {
  assert.equal(isAwaiting(COMMENTS.get(TASK_OWNER.id), OWNER_ID), false);
});

test('a task with no comments is NOT awaiting', () => {
  assert.equal(isAwaiting([], OWNER_ID), false);
  assert.equal(isAwaiting(undefined, OWNER_ID), false);
});

test('newest is by DATE, not array position', () => {
  // The foreign comment is FIRST in the array and newest by date. An
  // implementation reading the last element gets the owner's and inverts the
  // whole answer.
  const newest = newestComment(COMMENTS.get(TASK_FOREIGN.id));
  assert.equal(newest.id, 'c10');
  assert.equal(commentAuthorId(newest), '222');
  assert.equal(commentAuthorName(newest), 'dana');
});

test('the owner id compares across string/number — an id is an id', () => {
  const owned = [comment({ id: 'c40', userId: 111, username: 'owner', date: t(2), text: 'mine' })];
  assert.equal(isAwaiting(owned, '111'), false, 'numeric user.id vs string owner id must still match');
  assert.equal(isAwaiting(owned, 111), false);
});

test('an author the API did not identify counts as SOMEONE ELSE, not as answered', () => {
  const anon = [{ id: 'c50', date: t(4), comment_text: 'from nowhere' }];
  assert.equal(isAwaiting(anon, OWNER_ID), true);
  assert.equal(commentAuthorName(anon[0]), 'unknown');
});

test('commentTimeMs reads the API ms STRING, and refuses garbage', () => {
  assert.equal(commentTimeMs({ date: '1700000000000' }), 1700000000000);
  assert.equal(commentTimeMs({ date: 'not-a-date' }), null);
  assert.equal(commentTimeMs({}), null);
});

// ── Selection, ordering, age window ────────────────────────────────────────

test('selectAwaiting matches the foreign ones and orders OLDEST first', () => {
  const r = selectAwaiting({
    tasks: ALL_TASKS, commentsByTaskId: COMMENTS, ownerUserId: OWNER_ID, now: NOW,
  });
  assert.deepEqual(r.rows.map((x) => x.id), [TASK_OLD_FOREIGN.id, TASK_FOREIGN.id],
    'oldest-first: the 20-day-old question must precede the 3-day-old one');
  assert.equal(r.matched, 2);
  assert.equal(r.examined, 4, 'examined counts every task walked, matched or not');
  assert.equal(r.withComments, 3, 'the silent task had none');
});

test('a row carries the task facts a human needs to act', () => {
  const r = selectAwaiting({
    tasks: [TASK_FOREIGN], commentsByTaskId: COMMENTS, ownerUserId: OWNER_ID, now: NOW,
  });
  // Pairwise-distinct fixture values: a mutant that swaps list for status, or
  // name for id, cannot produce this row.
  assert.deepEqual(
    {
      id: r.rows[0].id, name: r.rows[0].name, list: r.rows[0].list,
      status: r.rows[0].status, by: r.rows[0].lastCommentBy, ageMs: r.rows[0].ageMs,
    },
    {
      id: 'aaa1', name: 'Wire the widget', list: 'Platform',
      status: 'in progress', by: 'dana', ageMs: 3 * MS_PER_DAY,
    }
  );
});

test('--days keeps only what has been quiet at least that long', () => {
  const strict = selectAwaiting({
    tasks: ALL_TASKS, commentsByTaskId: COMMENTS, ownerUserId: OWNER_ID, now: NOW, minAgeDays: 7,
  });
  assert.deepEqual(strict.rows.map((x) => x.id), [TASK_OLD_FOREIGN.id],
    '--days 7 must drop the 3-day-old one and keep the 20-day-old one');
  assert.equal(strict.examined, 4, 'the age filter narrows MATCHES, never the examined count');

  const boundary = selectAwaiting({
    tasks: ALL_TASKS, commentsByTaskId: COMMENTS, ownerUserId: OWNER_ID, now: NOW, minAgeDays: 3,
  });
  assert.equal(boundary.rows.length, 2, '--days N is "at least N days", inclusive at the boundary');

  const past = selectAwaiting({
    tasks: ALL_TASKS, commentsByTaskId: COMMENTS, ownerUserId: OWNER_ID, now: NOW, minAgeDays: 21,
  });
  assert.equal(past.rows.length, 0);
  assert.equal(past.examined, 4);
});

// ── The fan-out cap ────────────────────────────────────────────────────────

test('capFanOut examines exactly max and reports what it left out', () => {
  const items = ['t1', 't2', 't3', 't4', 't5', 't6', 't7'];
  const c = capFanOut(items, 3);
  assert.deepEqual(c.examined, ['t1', 't2', 't3'], 'off-by-one on the cap changes this list');
  assert.equal(c.total, 7);
  assert.equal(c.skipped, 4);
  assert.equal(c.truncated, true);
});

test('capFanOut does not claim truncation when the cap was not reached', () => {
  const exact = capFanOut(['t1', 't2', 't3'], 3);
  assert.equal(exact.truncated, false, 'max == length is a COMPLETE scan, not a truncated one');
  assert.equal(exact.skipped, 0);

  const under = capFanOut(['t1'], 9);
  assert.equal(under.truncated, false);
  assert.equal(under.examined.length, 1);
});

test('capFanOut refuses a nonsense cap rather than scanning nothing quietly', () => {
  assert.throws(() => capFanOut(['t1'], 0), RangeError);
  assert.throws(() => capFanOut(['t1'], -2), RangeError);
  assert.throws(() => capFanOut(['t1'], 1.5), RangeError);
  assert.throws(() => capFanOut(['t1'], NaN), RangeError);
});

test('chunk splits without dropping or duplicating', () => {
  assert.deepEqual(chunk([1, 2, 3, 4, 5], 2), [[1, 2], [3, 4], [5]]);
  assert.deepEqual(chunk([], 3), []);
  assert.throws(() => chunk([1], 0), RangeError);
});

// ── Pacing ─────────────────────────────────────────────────────────────────

test('batchPauseMs spends the batch budget, minus the time the batch already took', () => {
  // 60 req/min = 1000ms per request. A 5-request batch owes 5000ms.
  assert.equal(batchPauseMs(5, 60, 0), 5000);
  assert.equal(batchPauseMs(5, 60, 1200), 3800, 'a batch that took 1.2s owes 1.2s less');
  assert.equal(batchPauseMs(5, 60, 9000), 0, 'never negative — a slow batch paid its own way');
  // A different rate must move the number: a constant would survive the cases
  // above but not this one.
  assert.equal(batchPauseMs(4, 120, 0), 2000);
});

test('batchPauseMs refuses a rate of zero rather than dividing by it', () => {
  assert.throws(() => batchPauseMs(5, 0), RangeError);
  assert.throws(() => batchPauseMs(5, -10), RangeError);
});

test('the default pacing stays under the 100 req/min token limit', () => {
  assert.ok(AWAITING_DEFAULTS.ratePerMin < 100,
    `default ratePerMin ${AWAITING_DEFAULTS.ratePerMin} must leave headroom under the ` +
      '100 req/min token limit — getMyTasks spends from the same budget');
  // 5 requests at 80/min = 3750ms of budget per batch.
  assert.equal(
    batchPauseMs(AWAITING_DEFAULTS.batchSize, AWAITING_DEFAULTS.ratePerMin, 0),
    3750
  );
});

// ── findAwaiting: the orchestration ────────────────────────────────────────

/** A clock that advances a fixed step per read, so pacing is deterministic. */
function fakeClock(startMs, stepMs) {
  let now = startMs;
  return () => {
    const v = now;
    now += stepMs;
    return v;
  };
}

test('findAwaiting reads every task under the cap and reports both numbers', async () => {
  const slept = [];
  const r = await findAwaiting({
    tasks: ALL_TASKS,
    ownerUserId: OWNER_ID,
    fetchComments: fetchFixtureComments,
    sleepFn: async (ms) => { slept.push(ms); },
    clock: () => NOW,
    batchSize: 2,
    ratePerMin: 60,
  });
  assert.equal(r.examined, 4);
  assert.equal(r.matched, 2);
  assert.equal(r.assigned, 4);
  assert.equal(r.commentFetches, 4);
  assert.equal(r.truncated, false);
  assert.deepEqual(r.unreadable, []);
  assert.deepEqual(r.rows.map((x) => x.id), [TASK_OLD_FOREIGN.id, TASK_FOREIGN.id]);
  // 2 batches → exactly ONE pause, between them, never after the last.
  assert.deepEqual(slept, [2000], 'one pause of 2 requests @60/min; no trailing sleep');
});

test('🔴 a capped scan is reported as EXAMINED-but-TRUNCATED, never as a clean zero', async () => {
  const r = await findAwaiting({
    tasks: ALL_TASKS,
    ownerUserId: OWNER_ID,
    // Cap at 2 → only TASK_FOREIGN and TASK_OWNER are read, so exactly one match.
    maxTasks: 2,
    fetchComments: fetchFixtureComments,
    sleepFn: async () => {},
    clock: () => NOW,
    batchSize: 5,
  });
  assert.equal(r.examined, 2);
  assert.equal(r.assigned, 4);
  assert.equal(r.skipped, 2);
  assert.equal(r.truncated, true);
  assert.equal(r.matched, 1, 'the 20-day-old one lay beyond the cap and was never read');
  assert.equal(r.commentFetches, 2, 'the cap bounds FETCHES, not just the reported list');

  const out = formatAwaiting(r);
  assert.match(out, /TRUNCATED/,
    'a truncated scan MUST say so: silence here reads as "covered everything"');
  assert.match(out, /2 of 4/, 'the notice must carry both numbers, not just the cap');
  assert.match(out, /1 matched of 2 task\(s\) examined/,
    'matched must be printed BESIDE examined — a bare count is unreadable');
});

test('🔴 a scan that matches nothing still prints how much it examined', async () => {
  const r = await findAwaiting({
    tasks: [TASK_OWNER, TASK_SILENT],
    ownerUserId: OWNER_ID,
    fetchComments: fetchFixtureComments,
    sleepFn: async () => {},
    clock: () => NOW,
  });
  assert.equal(r.matched, 0);
  const out = formatAwaiting(r);
  assert.match(out, /0 matched of 2 task\(s\) examined/,
    'a bare "0 awaiting" from a scan that walked nothing is the failure mode this pins');
});

test('🔴 a task whose comments could not be read is reported, not counted as answered', async () => {
  const r = await findAwaiting({
    tasks: [TASK_FOREIGN, TASK_OWNER],
    ownerUserId: OWNER_ID,
    fetchComments: async (id) => {
      if (id === TASK_OWNER.id) throw new Error('rate limited');
      return COMMENTS.get(id);
    },
    sleepFn: async () => {},
    clock: () => NOW,
  });
  assert.equal(r.examined, 1, 'an unreadable task is NOT examined');
  assert.equal(r.unreadable.length, 1);
  assert.equal(r.unreadable[0].id, TASK_OWNER.id);
  assert.match(formatAwaiting(r), /UNREADABLE/);
});

test('findAwaiting subtracts the batch time from the pause', async () => {
  const slept = [];
  await findAwaiting({
    tasks: ALL_TASKS,
    ownerUserId: OWNER_ID,
    fetchComments: fetchFixtureComments,
    sleepFn: async (ms) => { slept.push(ms); },
    // Each clock() read advances 500ms, so a batch "takes" 500ms.
    clock: fakeClock(NOW, 500),
    batchSize: 1,
    ratePerMin: 60,
  });
  // 4 batches of 1 → 3 pauses, each 1000ms of budget minus 500ms elapsed.
  assert.deepEqual(slept, [500, 500, 500]);
});

test('findAwaiting makes no requests at all for an empty board', async () => {
  let calls = 0;
  const r = await findAwaiting({
    tasks: [],
    ownerUserId: OWNER_ID,
    fetchComments: async () => { calls++; return []; },
    sleepFn: async () => { throw new Error('must not sleep with nothing to do'); },
    clock: () => NOW,
  });
  assert.equal(calls, 0);
  assert.equal(r.examined, 0);
  assert.equal(r.matched, 0);
});

// ── The output's own blind spot ────────────────────────────────────────────

test('🔴 the output states the predicate AND the no-bot-identity blind spot', async () => {
  const r = await findAwaiting({
    tasks: ALL_TASKS,
    ownerUserId: OWNER_ID,
    fetchComments: fetchFixtureComments,
    sleepFn: async () => {},
    clock: () => NOW,
  });
  const out = formatAwaiting(r);
  assert.match(out, /NEWEST comment/, 'the predicate must be stated in the output itself');
  assert.match(out, /no bot identity/i);
  assert.match(out, /indistinguishable from you answering/i);
  assert.match(out, /`resolved` is not readable/i,
    'the output must say what it does NOT measure, or a reader will assume it does');
});

test('formatAge is coarse but never lies about scale', () => {
  assert.equal(formatAge(0), '0m');
  assert.equal(formatAge(45 * 60000), '45m');
  assert.equal(formatAge(3 * 3600000), '3h');
  assert.equal(formatAge(3 * 3600000 + 25 * 60000), '3h 25m');
  assert.equal(formatAge(2 * MS_PER_DAY + 5 * 3600000), '2d 5h');
  assert.equal(formatAge(null), 'unknown');
});

// ── Inbox pagination (the second fix) ──────────────────────────────────────
//
// These commands cannot be exercised live on this host — every inbox endpoint
// needs a JWT accounts.json does not carry — so what is pinned is the CURSOR
// LOOP itself, against a fake transport. The response's cursor FIELD NAME
// remains an unverified assumption; that is stated in api/inbox.mjs and is not
// something a test can settle.

const bundle = (id) => ({ id, unreadCount: 1, countByNotificationType: {}, rootEntityResourceName: `clickup:task:1:${id}` });
const page = (ids, next) => ({
  notificationBundleGroups: [{ notificationBundles: ids.map(bundle) }],
  resources: ids.map((id) => ({ entityResourceName: `clickup:task:1:${id}`, name: `task ${id}` })),
  users: {},
  pagination: { nextCursor: next },
});

test('nextInboxCursor reads the cursor, and reports absence as null', () => {
  assert.equal(nextInboxCursor(page(['b1'], 'CURSOR-2')), 'CURSOR-2');
  assert.equal(nextInboxCursor({ nextCursor: 'FLAT-3' }), 'FLAT-3', 'a flat response shape too');
  assert.equal(nextInboxCursor(page(['b1'], '')), null, 'an empty cursor means no more pages');
  assert.equal(nextInboxCursor(page(['b1'], null)), null);
  assert.equal(nextInboxCursor({}), null);
  assert.equal(nextInboxCursor(undefined), null);
});

test('mergeInboxPages concatenates bundles and de-duplicates resources', () => {
  const merged = mergeInboxPages([page(['b1', 'b2'], 'c2'), page(['b2', 'b3'], null)]);
  assert.deepEqual(bundlesOf(merged).map((b) => b.id), ['b1', 'b2', 'b2', 'b3']);
  assert.deepEqual(merged.resources.map((r) => r.name), ['task b1', 'task b2', 'task b3']);
  assert.equal(merged.pagesFetched, 2);
});

test('🔴 fetchAllInboxNotifications follows the cursor past page one', async () => {
  const pages = [page(['b1', 'b2'], 'C2'), page(['b3'], 'C3'), page(['b4'], null)];
  const seenCursors = [];
  let i = 0;
  const merged = await fetchAllInboxNotifications({
    // The injected transport is the control: a single-page read returns 2
    // bundles here, the whole queue is 4. That gap is the bug being fixed.
    fetchPage: async (opts) => { seenCursors.push(opts.cursor); return pages[i++]; },
  });
  assert.deepEqual(seenCursors, ['', 'C2', 'C3'], 'each page must be requested with the PREVIOUS page\'s cursor');
  assert.deepEqual(bundlesOf(merged).map((b) => b.id), ['b1', 'b2', 'b3', 'b4']);
  assert.equal(merged.pagesFetched, 3);
  assert.equal(merged.truncated, false);
});

test('the cursor loop stops on a repeated cursor instead of spinning', async () => {
  let calls = 0;
  const merged = await fetchAllInboxNotifications({
    fetchPage: async () => { calls++; return page(['b9'], 'SAME'); },
    maxPages: 50,
  });
  assert.ok(calls <= 3, `a server that repeats its cursor must not loop; made ${calls} calls`);
  assert.ok(bundlesOf(merged).length > 0);
});

test('the page bound is reported as TRUNCATED, not as the whole queue', async () => {
  let n = 0;
  const merged = await fetchAllInboxNotifications({
    fetchPage: async () => page([`b${n++}`], `C${n}`),
    maxPages: 3,
  });
  assert.equal(merged.pagesFetched, 3);
  assert.equal(merged.truncated, true, 'hitting the bound is not a complete read');
});
