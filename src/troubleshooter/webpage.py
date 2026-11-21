"""The web page's own files, kept as text in a .py file so an update is .py files only.

Nothing is loaded from anywhere else: no fonts, scripts or styles from the internet. The script puts
text on the page as text, never as markup, so nothing a log or a model says can run in the browser.

Five views: Map (every host and cluster; the first page), Trace (the chat, drawn as a trail from symptom
to fix), What changed (one lane per host and cluster), Wall (the Map made big for a TV) and Library.
"""

INDEX = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tracer</title>
<link rel="stylesheet" href="/app.css">
</head>
<body>
<main id="login" hidden>
  <form id="login-form" class="card login">
    <div class="brand big">TRACER</div>
    <div class="trail-mark" aria-hidden="true"><i></i><b></b><i></i><b></b><i></i><b></b><em></em></div>
    <label>Name <input id="login-user" autocomplete="username" required></label>
    <label>Password <input id="login-password" type="password" autocomplete="current-password" required></label>
    <p id="login-error" class="error" role="alert"></p>
    <button class="primary" type="submit">Sign in</button>
  </form>
</main>

<div id="app" hidden>
  <header class="top">
    <span class="brand">TRACER</span>
    <span class="trail-mark small" aria-hidden="true"><i></i><b></b><i></i><b></b><i></i></span>
    <nav aria-label="Sections">
      <button type="button" data-view="map"><svg class="ic" viewBox="0 0 20 20" aria-hidden="true"><path d="M3 5l5-2 4 2 5-2v12l-5 2-4-2-5 2z M8 3v12 M12 5v12"/></svg><span class="lbl">Map</span> <span id="map-count" class="count" hidden></span></button>
      <button type="button" data-view="trace"><svg class="ic" viewBox="0 0 20 20" aria-hidden="true"><circle cx="4" cy="15" r="2"/><circle cx="16" cy="5" r="2"/><path d="M6 15h4a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h5"/></svg><span class="lbl">Trace</span> <span id="trace-live" class="live" hidden title="Traces running now"><i></i><b></b></span></button>
      <button type="button" data-view="changes"><svg class="ic" viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7"/><path d="M10 6v4l3 2"/></svg><span class="lbl long">What changed</span><span class="lbl short">Changes</span></button>
      <button type="button" data-view="library"><svg class="ic" viewBox="0 0 20 20" aria-hidden="true"><path d="M4 3h4v14H4z M9 3h4v14H9z M14 4l3 1-2 12-3-1"/></svg><span class="lbl">Library</span></button>
    </nav>
    <span class="grow"></span>
    <button type="button" id="status-pill" class="pill" title="Go to the Map"></button>
    <span id="top-checked" class="muted small checked-note"></span>
    <button type="button" id="wall-open" class="wallbtn" title="Show the wall screen: big, look-only, for a TV"><svg class="ic" viewBox="0 0 20 20" aria-hidden="true"><rect x="2" y="3" width="16" height="11" rx="1.5"/><path d="M7 17h6 M10 14v3"/></svg>Wall</button>
    <span id="who" class="chip mono"></span>
    <button type="button" id="sign-out">Sign out</button>
  </header>

  <!-- ===== Trace ===== -->
  <section id="view-trace" class="view">
    <div>
      <h1 id="trace-title" class="title">New trace</h1>
      <p id="trace-sub" class="muted">Ask about a problem. Tracer looks, and draws the trail from symptom to fix.</p>
    </div>
    <div id="trace-auto" class="card auto-note" hidden>
      <span>Tracer started this trace by itself when the watch found the problem. Everyone can read it and ask more.</span>
      <span class="grow"></span>
      <button type="button" id="back-mine">Back to my trace</button>
    </div>
    <ol class="trail" aria-label="Trail">
      <li id="stage-1" class="stage"><span class="dot">1</span><span><b>Symptom</b><span class="detail">not asked yet</span></span></li>
      <li class="link" aria-hidden="true"></li>
      <li id="stage-2" class="stage"><span class="dot">2</span><span><b>Evidence</b><span class="detail">nothing yet</span></span></li>
      <li class="link" aria-hidden="true"></li>
      <li id="stage-3" class="stage"><span class="dot">3</span><span><b>Cause</b><span class="detail">not found yet</span></span></li>
      <li class="link" aria-hidden="true"></li>
      <li id="stage-4" class="stage"><span class="dot">4</span><span><b>Fix</b><span class="detail">not yet</span></span></li>
    </ol>
    <div class="two">
      <div class="main">
        <div id="talk" class="talk" aria-live="polite"></div>
        <form id="ask-form" class="ask">
          <div class="row">
            <label id="host-pick" hidden>About host <select id="host"></select></label>
            <label id="context-pick" hidden>Context <select id="context"></select></label>
            <span class="grow"></span>
            <span class="muted small">This page looks only. Fixes are done in ./ts.</span>
          </div>
          <label class="wide"><span>Ask, or add what you know</span>
            <textarea id="question" rows="3" placeholder="Why is pod X restarting?"></textarea>
          </label>
          <div class="row">
            <button class="primary" type="submit" id="ask">Ask</button>
            <button type="button" id="stop" hidden>Stop</button>
          </div>
        </form>
      </div>
      <aside class="side card" aria-label="Case so far">
        <h2>Case so far</h2>
        <dl class="facts">
          <dt>Where</dt><dd id="case-where">any host</dd>
          <dt>Steps</dt><dd id="case-steps">none yet</dd>
          <dt>Cause</dt><dd id="case-cause" class="muted">not found yet</dd>
          <dt>Fix</dt><dd id="case-fix" class="muted">not yet</dd>
        </dl>
        <p class="muted small">Filled in from the answers as Tracer works. Saved cases are searched first in later traces.</p>
        <button type="button" id="report">Write a report</button>
        <button type="button" id="case">Save as a case</button>
        <button type="button" id="new">New trace</button>
      </aside>
    </div>
  </section>

  <!-- ===== Map ===== -->
  <section id="view-map" class="view" hidden>
    <div class="row"><h1 class="title">Map</h1><span class="grow"></span><span id="map-when" class="muted small"></span></div>
    <div class="two">
      <div class="main">
        <div id="cluster-block" hidden>
          <h2>Kubernetes</h2>
          <div id="clusters" class="cards"></div>
        </div>
        <h2>Hosts</h2>
        <div id="hostcards" class="cards hosts"></div>
        <div class="legend muted small">
          <span><i class="sq running"></i>running</span>
          <span><i class="sq restarting"></i>restarting or unhealthy</span>
          <span><i class="sq exited"></i>stopped</span>
          <span>Each square is one container. Select one to see it.</span>
        </div>
      </div>
      <aside id="map-side" class="side card" aria-label="Details">
        <div id="problems-pane">
          <h2>Open problems <span id="problem-count" class="warn"></span></h2>
          <div id="problems" class="list"></div>
        </div>
        <div id="container-pane" hidden>
          <div class="row"><span id="ct-where" class="muted small"></span><span class="grow"></span>
            <button type="button" id="ct-close">Back to problems</button></div>
          <h2 id="ct-name" class="title small-title"></h2>
          <p id="ct-status"></p>
          <dl class="facts">
            <dt>Image</dt><dd id="ct-image" class="mono"></dd>
            <dt>Restarts</dt><dd id="ct-restarts"></dd>
            <dt>Started</dt><dd id="ct-started"></dd>
          </dl>
          <h3 class="label">Changed in the last day</h3>
          <div id="ct-changes" class="small"></div>
          <h3 class="label">Last log lines</h3>
          <div class="logtools"><span class="grow"></span>
            <button type="button" id="ct-errors" aria-pressed="false" title="Only errors and warnings, with the lines under them">Errors only</button>
            <button type="button" id="ct-wrap" aria-pressed="true" title="Wrap long lines; off keeps one row per line">Wrap</button></div>
          <div id="ct-logs" class="logs" role="log" aria-label="Log lines"></div>
          <button type="button" id="ct-trace" class="primary">Trace this</button>
          <p class="muted small">Starts a trace about this container on its host.</p>
        </div>
        <div id="space-pane" hidden>
          <div class="row"><span id="sp-where" class="muted small"></span><span class="grow"></span>
            <button type="button" id="sp-close">Back to problems</button></div>
          <h2 class="title small-title"><span id="sp-name"></span> <span id="sp-count" class="warn small"></span></h2>
          <p id="sp-sub" class="muted small"></p>
          <div id="sp-list"></div>
          <p class="muted small">Select a pod to see its log lines. The list is read again every minute while it is open.</p>
        </div>
        <div id="pod-pane" hidden>
          <div class="row"><span id="pd-where" class="muted small"></span><span class="grow"></span>
            <button type="button" id="pd-back">Back</button></div>
          <h2 id="pd-name" class="title small-title"></h2>
          <p id="pd-status"></p>
          <dl class="facts">
            <dt>Image</dt><dd id="pd-image" class="mono"></dd>
            <dt>Node</dt><dd id="pd-node"></dd>
            <dt>Started</dt><dd id="pd-started"></dd>
            <dt>Restarts</dt><dd id="pd-restarts"></dd>
          </dl>
          <h3 class="label">Log lines</h3>
          <div class="logtools">
            <button type="button" id="pd-now" aria-pressed="true" title="The log of the container running now">Logs</button>
            <button type="button" id="pd-prev" aria-pressed="false" title="The log of the run before the last restart: where a crash usually shows">Previous run</button>
            <span class="grow"></span>
            <button type="button" id="pd-errors" aria-pressed="false" title="Only errors and warnings, with the lines under them">Errors only</button>
            <button type="button" id="pd-wrap" aria-pressed="true" title="Wrap long lines; off keeps one row per line">Wrap</button></div>
          <div id="pd-logs" class="logs" role="log" aria-label="Pod log lines"></div>
          <button type="button" id="pd-trace" class="primary">Trace this</button>
          <p id="pd-trace-note" class="muted small"></p>
        </div>
      </aside>
    </div>
  </section>

  <!-- ===== What changed ===== -->
  <section id="view-changes" class="view" hidden>
    <div class="row">
      <h1 class="title">What changed</h1>
      <span class="grow"></span>
      <div class="seg" role="group" aria-label="Period">
        <button type="button" data-hours="1">1 h</button>
        <button type="button" data-hours="6">6 h</button>
        <button type="button" data-hours="24">24 h</button>
        <button type="button" data-hours="168">7 d</button>
      </div>
      <label>Find <input id="changes-find" type="search" placeholder="name or word"></label>
    </div>
    <div class="card river-box">
      <div id="river" class="river"></div>
      <div class="legend muted small">
        <span><i class="mk change"></i>a change</span>
        <span><i class="mk problem"></i>a problem starts</span>
        <span><i class="mk cleared"></i>ok again</span>
        <span><i class="mk traced"></i>auto-trace done</span>
        <span><i class="joint-key"></i>change → problem link</span>
        <span><i class="gap"></i>not recording</span>
        <span id="changes-note"></span>
      </div>
    </div>
    <div id="picked-bar" class="card picked" hidden>
      <span id="picked-count"></span>
      <span class="grow"></span>
      <button type="button" id="picked-trace" class="primary">Trace these</button>
      <button type="button" id="picked-clear">Clear</button>
    </div>
    <div id="changes" class="list"></div>
  </section>

  <!-- ===== Wall screen ===== -->
  <section id="view-wall" class="wall" hidden>
    <div class="wallhead">
      <span class="row nogap"><span class="brand">TRACER</span><span class="trail-mark" aria-hidden="true"><i></i><b></b><i></i><b></b><i></i></span></span>
      <div id="wall-state" class="state" role="status"><span class="ring"></span><span id="wall-words">Loading…</span></div>
      <div class="clock"><b id="wall-clock">--:--</b><span id="wall-checked"></span></div>
    </div>
    <div class="wallbody">
      <div class="wallmain">
        <div id="wall-k8s-block"><h2>Kubernetes</h2><div id="wall-k8s" class="wcards"></div><p id="wall-k8s-more" class="muted small"></p></div>
        <div><h2>Hosts</h2><div id="wall-hosts" class="wcards"></div></div>
      </div>
      <div class="wallside">
        <h2>Needs a look</h2><div id="wall-need" class="need"></div>
        <h2>Last hour</h2><div id="wall-recent" class="recent"></div>
      </div>
    </div>
    <div class="walltail"><span>Wall screen · look only · refreshed every 30 s</span><span class="grow"></span><span>Esc or a mouse move shows the menu</span></div>
  </section>

  <!-- ===== Library ===== -->
  <section id="view-library" class="view" hidden>
    <h1 class="title">Library</h1>
    <div class="two">
      <div class="side-left">
        <div class="seg" role="group" aria-label="Kind">
          <button type="button" data-kind="cases">Cases</button>
          <button type="button" data-kind="runbooks">Runbooks</button>
          <button type="button" data-kind="reports">Reports</button>
        </div>
        <label class="wide"><span>Find</span><input id="library-find" type="search" placeholder="error text or app"></label>
        <div id="library-list" class="list"></div>
      </div>
      <div class="main card">
        <div class="row"><h2 id="doc-title">Select an item</h2><span class="grow"></span>
          <button type="button" id="doc-edit" hidden>Edit</button>
          <button type="button" id="doc-delete" hidden>Delete</button></div>
        <p id="doc-note" class="muted"></p>
        <pre id="doc" class="mono doc"></pre>
      </div>
    </div>
  </section>
</div>

<div id="dialog" class="veil" hidden>
  <div class="card dialog" role="dialog" aria-modal="true" aria-labelledby="dialog-title">
    <h2 id="dialog-title"></h2>
    <p id="dialog-text"></p>
    <textarea id="dialog-input" rows="14" hidden aria-label="Text"></textarea>
    <div id="dialog-buttons" class="row end"></div>
  </div>
</div>
<script src="/app.js"></script>
</body>
</html>
'''

CSS = r'''
:root{color-scheme:dark;--ground:#0b0f14;--top:#0e131a;--panel:#121821;--deep:#0a0e13;--line:#1e2733;--line2:#2a3442;
--text:#e6edf5;--muted:#9aa7b6;--cyan:#33e1ff;--cyan2:#2fb4ff;--blue:#3d7bff;--blue-text:#9cc0ff;--cyan-text:#7fe9ff;
--ink:#06202a;--warn:#ffb547;--warn-text:#ffcf8a;--warn-ground:#2a2113;--ok:#4fd1a5;--bad:#ffb3a7;--run:#1c5a4a;
--mono:ui-monospace,"JetBrains Mono","SFMono-Regular",Menlo,Consolas,monospace}
*{box-sizing:border-box}
html,body{margin:0;background:var(--ground);color:var(--text);font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
[hidden]{display:none!important}
h1,h2,h3{margin:0}
h2{font-size:15px;font-weight:600}
p{margin:0}
a{color:var(--cyan-text)}
button,select,input,textarea{font:inherit;color:var(--text)}
button{min-height:40px;padding:0 14px;border-radius:8px;border:1px solid var(--line2);background:none;cursor:pointer}
button:hover{border-color:#55606e}
button:focus-visible,select:focus-visible,input:focus-visible,textarea:focus-visible{outline:2px solid var(--cyan);outline-offset:2px}
button:disabled{opacity:.5;cursor:default}
button.primary{background:var(--cyan);color:var(--ink);border:0;font-weight:600}
button.danger{border-color:#ff9d8f;color:var(--bad);font-weight:600}
select,input,textarea{background:var(--panel);border:1px solid var(--line2);border-radius:8px;padding:8px 10px;min-height:40px}
textarea{width:100%;resize:vertical}
label{color:var(--muted);display:inline-flex;gap:8px;align-items:center}
label.wide{display:flex;flex-direction:column;align-items:stretch;gap:4px;font-size:12px}
.mono{font-family:var(--mono);font-size:12.5px}
.small{font-size:12.5px}
.muted{color:var(--muted)}
.warn{color:var(--warn)}
.okc{color:var(--ok)}
.error{color:var(--bad);min-height:1.4em}
.grow{flex:1 1 20px}
.row{display:flex;flex-wrap:wrap;gap:10px;align-items:center}
.row.end{justify-content:flex-end}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.chip{padding:6px 10px;border:1px solid var(--line2);border-radius:6px;color:var(--muted)}
.count{background:var(--warn);color:#20170a;border-radius:999px;padding:0 7px;font-size:12px;font-weight:600}
.tag{padding:2px 8px;border-radius:999px;background:#13303a;color:var(--cyan-text);font-size:12px;white-space:nowrap}
.tag.hot{background:#3a2a10;color:var(--warn-text)}
.tag.good{background:#123327;color:var(--ok)}
.label{margin-top:6px;font-size:11.5px;letter-spacing:.6px;text-transform:uppercase;color:var(--muted);font-weight:600}
.title{font-size:22px;font-weight:700;letter-spacing:.2px}
.small-title{font-size:18px}
.box{background:var(--deep);border-radius:8px;padding:10px 12px;white-space:pre;overflow:auto;max-height:260px}

/* brand */
.brand{font-family:var(--mono);font-weight:700;font-size:19px;letter-spacing:3px;color:var(--cyan)}
.brand.big{font-size:30px;text-align:center}
.trail-mark{display:inline-flex;align-items:center}
.trail-mark i{width:7px;height:7px;border-radius:50%;background:var(--cyan)}
.trail-mark i:nth-of-type(2){background:var(--cyan2)}.trail-mark i:nth-of-type(3){background:var(--blue)}
.trail-mark b{width:14px;height:1px;background:#3a4656}
.trail-mark em{width:0;height:0;border-left:8px solid var(--blue);border-top:5px solid transparent;border-bottom:5px solid transparent}
.login .trail-mark{justify-content:center;margin-bottom:8px}
.login .trail-mark b{width:30px}

.top{display:flex;flex-wrap:wrap;gap:14px;align-items:center;padding:10px 22px;border-bottom:1px solid var(--line);background:var(--top)}
nav{display:flex;flex-wrap:wrap;gap:4px}
nav button{border:0;color:var(--muted)}
nav button[aria-current="page"]{background:#16202c;color:var(--text);font-weight:500}
.login{max-width:360px;margin:12vh auto;display:flex;flex-direction:column;gap:12px}
.login label{display:flex;flex-direction:column;align-items:stretch;gap:4px}
.view{padding:18px 22px;display:flex;flex-direction:column;gap:16px}
.two{display:flex;flex-wrap:wrap;align-items:flex-start;gap:18px}
.main{flex:999 1 560px;min-width:0;display:flex;flex-direction:column;gap:12px}
.side{flex:1 1 400px;min-width:0;display:flex;flex-direction:column;gap:10px;position:sticky;top:12px;max-height:calc(100vh - 24px);overflow:auto}
.side-left{flex:1 1 340px;min-width:0;display:flex;flex-direction:column;gap:10px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;margin:0}
.facts{display:grid;grid-template-columns:74px minmax(0,1fr);gap:8px 12px;margin:0;font-size:13.5px}
.facts dt{color:var(--muted)}
.facts dd{margin:0;overflow-wrap:anywhere}
.legend{display:flex;flex-wrap:wrap;gap:16px;align-items:center}
.legend span{display:inline-flex;gap:6px;align-items:center}

/* trace */
.trail{list-style:none;margin:0;padding:16px 18px;border:1px solid var(--line);border-radius:12px;background:var(--top);display:flex;flex-wrap:wrap;align-items:center;row-gap:12px}
.stage{display:flex;align-items:center;gap:10px;flex:0 1 auto;min-width:0}
.stage>span:last-child{display:flex;flex-direction:column;min-width:0}
.stage .detail{color:var(--muted);font-size:12.5px;max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.stage .dot{width:30px;height:30px;flex:0 0 30px;border-radius:50%;border:2px solid #3a4656;color:var(--muted);display:flex;align-items:center;justify-content:center;font-weight:700}
.stage.done .dot{background:var(--cyan);border-color:var(--cyan);color:var(--ink)}
#stage-2.done .dot{background:var(--cyan2);border-color:var(--cyan2)}
#stage-3.done .dot,#stage-4.done .dot{background:var(--blue);border-color:var(--blue);color:#fff}
.stage.now .dot{border-color:var(--blue);color:var(--blue-text)}
.stage.now .detail{color:var(--blue-text)}
.link{flex:1 1 30px;min-width:20px;height:0;border-top:2px dashed #3a4656;margin:0 14px}
.link.done{border-top:2px solid var(--cyan2)}
.talk{display:flex;flex-direction:column;gap:12px;min-height:120px}
.bubble{align-self:flex-end;max-width:80%;background:#16324a;border-radius:12px 12px 4px 12px;padding:10px 14px;white-space:pre-wrap;overflow-wrap:anywhere}
.turn{display:flex;flex-direction:column;gap:10px}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chips button{min-height:32px;padding:2px 12px;border-radius:999px;border-color:#23415a;color:var(--cyan-text);font-family:var(--mono);font-size:12px}
.chips button.run{border-color:#2a3a63;color:var(--blue-text)}
.chips button[aria-pressed="true"]{background:#13303a}
.evidence{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 14px;display:flex;flex-direction:column;gap:8px}
.evidence .head{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.evidence .tag{border-radius:4px;font-weight:600;letter-spacing:.5px}
.answer{background:#0f1a2b;border:1px solid #2a3a63;border-radius:12px;padding:12px 16px;display:flex;flex-direction:column;gap:8px;overflow-wrap:anywhere}
.answer:empty{display:none}
.answer p{white-space:pre-wrap}
.answer p.key{border-left:0;background:#13213a;border-radius:8px;padding:8px 10px}
.answer h3{margin:6px 0 0;font-size:12px;text-transform:uppercase;letter-spacing:.6px;color:var(--muted);font-weight:600}
.answer ul{margin:0;padding-left:20px}
.answer code{background:var(--deep);border-radius:4px;padding:1px 5px}
.answer pre{background:var(--deep);border-radius:6px;padding:10px 12px;overflow-x:auto;white-space:pre}
.notice{color:var(--warn)}
.info{color:var(--ok)}
.ask{display:flex;flex-direction:column;gap:8px;position:sticky;bottom:0;background:var(--ground);padding:10px 0}

/* map */
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px}
.cards.hosts{grid-template-columns:repeat(auto-fill,minmax(270px,1fr))}
.cards .card{display:flex;flex-direction:column;gap:10px}
.cards .card.hot{border-color:var(--warn)}
.cards .card.hub{border-color:var(--cyan)}
.cards .card.stale{border-color:var(--warn);border-style:dashed}
.cards .card.flash{box-shadow:0 0 0 2px var(--cyan)}
.cardlinks{display:flex;flex-wrap:wrap;align-items:center;gap:6px}
.spaces{display:flex;flex-wrap:wrap;gap:2px 10px;margin-top:4px}
#map-side.wide{flex:0 1 640px}
.spaces button{min-height:28px;padding:0 9px;border-radius:6px;font-size:12.5px;color:var(--cyan-text)}
.spaces button.hot{color:var(--warn-text);border-color:#8a6526;background:var(--warn-ground)}
.spaces button[aria-pressed="true"]{outline:2px solid var(--cyan);outline-offset:1px}
.spaces .sys{min-height:28px;padding:0 6px;border:0;color:var(--muted);text-decoration:underline;font-size:12px;text-align:left}
#container-pane,#space-pane,#pod-pane{display:flex;flex-direction:column;gap:10px}
.wl{display:flex;flex-direction:column;gap:6px;padding:8px 0;border-top:1px solid var(--line)}
.wl:first-child{border-top:0}
.wlhead{display:flex;justify-content:space-between;gap:8px;font-size:13px}
.pod{display:grid;grid-template-columns:16px minmax(0,1fr);gap:8px;align-items:center;text-align:left;width:100%;
 min-height:40px;padding:4px 8px;border-radius:8px;border:1px solid var(--line);background:var(--deep);font-size:13px}
.pod .n{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:var(--mono);font-size:12.5px}
.pod.hot{background:var(--warn-ground);border-color:#8a6526}
.pod.old{opacity:.65}
.logtools{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.logtools button{min-height:30px;padding:0 10px;font-size:12.5px}
.logtools button[aria-pressed="true"]{background:#16202c;border-color:var(--cyan2);color:var(--cyan-text)}
.logs{background:var(--deep);border-radius:8px;padding:8px 10px;max-height:420px;overflow-y:auto;overflow-x:hidden;font-family:var(--mono);font-size:12px;line-height:1.45}
.logs.nowrap{overflow-x:auto}
.ll{display:grid;grid-template-columns:62px minmax(0,1fr);gap:0 8px;padding:1px 0}
.ll .t{color:var(--muted)}
.ll .m{white-space:pre-wrap;overflow-wrap:anywhere}
.logs.nowrap .ll .m{white-space:pre;overflow-wrap:normal}
.ll.err .m{color:var(--bad)}
.ll.wrn .m{color:var(--warn-text)}
.ll.more .m{padding-left:12px}
.cardlinks button{min-height:32px;padding:0 10px;border-radius:6px;color:var(--cyan-text);font-size:13px}
.cards .head{display:flex;justify-content:space-between;gap:8px;align-items:baseline}
.cards .name{font-weight:700;font-size:16px;overflow-wrap:anywhere}
.squares{display:flex;flex-wrap:wrap;gap:5px}
.squares button,.sq{display:inline-block;width:26px;height:26px;min-height:0;padding:0;border-radius:6px;border:2px solid transparent}
.sq{width:14px;height:14px;border-radius:3px}
.running{background:var(--run)}
.restarting,.unhealthy{background:#3a2a10;border:2px solid var(--warn)!important}
.exited,.created,.dead,.paused,.removing{background:none;border:2px dashed #55606e!important}
.squares button[aria-pressed="true"]{outline:2px solid var(--cyan);outline-offset:2px}
.meters{display:grid;grid-template-columns:52px minmax(0,1fr) 44px;gap:6px 8px;align-items:center;font-size:12.5px;color:var(--muted)}
.meter{height:6px;border-radius:3px;background:var(--line)}
.meter span{display:block;height:6px;border-radius:3px;background:var(--cyan2)}
.meter span.high{background:var(--warn)}
.list{display:flex;flex-direction:column;gap:8px}
.item{display:flex;flex-wrap:wrap;gap:10px;align-items:center;text-align:left;width:100%;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.side .item{background:var(--deep)}
.item.hot{background:var(--warn-ground);border-color:#8a6526}
.item .what{flex:1 1 200px;min-width:0;overflow-wrap:anywhere}
.item .when{min-width:56px;color:var(--muted)}
.item.pick{cursor:pointer;text-align:left;width:100%;min-height:0;flex-direction:column;align-items:stretch;gap:2px}
.item.pick[aria-pressed="true"]{border-color:var(--cyan);background:#13303a}
.item[aria-pressed="true"]{border-color:var(--blue);background:#13213a}

/* river */
.seg{display:flex;border:1px solid var(--line2);border-radius:8px;overflow:hidden}
.seg button{border:0;border-radius:0;color:var(--muted)}
.seg button[aria-pressed="true"]{background:#16202c;color:var(--text);font-weight:500}
.river-box{display:flex;flex-direction:column;gap:12px;background:var(--top)}
.river{overflow-x:auto}
.river-grid{min-width:720px;display:grid;grid-template-columns:130px minmax(0,1fr)}
.river-grid .times{display:flex;justify-content:space-between;color:var(--muted);font-family:var(--mono);font-size:12px;padding-bottom:8px}
.lane-name{padding:14px 8px 0 0;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;border-top:1px solid var(--line)}
.track{position:relative;height:50px;border-top:1px solid var(--line)}
.track::before{content:"";position:absolute;left:0;right:0;top:24px;height:2px;background:var(--line)}
.gap,.track .off{background:repeating-linear-gradient(135deg,#1e2733 0 6px,transparent 6px 12px)}
.legend .gap{display:inline-block;width:18px;height:12px;border-radius:3px}
.track .off{position:absolute;top:8px;height:34px;border-radius:6px}
.mk{position:absolute;min-height:0;padding:0;border:0;cursor:pointer;transform:translateX(-50%)}
.legend .mk{position:static;transform:none;display:inline-block}
.mk.change{top:17px;width:16px;height:16px;border-radius:50%;background:var(--blue)}
.mk.cluster{top:19px;width:12px;height:12px;border-radius:50%;background:var(--blue-text)}
.mk.problem{top:14px;width:0;height:0;border-radius:0;background:none;border-left:10px solid transparent;border-right:10px solid transparent;border-bottom:18px solid var(--warn)}
.mk.cleared{top:17px;width:16px;height:16px;border-radius:50%;background:none;border:3px solid var(--ok)}
.legend .mk.change,.legend .mk.cleared{width:12px;height:12px}
.legend .mk.problem{border-left-width:7px;border-right-width:7px;border-bottom-width:12px}
.mk[aria-pressed="true"]{outline:3px solid var(--cyan);outline-offset:3px}
.joint{position:absolute;top:24px;height:0;border-top:3px solid var(--ok);pointer-events:none}
.joint.maybe{border-top:3px dashed var(--warn)}
.joint-key{display:inline-block;width:22px;border-top:3px solid var(--ok)}
.mk.traced{top:18px;width:12px;height:12px;border-radius:2px;background:#a58bf0;transform:translateX(-50%) rotate(45deg)}
.legend .mk.traced{transform:rotate(45deg)}
.linkline{font-size:12.5px;color:var(--ok)}
.linkline.maybe{color:var(--warn-text)}
.traceline{font-size:12.5px;color:#c3b2f7}
.auto-note{display:flex;flex-wrap:wrap;gap:10px;align-items:center;border-color:#7c5cd6;background:#17142a}
.tag.auto{background:#241c40;color:#c3b2f7}
.picked{display:flex;flex-wrap:wrap;gap:10px;align-items:center;border-color:var(--blue)}

/* top bar: icon tabs, glowing underline, status pill */
.top{min-height:60px;padding-top:0;padding-bottom:0;background:linear-gradient(180deg,#0f1620,#0b1017)}
nav{align-self:stretch}
nav button{display:inline-flex;align-items:center;gap:8px;position:relative;border-radius:0;min-height:60px;padding:0 14px;font-weight:500}
nav button:hover{color:var(--text)}
nav button[aria-current="page"]{background:none;color:var(--text)}
nav button[aria-current="page"]::after{content:"";position:absolute;left:10px;right:10px;bottom:-1px;height:3px;border-radius:3px 3px 0 0;
 background:linear-gradient(90deg,var(--cyan),var(--blue));box-shadow:0 0 12px rgba(51,225,255,.7)}
nav button[aria-current="page"] .ic{color:var(--cyan)}
.ic{width:18px;height:18px;flex:none;fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}
.live{display:inline-flex;align-items:center;gap:5px;border:1px solid #1d5d6b;border-radius:999px;padding:0 7px;font-size:11.5px;font-weight:600;color:var(--cyan)}
.live i{width:6px;height:6px;border-radius:50%;background:var(--cyan);box-shadow:0 0 6px var(--cyan)}
.pill{display:inline-flex;align-items:center;gap:8px;min-height:34px;border-radius:999px;padding:0 12px;font-size:13px}
.pill.bad{color:var(--warn-text);border-color:#8a6526;background:var(--warn-ground)}
.pill.good{color:var(--ok);border-color:#1f5a47;background:#0f241d}
.pill b{font-size:15px}
.checked-note.late{color:var(--warn)}
.wallbtn{display:inline-flex;align-items:center;gap:6px;min-height:34px;padding:0 12px;font-size:13px;color:var(--cyan-text)}
.nogap{gap:0}
.lbl.short{display:none}
/* wall screen */
body.walling .top{position:fixed;inset:0 0 auto;z-index:8;transform:translateY(-100%);transition:transform .2s}
body.walling.peek .top{transform:none}
.wall{min-height:100vh;display:flex;flex-direction:column;background:radial-gradient(1200px 500px at 50% -200px,#13202d,var(--ground))}
.wallhead{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:12px;padding:18px 32px;border-bottom:1px solid var(--line)}
.wallhead .brand{font-size:24px}
.wallhead .trail-mark{margin-left:12px}
.state{display:flex;align-items:center;gap:14px;font-size:34px;font-weight:800;letter-spacing:.5px;color:var(--muted)}
.state.bad{color:var(--warn)}
.state.good{color:var(--ok)}
.ring{width:20px;height:20px;border-radius:50%;background:currentColor;box-shadow:0 0 0 6px rgba(154,167,182,.15),0 0 24px currentColor}
.clock{justify-self:end;text-align:right}
.clock b{font-size:34px;font-family:var(--mono);font-weight:600;letter-spacing:1px;font-variant-numeric:tabular-nums}
.clock span{display:block;color:var(--muted);font-size:13px}
.clock span.late{color:var(--warn)}
.wallbody{flex:1;display:grid;grid-template-columns:minmax(0,1fr) minmax(300px,460px);gap:24px;padding:22px 32px}
.wallmain,.wallside{display:flex;flex-direction:column;gap:22px;min-width:0}
.wallside{gap:10px}
.wall h2{font-size:13px;letter-spacing:1.4px;text-transform:uppercase;color:var(--muted);margin-bottom:10px}
.wallside h2{margin:0}
.wallside h2+div{margin-bottom:12px}
.wcards{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:14px}
.wcard{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px 18px;display:flex;flex-direction:column;gap:8px;min-width:0}
.wcard.hot{border-color:var(--warn);box-shadow:0 0 0 1px var(--warn),0 0 22px rgba(255,181,71,.18)}
.wcard.stale{border:1px dashed var(--warn)}
.wcard .top2{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.wcard .nm{font-size:19px;font-weight:700;overflow-wrap:anywhere}
.wcard .st{font-size:14px;font-weight:600;white-space:nowrap}
.wcard .ln{color:var(--muted);font-size:14px;overflow-wrap:anywhere}
.kind{font-size:11px;letter-spacing:1px;text-transform:uppercase;color:var(--cyan-text)}
.big{font-size:30px;font-weight:800;font-family:var(--mono);line-height:1;font-variant-numeric:tabular-nums}
.big small{font-size:14px;color:var(--muted);font-family:var(--sans,system-ui);font-weight:500}
.wsq{display:flex;flex-wrap:wrap;gap:4px}
.wsq i{width:16px;height:16px;border-radius:4px;background:var(--run)}
.wsq i.u{background:#3a2a10;outline:2px solid var(--warn);outline-offset:-2px}
.wsq i.x{background:none;outline:2px dashed #55606e;outline-offset:-2px}
.need{display:flex;flex-direction:column;gap:12px}
.nitem{background:var(--warn-ground);border:1px solid #8a6526;border-radius:12px;padding:14px 16px;display:flex;flex-direction:column;gap:6px}
.nitem .t1{font-size:17px;font-weight:600;overflow-wrap:anywhere}
.nitem .t2{color:var(--warn-text);font-size:13.5px}
.nitem .t3{color:var(--muted);font-size:13px}
.recent{display:flex;flex-direction:column;gap:6px;font-size:13.5px;color:var(--muted)}
.recent div{overflow-wrap:anywhere}
.recent .tm{font-family:var(--mono);color:#6f7d8d;margin-right:8px}
.walltail{padding:10px 32px;border-top:1px solid var(--line);color:var(--muted);font-size:13px;display:flex;flex-wrap:wrap;gap:12px 24px}
@media (prefers-reduced-motion:reduce){body.walling .top{transition:none}}

.doc{max-height:70vh;overflow:auto}
.veil{position:fixed;inset:0;background:rgba(5,8,12,.72);display:flex;align-items:center;justify-content:center;padding:16px;z-index:10}
.dialog{width:min(720px,100%);max-height:90vh;overflow:auto;display:flex;flex-direction:column;gap:12px}
@media (max-width:720px){.side{position:static;max-height:none}.bubble{max-width:100%}.ask{position:static}
.link{display:none}.trail{flex-direction:column;align-items:flex-start}.view{padding:14px 16px}
 .top{gap:10px;padding:0 16px;min-height:56px}
 #who,#top-checked,#wall-open{display:none}
 .lbl.long{display:none}.lbl.short{display:inline}
 .pill .pw{display:none} #sign-out{min-height:34px;padding:0 10px;font-size:13px}
 nav{position:fixed;left:0;right:0;bottom:0;z-index:7;background:#0e141c;border-top:1px solid var(--line);justify-content:space-around;
  padding:0 4px env(safe-area-inset-bottom,0px);align-self:auto}
 nav button{flex:1;flex-direction:column;gap:2px;min-height:60px;padding:6px 2px 0;font-size:11px;justify-content:center}
 nav button .count,nav button .live{position:absolute;top:4px;left:50%;margin-left:6px;font-size:10px;padding:0 5px}
 nav button[aria-current="page"]::after{top:0;bottom:auto;border-radius:0 0 3px 3px}
 #app{padding-bottom:70px}
 .wallhead{grid-template-columns:1fr;justify-items:start;padding:14px 16px}.clock{justify-self:start;text-align:left}
 .state{font-size:24px}.wallbody{grid-template-columns:minmax(0,1fr);padding:16px}.walltail{padding:10px 16px}}
'''

JS = r'''"use strict";
(function () {
  var $ = function (id) { return document.getElementById(id); };
  var each = function (sel, fn) { Array.prototype.forEach.call(document.querySelectorAll(sel), fn); };
  var me = null, chat = null, next = 0, busy = false, polling = false;
  var turn = null, raw = "", drawTimer = 0, health = null, timer = 0, view = "trace";
  var trace = null, hours = 6, kind = "cases", library = null, openDoc = null, picked = {}, changes = null, chosen = null;
  var space = null, spaceAt = 0, podOpen = null, showSystem = {};   // the namespace and pod open on the Map

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  function clear(e) { while (e.firstChild) e.removeChild(e.firstChild); }
  // the same words as links.say on the server
  function linkText(l) {
    var when = l.gap < 60 ? "at about the same time as" : Math.round(l.gap / 60) + " min after";
    return (l.strength === "maybe" ? "maybe: " : "") + when + ": " + l.text;
  }
  function short(text, n) { text = String(text || "").replace(/\s+/g, " ").trim(); return text.length > n ? text.slice(0, n - 1) + "…" : text; }

  function api(method, path, body) {
    var opt = { method: method, credentials: "same-origin", headers: { "X-Requested-With": "troubleshooter" } };
    if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
    return fetch(path, opt).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (r.status === 401 && path !== "/api/login") { showLogin(); throw new Error("Sign in first."); }
        if (!r.ok) throw new Error(data.error || "The request failed (" + r.status + ").");
        return data;
      });
    });
  }

  // ---------- in-page dialog: every confirm and notice goes through here ----------
  function dialog(o) {
    return new Promise(function (resolve) {
      var back = document.activeElement;
      $("dialog-title").textContent = o.title;
      $("dialog-text").textContent = o.text || "";
      var input = $("dialog-input");
      input.hidden = o.input === undefined;
      input.value = o.input || "";
      var row = $("dialog-buttons");
      clear(row);
      (o.buttons || [{ label: "Close", value: true }]).forEach(function (b) {
        var btn = el("button", b.cls || "", b.label);
        btn.type = "button";
        btn.addEventListener("click", function () {
          $("dialog").hidden = true;
          document.removeEventListener("keydown", onKey);
          if (back && back.focus) back.focus();
          resolve(b.value === "input" ? input.value : b.value);
        });
        row.appendChild(btn);
      });
      function onKey(e) { if (e.key === "Escape") { row.lastChild.click(); } }
      document.addEventListener("keydown", onKey);
      $("dialog").hidden = false;
      (o.input === undefined ? row.firstChild : input).focus();
    });
  }
  function tell(title, text) { return dialog({ title: title, text: text }); }
  function failed(e) { tell("That did not work", e.message); }

  // ---------- time ----------
  function two(n) { return (n < 10 ? "0" : "") + n; }
  function clock(seconds) {
    var d = new Date(seconds * 1000), now = new Date();
    var t = two(d.getHours()) + ":" + two(d.getMinutes());
    if (d.toDateString() === now.toDateString()) return t;
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" }) + " " + t;
  }
  function ago(seconds) {
    var s = Math.max(0, Math.floor(Date.now() / 1000 - seconds));
    if (s < 90) return s + " s";
    if (s < 5400) return Math.round(s / 60) + " min";
    if (s < 129600) return Math.round(s / 3600) + " h";
    return Math.round(s / 86400) + " days";
  }

  // ---------- signing in ----------
  function showLogin() {
    $("app").hidden = true;
    $("login").hidden = false;
    $("login-user").focus();
  }
  $("login-form").addEventListener("submit", function (e) {
    e.preventDefault();
    $("login-error").textContent = "";
    api("POST", "/api/login", { user: $("login-user").value, password: $("login-password").value })
      .then(function () { $("login-password").value = ""; start(); })
      .catch(function (err) { $("login-error").textContent = err.message; });
  });
  $("sign-out").addEventListener("click", function () {
    api("POST", "/api/logout", {}).then(function () { sessionStorage.removeItem("chat"); location.reload(); }).catch(failed);
  });

  function start() {
    api("GET", "/api/me").then(function (data) {
      me = data;
      $("login").hidden = true;
      $("app").hidden = false;
      $("who").textContent = me.user + " · " + me.host + (me.model ? " · " + me.model : "");  // the model only with web-show-model "on"
      fill($("host"), [""].concat(me.hosts), function (h) { return h || "any"; });
      $("host-pick").hidden = me.hosts.length < 2;
      fill($("context"), me.contexts, function (c) { return c; });
      $("context-pick").hidden = !me.kubernetes;
      show(location.hash.slice(1) || "map");
      openChat();
      refresh();
      clearInterval(timer);
      timer = setInterval(refresh, 30000);
    }).catch(function () { showLogin(); });
  }
  function fill(select, values, label) {
    clear(select);
    values.forEach(function (v) { var o = el("option", "", label(v)); o.value = v; select.appendChild(o); });
  }

  // ---------- views ----------
  var views = ["map", "trace", "changes", "wall", "library"];
  var old = { chat: "trace", health: "map", console: "wall" };
  function show(name) {
    name = old[name] || name;
    if (views.indexOf(name) < 0) name = "map";
    view = name;
    views.forEach(function (v) { $("view-" + v).hidden = v !== name; });
    document.body.classList.toggle("walling", name === "wall");
    document.body.classList.remove("peek");
    if (name !== "wall" && document.fullscreenElement) document.exitFullscreen().catch(function () {});
    each("nav button", function (b) {
      if (b.dataset.view === name) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
    });
    if (location.hash.slice(1) !== name) history.replaceState(null, "", "#" + name);
    if (name === "map" && health) drawMap();
    if (name === "changes") loadChanges();
    if (name === "wall") drawWall();
    if (name === "library") loadLibrary();
  }
  window.addEventListener("hashchange", function () { var want = location.hash.slice(1); if (me && want && want !== view) show(want); });
  each("nav button", function (b) { b.addEventListener("click", function () { show(b.dataset.view); }); });

  // Every 30 s: health for the map and the badge; the console also wants the changes.
  function refresh() {
    if (!me) return;
    api("GET", "/api/health").then(function (h) {
      health = h;
      if (h.contexts && h.contexts.join("\n") !== (me.contexts || []).join("\n")) {   // a login came or went since the page loaded
        var was = $("context").value;
        me.contexts = h.contexts; me.kubernetes = h.contexts.length > 0;
        fill($("context"), me.contexts, function (c) { return c; });
        if (me.contexts.indexOf(was) >= 0) $("context").value = was;
        $("context-pick").hidden = !me.kubernetes;
      }
      var n = h.problems.length;
      $("map-count").hidden = !n; $("map-count").textContent = n;
      drawTop();
      if (view === "map") drawMap();
      if (view === "map" && space && !$("space-pane").hidden && Date.now() - spaceAt > 55000) loadSpace();
      if (view === "wall") drawWall();
    }).catch(function () { drawTop(true); });
  }

  // ================= Trace =================
  function freshTrace() { trace = { title: "", started: 0, steps: 0, cause: "", fix: "", saved: false }; drawTrail(); }
  function openChat(id) {
    var saved = id || sessionStorage.getItem("chat");
    var got = saved ? Promise.resolve({ id: saved }) : api("POST", "/api/chat", {});
    got.then(function (c) {
      chat = c.id; next = 0;
      sessionStorage.setItem("chat", chat);
      $("trace-auto").hidden = true;
      clear($("talk"));
      turn = null;
      freshTrace();
      poll();
    }).catch(failed);
  }
  function poll() {
    if (polling || !chat) return;
    polling = true;
    api("GET", "/api/chat/" + chat + "/events?after=" + next).then(function (data) {
      polling = false;
      data.events.forEach(handle);
      next = data.next;
      setBusy(data.busy);
      if (data.context && $("context").value !== data.context) $("context").value = data.context;
      $("trace-auto").hidden = !data.auto;
      if (!data.auto) sessionStorage.setItem("mine", chat);
      if (data.busy) poll();
    }).catch(function (err) {
      polling = false;
      if (/gone/.test(err.message)) {
        sessionStorage.removeItem("chat");
        if (sessionStorage.getItem("mine") === chat) sessionStorage.removeItem("mine");
        openChat(sessionStorage.getItem("mine") || undefined);
      }
      else if (busy) setTimeout(poll, 3000);
    });
  }
  function setBusy(b) {
    busy = b;
    drawTop();
    $("ask").disabled = b; $("report").disabled = b; $("case").disabled = b; $("new").disabled = b;
    $("context").disabled = b;
    $("stop").hidden = !b;
    drawTrail();
  }
  function newTurn() {
    var box = el("div", "turn");
    var chips = el("div", "chips");
    chips.hidden = true;
    var cards = el("div", "list");
    var answer = el("div", "answer");
    box.appendChild(chips); box.appendChild(cards); box.appendChild(answer);
    $("talk").appendChild(box);
    turn = { box: box, chips: chips, cards: cards, answer: answer, rows: {} };
    raw = "";
  }
  function handle(e) {
    if (e.t === "ask") {
      if (!trace.title) {
        var auto = /^Auto-trace\. The watch found a new problem at [0-9:]+ on [^:]+: ([^\n]+)/.exec(e.d);
        trace.title = auto ? "Auto-trace: " + auto[1] : e.d;
        trace.by = auto ? "Tracer" : me ? me.user : "";
        trace.started = e.time || Math.floor(Date.now() / 1000);
      }
      $("talk").appendChild(el("div", "bubble", e.d));
      newTurn();
      drawTrail();
    } else if (e.t === "new") {
      clear($("talk")); turn = null; freshTrace();
    } else {
      if (!turn) newTurn();
      if (e.t === "step") {
        var b = el("button", "run", "… " + e.d);
        b.type = "button";
        b.disabled = true;
        b.setAttribute("aria-pressed", "false");
        b.addEventListener("click", function () { toggleEvidence(e.n, e.d, b); });
        turn.chips.hidden = false;
        turn.chips.appendChild(b);
        turn.rows[e.n] = { b: b, d: e.d, card: null };
        trace.steps += 1;
        drawTrail();
      } else if (e.t === "result") {
        var row = turn.rows[e.n];
        if (row) { row.b.textContent = "✓ " + row.d; row.b.className = ""; row.b.disabled = false; }
      } else if (e.t === "text") {
        raw += e.d;
        if (!drawTimer) drawTimer = setTimeout(draw, 120);
      } else if (e.t === "notice") {
        turn.box.appendChild(el("p", "notice", e.d));
      } else if (e.t === "saved") {
        turn.box.appendChild(el("p", "info", e.d));
        if (/^Case saved/.test(e.d)) { trace.saved = true; drawTrail(); }
      } else if (e.t === "case") {
        offerCase(e.d);
      } else if (e.t === "done") {
        draw();
        Object.keys(turn.rows).forEach(function (k) {
          var r = turn.rows[k];
          if (r.b.disabled) { r.b.textContent = "· " + r.d; r.b.className = ""; }
        });
      }
    }
    if (view === "trace") window.scrollTo(0, document.body.scrollHeight);
  }
  function draw() {
    clearTimeout(drawTimer); drawTimer = 0;
    if (!turn) return;
    clear(turn.answer);
    render(raw, turn.answer);
    var cause = said(raw, "cause"), fix = said(raw, "fix");
    if (cause) trace.cause = cause;
    if (fix) trace.fix = fix;
    drawTrail();
  }
  // The model is asked to write "Cause: ..." and "Fix: ..." lines; "not found yet" and "not yet" mean none.
  function said(text, word) {
    var re = new RegExp("^\\s*[*_#\\s]*(likely |root |probable )?" + word + "[*_]*\\s*:[*_]*\\s*(.+)$", "gim"), m, last = "";
    while ((m = re.exec(text))) last = m[2].replace(/[*`]/g, "").replace(/^_+|_+$/g, "").trim();
    return /^not (found|yet|known|established)/i.test(last) ? "" : last;
  }
  function stage(n, state, detail) {
    var s = $("stage-" + n);
    s.className = "stage" + (state ? " " + state : "");
    s.querySelector(".detail").textContent = detail;
    var link = s.previousElementSibling;
    if (link) link.className = "link" + (state === "done" || state === "now" ? " done" : "");
  }
  function drawTrail() {
    if (!trace) return;
    var asked = !!trace.title;
    stage(1, asked ? "done" : "", asked ? short(trace.title, 48) : "not asked yet");
    stage(2, trace.steps ? "done" : busy ? "now" : "", trace.steps ? trace.steps + (trace.steps === 1 ? " step run" : " steps run") : busy ? "looking…" : "nothing yet");
    stage(3, trace.cause ? "done" : busy ? "now" : "", trace.cause ? short(trace.cause, 60) : busy ? "working on it" : "not found yet");
    stage(4, trace.saved || trace.fix ? "done" : "", trace.saved ? "saved as a case" : trace.fix ? short(trace.fix, 60) : "not yet");
    $("trace-title").textContent = asked ? short(trace.title, 110) : "New trace";
    $("trace-sub").textContent = asked
      ? "Started " + clock(trace.started) + " by " + (trace.by || "") + " · " + trace.steps + (trace.steps === 1 ? " step" : " steps") + " · this page changes nothing"
      : "Ask about a problem. Tracer looks, and draws the trail from symptom to fix.";
    $("case-where").textContent = ($("host").value || "any host") + (me && me.kubernetes && $("context").value ? " · " + $("context").value : "");
    $("case-steps").textContent = trace.steps ? trace.steps + " run" : "none yet";
    $("case-cause").textContent = trace.cause || "not found yet";
    $("case-cause").className = trace.cause ? "" : "muted";
    $("case-fix").textContent = trace.saved ? (trace.fix || "saved") + " (saved as a case)" : trace.fix || "not yet";
    $("case-fix").className = trace.fix || trace.saved ? "" : "muted";
  }
  function toggleEvidence(n, title, button) {
    var row = turn && turn.rows[n] ? turn.rows[n] : null;
    var open = button.getAttribute("aria-pressed") === "true";
    button.setAttribute("aria-pressed", String(!open));
    var holder = button.parentNode.nextSibling;
    var found = holder.querySelector('[data-step="' + n + '"]');
    if (open) { if (found) holder.removeChild(found); return; }
    var card = el("article", "evidence");
    card.dataset.step = n;
    var head = el("div", "head");
    head.appendChild(el("span", "tag", "EVIDENCE " + n));
    head.appendChild(el("span", "muted small", title));
    card.appendChild(head);
    var pre = el("pre", "mono box", "Loading...");
    card.appendChild(pre);
    holder.appendChild(card);
    api("GET", "/api/chat/" + chat + "/step/" + n).then(function (d) { pre.textContent = d.text; })
      .catch(function (err) { pre.textContent = err.message; });
  }
  function send(path, body) {
    if (!chat || busy) return;
    setBusy(true);
    api("POST", "/api/chat/" + chat + "/" + path, body || {}).then(poll).catch(function (err) { setBusy(false); failed(err); });
  }
  $("ask-form").addEventListener("submit", function (e) {
    e.preventDefault();
    var q = $("question").value.trim();
    if (!q) return;
    $("question").value = "";
    send("ask", { question: q, host: $("host").value });
  });
  $("question").addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("ask").click(); }
  });
  $("host").addEventListener("change", drawTrail);
  $("stop").addEventListener("click", function () { api("POST", "/api/chat/" + chat + "/stop", {}).catch(failed); });
  $("report").addEventListener("click", function () { send("report"); });
  $("case").addEventListener("click", function () { send("case"); });
  $("new").addEventListener("click", function () {
    dialog({ title: "Start a new trace?", text: "The model forgets this one. The log keeps it.",
      buttons: [{ label: "Start new", cls: "primary", value: true }, { label: "Keep this one", value: false }] })
      .then(function (yes) { if (yes) send("new"); });
  });
  $("context").addEventListener("change", function () {
    api("POST", "/api/chat/" + chat + "/context", { context: $("context").value }).then(function () { poll(); drawTrail(); }).catch(failed);
  });
  function offerCase(text) {
    dialog({ title: "Save this case for every host?",
      text: "Later traces search saved cases first. Correct anything that is wrong before saving.",
      input: text,
      buttons: [{ label: "Save case", cls: "primary", value: "input" }, { label: "Drop it", value: null }] })
      .then(function (kept) {
        if (kept) api("POST", "/api/chat/" + chat + "/save-case", { text: kept }).then(poll).catch(failed);
      });
  }
  // An auto-trace from the Map or What changed. Your own trace is kept to come back to.
  function openTrace(id) {
    show("trace");
    if (id !== chat) openChat(id);
  }
  $("back-mine").addEventListener("click", function () {
    var mine = sessionStorage.getItem("mine");
    if (mine && mine !== chat) { openChat(mine); return; }
    sessionStorage.removeItem("chat"); openChat();
  });
  // From the other views: open the trace with a question ready, about the right host.
  function traceThis(question, lane) {
    show("trace");
    if (lane && me.hosts.indexOf(lane) >= 0) $("host").value = lane;
    // A problem on a cluster: trace it on that context, not on whichever one the list shows.
    if (lane && !busy && (me.contexts || []).indexOf(lane) >= 0 && $("context").value !== lane) {
      $("context").value = lane;
      $("context").dispatchEvent(new Event("change"));
    }
    $("question").value = question;
    drawTrail();
    $("question").focus();
    if (busy) tell("Tracer is still answering", "The question is ready below. Send it when this answer is done, or start a new trace first.");
  }

  // ---------- a small, safe renderer for the model's text ----------
  function inline(text, into) {
    var re = /(`[^`\n]+`|\*\*[^*\n]+\*\*)/g, last = 0, m;
    while ((m = re.exec(text))) {
      if (m.index > last) into.appendChild(document.createTextNode(text.slice(last, m.index)));
      var t = m[0];
      if (t.charAt(0) === "`") into.appendChild(el("code", "mono", t.slice(1, -1)));
      else into.appendChild(el("strong", "", t.slice(2, -2)));
      last = m.index + t.length;
    }
    if (last < text.length) into.appendChild(document.createTextNode(text.slice(last)));
  }
  function render(text, into) {
    var lines = text.split("\n"), i = 0, list = null, para = [];
    function flush() {
      if (para.length) {
        var p = el("p", /^\s*[*_]*(likely |root |probable )?(cause|fix)[*_]*\s*:/i.test(para[0]) ? "key" : "");
        inline(para.join("\n"), p); into.appendChild(p); para = [];
      }
      list = null;
    }
    while (i < lines.length) {
      var line = lines[i];
      if (/^\s*```/.test(line)) {
        flush();
        var code = [];
        i += 1;
        while (i < lines.length && !/^\s*```/.test(lines[i])) { code.push(lines[i]); i += 1; }
        into.appendChild(el("pre", "mono", code.join("\n")));
      } else if (/^#{1,4}\s+/.test(line) || /^[A-Z][A-Z ]{3,30}:?$/.test(line.trim())) {
        flush();
        var h = el("h3"); inline(line.replace(/^#{1,4}\s+/, "").replace(/\*\*/g, ""), h); into.appendChild(h);
      } else if (/^\s*([-*]|\d+[.)])\s+/.test(line)) {
        if (para.length) { var keep = list; flush(); list = keep; }
        if (!list) { list = el("ul"); into.appendChild(list); }
        var li = el("li"); inline(line.replace(/^\s*([-*]|\d+[.)])\s+/, ""), li); list.appendChild(li);
      } else if (!line.trim()) {
        flush();
      } else {
        if (para.length && /^\s*[*_]*(likely |root |probable )?(cause|fix)[*_]*\s*:/i.test(line)) flush();
        list = null;
        para.push(line);
      }
      i += 1;
    }
    flush();
  }

  // ================= Map =================
  function pct(a, b) { return b ? Math.round(a / b * 100) : 0; }
  function meter(box, name, value, shown) {
    box.appendChild(el("span", "", name));
    var m = el("span", "meter"), fillBar = el("span", value >= 85 ? "high" : "");
    fillBar.style.width = Math.min(100, Math.max(0, value)) + "%";
    m.appendChild(fillBar);
    box.appendChild(m);
    box.appendChild(el("span", value >= 85 ? "warn" : "", shown));
  }
  function laneProblems(name) { return health.problems.filter(function (p) { return p.lane === name; }).length; }
  function drawMap() {
    var h = health;
    // when it was checked is in the top bar; here only why a check failed, or that the first one is running
    $("map-when").textContent = h.failed ? "The last check failed: " + h.failed : h.checked ? "" : "First check is running...";
    $("map-when").className = h.failed ? "warn small" : "muted small";
    // clusters
    var cl = $("clusters"); clear(cl);
    $("cluster-block").hidden = !h.clusters.length;
    var ROLE = { supervisor: "Supervisor", namespace: "vSphere namespace", cluster: "Kubernetes", vks: "VKS cluster" };
    var ORDER = { supervisor: 0, namespace: 1, vks: 2, cluster: 3 };
    var cards = {};
    function goCard(name) {
      var t = cards[name];
      if (!t) return;
      t.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "nearest" });
      t.classList.add("flash");
      setTimeout(function () { t.classList.remove("flash"); }, 1600);
    }
    function linkRow(card, label, names) {
      if (!names.length) return;
      var row = el("div", "cardlinks");
      row.appendChild(el("span", "muted small", label));
      names.forEach(function (n) {
        var b = el("button", "", n.label);
        b.type = "button";
        b.title = "Go to the card of " + n.target;
        b.addEventListener("click", function () { goCard(n.target); });
        row.appendChild(b);
      });
      card.appendChild(row);
    }
    var sorted = h.clusters.slice().sort(function (a, b) { return (a.role in ORDER ? ORDER[a.role] : 3) - (b.role in ORDER ? ORDER[b.role] : 3); });
    sorted.forEach(function (c) {
      var card = el("div", "card" + (c.problems ? " hot" : c.expired || c.paused ? " stale" : ""));
      cards[c.name] = card;
      var head = el("div", "head");
      head.appendChild(el("span", "name", c.name));
      head.appendChild(el("span", c.problems || c.expired || c.paused ? "warn small" : c.blind ? "muted small" : "okc small",
        c.problems ? "▲ " + c.problems + (c.problems === 1 ? " problem" : " problems")
          : c.expired ? "login expired" : c.paused ? "paused" : c.blind ? "○ can't see" : "● healthy"));
      card.appendChild(head);
      var line = c.expired ? "run kubectl vsphere login again" : c.line.replace(/^vSphere namespace [^:]+: /, "");
      card.appendChild(el("span", "muted small", (ROLE[c.role] || c.kind) + " · " + line));
      if (c.spaces && c.spaces.length) {  // pods per namespace: select one to see its pods; system ones fold away
        var sp = el("div", "spaces small"), sysPods = 0, sysSpaces = 0, all = !!showSystem[c.name];
        sp.setAttribute("aria-label", "Pods per namespace");
        c.spaces.forEach(function (s) {
          var system = SYSTEM.test(s.name) && !s.problems;
          if (system) { sysPods += s.pods; sysSpaces += 1; }
          if (system && !all) return;
          var b = el("button", s.problems ? "hot" : "", s.name + " " + s.pods + (s.problems ? " ▲" + s.problems : ""));
          b.type = "button";
          b.title = "Show the " + s.pods + " pods in namespace " + s.name + (s.problems ? ", " + s.problems + " need a look" : "");
          b.setAttribute("aria-pressed", String(!!space && space.cluster === c.name && space.namespace === s.name));
          b.addEventListener("click", function () { openSpace(c.name, s.name); });
          sp.appendChild(b);
        });
        if (sysSpaces) {
          var t = el("button", "sys", all ? "Hide system namespaces"
            : "+ " + sysPods + " system pods in " + sysSpaces + (sysSpaces === 1 ? " namespace" : " namespaces"));
          t.type = "button";
          t.title = "kube-system, vmware-system-*, tkg-system and the like: run by Kubernetes and VMware, not your apps";
          t.addEventListener("click", function () { showSystem[c.name] = !all; drawMap(); });
          sp.appendChild(t);
        }
        if (c.more_spaces) sp.appendChild(el("span", "muted", "+" + c.more_spaces + " more"));
        card.appendChild(sp);
      }
      if (c.role === "namespace") {  // its VKS clusters, each with its own card
        linkRow(card, "VKS clusters:", h.clusters.filter(function (x) { return x.role === "vks" && x.context === c.name; })
          .map(function (x) { return { label: x.name.split("/").pop(), target: x.name }; }));
      }
      if (c.role === "vks") {  // the cluster seen from inside, when you have a login to it
        var short = c.name.split("/").pop();
        linkRow(card, "Inside:", h.clusters.filter(function (x) {
          return x.role === "cluster" && (x.name === short || x.name.slice(-short.length - 1) === "." + short);
        }).map(function (x) { return { label: x.name, target: x.name }; }));
      }
      cl.appendChild(card);
    });
    // hosts
    var hc = $("hostcards"); clear(hc);
    (h.hosts || []).forEach(function (host) {
      var n = laneProblems(host.name);
      var card = el("div", "card" + (n ? " hot" : host.role === "hub" ? " hub" : ""));
      var head = el("div", "head");
      var left = el("span", "row");
      left.appendChild(el("span", "name", host.name));
      if (host.role) left.appendChild(el("span", "tag", host.role));
      head.appendChild(left);
      head.appendChild(el("span", n ? "warn small" : "okc small", n ? "▲ " + n : "● ok"));
      card.appendChild(head);
      if (host.error) card.appendChild(el("p", "warn small", host.error));
      var list = host.containers || [];
      if (list.length) {
        var sq = el("div", "squares");
        sq.setAttribute("aria-label", list.length + " containers");
        list.forEach(function (c) {
          var b = el("button", c.state);
          b.type = "button";
          b.title = c.name + ": " + (c.status || c.state);
          b.setAttribute("aria-label", c.name + ": " + (c.status || c.state));
          b.setAttribute("aria-pressed", String(!!chosen && chosen.host === host.name && chosen.c.name === c.name));
          b.addEventListener("click", function () { openContainer(host.name, c); });
          sq.appendChild(b);
        });
        card.appendChild(sq);
        var running = list.filter(function (c) { return c.state === "running"; }).length;
        card.appendChild(el("span", "muted small", running + " of " + list.length + " containers running"));
      }
      var m = el("div", "meters"), any = false;
      if (host.load !== null && host.load !== undefined) { meter(m, "Load", pct(host.load, host.cpus || 1), host.load.toFixed(1)); any = true; }
      if (host.memory) { var mp = pct(host.memory[0], host.memory[1]); meter(m, "Memory", mp, mp + "%"); any = true; }
      if (host.disks && host.disks.length) {
        var worst = host.disks.slice().sort(function (a, b) { return b[0] - a[0]; })[0];
        meter(m, "Disk", worst[0], worst[0] + "%");
        m.lastChild.title = "Fullest: " + worst[1];
        any = true;
      }
      if (any) card.appendChild(m);
      hc.appendChild(card);
    });
    // problems
    var n = h.problems.length;
    $("problem-count").textContent = n ? n : "none";
    var pl = $("problems"); clear(pl);
    if (!n) pl.appendChild(el("p", "muted", h.checked ? "Nothing needs a look." : ""));
    h.problems.forEach(function (p) {
      var row = el("div", "item" + (Date.now() / 1000 - p.since < 86400 && !p.known ? " hot" : ""));
      var what = el("div", "what");
      what.appendChild(el("div", "", p.text));
      what.appendChild(el("div", "muted small", (p.lane ? p.lane + " · " : "") + (p.known ? "already open when the page started" : "open for " + ago(p.since))));
      if (p.link) what.appendChild(el("div", "linkline" + (p.link.strength === "maybe" ? " maybe" : ""), "← " + linkText(p.link)));
      var t = p.trace;
      if (t) {
        what.appendChild(el("div", "traceline", {
          waiting: "Auto-trace waiting for its turn…", running: "Auto-trace running…",
          done: t.cause ? "Traced, likely: " + short(t.cause, 140) : "Traced: no cause found",
          limit: "Not traced: " + t.most + " auto-traces already this hour", failed: "Auto-trace failed: " + (t.why || "")
        }[t.state] || ""));
      }
      row.appendChild(what);
      if (t && t.id) {
        var o = el("button", "primary", t.state === "done" ? "Open trace" : "Watch it"); o.type = "button";
        o.addEventListener("click", function () { openTrace(t.id); });
        row.appendChild(o);
      }
      var b = el("button", "", "Trace this"); b.type = "button";
      b.addEventListener("click", function () { traceThis("Why: " + p.text + "?", p.lane); });
      row.appendChild(b);
      pl.appendChild(row);
    });
  }
  // ---- log lines: short time, wrapped message, errors in red, warnings in orange ----
  var LOGTIME = /^(\d{4}-\d\d-\d\d)[T ](\d\d:\d\d:\d\d)\S*\s?(.*)$/;
  var LEVEL_ERR = /\b(ERROR|ERR|FATAL|CRITICAL|PANIC|SEVERE|EXCEPTION|Traceback)\b/;
  var LEVEL_WRN = /\b(WARN|WARNING)\b/;
  var CONT = /^(\s+\S|at |Caused by|\.\.\. \d+ more)/;
  // one log box: its text, its Errors only and Wrap buttons (containers use "ct", pods "pd")
  function logBox(prefix) {
    var lb = { text: "", errorsOnly: false };
    lb.draw = function () {
      var box = $(prefix + "-logs"); clear(box);
      var shown = 0, keep = false, last = "";
      lb.text.split("\n").forEach(function (raw) {
        if (!raw) return;
        var m = LOGTIME.exec(raw), date = m ? m[1] + " " + m[2] + " UTC" : "", t = m ? m[2] : "", msg = m ? m[3] : raw;
        var more = CONT.test(msg);
        var kind = LEVEL_ERR.test(msg.slice(0, 200)) ? "err" : LEVEL_WRN.test(msg.slice(0, 200)) ? "wrn" : "";
        if (more && !kind) kind = last;      // a stack trace line belongs to the line above it
        if (!more) last = kind;
        keep = more ? keep : !!kind;
        if (lb.errorsOnly && !keep) return;
        var row = el("div", "ll" + (kind ? " " + kind : "") + (more ? " more" : ""));
        var ts = el("span", "t", more ? "" : t);
        if (date && !more) ts.title = date;
        row.appendChild(ts); row.appendChild(el("span", "m", more ? msg.replace(/^\s+/, "") : msg));   // the row indents it
        box.appendChild(row); shown++;
      });
      if (!shown) box.appendChild(el("span", "muted", lb.errorsOnly ? "No errors or warnings in these lines." : "(no log lines)"));
      box.scrollTop = box.scrollHeight;   // newest at the bottom, in view
    };
    lb.set = function (text) { lb.text = text || ""; lb.draw(); };
    lb.say = function (text, cls) { lb.text = ""; var box = $(prefix + "-logs"); clear(box); box.appendChild(el("span", cls || "muted", text)); };
    $(prefix + "-errors").addEventListener("click", function () {
      lb.errorsOnly = !lb.errorsOnly; this.setAttribute("aria-pressed", String(lb.errorsOnly)); lb.draw();
    });
    $(prefix + "-wrap").addEventListener("click", function () {
      var on = this.getAttribute("aria-pressed") !== "true";
      this.setAttribute("aria-pressed", String(on)); $(prefix + "-logs").classList.toggle("nowrap", !on);
    });
    return lb;
  }
  var ctLog = logBox("ct"), pdLog = logBox("pd");

  // the side panel shows one thing at a time: problems, a container, a namespace's pods, or one pod
  var PANES = ["problems", "container", "space", "pod"];
  function pane(name) {
    PANES.forEach(function (p) { $(p + "-pane").hidden = p !== name; });
    $("map-side").classList.toggle("wide", name !== "problems");
    if (name !== "space" && name !== "pod") space = null;
    if (name !== "container") chosen = null;
  }

  function openContainer(host, c) {
    pane("container");
    chosen = { host: host, c: c };
    $("ct-where").textContent = host + " · container";
    $("ct-name").textContent = c.name;
    $("ct-status").textContent = c.status || c.state;
    $("ct-status").className = c.state === "running" ? "okc" : "warn";
    $("ct-image").textContent = c.image || "?";
    $("ct-restarts").textContent = String(c.restarts || 0);
    $("ct-started").textContent = c.started ? c.started.replace("T", " ").slice(0, 16) + " UTC" : "?";
    ctLog.say("Loading...");
    clear($("ct-changes"));
    $("ct-changes").appendChild(el("p", "muted", "Loading..."));
    drawMap();
    api("GET", "/api/container?host=" + encodeURIComponent(host) + "&name=" + encodeURIComponent(c.name)).then(function (d) {
      if (!chosen || chosen.c.name !== c.name) return;
      ctLog.set(d.logs);
      var box = $("ct-changes"); clear(box);
      if (!d.changes.length) box.appendChild(el("p", "muted", "Nothing recorded."));
      d.changes.forEach(function (e) {
        var p = el("p"); p.appendChild(el("span", "muted mono", clock(e.time) + "  ")); p.appendChild(document.createTextNode(e.text)); box.appendChild(p);
      });
    }).catch(function (err) { ctLog.say(err.message, "warn"); clear($("ct-changes")); });
  }
  $("ct-close").addEventListener("click", function () { pane("problems"); drawMap(); });
  $("ct-trace").addEventListener("click", function () {
    if (!chosen) return;
    traceThis("Container " + chosen.c.name + " on " + chosen.host + " is " + (chosen.c.status || chosen.c.state) + ". What is wrong with it?", chosen.host);
  });

  // ================= Pods: a namespace's pods, then one pod =================
  function openSpace(cluster, namespace) {
    pane("space");
    space = { cluster: cluster, namespace: namespace, data: null };
    $("sp-where").textContent = cluster + " · namespace";
    $("sp-name").textContent = namespace;
    $("sp-count").textContent = "";
    $("sp-sub").textContent = "Loading...";
    clear($("sp-list"));
    drawMap();
    loadSpace();
  }
  function loadSpace() {
    if (!space) return;
    var want = space;
    spaceAt = Date.now();
    api("GET", "/api/pods?cluster=" + encodeURIComponent(want.cluster) + "&namespace=" + encodeURIComponent(want.namespace)).then(function (d) {
      if (space !== want) return;
      want.data = d; drawSpace();
    }).catch(function (err) { if (space === want) { $("sp-sub").textContent = err.message; $("sp-sub").className = "warn small"; } });
  }
  function drawSpace() {
    var d = space && space.data;
    if (!d) return;
    var bad = d.workloads.reduce(function (n, g) { return n + g.trouble; }, 0);
    $("sp-count").textContent = bad ? "▲ " + bad : "";
    $("sp-sub").className = "muted small";
    $("sp-sub").textContent = d.count + (d.count === 1 ? " pod" : " pods") + " in " + d.workloads.length
      + (d.workloads.length === 1 ? " workload" : " workloads") + (bad ? "; those that need a look come first" : "");
    var list = $("sp-list"); clear(list);
    if (!d.count) list.appendChild(el("p", "muted", "No pods in this namespace."));
    d.workloads.forEach(function (g) {
      var box = el("div", "wl"), head = el("div", "wlhead"), who = el("span");
      who.appendChild(document.createTextNode(g.kind + " ")); who.appendChild(el("b", "", g.name));
      head.appendChild(who);
      head.appendChild(el("span", g.ready === g.pods.length ? "okc" : "warn", g.ready + " of " + g.pods.length + " ready"));
      box.appendChild(head);
      g.pods.forEach(function (p) {
        var cls = p.trouble ? "hot" : p.left_over ? "old" : "";
        var b = el("button", "pod" + (cls ? " " + cls : "")); b.type = "button";
        b.appendChild(el("i", "sq " + (p.trouble ? "restarting" : p.ready || p.state === "Succeeded" ? "running" : "exited")));
        var words = el("span");
        words.appendChild(el("span", "n", p.name));
        var note = p.trouble ? p.trouble : p.left_over ? p.state + " · left over, not a problem" : p.state + " · " + p.restarts + " ↻"
          + (p.restarts && p.last_restart ? " · last " + ago(p.last_restart) + " ago" : "");
        words.appendChild(el("span", "small " + (p.trouble ? "warn" : "muted"), note));
        b.appendChild(words);
        b.setAttribute("aria-label", p.name + ": " + note);
        b.addEventListener("click", function () { openPod(space.cluster, space.namespace, p.name, false); });
        box.appendChild(b);
      });
      list.appendChild(box);
    });
  }
  function openPod(cluster, namespace, name, previous) {
    var back = space;
    pane("pod");
    space = back;   // keep the namespace, to go back to
    podOpen = { cluster: cluster, namespace: namespace, name: name, previous: previous };
    $("pd-where").textContent = cluster + " · " + namespace;
    $("pd-back").textContent = "Back to " + namespace;
    $("pd-name").textContent = name;
    $("pd-status").textContent = "Loading..."; $("pd-status").className = "muted";
    $("pd-now").setAttribute("aria-pressed", String(!previous));
    $("pd-prev").setAttribute("aria-pressed", String(!!previous));
    ["pd-image", "pd-node", "pd-started", "pd-restarts"].forEach(function (id) { $(id).textContent = ""; });
    $("pd-trace-note").textContent = "Starts a trace about this pod in cluster " + cluster + ".";
    pdLog.say("Loading...");
    var want = podOpen;
    api("GET", "/api/pod?cluster=" + encodeURIComponent(cluster) + "&namespace=" + encodeURIComponent(namespace)
        + "&name=" + encodeURIComponent(name) + (previous ? "&previous=1" : "")).then(function (d) {
      if (podOpen !== want) return;
      want.state = d.trouble || d.state;
      $("pd-status").textContent = (d.trouble || d.state) + (d.exit_code !== null && d.exit_code !== undefined && d.restarts ? " (last exit code " + d.exit_code + ")" : "");
      $("pd-status").className = d.trouble ? "warn" : "okc";
      $("pd-image").textContent = d.images.join(", ") || "?";
      $("pd-node").textContent = d.node || "?";
      $("pd-started").textContent = d.started ? d.started.replace("T", " ").slice(0, 16) + " UTC (" + ago(Date.parse(d.started) / 1000) + " ago)" : "?";
      $("pd-restarts").textContent = String(d.restarts);
      pdLog.set(d.logs);
    }).catch(function (err) {
      if (podOpen !== want) return;
      $("pd-status").textContent = ""; pdLog.say(err.message, "warn");
    });
  }
  $("sp-close").addEventListener("click", function () { pane("problems"); drawMap(); });
  $("pd-back").addEventListener("click", function () {
    var back = space; podOpen = null;
    if (back) { pane("space"); space = back; drawSpace(); loadSpace(); } else pane("problems");
    drawMap();
  });
  $("pd-now").addEventListener("click", function () { if (podOpen) openPod(podOpen.cluster, podOpen.namespace, podOpen.name, false); });
  $("pd-prev").addEventListener("click", function () { if (podOpen) openPod(podOpen.cluster, podOpen.namespace, podOpen.name, true); });
  $("pd-trace").addEventListener("click", function () {
    if (!podOpen) return;
    traceThis("Pod " + podOpen.namespace + "/" + podOpen.name + " in cluster " + podOpen.cluster + " is "
      + (podOpen.state || "not right") + ". What is wrong with it?", podOpen.cluster);
  });

  // ================= What changed =================
  function loadChanges() {
    each("[data-hours]", function (b) { b.setAttribute("aria-pressed", String(Number(b.dataset.hours) === hours)); });
    $("changes-note").textContent = "Loading...";
    api("GET", "/api/changes?hours=" + hours).then(function (d) { changes = d; picked = {}; drawChanges(); })
      .catch(function (err) { $("changes-note").textContent = err.message; });
  }
  function key(e) { return e.time + "|" + e.lane + "|" + e.text; }
  function where(t, d) { return (t - d.since) / (d.now - d.since) * 100; }
  function drawChanges() {
    var d = changes, find = $("changes-find").value.trim().toLowerCase();
    var shown = d.events.filter(function (e) { return !find || (e.text + " " + e.where + " " + e.lane).toLowerCase().indexOf(find) >= 0; });
    var lanes = d.lanes.slice();
    shown.forEach(function (e) { if (e.lane && lanes.indexOf(e.lane) < 0) lanes.push(e.lane); });
    var river = $("river"); clear(river);
    var grid = el("div", "river-grid");
    grid.appendChild(el("span"));
    var times = el("div", "times");
    for (var i = 0; i <= 6; i += 1) times.appendChild(el("span", "", i === 6 ? "now" : clock(d.since + (d.now - d.since) * i / 6)));
    grid.appendChild(times);
    var tracks = {};
    lanes.forEach(function (lane) {
      grid.appendChild(el("span", "lane-name", lane));
      var track = el("div", "track");
      var spans = (d.coverage || {})[lane];
      if (spans) { // hatch the stretches with no record
        var at = d.since;
        spans.concat([[d.now, d.now]]).forEach(function (s) {
          if (s[0] - at > (d.now - d.since) / 100) {
            var off = el("span", "off");
            off.style.left = Math.max(0, where(at, d)) + "%";
            off.style.width = Math.max(0, where(s[0], d) - where(at, d)) + "%";
            off.title = "Not recording " + clock(at) + " to " + clock(s[0]);
            track.appendChild(off);
          }
          at = Math.max(at, s[1]);
        });
      }
      tracks[lane] = track;
      grid.appendChild(track);
    });
    river.appendChild(grid);
    shown.forEach(function (e) {  // a line from the change to the problem it may explain
      if (e.kind !== "problem" || !e.link || !tracks[e.lane]) return;
      var a = Math.max(0, where(e.link.time, d)), b = Math.min(100, where(e.time, d));
      var j = el("span", "joint" + (e.link.strength === "maybe" ? " maybe" : ""));
      j.style.left = Math.min(a, b) + "%"; j.style.width = Math.max(0.3, Math.abs(b - a)) + "%";
      tracks[e.lane].appendChild(j);
    });
    shown.slice().reverse().forEach(function (e) {
      var track = tracks[e.lane] || tracks[lanes[0]];
      var k = key(e);
      var b = el("button", "mk " + ({ problem: "problem", cleared: "cleared", cluster: "cluster", traced: "traced" }[e.kind] || "change"));
      b.type = "button";
      b.style.left = Math.min(99.5, Math.max(0.5, where(e.time, d))) + "%";
      b.title = clock(e.time) + " " + e.lane + ": " + e.text;
      b.setAttribute("aria-label", b.title);
      b.setAttribute("aria-pressed", String(!!picked[k]));
      b.addEventListener("click", function () { pick(e); });
      track.appendChild(b);
    });
    var list = $("changes"); clear(list);
    shown.forEach(function (e) {
      var hot = e.kind === "problem", k = key(e);
      var row = el("button", "item" + (hot ? " hot" : ""));
      row.type = "button";
      row.setAttribute("aria-pressed", String(!!picked[k]));
      row.appendChild(el("span", "when mono", clock(e.time)));
      var label = { problem: "problem starts", cleared: "ok again", cluster: "cluster record", change: "change", traced: "auto-trace" }[e.kind] || e.kind;
      row.appendChild(el("span", "tag" + (hot ? " hot" : e.kind === "cleared" ? " good" : e.kind === "traced" ? " auto" : ""), label));
      var what = el("span", "what");
      what.appendChild(el("span", "", e.text));
      if (e.link) { what.appendChild(el("br")); what.appendChild(el("span", "linkline" + (e.link.strength === "maybe" ? " maybe" : ""), "← " + linkText(e.link))); }
      row.appendChild(what);
      row.appendChild(el("span", "muted small", e.lane || ""));
      row.addEventListener("click", function () { if (e.kind === "traced" && e.id) openTrace(e.id); else pick(e); });
      list.appendChild(row);
    });
    if (!shown.length) list.appendChild(el("p", "muted", find ? "Nothing in this period matches." : "Nothing changed in this period."));
    $("changes-note").textContent = shown.length + (shown.length === 1 ? " entry" : " entries") + " · select any to trace them together";
    var n = Object.keys(picked).length;
    $("picked-bar").hidden = !n;
    $("picked-count").textContent = n + (n === 1 ? " picked" : " picked") + ": " + Object.keys(picked).map(function (k) { return clock(picked[k].time); }).join(", ");
  }
  function pick(e) {
    var k = key(e);
    if (picked[k]) delete picked[k]; else picked[k] = e;
    drawChanges();
  }
  $("picked-clear").addEventListener("click", function () { picked = {}; drawChanges(); });
  $("picked-trace").addEventListener("click", function () {
    var rows = Object.keys(picked).map(function (k) { return picked[k]; }).sort(function (a, b) { return a.time - b.time; });
    var hostsSeen = rows.map(function (e) { return e.lane; }).filter(function (h, i, a) { return me.hosts.indexOf(h) >= 0 && a.indexOf(h) === i; });
    traceThis("These happened:\n" + rows.map(function (e) { return "- " + clock(e.time) + " on " + (e.lane || "?") + ": " + e.text; }).join("\n")
      + "\nAre they connected, and did a change cause a problem?", hostsSeen.length === 1 ? hostsSeen[0] : "");
  });
  each("[data-hours]", function (b) { b.addEventListener("click", function () { hours = Number(b.dataset.hours); loadChanges(); }); });
  $("changes-find").addEventListener("input", function () { if (changes) drawChanges(); });

  // ================= Top bar: status on every view =================
  function late(h) { return !h || !h.checked || Date.now() / 1000 - h.checked > 3 * (h.every || 60); }
  function drawTop(unreachable) {
    var h = health, pill = $("status-pill");
    if (!h) { pill.hidden = true; return; }
    pill.hidden = false; clear(pill);
    var n = h.problems.length;
    if (unreachable || h.failed) {
      pill.className = "pill bad"; pill.appendChild(el("span", "", "▲ checks failing"));
    } else if (n) {
      pill.className = "pill bad"; pill.appendChild(el("span", "", "▲")); pill.appendChild(el("b", "", String(n)));
      pill.appendChild(el("span", "pw", "need" + (n === 1 ? "s" : "") + " a look"));
    } else {
      pill.className = "pill good"; pill.appendChild(el("span", "", "●")); pill.appendChild(el("b", "", "All clear"));
    }
    $("top-checked").textContent = h.checked ? "checked " + ago(h.checked) + " ago" : "";
    $("top-checked").className = "muted small checked-note" + (late(h) ? " late" : "");
    var running = h.problems.filter(function (p) { return p.trace && (p.trace.state === "running" || p.trace.state === "waiting"); }).length + (busy ? 1 : 0);
    $("trace-live").hidden = !running;
    $("trace-live").querySelector("b").textContent = running;
  }
  $("status-pill").addEventListener("click", function () { show("map"); });

  // ================= Wall screen =================
  var feed = null, feedAt = 0, peekTimer = null;
  function drawWall() {
    if (!health || !me) return;
    var h = health, n = h.problems.length;
    $("wall-state").className = "state " + (h.failed ? "bad" : n ? "bad" : "good");
    $("wall-words").textContent = h.failed ? "Checks are failing" : n ? n + " need" + (n === 1 ? "s" : "") + " a look" : "All clear";
    $("wall-checked").textContent = (h.checked ? "checked " + ago(h.checked) + " ago" : "not checked yet") + (late(h) ? " · checks have stopped" : "");
    $("wall-checked").className = late(h) ? "late" : "";
    tick();
    // Kubernetes: the clusters themselves; logins (Supervisor, vSphere namespace) only when they need you
    var k = $("wall-k8s"); clear(k);
    var shown = 0, quiet = 0;
    (h.clusters || []).forEach(function (c) {
      var wants = c.role === "vks" || c.role === "cluster" || c.role === "" || c.problems || c.expired;
      if (!wants) { quiet += 1; return; }
      shown += 1;
      k.appendChild(wallCluster(c));
    });
    $("wall-k8s-block").hidden = !(h.clusters || []).length;
    $("wall-k8s-more").textContent = quiet ? "+ " + quiet + " Supervisor and vSphere namespace login" + (quiet === 1 ? "" : "s") + ", nothing wrong" : "";
    var hc = $("wall-hosts"); clear(hc);
    (h.hosts || []).forEach(function (host) { hc.appendChild(wallHost(host)); });
    // needs a look
    var need = $("wall-need"); clear(need);
    if (!n) {
      var ok = el("div", "wcard"); ok.appendChild(el("span", "nm okc", "Nothing needs a look"));
      var last = ((feed && feed.events) || []).filter(function (e) { return e.kind === "cleared"; })[0];
      if (last) ok.appendChild(el("span", "ln", "Last problem cleared at " + clock(last.time) + ": " + last.text));
      need.appendChild(ok);
    }
    h.problems.slice(0, 6).forEach(function (p) {
      var item = el("div", "nitem");
      item.appendChild(el("span", "t1", p.text));
      item.appendChild(el("span", "t2", (p.lane ? p.lane + " · " : "") + (p.known ? "open since before the page started" : "open for " + ago(p.since))));
      var t = p.trace;
      if (t) item.appendChild(el("span", "t3", {
        waiting: "Auto-trace waiting for its turn…", running: "Tracing now…",
        done: t.cause ? "Traced: " + short(t.cause, 160) : "Traced: no cause found",
        limit: "Not traced: the hourly auto-trace limit is reached", failed: "Auto-trace failed"
      }[t.state] || ""));
      else if (p.link) item.appendChild(el("span", "t3", "← " + linkText(p.link)));
      need.appendChild(item);
    });
    if (n > 6) need.appendChild(el("p", "muted small", "+ " + (n - 6) + " more on the Map"));
    if (Date.now() - feedAt > 25000) {
      feedAt = Date.now();
      api("GET", "/api/changes?hours=1").then(function (d) { feed = d; drawRecent(); }).catch(function () {});
    }
    drawRecent();
  }
  function wallCard(kind, name, state, cls) {
    var card = el("div", "wcard" + (cls ? " " + cls : ""));
    card.appendChild(el("span", "kind", kind));
    var top = el("div", "top2");
    top.appendChild(el("span", "nm", name));
    top.appendChild(el("span", "st " + (cls ? "warn" : state.charAt(0) === "○" ? "muted" : "okc"), state));
    card.appendChild(top);
    return card;
  }
  function bigNumber(card, number, words) {
    var b = el("span", "big", number + " "); b.appendChild(el("small", "", words)); card.appendChild(b);
  }
  function wallCluster(c) {
    var ROLE_WALL = { supervisor: "Supervisor", namespace: "vSphere namespace", cluster: "Cluster", vks: "VKS cluster" };
    var state = c.problems ? "▲ " + c.problems : c.expired ? "login expired" : c.paused ? "paused" : c.blind ? "○ can't see" : "● ok";
    var card = wallCard(ROLE_WALL[c.role] || c.kind, c.name, state, c.problems ? "hot" : c.expired || c.paused ? "stale" : "");
    var m = /(\d+) of (\d+) machines running/.exec(c.line), k = /(\d+) nodes?, (\d+) pods/.exec(c.line);
    if (m) bigNumber(card, m[1] + "/" + m[2], "machines");
    else if (k) bigNumber(card, k[2], "pods · " + k[1] + " nodes");
    var line = c.expired ? "run kubectl vsphere login again" : c.paused ? "paused: VMware won't repair, scale or upgrade it" : "";
    if (!line && c.spaces && c.spaces.length) {
      line = c.spaces.filter(function (s) { return s.problems || !SYSTEM.test(s.name); }).slice(0, 4)
        .map(function (s) { return s.name + " " + s.pods + (s.problems ? " ▲" + s.problems : ""); }).join(" · ");
    }
    if (!line && !m && !k) line = c.line.replace(/^vSphere namespace [^:]+: /, "");
    if (line) card.appendChild(el("span", "ln", line));
    return card;
  }
  var SYSTEM = /^(kube-|vmware-system-|tkg-|tanzu-|secretgen-|kapp-|cert-manager$|ingress-nginx$)/;
  function wallHost(host) {
    var n = laneProblems(host.name), list = host.containers || [];
    var card = wallCard(host.role === "hub" ? "Hub" : host.role ? "Agent" : "Host", host.name,
      host.error ? "▲ not answering" : n ? "▲ " + n : "● ok", host.error || n ? "hot" : "");
    var running = list.filter(function (c) { return c.state === "running"; }).length;
    var worst = (host.disks || []).slice().sort(function (a, b) { return b[0] - a[0]; })[0];
    card.appendChild(el("span", "ln", host.error ? host.error : running + " of " + list.length + " running" + (worst ? " · disk " + worst[0] + "%" : "")));
    if (list.length) {
      var sq = el("div", "wsq");
      list.forEach(function (c) { var i = el("i", c.state === "running" ? "" : c.state === "restarting" || c.state === "unhealthy" ? "u" : "x"); i.title = c.name; sq.appendChild(i); });
      card.appendChild(sq);
    }
    return card;
  }
  function drawRecent() {
    var box = $("wall-recent"); clear(box);
    var now = Date.now() / 1000;
    var rows = ((feed && feed.events) || []).filter(function (e) { return now - e.time < 3600; }).slice(0, 10);
    if (!rows.length) box.appendChild(el("div", "", feed ? "Nothing happened in the last hour." : "Loading…"));
    rows.forEach(function (e) {
      var line = el("div", e.kind === "problem" ? "warn" : e.kind === "cleared" ? "okc" : "");
      line.appendChild(el("span", "tm", clock(e.time).slice(-5)));
      line.appendChild(document.createTextNode((e.lane ? e.lane + ": " : "") + ({ problem: "▲ ", cleared: "✓ ", traced: "◆ " }[e.kind] || "") + e.text));
      box.appendChild(line);
    });
  }
  function tick() {
    var d = new Date();
    $("wall-clock").textContent = ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
  }
  setInterval(function () { if (view === "wall") tick(); }, 5000);
  $("wall-open").addEventListener("click", function () {
    show("wall");
    var v = document.documentElement;
    if (v.requestFullscreen) v.requestFullscreen().catch(function () {});   // optional: the wall works without it
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && view === "wall") show("map"); });
  document.addEventListener("mousemove", function () {
    if (view !== "wall") return;
    document.body.classList.add("peek");
    clearTimeout(peekTimer);
    peekTimer = setTimeout(function () { document.body.classList.remove("peek"); }, 3000);
  });

  // ================= Library =================
  function loadLibrary() {
    api("GET", "/api/library").then(function (d) { library = d; drawLibrary(); }).catch(failed);
  }
  function drawLibrary() {
    each("[data-kind]", function (b) {
      b.setAttribute("aria-pressed", String(b.dataset.kind === kind));
      var name = { cases: "Cases", runbooks: "Runbooks", reports: "Reports" }[b.dataset.kind];
      b.textContent = name + " " + library[b.dataset.kind].length;
    });
    var find = $("library-find").value.trim().toLowerCase(), list = $("library-list");
    clear(list);
    var rows = library[kind].filter(function (r) {
      return !find || find.split(/\s+/).every(function (w) { return (r.find + " " + r.title.toLowerCase()).indexOf(w) >= 0; });
    });
    if (!rows.length) list.appendChild(el("p", "muted", find ? "Nothing matches." : "Nothing here yet."));
    rows.forEach(function (r) {
      var b = el("button", "item pick"); b.type = "button";
      b.setAttribute("aria-pressed", String(!!openDoc && openDoc.kind === kind && openDoc.id === r.id));
      b.appendChild(el("span", "", r.title));
      b.appendChild(el("span", "muted small", r.host + (r.saved ? " · saved " + r.saved : "") + (r.status ? " · " + r.status : "")));
      b.addEventListener("click", function () { openDocument(kind, r); });
      list.appendChild(b);
    });
  }
  function openDocument(k, r) {
    openDoc = { kind: k, id: r.id, title: r.title };
    $("doc-title").textContent = r.title;
    $("doc-note").textContent = k === "cases" ? "A lead written by a model and confirmed by an operator, not proof."
      : k === "runbooks" ? (r.status === "REVIEWED" ? "Reviewed by a person." : "A draft written by a model. Nobody has confirmed it.")
      : "Written by a model from a trace. Check it before passing it on.";
    $("doc-edit").hidden = $("doc-delete").hidden = k !== "cases";
    $("doc").textContent = "Loading...";
    drawLibrary();
    api("GET", "/api/library/" + k + "?id=" + encodeURIComponent(r.id)).then(function (d) {
      openDoc.text = d.text; $("doc").textContent = d.text;
    }).catch(function (err) { $("doc").textContent = err.message; });
  }
  function closeDocument() {
    openDoc = null;
    $("doc-title").textContent = "Select an item"; $("doc-note").textContent = ""; $("doc").textContent = "";
    $("doc-edit").hidden = $("doc-delete").hidden = true;
  }
  each("[data-kind]", function (b) {
    b.addEventListener("click", function () { kind = b.dataset.kind; if (library) drawLibrary(); });
  });
  $("library-find").addEventListener("input", function () { if (library) drawLibrary(); });
  $("doc-delete").addEventListener("click", function () {
    if (!openDoc) return;
    dialog({ title: "Delete this case for every host?",
      text: openDoc.title + "\n\nIt will not come back. Other hosts stop seeing it after ./ts next runs on this host.",
      buttons: [{ label: "Delete it", cls: "danger", value: true }, { label: "Keep it", value: false }] })
      .then(function (yes) {
        if (yes) api("POST", "/api/library/delete", { id: openDoc.id }).then(function () { closeDocument(); loadLibrary(); }).catch(failed);
      });
  });
  $("doc-edit").addEventListener("click", function () {
    if (!openDoc || openDoc.text === undefined) return;
    dialog({ title: "Edit this case", text: "Other hosts see the change after ./ts next runs on this host.", input: openDoc.text,
      buttons: [{ label: "Save changes", cls: "primary", value: "input" }, { label: "Cancel", value: null }] })
      .then(function (text) {
        if (text === null) return;
        var id = openDoc.id, title = openDoc.title;
        api("POST", "/api/library/save", { id: id, text: text }).then(function () {
          loadLibrary(); openDocument("cases", { id: id, title: title });
        }).catch(failed);
      });
  });

  start();
})();
'''
