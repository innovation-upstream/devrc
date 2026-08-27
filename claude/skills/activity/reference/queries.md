# activity — query patterns (the non-obvious ones)

Read this when writing a NEW query over `activity.events`, or when touching the
Grafana dashboard's attention/reading panels. Everyday status/health queries are
inline in `SKILL.md`.

## Dashboard context

Row **"Attention (i3 focus) & Reading (scroll)"** (homelab PR #78): Attention-by-app,
Attention-by-i3-workspace, Reading-depth-by-domain, i3-window-switches-over-time.
"Browser active time by domain (s)" was **replaced** by "Browser attention by domain
(i3-derived, s)" (homelab-infra PR #79) — extension `active_ms` is retired, so browser
attention is the i3∩domain intersection below.

## Caveats that make these queries non-obvious

- i3 focus events are **point-in-time — there is NO stored duration**. Dwell = the gap to
  the NEXT focus event, CAPPED (30 min) so an idle focus does not inflate it.
- The URL is the **`text`** column, NOT `payload.url`.
- Scroll must be extracted with `JSONExtractInt(toString(payload),'scroll_pct')` —
  **`payload.scroll_pct` subcolumn access is NOT available.**
- A nav event's `text`/`title` are the **DESTINATION** tab, but its `scroll_pct`/`scroll_ms`
  belong to the **LEAVING** tab.
- `scroll_pct`/`scroll_ms` exist on a SUBSET of nav events only (scrolled pages;
  un-scrolled report 0).
- 🔴 **i3 is on BOTH hosts — never filter it to `host='laptop'`.** The workbench runs a
  real X/i3 session and is the MAJORITY of the data: measured 2026-08-26 over a 7d window,
  workbench **7,672** window-focus rows vs laptop **1,205**, so a laptop-only filter drops
  ~86% of i3 attention. (This bullet said "i3 + scroll exist only for `host=laptop`" until
  2026-08-26 — the same claim `SKILL.md` already retracts in its source table. Do not
  re-derive it.)
- 🔴 **`browser` is on BOTH hosts as of 2026-08-26 21:46 — it is no longer laptop-only.**
  This bullet read "genuinely IS laptop-only" until 2026-08-27. The measurement behind it was
  real and is kept: over the 7d window ending 2026-08-26, 867 `browser` rows with **zero** on
  the workbench, all 7,571 scroll-bearing nav rows laptop. It went out of date hours later,
  when the extension was loaded in the workbench's Brave too (from
  `~/.config/activity-collector/browser-ext`, a different path than the laptop's — see
  `SKILL.md`). **Scroll-bearing rows may still be laptop-heavy; that is not re-measured here.**
  An i3∩browser query must still pin **one host on both sides** — host-consistency is what
  makes it correct, never the `'laptop'` literal — and that now matters far more, because a
  mismatch no longer returns nothing (see the intersect query below).
- `app` is empty on **every** `workspace-focus` row, on both hosts, by construction
  (0 of 1,100 laptop / 0 of 4,179 workbench populated) — so filter `kind='window-focus'`
  before grouping by `app`; `app != ''` alone reads as a data-quality filter but is really
  doing the kind filter's job. On `window-focus` itself `app` is populated 96.9% on the
  workbench (7,434/7,672) and 100% on the laptop. `workspace` was populated on 100% of
  window-focus rows on both hosts in that window.
- `ts` is the **UTC instant**. Group by local hour/day only with an explicit
  `'America/Winnipeg'` tz arg (`toHour`/`toDate`); NEVER tz-shift `$__timeFilter` or a
  range comparison against `now()`.

## i3 attention / dwell by app

Both hosts, reported **per host** — `PARTITION BY host` already keeps the dwell gap from
bleeding across machines, so the only thing the old `host IN ('laptop')` filter did was
discard the workbench. Group by `host` as well as `app`: summing the two together silently
adds a workbench Slack minute to a laptop Slack minute.

```sql
SELECT host, app, round(sum(dwell_ms)/60000,1) AS dwell_min FROM (
  SELECT host, app, kind, least(
    leadInFrame(toUnixTimestamp64Milli(ts),1,toUnixTimestamp64Milli(ts))
      OVER (PARTITION BY host ORDER BY ts ASC ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING)
      - toUnixTimestamp64Milli(ts), 1800000) AS dwell_ms
  FROM activity.events WHERE source='i3' AND ts > now()-interval 7 day)
WHERE kind='window-focus' AND app != '' GROUP BY host, app ORDER BY host, dwell_min DESC;
```

Add `WHERE host='<h>'` to scope it to one machine — never as the default.

## Browser reading depth by domain

```sql
SELECT domain(text) d, avg(JSONExtractInt(toString(payload),'scroll_pct')) avg_depth,
       max(JSONExtractInt(toString(payload),'scroll_pct')) max_depth,
       sum(JSONExtractInt(toString(payload),'scroll_ms'))/1000 scroll_s
FROM activity.events WHERE source='browser' AND kind='nav' AND text!='' GROUP BY d ORDER BY scroll_s DESC;
```

## Browser ATTENTION by domain (i3-DERIVED — replaces the retired `active_ms`)

Intersect i3 "Brave-focused" intervals with the active-tab domain timeline.

🔴 **The `host='laptop'` below is load-bearing on BOTH sides and must stay matched** — this
is the one query here that should NOT be widened. The `CROSS JOIN` has no host predicate,
so dropping (or mismatching) either filter intersects **workbench i3 dwell with laptop
browser navigation** and invents attention that never happened.

🔴 **THE SAFETY NET UNDER THIS IS GONE — as of 2026-08-26 21:46 both hosts emit `browser`.**
This note used to end "today only `laptop` returns rows at all, because `browser` is
laptop-only; if the extension ever lands on a second host, change both filters together."
That condition has now occurred. Until it did, a mismatched pair failed LOUDLY-ish by
returning nothing, because workbench `browser` had zero rows. It now returns a **plausible,
populated, and wrong** result instead. Change both filters together to the same `<h>`, and
treat any output from this query as suspect unless you have read both host predicates.

```sql
WITH brave AS (SELECT bs, be FROM (SELECT toUnixTimestamp64Milli(ts) bs, app,
    least(leadInFrame(toUnixTimestamp64Milli(ts),1,toUnixTimestamp64Milli(ts)) OVER (PARTITION BY host ORDER BY ts ASC ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING), toUnixTimestamp64Milli(ts)+1800000) be
  FROM activity.events WHERE source='i3' AND kind='window-focus' AND host='laptop' AND ts>now()-interval 7 day) WHERE app='Brave-browser'),
dom AS (SELECT d, ds, least(de, ds+1800000) de FROM (SELECT if(domain(text)!='',domain(text),netloc(text)) d, toUnixTimestamp64Milli(ts) ds,
    leadInFrame(toUnixTimestamp64Milli(ts),1,toUnixTimestamp64Milli(ts)+1800000) OVER (PARTITION BY host ORDER BY ts ASC ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING) de
  FROM activity.events WHERE source='browser' AND kind='nav' AND text!='' AND host='laptop' AND ts>now()-interval 7 day))
SELECT d domain, round(sum(greatest(0, least(be,de)-greatest(bs,ds)))/60000,1) min FROM brave CROSS JOIN dom WHERE be>ds AND de>bs GROUP BY d HAVING min>0 ORDER BY min DESC;
```

## Append-only kinds — dedupe on read

`kind=session-summary` and `kind=session-insight` are **append-only**: several rows exist
per `session`. Always dedupe with `argMax(<field>, ingested_at)` grouped by `session` —
the newest row is the most complete (every emit re-reads the whole transcript).
