"""The web page's own files, kept as text in a .py file so an update is .py files only.

Nothing is loaded from anywhere else: no fonts, scripts or styles from the internet. The script puts
text on the page as text, never as markup, so nothing a log or a model says can run in the browser.
"""

INDEX = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Troubleshooter</title>
<link rel="stylesheet" href="/app.css">
</head>
<body>
<main id="login" hidden>
  <form id="login-form" class="card login">
    <h1>Troubleshooter</h1>
    <label>Name <input id="login-user" autocomplete="username" required></label>
    <label>Password <input id="login-password" type="password" autocomplete="current-password" required></label>
    <p id="login-error" class="error" role="alert"></p>
    <button class="primary" type="submit">Sign in</button>
  </form>
</main>

<div id="app" hidden>
  <header class="top">
    <strong class="brand">Troubleshooter</strong>
    <nav aria-label="Sections">
      <button type="button" data-view="chat">Chat</button>
      <button type="button" data-view="health">Health <span id="health-count" class="count" hidden></span></button>
      <button type="button" data-view="changes">What changed</button>
      <button type="button" data-view="library">Library</button>
    </nav>
    <span class="grow"></span>
    <span id="who" class="chip"></span>
    <button type="button" id="sign-out">Sign out</button>
  </header>

  <section id="view-chat" class="view two">
    <div class="main">
      <div id="talk" class="talk" aria-live="polite"></div>
      <form id="ask-form" class="ask">
        <div class="row">
          <label id="host-pick" hidden>About host <select id="host"></select></label>
          <label id="context-pick" hidden>Context <select id="context"></select></label>
          <span class="grow"></span>
          <span class="muted">This page looks only. Fixes are done in ./ts.</span>
        </div>
        <label class="wide">Ask, or paste command output
          <textarea id="question" rows="3" placeholder="Why is pod X restarting?"></textarea>
        </label>
        <div class="row">
          <button class="primary" type="submit" id="ask">Ask</button>
          <button type="button" id="stop" hidden>Stop</button>
          <span class="grow"></span>
          <button type="button" id="report">Write report</button>
          <button type="button" id="case">Save as case</button>
          <button type="button" id="new">New conversation</button>
        </div>
      </form>
    </div>
    <aside class="side card" aria-label="Full output of a step">
      <h2 id="evidence-title">Step output</h2>
      <pre id="evidence" class="mono">Select a step to see everything it returned.</pre>
    </aside>
  </section>

  <section id="view-health" class="view two" hidden>
    <div class="main">
      <div class="row"><h2>Open problems <span id="problem-count" class="warn"></span></h2><span class="grow"></span><span id="health-when" class="muted"></span></div>
      <div id="problems" class="list"></div>
      <h2>Everything watched</h2>
      <div id="cards" class="cards"></div>
    </div>
    <aside class="side card" aria-label="What came and went">
      <h2>What came and went</h2>
      <div id="health-events" class="mono small"></div>
    </aside>
  </section>

  <section id="view-changes" class="view" hidden>
    <div class="row">
      <div class="seg" role="group" aria-label="Period">
        <button type="button" data-hours="1">1 h</button>
        <button type="button" data-hours="6">6 h</button>
        <button type="button" data-hours="24">24 h</button>
        <button type="button" data-hours="168">7 d</button>
      </div>
      <label>Find <input id="changes-find" type="search" placeholder="name or word"></label>
      <span class="grow"></span>
      <span id="changes-note" class="muted"></span>
    </div>
    <div class="card strip">
      <div class="strip-row"><span class="muted">Recording</span><div id="strip-recording" class="bar"></div></div>
      <div class="strip-row"><span class="muted">Changes</span><div id="strip-changes" class="bar clear"></div></div>
      <div class="strip-row"><span class="muted">Problems</span><div id="strip-problems" class="bar clear"></div></div>
      <div class="strip-row"><span></span><div id="strip-times" class="times mono small"></div></div>
    </div>
    <div id="changes" class="list"></div>
  </section>

  <section id="view-library" class="view two" hidden>
    <div class="side-left">
      <div class="row">
        <div class="seg" role="group" aria-label="Kind">
          <button type="button" data-kind="cases">Cases</button>
          <button type="button" data-kind="runbooks">Runbooks</button>
          <button type="button" data-kind="reports">Reports</button>
        </div>
      </div>
      <label class="wide">Find <input id="library-find" type="search" placeholder="error text or app"></label>
      <div id="library-list" class="list"></div>
    </div>
    <div class="main card">
      <div class="row"><h2 id="doc-title">Select an item</h2><span class="grow"></span>
        <button type="button" id="doc-edit" hidden>Edit</button>
        <button type="button" id="doc-delete" hidden>Delete</button></div>
      <p id="doc-note" class="muted"></p>
      <pre id="doc" class="mono doc"></pre>
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
:root{color-scheme:dark;--ground:#0f1419;--panel:#171d25;--deep:#10161d;--line:#2a323d;--text:#e6e9ee;--muted:#aab4c2;
--blue:#6cb0ff;--blue-ink:#08121d;--blue-soft:#1f2f3f;--blue-text:#b9daff;--warn:#ffc674;--warn-ground:#2a2113;
--warn-line:#b9843a;--ok:#9fe0bd;--bad:#ffb3a7}
*{box-sizing:border-box}
html,body{margin:0;background:var(--ground);color:var(--text);font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
[hidden]{display:none!important}
h1{font-size:20px;margin:0 0 8px}
h2{font-size:15px;margin:0;font-weight:600}
p{margin:0}
a{color:#8cc2ff}
button,select,input,textarea{font:inherit;color:var(--text)}
button{min-height:40px;padding:0 14px;border-radius:8px;border:1px solid var(--line);background:none;cursor:pointer}
button:hover{border-color:#55606e}
button:focus-visible,select:focus-visible,input:focus-visible,textarea:focus-visible{outline:2px solid var(--blue);outline-offset:2px}
button:disabled{opacity:.5;cursor:default}
button.primary{background:var(--blue);color:var(--blue-ink);border:0;font-weight:600}
button.danger{border-color:#ff9d8f;color:var(--bad);font-weight:600}
button.warnb{background:var(--warn);color:#20170a;border:0;font-weight:600}
select,input,textarea{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:8px 10px;min-height:40px}
textarea{width:100%;resize:vertical}
label{color:var(--muted);display:inline-flex;gap:8px;align-items:center}
label.wide{display:flex;flex-direction:column;align-items:stretch;gap:4px;font-size:12px}
.mono{font-family:ui-monospace,"SFMono-Regular",Menlo,Consolas,monospace;font-size:12.5px}
.small{font-size:12px}
.muted{color:var(--muted)}
.warn{color:var(--warn)}
.error{color:var(--bad);min-height:1.4em}
.grow{flex:1 1 20px}
.row{display:flex;flex-wrap:wrap;gap:10px;align-items:center}
.row.end{justify-content:flex-end}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.chip{padding:6px 10px;border:1px solid var(--line);border-radius:6px;color:var(--muted)}
.count{background:var(--warn);color:#20170a;border-radius:999px;padding:0 7px;font-size:12px;font-weight:600}
.top{display:flex;flex-wrap:wrap;gap:12px;align-items:center;padding:10px 20px;border-bottom:1px solid var(--line);background:#131920}
.brand{font-size:16px}
nav{display:flex;flex-wrap:wrap;gap:4px}
nav button{border:0;color:var(--muted)}
nav button[aria-current="page"]{background:#223043;color:var(--text);font-weight:500}
.login{max-width:360px;margin:12vh auto;display:flex;flex-direction:column;gap:12px}
.login label{display:flex;flex-direction:column;align-items:stretch;gap:4px}
.view{padding:16px 20px;display:flex;flex-direction:column;gap:14px}
.view.two{flex-direction:row;flex-wrap:wrap;align-items:flex-start;gap:16px}
.main{flex:999 1 520px;min-width:0;display:flex;flex-direction:column;gap:12px}
.side{flex:1 1 340px;min-width:0;display:flex;flex-direction:column;gap:8px;position:sticky;top:12px;max-height:calc(100vh - 24px)}
.side pre,.side>div{overflow:auto;margin:0}
.side pre{white-space:pre}
.side-left{flex:1 1 340px;min-width:0;display:flex;flex-direction:column;gap:10px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;margin:0}
.talk{display:flex;flex-direction:column;gap:12px;min-height:180px}
.bubble{align-self:flex-end;max-width:80%;background:#223043;border-radius:10px;padding:10px 14px;white-space:pre-wrap;overflow-wrap:anywhere}
.steps{display:flex;flex-direction:column;gap:2px}
.steps .head{font-size:12px;text-transform:uppercase;letter-spacing:.6px;color:var(--muted);display:flex;justify-content:space-between}
.step{display:flex;gap:10px;align-items:center;text-align:left;border:0;border-radius:6px;min-height:34px;padding:4px 8px;width:100%}
.step:hover,.step[aria-pressed="true"]{background:#1d2732}
.step .state{color:var(--ok);min-width:44px}
.step .state.run{color:var(--warn)}
.step .more{margin-left:auto;color:#8cc2ff;font-size:12px}
.answer{display:flex;flex-direction:column;gap:8px;overflow-wrap:anywhere}
.answer p{white-space:pre-wrap}
.answer h3{margin:6px 0 0;font-size:12px;text-transform:uppercase;letter-spacing:.6px;color:var(--muted);font-weight:600}
.answer ul{margin:0;padding-left:20px}
.answer code{background:var(--deep);border-radius:4px;padding:1px 5px}
.answer pre{background:var(--deep);border-radius:6px;padding:10px 12px;overflow-x:auto;white-space:pre}
.notice{color:var(--warn)}
.info{color:var(--ok)}
.ask{display:flex;flex-direction:column;gap:8px;position:sticky;bottom:0;background:var(--ground);padding:10px 0}
.list{display:flex;flex-direction:column;gap:8px}
.item{display:flex;flex-wrap:wrap;gap:12px;align-items:center;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 14px}
.item.hot{background:var(--warn-ground);border-color:var(--warn-line)}
.item.pick{cursor:pointer;text-align:left;width:100%;min-height:0;flex-direction:column;align-items:stretch;gap:2px}
.item.pick[aria-pressed="true"]{border-color:var(--blue);background:#1d2732}
.item .when{min-width:64px;color:var(--muted)}
.item.hot .when{color:var(--warn)}
.item .what{flex:1 1 280px;min-width:0;overflow-wrap:anywhere}
.tag{padding:2px 8px;border-radius:999px;background:var(--blue-soft);color:var(--blue-text);font-size:12px;white-space:nowrap}
.tag.hot{background:#4a3a1c;color:#ffd596}
.tag.good{background:#1d3a2d;color:var(--ok)}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px}
.cards .card{display:flex;flex-direction:column;gap:4px;padding:12px 14px}
.cards .card.hot{border-color:var(--warn-line)}
.cards .head{display:flex;justify-content:space-between;gap:8px}
.cards .name{font-weight:600;overflow-wrap:anywhere}
.seg{display:flex;border:1px solid var(--line);border-radius:8px;overflow:hidden}
.seg button{border:0;border-radius:0;color:var(--muted)}
.seg button[aria-pressed="true"]{background:#223043;color:var(--text);font-weight:500}
.strip{display:flex;flex-direction:column;gap:10px;overflow-x:auto}
.strip-row{display:grid;grid-template-columns:100px minmax(520px,1fr);gap:12px;align-items:center}
.bar{position:relative;height:12px;background:var(--deep);border-radius:6px}
.bar.clear{background:none;height:22px}
.bar span{position:absolute;top:0;bottom:0;border-radius:4px;min-width:4px}
#strip-recording span{background:#3d5a7a}
#strip-changes span{background:var(--blue)}
#strip-problems span{background:var(--warn)}
.times{display:flex;justify-content:space-between;color:var(--muted)}
.doc{max-height:70vh;overflow:auto}
.veil{position:fixed;inset:0;background:rgba(5,8,12,.72);display:flex;align-items:center;justify-content:center;padding:16px;z-index:10}
.dialog{width:min(720px,100%);max-height:90vh;overflow:auto;display:flex;flex-direction:column;gap:12px}
@media (max-width:720px){.side{position:static;max-height:none}.bubble{max-width:100%}.ask{position:static}}
'''

JS = r'''"use strict";
(function () {
  var $ = function (id) { return document.getElementById(id); };
  var me = null, chat = null, next = 0, busy = false, polling = false;
  var turn = null, raw = "", drawTimer = 0, picked = null;
  var hours = 6, kind = "cases", library = null, openDoc = null, healthTimer = 0;

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }
  function clear(e) { while (e.firstChild) e.removeChild(e.firstChild); }

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
      show(location.hash.slice(1) || "chat");
      openChat();
      refreshHealth();
      clearInterval(healthTimer);
      healthTimer = setInterval(refreshHealth, 30000);
    }).catch(function () { showLogin(); });
  }
  function fill(select, values, label) {
    clear(select);
    values.forEach(function (v) { var o = el("option", "", label(v)); o.value = v; select.appendChild(o); });
  }

  // ---------- sections ----------
  var views = ["chat", "health", "changes", "library"];
  function show(name) {
    if (views.indexOf(name) < 0) name = "chat";
    views.forEach(function (v) { $("view-" + v).hidden = v !== name; });
    Array.prototype.forEach.call(document.querySelectorAll("nav button"), function (b) {
      if (b.dataset.view === name) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
    });
    if (location.hash.slice(1) !== name) history.replaceState(null, "", "#" + name);
    if (name === "health") refreshHealth();
    if (name === "changes") loadChanges();
    if (name === "library") loadLibrary();
  }
  Array.prototype.forEach.call(document.querySelectorAll("nav button"), function (b) {
    b.addEventListener("click", function () { show(b.dataset.view); });
  });

  // ---------- chat ----------
  function openChat() {
    var saved = sessionStorage.getItem("chat");
    var got = saved ? Promise.resolve({ id: saved }) : api("POST", "/api/chat", {});
    got.then(function (c) {
      chat = c.id; next = 0;
      sessionStorage.setItem("chat", chat);
      clear($("talk"));
      turn = null;
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
  }
  function newTurn() {
    var box = el("div", "card");
    var steps = el("div", "steps");
    var head = el("div", "head");
    head.appendChild(el("span", "", "What I looked at"));
    var count = el("span", "", "");
    head.appendChild(count);
    steps.appendChild(head);
    steps.hidden = true;
    var answer = el("div", "answer");
    box.appendChild(steps); box.appendChild(answer);
    $("talk").appendChild(box);
    turn = { box: box, steps: steps, count: count, answer: answer, n: 0, rows: {} };
    raw = "";
  }
  function handle(e) {
    if (e.t === "ask") {
      $("talk").appendChild(el("div", "bubble", e.d));
      newTurn();
    } else if (e.t === "new") {
      clear($("talk")); turn = null;
      $("evidence").textContent = "Select a step to see everything it returned.";
      $("evidence-title").textContent = "Step output";
    } else {
      if (!turn) newTurn();
      if (e.t === "step") {
        var b = el("button", "step");
        b.type = "button";
        var state = el("span", "state run mono", "now");
        b.appendChild(state);
        b.appendChild(el("span", "", e.d));
        var more = el("span", "more", "");
        b.appendChild(more);
        b.disabled = true;
        b.addEventListener("click", function () { showStep(e.n, e.d, b); });
        turn.steps.hidden = false;
        turn.steps.appendChild(b);
        turn.rows[e.n] = { b: b, state: state, more: more };
        turn.n += 1;
        turn.count.textContent = turn.n + (turn.n === 1 ? " step" : " steps");
        if (raw) { raw += "\n\n"; }
      } else if (e.t === "result") {
        var row = turn.rows[e.n];
        if (row) {
          row.state.textContent = "done"; row.state.className = "state mono";
          row.more.textContent = "see output";
          row.b.disabled = false;
        }
      } else if (e.t === "text") {
        raw += e.d;
        if (!drawTimer) drawTimer = setTimeout(draw, 120);
      } else if (e.t === "notice") {
        turn.box.appendChild(el("p", "notice", e.d));
      } else if (e.t === "saved") {
        turn.box.appendChild(el("p", "info", e.d));
      } else if (e.t === "case") {
        offerCase(e.d);
      } else if (e.t === "done") {
        draw();
        Object.keys(turn.rows).forEach(function (k) {
          var r = turn.rows[k];
          if (r.state.textContent === "now") { r.state.textContent = "ended"; r.state.className = "state mono"; }
        });
      }
    }
    window.scrollTo(0, document.body.scrollHeight);
  }
  function draw() {
    clearTimeout(drawTimer); drawTimer = 0;
    if (!turn) return;
    clear(turn.answer);
    render(raw, turn.answer);
  }
  function showStep(n, title, button) {
    if (picked) picked.setAttribute("aria-pressed", "false");
    picked = button; button.setAttribute("aria-pressed", "true");
    $("evidence-title").textContent = title;
    $("evidence").textContent = "Loading...";
    api("GET", "/api/chat/" + chat + "/step/" + n).then(function (d) { $("evidence").textContent = d.text; })
      .catch(function (err) { $("evidence").textContent = err.message; });
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
  $("stop").addEventListener("click", function () { api("POST", "/api/chat/" + chat + "/stop", {}).catch(failed); });
  $("report").addEventListener("click", function () { send("report"); });
  $("case").addEventListener("click", function () { send("case"); });
  $("new").addEventListener("click", function () {
    dialog({ title: "Start a new conversation?", text: "The model forgets this one. The log keeps it.",
      buttons: [{ label: "Start new", cls: "primary", value: true }, { label: "Keep this one", value: false }] })
      .then(function (yes) { if (yes) send("new"); });
  });
  $("context").addEventListener("change", function () {
    api("POST", "/api/chat/" + chat + "/context", { context: $("context").value }).then(poll).catch(failed);
  });
  function offerCase(text) {
    dialog({ title: "Save this case for every host?",
      text: "Later chats search saved cases first. Correct anything that is wrong before saving.",
      input: text,
      buttons: [{ label: "Save case", cls: "primary", value: "input" }, { label: "Drop it", value: null }] })
      .then(function (kept) {
        if (kept) api("POST", "/api/chat/" + chat + "/save-case", { text: kept }).then(poll).catch(failed);
      });
  }
  function askAbout(question) {
    show("chat");
    $("question").value = question;
    $("question").focus();
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
      if (para.length) { var p = el("p"); inline(para.join("\n"), p); into.appendChild(p); para = []; }
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
        list = null;
        para.push(line);
      }
      i += 1;
    }
    flush();
  }

  // ---------- health ----------
  function refreshHealth() {
    if (!me) return;
    api("GET", "/api/health").then(function (h) {
      var n = h.problems.length;
      $("health-count").hidden = !n; $("health-count").textContent = n;
      if ($("view-health").hidden) return;
      $("problem-count").textContent = n ? n : "none";
      $("health-when").textContent = h.failed ? "The last check failed: " + h.failed
        : h.checked ? "Checked " + ago(h.checked) + " ago, every " + h.every + " s" : "First check is running...";
      var list = $("problems"); clear(list);
      if (!n) list.appendChild(el("p", "muted", h.checked ? "Nothing needs a look." : ""));
      h.problems.forEach(function (p) {
        var row = el("div", "item" + (Date.now() / 1000 - p.since < 86400 && !p.known ? " hot" : ""));
        row.appendChild(el("span", "when mono", p.known ? "earlier" : clock(p.since)));
        var what = el("div", "what");
        what.appendChild(el("div", "", p.text));
        what.appendChild(el("div", "muted small", p.known ? "already open when the page started" : "open for " + ago(p.since)));
        row.appendChild(what);
        var b = el("button", "warnb", "Ask why"); b.type = "button";
        b.addEventListener("click", function () { askAbout("Why: " + p.text + "?"); });
        row.appendChild(b);
        list.appendChild(row);
      });
      var cards = $("cards"); clear(cards);
      h.lines.forEach(function (c) {
        var card = el("div", "card" + (c.problems ? " hot" : ""));
        var head = el("div", "head");
        head.appendChild(el("span", "muted", c.kind));
        head.appendChild(el("span", c.problems ? "warn" : "info", c.problems ? c.problems + (c.problems === 1 ? " problem" : " problems") : "all well"));
        card.appendChild(head);
        card.appendChild(el("div", "name", c.where));
        card.appendChild(el("div", "muted small", c.line));
        cards.appendChild(card);
      });
      var ev = $("health-events"); clear(ev);
      if (!h.events.length) ev.appendChild(el("p", "muted", "Nothing has changed since the page started."));
      h.events.forEach(function (e) {
        var p = el("p");
        p.appendChild(el("span", "muted", clock(e.time) + "  "));
        p.appendChild(el("span", e.kind === "new" ? "warn" : "info", e.kind === "new" ? "NEW  " : "OK AGAIN  "));
        p.appendChild(document.createTextNode(e.text));
        ev.appendChild(p);
      });
    }).catch(function () {});
  }

  // ---------- what changed ----------
  var changes = null;
  function loadChanges() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-hours]"), function (b) {
      b.setAttribute("aria-pressed", String(Number(b.dataset.hours) === hours));
    });
    $("changes-note").textContent = "Loading...";
    api("GET", "/api/changes?hours=" + hours).then(function (d) { changes = d; drawChanges(); })
      .catch(function (err) { $("changes-note").textContent = err.message; });
  }
  function place(bar, from, to, d) {
    var span = el("span"), width = d.now - d.since;
    span.style.left = Math.max(0, (from - d.since) / width * 100) + "%";
    span.style.width = Math.max(0.4, (Math.min(to, d.now) - Math.max(from, d.since)) / width * 100) + "%";
    bar.appendChild(span);
  }
  function drawChanges() {
    var d = changes, find = $("changes-find").value.trim().toLowerCase();
    ["strip-recording", "strip-changes", "strip-problems", "strip-times"].forEach(function (id) { clear($(id)); });
    d.coverage.forEach(function (c) { place($("strip-recording"), c[0], c[1], d); });
    for (var i = 0; i <= 6; i += 1) $("strip-times").appendChild(el("span", "", i === 6 ? "now" : clock(d.since + (d.now - d.since) * i / 6)));
    var list = $("changes"); clear(list);
    var shown = d.events.filter(function (e) { return !find || (e.text + " " + e.where).toLowerCase().indexOf(find) >= 0; });
    shown.forEach(function (e) {
      var hot = e.kind === "problem";
      place($(hot || e.kind === "cleared" ? "strip-problems" : "strip-changes"), e.time, e.time, d);
      var row = el("div", "item" + (hot ? " hot" : ""));
      row.appendChild(el("span", "when mono", clock(e.time)));
      var label = { problem: "problem starts", cleared: "ok again", cluster: "cluster record", change: "change" }[e.kind] || e.kind;
      row.appendChild(el("span", "tag" + (hot ? " hot" : e.kind === "cleared" ? " good" : ""), label));
      var what = el("div", "what");
      what.appendChild(el("div", "", e.text));
      if (e.where) what.appendChild(el("div", "muted small", e.where));
      row.appendChild(what);
      var b = el("button", "", "Ask about this"); b.type = "button";
      b.addEventListener("click", function () {
        askAbout("At " + clock(e.time) + (e.where ? " (" + e.where + ")" : "") + ": " + e.text + ". What does it mean, and did it cause a problem?");
      });
      row.appendChild(b);
      list.appendChild(row);
    });
    if (!shown.length) list.appendChild(el("p", "muted", find ? "Nothing in this period matches." : "Nothing changed in this period."));
    var gaps = d.coverage.length ? "" : "Nothing was recording in this period. ";
    $("changes-note").textContent = gaps + shown.length + (shown.length === 1 ? " entry" : " entries") + " · times are this browser's";
  }
  Array.prototype.forEach.call(document.querySelectorAll("[data-hours]"), function (b) {
    b.addEventListener("click", function () { hours = Number(b.dataset.hours); loadChanges(); });
  });
  $("changes-find").addEventListener("input", function () { if (changes) drawChanges(); });

  // ---------- library ----------
  function loadLibrary() {
    api("GET", "/api/library").then(function (d) { library = d; drawLibrary(); }).catch(failed);
  }
  function drawLibrary() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-kind]"), function (b) {
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
      : "Written by a model from a chat. Check it before passing it on.";
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
  Array.prototype.forEach.call(document.querySelectorAll("[data-kind]"), function (b) {
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
