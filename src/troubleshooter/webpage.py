"""The web page's own files, kept as text in a .py file so an update is .py files only.

Nothing is loaded from anywhere else: no fonts, scripts or styles from the internet. The script puts
text on the page as text, never as markup, so nothing a log or a model says can run in the browser.

Five views: Trace (the chat, drawn as a trail from symptom to fix), Map (every host and cluster),
What changed (one lane per host and cluster), Console (for a wall screen) and Library.
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
      <button type="button" data-view="trace">Trace</button>
      <button type="button" data-view="map">Map <span id="map-count" class="count" hidden></span></button>
      <button type="button" data-view="changes">What changed</button>
      <button type="button" data-view="console">Console</button>
      <button type="button" data-view="library">Library</button>
    </nav>
    <span class="grow"></span>
    <span id="who" class="chip mono"></span>
    <button type="button" id="sign-out">Sign out</button>
  </header>

  <!-- ===== Trace ===== -->
  <section id="view-trace" class="view">
    <div>
      <h1 id="trace-title" class="title">New trace</h1>
      <p id="trace-sub" class="muted">Ask about a problem. Tracer looks, and draws the trail from symptom to fix.</p>
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
          <pre id="ct-logs" class="mono box"></pre>
          <button type="button" id="ct-trace" class="primary">Trace this</button>
          <p class="muted small">Starts a trace about this container on its host.</p>
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

  <!-- ===== Console ===== -->
  <section id="view-console" class="view console" hidden>
    <div class="row end-row">
      <pre class="logo" aria-label="Tracer"><span>████████╗██████╗  █████╗  ██████╗███████╗██████╗ </span>
<span>╚══██╔══╝██╔══██╗██╔══██╗██╔════╝██╔════╝██╔══██╗</span>
<span>   ██║   ██████╔╝███████║██║     █████╗  ██████╔╝</span>
<span>   ██║   ██╔══██╗██╔══██║██║     ██╔══╝  ██╔══██╗</span>
<span>   ██║   ██║  ██║██║  ██║╚██████╗███████╗██║  ██║</span>
<span>   ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝╚══════╝╚═╝  ╚═╝</span></pre>
      <span id="console-watching" class="muted"></span>
      <span class="grow"></span>
      <button type="button" id="full-screen">Full screen</button>
    </div>
    <div class="row top-row">
      <div id="console-status" class="card status">
        <span class="muted">STATUS</span>
        <span id="console-count" class="big-number">-</span>
        <span id="console-words"></span>
        <span id="console-oldest" class="muted"></span>
      </div>
      <div class="card lanes-box">
        <div id="console-lanes" class="console-lanes"></div>
      </div>
    </div>
    <div class="card feed">
      <span class="muted">LIVE · newest first · refreshed every 30 s</span>
      <div id="console-feed"></div>
    </div>
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
.side{flex:1 1 320px;min-width:0;display:flex;flex-direction:column;gap:10px;position:sticky;top:12px;max-height:calc(100vh - 24px);overflow:auto}
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
.picked{display:flex;flex-wrap:wrap;gap:10px;align-items:center;border-color:var(--blue)}

/* console */
.console{font-family:var(--mono);background:#06090d;min-height:calc(100vh - 62px)}
.console .card{background:#06090d;border-color:#1c2530;border-radius:4px}
.logo{margin:0;font-size:12px;line-height:1.05;font-weight:700;white-space:pre;font-family:var(--mono)}
.logo span:nth-child(1){color:#33e1ff}.logo span:nth-child(2){color:#2fd0ff}.logo span:nth-child(3){color:#2fb4ff}
.logo span:nth-child(4){color:#3498ff}.logo span:nth-child(5){color:#3d7bff}.logo span:nth-child(6){color:#5a6cff}
.end-row{align-items:flex-end;gap:20px}
.top-row{align-items:stretch;gap:16px}
.status{flex:1 1 240px;display:flex;flex-direction:column;gap:4px}
.status.hot{border-color:var(--warn)!important}
.big-number{font-size:64px;line-height:1;font-weight:700;color:var(--ok)}
.status.hot .big-number{color:var(--warn)}
.lanes-box{flex:999 1 560px;min-width:0;overflow-x:auto}
.console-lanes{display:grid;grid-template-columns:minmax(90px,150px) minmax(180px,1fr) minmax(120px,190px);gap:10px 16px;align-items:center;font-size:13.5px}
.bars{display:flex;gap:3px;align-items:flex-end;height:24px}
.bars span{flex:1;background:#1f6f8a;min-height:2px}
.bars span.hot{background:var(--warn)}
.feed{display:flex;flex-direction:column;gap:4px;font-size:13.5px}
.feed div{white-space:pre-wrap;overflow-wrap:anywhere}

.doc{max-height:70vh;overflow:auto}
.veil{position:fixed;inset:0;background:rgba(5,8,12,.72);display:flex;align-items:center;justify-content:center;padding:16px;z-index:10}
.dialog{width:min(720px,100%);max-height:90vh;overflow:auto;display:flex;flex-direction:column;gap:12px}
@media (max-width:720px){.side{position:static;max-height:none}.bubble{max-width:100%}.ask{position:static}
.link{display:none}.trail{flex-direction:column;align-items:flex-start}.view{padding:14px 16px}.logo{font-size:7px}}
'''

JS = r'''"use strict";
(function () {
  var $ = function (id) { return document.getElementById(id); };
  var each = function (sel, fn) { Array.prototype.forEach.call(document.querySelectorAll(sel), fn); };
  var me = null, chat = null, next = 0, busy = false, polling = false;
  var turn = null, raw = "", drawTimer = 0, health = null, timer = 0, view = "trace";
  var trace = null, hours = 6, kind = "cases", library = null, openDoc = null, picked = {}, changes = null, chosen = null;

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  function clear(e) { while (e.firstChild) e.removeChild(e.firstChild); }
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
      $("who").textContent = me.user + " · " + me.host + " · " + (me.model || "no model set");
      fill($("host"), [""].concat(me.hosts), function (h) { return h || "any"; });
      $("host-pick").hidden = me.hosts.length < 2;
      fill($("context"), me.contexts, function (c) { return c; });
      $("context-pick").hidden = !me.kubernetes;
      show(location.hash.slice(1) || "trace");
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
  var views = ["trace", "map", "changes", "console", "library"];
  var old = { chat: "trace", health: "map" };
  function show(name) {
    name = old[name] || name;
    if (views.indexOf(name) < 0) name = "trace";
    view = name;
    views.forEach(function (v) { $("view-" + v).hidden = v !== name; });
    each("nav button", function (b) {
      if (b.dataset.view === name) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
    });
    if (location.hash.slice(1) !== name) history.replaceState(null, "", "#" + name);
    if (name === "map" && health) drawMap();
    if (name === "changes") loadChanges();
    if (name === "console") drawConsole();
    if (name === "library") loadLibrary();
  }
  each("nav button", function (b) { b.addEventListener("click", function () { show(b.dataset.view); }); });

  // Every 30 s: health for the map and the badge; the console also wants the changes.
  function refresh() {
    if (!me) return;
    api("GET", "/api/health").then(function (h) {
      health = h;
      var n = h.problems.length;
      $("map-count").hidden = !n; $("map-count").textContent = n;
      if (view === "map") drawMap();
      if (view === "console") drawConsole();
    }).catch(function () {});
  }

  // ================= Trace =================
  function freshTrace() { trace = { title: "", started: 0, steps: 0, cause: "", fix: "", saved: false }; drawTrail(); }
  function openChat() {
    var saved = sessionStorage.getItem("chat");
    var got = saved ? Promise.resolve({ id: saved }) : api("POST", "/api/chat", {});
    got.then(function (c) {
      chat = c.id; next = 0;
      sessionStorage.setItem("chat", chat);
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
      if (data.busy) poll();
    }).catch(function (err) {
      polling = false;
      if (/gone/.test(err.message)) { sessionStorage.removeItem("chat"); openChat(); }
      else if (busy) setTimeout(poll, 3000);
    });
  }
  function setBusy(b) {
    busy = b;
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
      if (!trace.title) { trace.title = e.d; trace.started = e.time || Math.floor(Date.now() / 1000); }
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
    while ((m = re.exec(text))) last = m[2].replace(/[*_`]/g, "").trim();
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
      ? "Started " + clock(trace.started) + " by " + (me ? me.user : "") + " · " + trace.steps + (trace.steps === 1 ? " step" : " steps") + " · this page changes nothing"
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
  // From the other views: open the trace with a question ready, about the right host.
  function traceThis(question, host) {
    show("trace");
    if (host && me.hosts.indexOf(host) >= 0) $("host").value = host;
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
    $("map-when").textContent = h.failed ? "The last check failed: " + h.failed
      : h.checked ? "Checked " + ago(h.checked) + " ago, every " + h.every + " s" : "First check is running...";
    // clusters
    var cl = $("clusters"); clear(cl);
    $("cluster-block").hidden = !h.clusters.length;
    h.clusters.forEach(function (c) {
      var card = el("div", "card" + (c.problems ? " hot" : ""));
      var head = el("div", "head");
      head.appendChild(el("span", "name", c.name));
      head.appendChild(el("span", c.problems ? "warn small" : "okc small", c.problems ? "▲ " + c.problems + (c.problems === 1 ? " problem" : " problems") : "● healthy"));
      card.appendChild(head);
      card.appendChild(el("span", "muted small", c.kind + " · " + c.line));
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
      row.appendChild(what);
      var b = el("button", "", "Trace this"); b.type = "button";
      b.addEventListener("click", function () { traceThis("Why: " + p.text + "?", p.lane); });
      row.appendChild(b);
      pl.appendChild(row);
    });
  }
  function openContainer(host, c) {
    chosen = { host: host, c: c };
    $("problems-pane").hidden = true;
    $("container-pane").hidden = false;
    $("ct-where").textContent = host + " · container";
    $("ct-name").textContent = c.name;
    $("ct-status").textContent = c.status || c.state;
    $("ct-status").className = c.state === "running" ? "okc" : "warn";
    $("ct-image").textContent = c.image || "?";
    $("ct-restarts").textContent = String(c.restarts || 0);
    $("ct-started").textContent = c.started ? c.started.replace("T", " ").slice(0, 16) + " UTC" : "?";
    $("ct-logs").textContent = "Loading...";
    clear($("ct-changes"));
    $("ct-changes").appendChild(el("p", "muted", "Loading..."));
    drawMap();
    api("GET", "/api/container?host=" + encodeURIComponent(host) + "&name=" + encodeURIComponent(c.name)).then(function (d) {
      if (!chosen || chosen.c.name !== c.name) return;
      $("ct-logs").textContent = d.logs || "(no log lines)";
      var box = $("ct-changes"); clear(box);
      if (!d.changes.length) box.appendChild(el("p", "muted", "Nothing recorded."));
      d.changes.forEach(function (e) {
        var p = el("p"); p.appendChild(el("span", "muted mono", clock(e.time) + "  ")); p.appendChild(document.createTextNode(e.text)); box.appendChild(p);
      });
    }).catch(function (err) { $("ct-logs").textContent = err.message; clear($("ct-changes")); });
  }
  $("ct-close").addEventListener("click", function () { chosen = null; $("problems-pane").hidden = false; $("container-pane").hidden = true; drawMap(); });
  $("ct-trace").addEventListener("click", function () {
    if (!chosen) return;
    traceThis("Container " + chosen.c.name + " on " + chosen.host + " is " + (chosen.c.status || chosen.c.state) + ". What is wrong with it?", chosen.host);
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
    shown.slice().reverse().forEach(function (e) {
      var track = tracks[e.lane] || tracks[lanes[0]];
      var k = key(e);
      var b = el("button", "mk " + (e.kind === "problem" ? "problem" : e.kind === "cleared" ? "cleared" : e.kind === "cluster" ? "cluster" : "change"));
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
      var label = { problem: "problem starts", cleared: "ok again", cluster: "cluster record", change: "change" }[e.kind] || e.kind;
      row.appendChild(el("span", "tag" + (hot ? " hot" : e.kind === "cleared" ? " good" : ""), label));
      var what = el("span", "what");
      what.appendChild(el("span", "", e.text));
      row.appendChild(what);
      row.appendChild(el("span", "muted small", e.lane || ""));
      row.addEventListener("click", function () { pick(e); });
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

  // ================= Console =================
  var feed = null, feedAt = 0;
  function drawConsole() {
    if (!health || !me) return;
    var h = health, n = h.problems.length;
    $("console-watching").textContent = "─●─────●─────●─────▶ watching " + me.hosts.length + (me.hosts.length === 1 ? " host" : " hosts")
      + (h.clusters.length ? " · " + h.clusters.length + (h.clusters.length === 1 ? " cluster" : " clusters") : "");
    $("console-status").className = "card status" + (n ? " hot" : "");
    $("console-count").textContent = n;
    $("console-words").textContent = n ? (n === 1 ? "problem right now" : "problems right now") : "all well";
    $("console-words").className = n ? "warn" : "okc";
    var oldest = h.problems.filter(function (p) { return !p.known; }).map(function (p) { return p.since; }).sort()[0];
    $("console-oldest").textContent = n ? (oldest ? "oldest started " + clock(oldest) + " (" + ago(oldest) + ")" : "open since before the page started") : "checked " + (h.checked ? ago(h.checked) + " ago" : "soon");
    if (Date.now() - feedAt > 25000) {
      feedAt = Date.now();
      api("GET", "/api/changes?hours=6").then(function (d) { feed = d; drawConsoleRest(); }).catch(function () {});
    }
    drawConsoleRest();
  }
  function drawConsoleRest() {
    var h = health, box = $("console-lanes"); clear(box);
    ["NAME", "LAST HOUR", "STATE"].forEach(function (t) { box.appendChild(el("span", "muted", t)); });
    var lanes = me.hosts.concat(h.clusters.filter(function (c) { return c.kind === "Kubernetes"; }).map(function (c) { return c.name; }));
    var now = Math.floor(Date.now() / 1000);
    lanes.forEach(function (lane) {
      box.appendChild(el("span", "", lane));
      var bars = el("span", "bars"), counts = [], hot = [], top = 1;
      for (var i = 0; i < 12; i += 1) { counts.push(0); hot.push(false); }
      ((feed && feed.events) || []).forEach(function (e) {
        var slot = 11 - Math.floor((now - e.time) / 300);
        if (e.lane === lane && slot >= 0 && slot < 12) { counts[slot] += 1; if (e.kind === "problem") hot[slot] = true; }
      });
      counts.forEach(function (c) { top = Math.max(top, c); });
      counts.forEach(function (c, i) { var s = el("span", hot[i] ? "hot" : ""); s.style.height = (c ? 20 + c / top * 80 : 8) + "%"; bars.appendChild(s); });
      bars.setAttribute("aria-label", counts.reduce(function (a, b) { return a + b; }, 0) + " events in the last hour");
      box.appendChild(bars);
      var n = laneProblems(lane), host = (h.hosts || []).filter(function (x) { return x.name === lane; })[0];
      var state = n ? "▲ " + n + (n === 1 ? " problem" : " problems") : "● ok";
      if (host && host.containers) state += " · " + host.containers.filter(function (c) { return c.state === "running"; }).length + "/" + host.containers.length;
      if (host && host.error) state = "▲ not answering";
      box.appendChild(el("span", n || (host && host.error) ? "warn" : "okc", state));
    });
    var f = $("console-feed"); clear(f);
    var rows = ((feed && feed.events) || []).slice(0, 40);
    if (!rows.length) f.appendChild(el("div", "muted", feed ? "Nothing in the last 6 hours." : "Loading..."));
    rows.forEach(function (e) {
      var line = el("div");
      line.appendChild(el("span", "muted", clock(e.time) + "  "));
      line.appendChild(el("span", e.kind === "problem" ? "warn" : "", (e.lane || "") + "  "));
      line.appendChild(el("span", e.kind === "problem" ? "warn" : e.kind === "cleared" ? "okc" : "",
        (e.kind === "problem" ? "▲ " : e.kind === "cleared" ? "✓ " : "● ") + e.text));
      f.appendChild(line);
    });
  }
  $("full-screen").addEventListener("click", function () {
    var v = $("view-console");
    if (document.fullscreenElement) document.exitFullscreen(); else if (v.requestFullscreen) v.requestFullscreen();
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
