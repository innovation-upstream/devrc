#!/usr/bin/env python3
"""Assemble ONE self-contained HTML file.

🔴 SELF-CONTAINMENT IS A HARD REQUIREMENT, NOT A PREFERENCE. The hosted page and
the portable export are the SAME artefact: there is no second renderer and
nothing to keep in sync. The output must open correctly from `file://` with no
network at all, which means:

  * no `<script src>`, no `<link rel=stylesheet>`, no `@import`, no web font,
    no `url(http…)` — every byte is inline;
  * no diagram library. The diagrams are hand-authored inline SVG in
    `content.py`, so there is no state between "the file is there" and "it is
    not";
  * the ONE external-looking string in the output is the SVG namespace URI,
    which is an XML identifier and is never fetched. `test_present_render.py`
    allowlists exactly that token and fails on anything else.

THEME. The page renders in the reader's theme. Colours are defined as custom
properties on bare `:root` and REDEFINED under `prefers-color-scheme: dark`;
nothing gets its only definition inside a media query, and `body` carries an
explicit background so the page never borrows a host's.
"""
from __future__ import annotations

import html
import re

from present import content as _content
from present import measure as _measure

# --------------------------------------------------------------------------- #

CSS = """
:root{
  --bg:#fbfbf9; --panel:#ffffff; --ink:#1b1b1a; --ink-2:#55544f; --ink-3:#83817a;
  --line:#e3e1da; --line-2:#cfccc2;
  --accent:#3f6f5b; --accent-soft:#e8f0eb;
  --warn:#8a5a12; --warn-soft:#fbf1de;
  --bad:#93392f; --bad-soft:#fbeae7;
  --ok:#2f6b46; --ok-soft:#e7f2ea;
  --code-bg:#f2f1ec;
  --d-a:#e8f0eb; --d-b:#eef0f6; --d-c:#f4f1e8; --d-d:#f6eeec; --d-warn:#fbf1de;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --bg:#141513; --panel:#1c1e1b; --ink:#e9e8e2; --ink-2:#b3b1a8; --ink-3:#87857c;
    --line:#2d2f2b; --line-2:#3d403a;
    --accent:#8fc4a9; --accent-soft:#1e2a24;
    --warn:#dcae63; --warn-soft:#2b2418;
    --bad:#e08a7c; --bad-soft:#2c1c19;
    --ok:#8bc79e; --ok-soft:#1a2a20;
    --code-bg:#232520;
    --d-a:#1e2a24; --d-b:#1d2129; --d-c:#272419; --d-d:#2c1e1b; --d-warn:#2b2418;
  }
}
:root[data-theme="dark"]{
  --bg:#141513; --panel:#1c1e1b; --ink:#e9e8e2; --ink-2:#b3b1a8; --ink-3:#87857c;
  --line:#2d2f2b; --line-2:#3d403a;
  --accent:#8fc4a9; --accent-soft:#1e2a24;
  --warn:#dcae63; --warn-soft:#2b2418;
  --bad:#e08a7c; --bad-soft:#2c1c19;
  --ok:#8bc79e; --ok-soft:#1a2a20;
  --code-bg:#232520;
  --d-a:#1e2a24; --d-b:#1d2129; --d-c:#272419; --d-d:#2c1e1b; --d-warn:#2b2418;
}
*{box-sizing:border-box}
body{
  margin:0; background:var(--bg); color:var(--ink);
  font:16px/1.65 ui-sans-serif,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  -webkit-text-size-adjust:100%;
}
code,pre,.mono{font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace}
code{background:var(--code-bg); padding:.1em .35em; border-radius:4px; font-size:.88em}
a{color:var(--accent)}
b{font-weight:650}

.wrap{display:grid; grid-template-columns:255px minmax(0,1fr); gap:0; max-width:1360px; margin:0 auto}
nav.side{
  position:sticky; top:0; align-self:start; max-height:100vh; overflow-y:auto;
  padding:22px 16px 40px; border-right:1px solid var(--line);
}
nav.side .brand{font-weight:700; letter-spacing:-.01em; font-size:1.02rem; margin-bottom:2px}
nav.side .brandsub{color:var(--ink-3); font-size:.76rem; line-height:1.4; margin-bottom:16px}
nav.side a{
  display:block; padding:5px 9px; border-radius:6px; text-decoration:none;
  color:var(--ink-2); font-size:.845rem; border-left:2px solid transparent;
}
nav.side a:hover{background:var(--accent-soft); color:var(--ink)}
nav.side a.on{background:var(--accent-soft); color:var(--ink); border-left-color:var(--accent); font-weight:600}
nav.side a .n{color:var(--ink-3); font-variant-numeric:tabular-nums; margin-right:7px}
.navtools{margin-top:18px; padding-top:14px; border-top:1px solid var(--line)}
.navtools button{
  width:100%; text-align:left; background:transparent; color:var(--ink-2);
  border:1px solid var(--line-2); border-radius:6px; padding:6px 9px;
  font:inherit; font-size:.8rem; cursor:pointer; margin-bottom:6px;
}
.navtools button:hover{background:var(--accent-soft); color:var(--ink)}
.navtools .hint{color:var(--ink-3); font-size:.72rem; line-height:1.45}

main{padding:34px 40px 120px; min-width:0}
header.masthead{border-bottom:1px solid var(--line); padding-bottom:22px; margin-bottom:8px}
header.masthead h1{font-size:1.9rem; line-height:1.2; margin:0 0 8px; letter-spacing:-.02em}
header.masthead p.sub{color:var(--ink-2); margin:0 0 14px; max-width:62ch}
.buildbar{display:flex; flex-wrap:wrap; gap:8px; font-size:.76rem}
.buildbar span{
  background:var(--panel); border:1px solid var(--line); border-radius:999px;
  padding:3px 11px; color:var(--ink-2);
}
.buildbar span b{color:var(--ink)}

section{padding:34px 0 8px; border-bottom:1px solid var(--line); scroll-margin-top:14px}
section:last-of-type{border-bottom:none}
section > h2{font-size:1.35rem; margin:0 0 6px; letter-spacing:-.01em}
section > h2 .n{color:var(--ink-3); font-weight:500; margin-right:10px; font-variant-numeric:tabular-nums}
section > p.lede{color:var(--ink-2); margin:0 0 20px; max-width:74ch; font-size:1.01rem}
section h3{font-size:1.02rem; margin:26px 0 8px; letter-spacing:-.005em}
section p{max-width:78ch}
section ul{max-width:78ch; padding-left:20px}
section li{margin:.45em 0}
/* 🔴 An UNMEASURED reason routinely carries a 100+ character absolute path with
   no break opportunity in it. Without this, one such row widens the grid column
   and the BODY scrolls horizontally — the failure the narrow-viewport rule
   exists to prevent. Applied to every container that can hold machine text. */
section p,section li,.reason,.md,dl.kv dd,.card p,.note p,.unbanner li{overflow-wrap:anywhere}

.stub{
  background:var(--bad-soft); border:2px dashed var(--bad); border-radius:10px;
  padding:16px 18px; color:var(--bad);
}
.stub b{display:block; font-size:.8rem; letter-spacing:.08em; text-transform:uppercase; margin-bottom:5px}

.note{
  border:1px solid var(--line); border-left:3px solid var(--ink-3);
  background:var(--panel); border-radius:8px; padding:12px 15px; margin:16px 0; max-width:80ch;
}
.note .nt{font-size:.7rem; letter-spacing:.09em; text-transform:uppercase; color:var(--ink-3); margin-bottom:3px}
.note .nh{font-weight:650; margin-bottom:5px}
.note p{margin:0}
.note-hazard{border-left-color:var(--bad); background:var(--bad-soft)}
.note-hazard .nt{color:var(--bad)}
.note-why{border-left-color:var(--accent); background:var(--accent-soft)}
.note-why .nt{color:var(--accent)}
.note-what{border-left-color:var(--warn); background:var(--warn-soft)}
.note-what .nt{color:var(--warn)}

.cards{display:grid; grid-template-columns:repeat(auto-fit,minmax(255px,1fr)); gap:12px; margin:18px 0}
.card{background:var(--panel); border:1px solid var(--line); border-radius:9px; padding:14px 16px}
.card h4{margin:0 0 6px; font-size:.94rem}
.card p{margin:0; font-size:.9rem; color:var(--ink-2)}

dl.kv{margin:16px 0; max-width:82ch}
dl.kv dt{font-weight:650; margin-top:14px; font-size:.95rem}
dl.kv dd{margin:3px 0 0; color:var(--ink-2); font-size:.93rem}
/* 🔴 Deliberately NOT `white-space:nowrap`. The longest tag here is a whole
   clause, and at a 360px viewport an unwrappable one is wider than the column —
   MEASURED forcing the BODY to scroll sideways, which is the one layout failure
   this page must not have. Tags sit on one line wherever there is room, so
   allowing them to wrap costs nothing at desktop widths. */
.tag{
  display:inline-block; font-size:.68rem; letter-spacing:.05em; text-transform:uppercase;
  background:var(--ok-soft); color:var(--ok); border-radius:4px; padding:1px 6px;
  font-weight:650; overflow-wrap:anywhere;
}
.tag-soft{background:var(--warn-soft); color:var(--warn)}

.m{border:1px solid var(--line); border-radius:10px; background:var(--panel); margin:18px 0; overflow:hidden}
.m.un{border-color:var(--warn); background:var(--warn-soft)}
.m > .mh{display:flex; flex-wrap:wrap; align-items:baseline; gap:10px; padding:12px 16px 0}
.m > .mh .ml{font-weight:650; font-size:.95rem}
.m > .mv{padding:2px 16px 0; font-size:1.32rem; font-weight:660; letter-spacing:-.01em; font-variant-numeric:tabular-nums}
.m > .md{padding:6px 16px 0; color:var(--ink-2); font-size:.9rem; max-width:84ch}
.m > .mf{
  padding:11px 16px 12px; margin-top:11px; border-top:1px solid var(--line);
  color:var(--ink-3); font-size:.75rem; display:flex; flex-wrap:wrap; gap:6px 16px;
}
.m > .mf .mono{color:var(--ink-2); word-break:break-all}
.pill{
  display:inline-block; font-size:.66rem; letter-spacing:.07em; text-transform:uppercase;
  border-radius:4px; padding:2px 7px; font-weight:700; white-space:nowrap;
}
.pill-ok{background:var(--ok-soft); color:var(--ok)}
.pill-un{background:var(--warn-soft); color:var(--warn); border:1px solid var(--warn)}
.pill-na{background:var(--code-bg); color:var(--ink-3)}
.reason{padding:8px 16px 0; color:var(--warn); font-size:.9rem; max-width:84ch}
.reason b{color:var(--warn)}
.settle{padding:8px 16px 0}
.settle .lbl{font-size:.7rem; letter-spacing:.07em; text-transform:uppercase; color:var(--ink-3)}
.settle pre{
  margin:4px 0 0; background:var(--code-bg); border-radius:6px; padding:9px 11px;
  font-size:.78rem; overflow-x:auto; white-space:pre; color:var(--ink);
}
.tw{overflow-x:auto; margin:11px 0 0}
table{border-collapse:collapse; width:100%; font-size:.83rem}
th,td{text-align:left; padding:6px 16px 6px 0; border-bottom:1px solid var(--line); vertical-align:top}
th{color:var(--ink-3); font-weight:650; font-size:.72rem; letter-spacing:.05em; text-transform:uppercase; white-space:nowrap}
td:first-child,th:first-child{padding-left:16px}
td{color:var(--ink-2)}
td.num{font-variant-numeric:tabular-nums; white-space:nowrap; color:var(--ink)}
tr:last-child td{border-bottom:none}
details.more > summary{
  cursor:pointer; padding:8px 16px; font-size:.78rem; color:var(--ink-3);
  border-top:1px solid var(--line);
}
details.more[open] > summary{color:var(--ink-2)}

svg.diagram{display:block; width:100%; height:auto; max-width:920px; margin:20px 0; color:var(--ink-3)}
.dbox{stroke:var(--line-2); stroke-width:1; fill:var(--panel)}
.dbox-a{fill:var(--d-a)} .dbox-b{fill:var(--d-b)} .dbox-c{fill:var(--d-c)}
.dbox-d{fill:var(--d-d)} .dbox-warn{fill:var(--d-warn); stroke:var(--warn)}
.dlabel{fill:var(--ink); font-size:12.5px; font-weight:660; text-anchor:middle;
        font-family:ui-sans-serif,system-ui,sans-serif}
.dsub{fill:var(--ink-2); font-size:10.5px; text-anchor:middle;
      font-family:ui-sans-serif,system-ui,sans-serif}
.dnote{fill:var(--ink-3); font-size:10.5px; text-anchor:middle;
       font-family:ui-sans-serif,system-ui,sans-serif}
.dnote-l{text-anchor:start}
.dhead{fill:var(--ink-2); font-size:11px; font-weight:660; letter-spacing:.05em;
       font-family:ui-sans-serif,system-ui,sans-serif}
.dedge{fill:var(--ink-3); font-size:9.5px; text-anchor:middle;
       font-family:ui-sans-serif,system-ui,sans-serif}
.darrow{stroke:currentColor; stroke-width:1.3; fill:none}

/* The §0 overview cycle. It is the page's primary navigation, so it must stay
   legible rather than shrink to fit: below `min-width` it scrolls INSIDE its own
   container. That container is what scrolls — never the page body. */
.ovwrap{overflow-x:auto; margin:20px 0; -webkit-overflow-scrolling:touch}
svg.overview{display:block; width:100%; min-width:700px; max-width:940px;
             height:auto; margin:0 auto; color:var(--ink-3)}
svg.overview a{text-decoration:none; cursor:pointer}
svg.overview a .dbox{transition:stroke .12s,stroke-width .12s}
svg.overview a:hover .dbox,svg.overview a:focus .dbox{stroke:var(--accent); stroke-width:2.2}
svg.overview a:hover .dlabel,svg.overview a:focus .dlabel{fill:var(--accent)}
.dcount{fill:var(--ink); font-size:12px; font-weight:700; text-anchor:middle;
        font-variant-numeric:tabular-nums;
        font-family:ui-sans-serif,system-ui,sans-serif}
/* 🔴 An UNMEASURED stage is rendered LOUDLY, never as a blank, a dash or a zero.
   A gap in a diagram reads as "nothing there", which is the exact silent-zero
   this page exists to teach against. */
.dcount-un{fill:var(--warn); font-weight:800; letter-spacing:.07em}
.dctr{fill:var(--ink-2); font-size:11.5px; font-weight:660; letter-spacing:.05em;
      text-anchor:middle; font-family:ui-sans-serif,system-ui,sans-serif}

.unbanner{
  border:2px solid var(--warn); background:var(--warn-soft); color:var(--warn);
  border-radius:10px; padding:13px 16px; margin:18px 0;
}
.unbanner b{display:block; margin-bottom:4px}
.unbanner ul{margin:6px 0 0; color:var(--ink-2)}

footer{padding:30px 40px 60px; color:var(--ink-3); font-size:.8rem; max-width:80ch}
footer p{max-width:78ch}

@media (max-width:900px){
  .wrap{grid-template-columns:1fr}
  nav.side{position:static; max-height:none; border-right:none; border-bottom:1px solid var(--line)}
  main{padding:22px 18px 80px}
  footer{padding:24px 18px 50px}
}
@media print{ nav.side{display:none} .wrap{grid-template-columns:1fr} }
"""

JS = """
(function(){
  var links = Array.prototype.slice.call(document.querySelectorAll('nav.side a[href^="#"]'));
  var secs  = links.map(function(a){ return document.getElementById(a.getAttribute('href').slice(1)); });
  function spy(){
    var best = 0, top = -1e9;
    for (var i=0;i<secs.length;i++){
      if(!secs[i]) continue;
      var t = secs[i].getBoundingClientRect().top - 90;
      if (t <= 0 && t > top){ top = t; best = i; }
    }
    for (var j=0;j<links.length;j++){ links[j].classList.toggle('on', j===best); }
  }
  var tick=false;
  addEventListener('scroll', function(){
    if(tick) return; tick=true;
    requestAnimationFrame(function(){ spy(); tick=false; });
  }, {passive:true});
  spy();

  var root = document.documentElement;
  var tb = document.getElementById('themebtn');
  if (tb) tb.addEventListener('click', function(){
    var cur = root.getAttribute('data-theme');
    var dark = cur ? cur === 'dark'
                   : matchMedia('(prefers-color-scheme: dark)').matches;
    root.setAttribute('data-theme', dark ? 'light' : 'dark');
  });

  var ub = document.getElementById('unbtn');
  if (ub) ub.addEventListener('click', function(){
    var first = document.querySelector('.m.un');
    if (first) first.scrollIntoView({behavior:'smooth', block:'center'});
  });

  var eb = document.getElementById('expandbtn');
  if (eb) eb.addEventListener('click', function(){
    var ds = document.querySelectorAll('details.more');
    var anyClosed = false;
    for (var i=0;i<ds.length;i++){ if(!ds[i].open){ anyClosed = true; break; } }
    for (var k=0;k<ds.length;k++){ ds[k].open = anyClosed; }
  });
})();
"""

# --------------------------------------------------------------------------- #

_ROW_LIMIT = 12          # rows shown before a table folds into <details>


def esc(s) -> str:
    return html.escape(str(s), quote=True)


_TICKS = re.compile(r"`([^`\n]{1,120})`")


def rich(s) -> str:
    """Escape a MEASURER-SUPPLIED string, then honour its backticks.

    🔴 ESCAPE FIRST, MARK UP SECOND. Measurement text is built from values read
    off the local machine — file paths, error strings, a subprocess's stderr —
    so interpolating it raw would let a filename containing a tag rewrite the
    page. Escaping first makes that impossible; the backtick pass then runs over
    text that is already inert.
    """
    return _TICKS.sub(r"<code>\1</code>", esc(s))


def _table(columns, rows, key: str) -> str:
    if not rows:
        return ""
    cols = columns or tuple(f"col {i + 1}" for i in range(len(rows[0])))
    head = "".join(f"<th>{esc(c)}</th>" for c in cols)

    def body(subset):
        out = []
        for r in subset:
            cells = []
            for i, c in enumerate(r):
                num = bool(re.fullmatch(r"[\d,._ ]+", str(c).strip()))
                # An EMPTY class attribute is ~11 dead bytes per cell, and the
                # tables here are the largest single thing on the page. Emit the
                # attribute only when it carries a class.
                cells.append(
                    f'<td class="num">{rich(c)}</td>' if (num and i)
                    else f"<td>{rich(c)}</td>"
                )
            out.append("<tr>" + "".join(cells) + "</tr>")
        return "".join(out)

    if len(rows) <= _ROW_LIMIT:
        return f'<div class="tw"><table><thead><tr>{head}</tr></thead><tbody>{body(rows)}</tbody></table></div>'
    shown, hidden = rows[:_ROW_LIMIT], rows[_ROW_LIMIT:]
    return (
        f'<div class="tw"><table><thead><tr>{head}</tr></thead>'
        f'<tbody>{body(shown)}</tbody></table></div>'
        f'<details class="more"><summary>show the remaining {len(hidden)} row(s) '
        f'&mdash; every row is rendered, never truncated away</summary>'
        f'<div class="tw"><table><thead><tr>{head}</tr></thead>'
        f'<tbody>{body(hidden)}</tbody></table></div></details>'
    )


def render_measurement(m: _measure.Measurement) -> str:
    """One measured fact, or one honest absence. Never a blank, never a gap."""
    un = not m.measured
    parts = [f'<div class="m{" un" if un else ""}" id="m-{esc(m.key)}">']
    pill = ('<span class="pill pill-un">UNMEASURED</span>' if un
            else '<span class="pill pill-ok">MEASURED</span>')
    parts.append(
        f'<div class="mh"><span class="ml">{esc(m.label)}</span>{pill}</div>'
    )
    if un:
        parts.append(
            f'<div class="reason"><b>Why not:</b> {rich(m.reason or "no reason recorded — which is itself a defect")}</div>'
        )
        if m.settle:
            parts.append(
                '<div class="settle"><div class="lbl">what would settle it</div>'
                f'<pre>{esc(m.settle)}</pre></div>'
            )
        parts.append(
            f'<div class="mf"><span>attempted <b>{esc(m.asof)}</b></span>'
            f'<span class="mono">{esc(m.key)}</span></div></div>'
        )
        return "".join(parts)

    parts.append(f'<div class="mv">{esc(m.value)}</div>')
    if m.detail:
        parts.append(f'<div class="md">{rich(m.detail)}</div>')
    parts.append(_table(m.columns, m.rows, m.key))
    parts.append(
        f'<div class="mf"><span>measured <b>{esc(m.asof)}</b></span>'
        f'<span class="mono">from: {esc(m.source)}</span>'
        f'<span class="mono">{esc(m.key)}</span></div>'
    )
    parts.append("</div>")
    return "".join(parts)


def _blocks(blocks, ms: _measure.MeasurementSet) -> str:
    out = []
    for kind, payload in blocks:
        if kind == "p":
            out.append(f"<p>{payload}</p>")
        elif kind == "h3":
            # AUTHORED markup, like the `p` and `ul` blocks beside it — a
            # sub-heading that names a flag wants `<code>` around it. This is
            # NOT the same trust level as `rich()`, which handles text built
            # from values read off the machine; content.py is written by hand.
            out.append(f"<h3>{payload}</h3>")
        elif kind == "ul":
            out.append("<ul>" + "".join(f"<li>{i}</li>" for i in payload) + "</ul>")
        elif kind == "note":
            tone, title, body = payload
            label = {"hazard": "hazard", "why": "why", "what": "context"}.get(tone, tone)
            out.append(
                f'<div class="note note-{esc(tone)}"><div class="nt">{esc(label)}</div>'
                f'<div class="nh">{esc(title)}</div><p>{body}</p></div>'
            )
        elif kind == "cards":
            cards = "".join(
                f'<div class="card"><h4>{esc(t)}</h4><p>{b}</p></div>' for t, b in payload
            )
            out.append(f'<div class="cards">{cards}</div>')
        elif kind == "kv":
            items = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in payload)
            out.append(f'<dl class="kv">{items}</dl>')
        elif kind == "svg":
            fn = _content.DIAGRAMS.get(payload)
            out.append(fn() if fn else "")
        elif kind == "svgm":
            # A MEASUREMENT-AWARE diagram. It is handed the whole set rather
            # than a pre-formatted string so that a key it cannot find renders
            # as UNMEASURED inside the picture, with the same loudness as a
            # missing row — see content.diagram_overview.
            fn = _content.LIVE_DIAGRAMS.get(payload)
            out.append(fn(ms) if fn else "")
        elif kind == "unbanner":
            out.append(_unmeasured_banner(ms))
        elif kind == "measure":
            m = ms.by_key(payload)
            if m is None:
                # A section asked for a fact the registry does not produce. That
                # is a generator defect, and it renders as one rather than as a
                # silently missing row.
                out.append(
                    '<div class="m un"><div class="mh"><span class="ml">'
                    f'{esc(payload)}</span><span class="pill pill-un">UNMEASURED</span></div>'
                    '<div class="reason"><b>Why not:</b> this section asked for a '
                    'measurement key that no measurer produces — a generator defect, '
                    'rendered rather than hidden.</div>'
                    '<div class="settle"><div class="lbl">what would settle it</div>'
                    f'<pre>grep -n "{esc(payload)}" scripts/present/measure.py</pre></div></div>'
                )
            else:
                out.append(render_measurement(m))
    return "".join(out)


def _nav(sections) -> str:
    links = "".join(
        f'<a href="#{esc(s.slug)}"><span class="n">{esc(s.number)}</span>{esc(s.title)}</a>'
        for s in sections
    )
    return (
        '<nav class="side">'
        '<div class="brand">devrc &mdash; the agent layer</div>'
        '<div class="brandsub">A generated explainer. Every number was measured at '
        'build time and is stamped per row.</div>'
        f"{links}"
        '<div class="navtools">'
        '<button id="unbtn" type="button">Jump to the first UNMEASURED row</button>'
        '<button id="expandbtn" type="button">Expand / collapse all tables</button>'
        '<button id="themebtn" type="button">Toggle light / dark</button>'
        '<div class="hint">This page writes nothing, stores nothing and fetches '
        'nothing. It is safe to open from a file.</div>'
        "</div></nav>"
    )


def _unmeasured_banner(ms: _measure.MeasurementSet) -> str:
    un = ms.unmeasured
    if not un:
        return (
            '<div class="note note-why"><div class="nt">build</div>'
            '<div class="nh">Every registered fact measured on this build</div>'
            '<p>No row is UNMEASURED. That is a statement about <i>this machine at '
            'this moment</i>, not a property of the system &mdash; a build on a host '
            'without a given surface will legitimately carry unmeasured rows.</p></div>'
        )
    items = "".join(
        f"<li><b>{esc(m.label)}</b> &mdash; {esc((m.reason or '')[:210])}</li>" for m in un
    )
    return (
        f'<div class="unbanner"><b>{len(un)} of {len(ms)} facts could not be measured '
        'on this build.</b>'
        '<p>They are rendered in place, each with its reason and the command that '
        'would settle it. None was omitted &mdash; an omitted row is indistinguishable '
        'from one that measured clean.</p>'
        f"<ul>{items}</ul></div>"
    )


def build_html(ms: _measure.MeasurementSet, *, sanitized: bool, san=None,
               sections=None) -> str:
    sections = sections or _content.SECTIONS
    head = ms.by_key("repo.head")
    stamp = head.value if (head and head.measured) else "provenance UNMEASURED"

    chips = [
        f"<span>built <b>{esc(_measure._now())}</b></span>",
        f"<span>tree <b>{esc(stamp)}</b></span>",
        f"<span>facts <b>{len(ms.measured)} measured / {len(ms.unmeasured)} unmeasured</b></span>",
    ]
    if sanitized:
        legend = ", ".join(f"{k}&times;{n}" for k, n in (san.legend() if san else ()))
        chips.append(
            f"<span>mode <b>SANITIZED</b>{' &mdash; ' + legend if legend else ''}</span>"
        )
    else:
        chips.append("<span>mode <b>full</b> &mdash; contains local identifiers</span>")

    body = []
    for s in sections:
        body.append(f'<section id="{esc(s.slug)}">')
        body.append(f'<h2><span class="n">{esc(s.number)}</span>{esc(s.title)}</h2>')
        body.append(f'<p class="lede">{esc(s.lede)}</p>')
        if s.stub:
            body.append(
                '<div class="stub"><b>Not yet written</b>'
                f"<p>{esc(s.stub)}</p></div>"
            )
        else:
            body.append(_blocks(s.blocks, ms))
        body.append("</section>")

    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>devrc &mdash; the agent layer</title>"
        "<meta name=\"robots\" content=\"noindex\">"
        f"<style>{CSS}</style></head><body>"
        '<div class="wrap">'
        + _nav(sections)
        + "<main>"
        '<header class="masthead">'
        "<h1>devrc &mdash; how the agent layer works</h1>"
        f'<div class="buildbar">{"".join(chips)}</div>'
        "</header>"
        # The what-this-is line and the UNMEASURED roll-up now live INSIDE §0,
        # directly above and below the overview diagram, so a cold reader gets
        # "what am I looking at" and "how fresh is it" in one glance instead of
        # two competing summaries.
        + "".join(body)
        + "</main></div>"
        "<footer><p><b>This page is a measurement, not a document.</b> It is "
        "regenerated by <code>scripts/present/generate.py</code>; every figure comes "
        "from <code>scripts/present/measure.py</code> and carries the moment it was "
        "taken. Prose that names a mechanism is hand-written; no quantity is. If a "
        "row looks stale, re-run the generator rather than trusting the sentence "
        "around it.</p>"
        "<p>It is read-only: it writes nothing, stores nothing, submits nothing and "
        "fetches nothing.</p></footer>"
        f"<script>{JS}</script></body></html>"
    )
