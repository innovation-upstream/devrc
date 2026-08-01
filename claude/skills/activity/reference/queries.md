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
- i3 + scroll exist only for **`host=laptop`** (GUI). `workspace` is empty on a small
  share of window-focus events.
- `ts` is the **UTC instant**. Group by local hour/day only with an explicit
  `'America/Winnipeg'` tz arg (`toHour`/`toDate`); NEVER tz-shift `$__timeFilter` or a
  range comparison against `now()`.

## i3 attention / dwell by app

```sql
SELECT app, round(sum(dwell_ms)/60000,1) AS dwell_min FROM (
  SELECT app, kind, least(
    leadInFrame(toUnixTimestamp64Milli(ts),1,toUnixTimestamp64Milli(ts))
      OVER (PARTITION BY host ORDER BY ts ASC ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING)
      - toUnixTimestamp64Milli(ts), 1800000) AS dwell_ms
  FROM activity.events WHERE source='i3' AND host IN ('laptop') AND ts > now()-interval 7 day)
WHERE kind='window-focus' AND app != '' GROUP BY app ORDER BY dwell_min DESC;
```

## Browser reading depth by domain

```sql
SELECT domain(text) d, avg(JSONExtractInt(toString(payload),'scroll_pct')) avg_depth,
       max(JSONExtractInt(toString(payload),'scroll_pct')) max_depth,
       sum(JSONExtractInt(toString(payload),'scroll_ms'))/1000 scroll_s
FROM activity.events WHERE source='browser' AND kind='nav' AND text!='' GROUP BY d ORDER BY scroll_s DESC;
```

## Browser ATTENTION by domain (i3-DERIVED — replaces the retired `active_ms`)

Intersect i3 "Brave-focused" intervals with the active-tab domain timeline.

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
