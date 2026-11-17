"""The web page, served by the hub over https.

It looks and never changes anything: the fix tools are not offered here, so fixes stay in ./ts, where
they run as the person typing. Everyone signs in with an account from .env (./ts user add NAME).

  /                     the page (its files are in webpage.py)
  /api/login, /logout   sign in and out
  /api/chat...          one conversation per browser tab, read by the page as a list of events
  /api/health           what the watch sees, kept fresh in the background
  /api/changes          the timeline of what changed
  /api/library...       cases, runbooks and incident reports
"""
import dataclasses
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from . import cases, config, history, hosts, llm, watch, webpage
from .engine import Session
from .redact import mask, strip_control
from .tools import NAME, BadInput, Toolbox

SESSION_HOURS = 12
MOST_CHATS = 40
MOST_BODY = 300_000
WAIT = 25                 # seconds a request for events waits for news
HEALTH_EVERY = 60
EVENTS_KEPT = 2000
ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def users(settings):
    return dict(u.split(":", 1) for u in settings.web_users.split(",") if ":" in u)


def password_ok(stored, given):
    try:
        kind, rounds, salt, want = stored.split("$")
        if kind != "pbkdf2":
            return False
        got = hashlib.pbkdf2_hmac("sha256", given.encode("utf-8"), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(got.hex(), want)
    except (ValueError, TypeError):
        return False


class Chat:
    """One conversation. It is also the engine's UI: what the engine says becomes a list of events."""

    def __init__(self, cid, user, settings, log):
        self.id, self.user, self.s = cid, user, settings
        self.events, self.outputs, self.steps = [], {}, 0
        self.cond = threading.Condition()
        self.busy, self.stopped, self.used = False, False, time.time()
        self.log = lambda line: log(f"[page, {user}] {line}")
        self.session = Session(settings, self, self.log)

    def add(self, kind, **data):
        with self.cond:
            self.events.append({"t": kind, **data})
            self.cond.notify_all()

    # --- what the engine calls ---
    def text(self, text):
        if self.stopped:
            raise KeyboardInterrupt
        self.add("text", d=text)

    def step(self, text):
        if self.stopped:
            raise KeyboardInterrupt
        self.steps += 1
        self.add("step", n=self.steps, d=strip_control(text))

    def result(self, text):
        self.outputs[self.steps] = text
        self.add("result", n=self.steps, size=len(text))

    def notice(self, text):
        self.add("notice", d=text)

    def approve(self, title, reason, command):
        return False  # the page looks only

    # --- what the page asks for ---
    def run(self, job):
        """Start a piece of work in the background. False if one is already running."""
        with self.cond:
            if self.busy:
                return False
            self.busy, self.stopped, self.used = True, False, time.time()

        def work():
            try:
                job()
            except Exception as e:  # the page must hear about it, whatever it was
                self.add("notice", d=f"That failed: {e}")
            finally:
                with self.cond:
                    self.busy = False
                    self.events.append({"t": "done"})
                    self.cond.notify_all()
        threading.Thread(target=work, daemon=True).start()
        return True

    def ask(self, question, host):
        self.add("ask", d=question, time=int(time.time()))
        if host and host in self.session.box.agents:
            self.session.note = f"(The operator is asking about the host {host}.)\n" + self.session.note
        self.session.ask(question)

    def report(self):
        self.add("ask", d="Write this conversation up as an incident report.")
        text = self.session.report()
        if text:
            self.add("saved", d=f"Saved in the log folder: reports/{cases.save_report(self.s, text)}")

    def case(self):
        self.add("ask", d="Write a case note about this problem.")
        text = self.session.case()
        if text:
            self.add("case", d=text)

    def since(self, after):
        """Events after number `after`, waiting a while if there are none yet."""
        with self.cond:
            if len(self.events) <= after and self.busy:
                self.cond.wait(WAIT)
            return self.events[after:after + 2000], self.busy


class Health:
    """The watch, kept running: the problems open now, when each started, and what came and went."""

    def __init__(self, settings, log):
        self.s, self.log = settings, log
        self.box = Toolbox(dataclasses.replace(settings, allow_changes=False))
        self.lock = threading.Lock()
        self.open, self.lines, self.checked, self.failed = {}, [], 0, ""
        self.hosts = []
        self.path = os.path.join(settings.log_dir, "watch-events.jsonl")
        self.events = self._load()

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return [json.loads(row) for row in f.read().splitlines()[-EVENTS_KEPT:] if row.strip()]
        except (OSError, ValueError):
            return []

    def _keep(self, new):
        try:
            if len(self.events) > EVENTS_KEPT * 2:
                self.events = self.events[-EVENTS_KEPT:]
                with open(self.path, "w", encoding="utf-8") as f:
                    f.write("".join(json.dumps(e) + "\n" for e in self.events))
            else:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write("".join(json.dumps(e) + "\n" for e in new))
        except OSError:
            pass

    def _hosts(self):
        """Every host's containers and fill, for the map. A host that can't say is shown with the reason."""
        box = self.box

        def one(host):
            if host == box.here:
                found = watch.local_map(box)
            else:
                try:
                    found = json.loads(hosts.ask(self.s, host, "_map", timeout=45))
                    if not isinstance(found, dict):
                        raise ValueError("not a map")
                except hosts.Unreachable as e:
                    found = {"error": str(e)}
                except ValueError:
                    found = {"error": "its agent is older than this page: copy the new .py files there"}
            return {"name": host, "role": "hub" if box.agents and host == box.here else "agent" if host != box.here else "",
                    **found}
        with ThreadPoolExecutor(max_workers=8) as pool:
            return list(pool.map(one, [box.here, *box.agents]))

    def round(self):
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                seen, found = pool.submit(watch.survey, self.box), pool.submit(self._hosts)
                seen, found = seen.result(), found.result()
        except Exception as e:
            with self.lock:
                self.failed = str(e)
            return
        now, new = int(time.time()), []
        where = lambda k: watch.lane(k, self.box.here, self.box.agents)
        with self.lock:
            first = not self.checked
            for k, text in seen["problems"].items():
                if k not in self.open:
                    self.open[k] = {"text": text, "since": now, "known": first, "lane": where(k)}
                    if not first:
                        new.append({"time": now, "kind": "new", "text": text, "lane": where(k)})
                        self.log(f"The page's watch found a new problem: {text}")
                else:
                    self.open[k]["text"] = text
            for k in [k for k in self.open if k not in seen["problems"]]:
                new.append({"time": now, "kind": "ok", "text": self.open.pop(k)["text"], "lane": where(k)})
            self.lines, self.hosts, self.checked, self.failed = seen["lines"], found, now, ""
            self.events += new
        if new:
            self._keep(new)

    def forever(self):
        while True:
            self.round()
            time.sleep(HEALTH_EVERY)

    def state(self):
        with self.lock:
            return {"checked": self.checked, "every": HEALTH_EVERY, "failed": self.failed,
                    "problems": sorted(({"text": p["text"], "since": p["since"], "known": p["known"], "lane": p["lane"]}
                                        for p in self.open.values()), key=lambda p: -p["since"]),
                    "lines": self.lines, "hosts": self.hosts, "events": self.events[-60:][::-1],
                    "clusters": [{"name": c["where"], "kind": c["kind"], "line": c["line"], "problems": c["problems"]}
                                 for c in self.lines if c["kind"] in ("Kubernetes", "VKS cluster")]}

    def problem_events(self, since):
        with self.lock:
            return [e for e in self.events if e["time"] >= since]


class Site:
    """Everything the page's requests share."""

    def __init__(self, settings, log):
        window = llm.context_tokens(settings) if settings.llm_url else None
        if window:
            room = max(window - settings.llm_max_tokens - 3000, 2000) * 3
            settings = dataclasses.replace(settings, max_context_chars=min(settings.max_context_chars, room))
        self.s = dataclasses.replace(settings, allow_changes=False)  # the page looks only
        self.log = log
        self.sessions, self.chats, self.fails = {}, {}, {}
        self.lock = threading.Lock()
        self.health = Health(settings, log)
        self._kube = (0, [])

    # --- signing in ---
    def sign_in(self, name, password, caller):
        now = time.time()
        with self.lock:
            tries = [t for t in self.fails.get(caller, []) if now - t < 300]
            if len(tries) >= 5:
                return None, "Too many wrong tries. Wait five minutes."
        stored = users(self.s).get(name)
        # Check a password even for an unknown name, so the answer takes as long either way.
        ok = password_ok(stored or "pbkdf2$200000$00$00", password) and stored is not None
        with self.lock:
            if not ok:
                self.fails[caller] = tries + [now]
                self.log(f"A sign-in to the page failed for the name {mask(strip_control(name))[:40]} from {caller}")
                return None, "Wrong name or password."
            self.fails.pop(caller, None)
            token = secrets.token_urlsafe(32)
            self.sessions = {t: v for t, v in self.sessions.items() if v[1] > now}
            self.sessions[token] = (name, now + SESSION_HOURS * 3600)
        self.log(f"{name} signed in to the page from {caller}")
        return token, ""

    def who(self, token):
        with self.lock:
            found = self.sessions.get(token or "")
            return found[0] if found and found[1] > time.time() else None

    def sign_out(self, token):
        with self.lock:
            self.sessions.pop(token or "", None)

    # --- chats ---
    def chat(self, user, cid=None):
        """The user's chat with that id, or a new one."""
        with self.lock:
            found = self.chats.get(cid or "")
            if found and found.user == user:
                found.used = time.time()
                return found
            if cid:
                return None
            idle = sorted((c for c in self.chats.values() if not c.busy), key=lambda c: c.used)
            for old in idle[:max(0, len(self.chats) - MOST_CHATS + 1)]:
                del self.chats[old.id]
            for old in [c for c in idle if time.time() - c.used > SESSION_HOURS * 3600]:
                self.chats.pop(old.id, None)
        made = Chat(secrets.token_urlsafe(12), user, self.s, self.log)  # outside the lock: it reads kubeconfigs
        with self.lock:
            self.chats[made.id] = made
        return made

    # --- the timeline ---
    def changes(self, hours):
        now = int(time.time())
        since = now - max(1, min(hours, 24 * history.KEEP_DAYS)) * 3600
        box = self.health.box
        found = [{**e, "kind": "change", "lane": box.here if e["where"] == "Docker" else e["where"],
                  "where": e["where"] if e["where"] != "Docker" else f"Docker on {box.here}"}
                 for e in history.events(self.s, since)]
        own = history.coverage(self.s, since)
        coverage = {box.here: own, **{cid: own for cid in box.clusters}}

        def other(host):
            try:
                rows = [{**e, "kind": "change", "lane": host, "where": f"{e.get('where', 'Docker')} on {host}"}
                        for e in json.loads(hosts.ask(self.s, host, "_events", {"since": since}, timeout=30))]
                spans = json.loads(hosts.ask(self.s, host, "_coverage", {"since": since}, timeout=30))
                return host, rows, spans
            except (hosts.Unreachable, ValueError):
                return host, [], None
        with ThreadPoolExecutor(max_workers=8) as pool:
            for host, rows, spans in pool.map(other, box.agents):
                found += rows
                coverage[host] = spans
        if now - self._kube[0] > 60:  # what the clusters themselves record; asked at most once a minute
            def own(cid):
                try:
                    return [{"time": when, "where": cid, "lane": cid, "text": text, "kind": "cluster"}
                            for when, text in history.timeline_rows(box, cid, now - 24 * history.KEEP_DAYS * 3600)]
                except Exception:
                    return []
            with ThreadPoolExecutor(max_workers=8) as pool:
                self._kube = (now, [row for rows in pool.map(own, list(box.clusters)) for row in rows])
        found += [e for e in self._kube[1] if e["time"] >= since]
        found += [{"time": e["time"], "where": e.get("lane", ""), "lane": e.get("lane", box.here), "text": e["text"],
                   "kind": "problem" if e["kind"] == "new" else "cleared"} for e in self.health.problem_events(since)]
        found.sort(key=lambda e: -e["time"])
        return {"since": since, "now": now, "events": found[:800], "lanes": [box.here, *box.agents, *box.clusters],
                "coverage": coverage}

    # --- one container, for the map ---
    def container(self, host, name):
        box = self.health.box
        if host not in (box.here, *box.agents):
            raise BadInput(f"{host} is not one of the hosts.")
        if not NAME.match(name):
            raise BadInput("That is not a container name.")
        logs = box.tools["docker_logs"]
        text = logs.fn(**logs.check({"container": name, "tail": 30, **({"host": host} if box.agents else {})}))
        word = name.lower()
        changed = [e for e in self.changes(24)["events"] if e.get("lane") == host and word in e["text"].lower()][:8]
        return {"logs": box.out(str(text)), "changes": changed}

    # --- the library ---
    def library(self):
        found = cases.all_cases(self.s)
        here = socket.gethostname()
        return {
            "cases": [{"id": f"{host}/{name}", "title": cases.title(text), "host": host,
                       "saved": f"{name[:4]}-{name[4:6]}-{name[6:8]}", "find": text.lower()[:4000]} for host, name, text in found],
            "runbooks": [{"id": f"{host}/{app}", "title": app, "host": host, "status": status,
                          "find": f"{app} {host}".lower()} for host, app, status in self.health.box._runbooks()],
            "reports": [{"id": name, "title": f"Incident report {name[:4]}-{name[4:6]}-{name[6:8]} {name[9:11]}:{name[11:13]}",
                         "host": here, "find": name} for name in cases.reports(self.s)]}

    def document(self, kind, key):
        if kind == "cases":
            return cases.find(self.s, key)
        if kind == "reports":
            return cases.read_report(self.s, key)
        if kind == "runbooks":
            host, _, app = key.partition("/")
            base = os.path.realpath(self.s.runbook_dir)
            path = os.path.realpath(os.path.join(base, host, app + ".md"))
            if not path.startswith(base + "/") or "/_" in path[len(base):]:
                return None
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    text = strip_control(f.read(200_000))
                return text
            except OSError:
                return None
        return None


def _handler(site):
    s = site.s

    class Handler(hosts.Secure):
        timeout = WAIT + 35

        # --- plumbing ---
        def _send(self, code, body, kind="application/json", extra=()):
            if not isinstance(body, bytes):
                body = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Strict-Transport-Security", "max-age=31536000")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
            for name, value in extra:
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _token(self):
            for part in self.headers.get("Cookie", "").split(";"):
                name, _, value = part.strip().partition("=")
                if name == "ts_session":
                    return value
            return ""

        def _body(self):
            size = int(self.headers.get("Content-Length", "0") or 0)
            if not 0 <= size <= MOST_BODY:
                raise ValueError("too large")
            found = json.loads(self.rfile.read(size) or b"{}")
            if not isinstance(found, dict):
                raise ValueError("not an object")
            return found

        def _path(self):
            parsed = urllib.parse.urlsplit(self.path)
            return [p for p in parsed.path.split("/") if p], {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}

        # --- requests ---
        def do_GET(self):
            try:
                parts, query = self._path()
                if not parts:
                    return self._send(200, webpage.INDEX.encode(), "text/html; charset=utf-8")
                if parts == ["app.js"]:
                    return self._send(200, webpage.JS.encode(), "text/javascript; charset=utf-8")
                if parts == ["app.css"]:
                    return self._send(200, webpage.CSS.encode(), "text/css; charset=utf-8")
                if parts == ["favicon.ico"]:
                    return self._send(204, b"", "image/x-icon")
                if parts[0] != "api":
                    return self._send(404, {"error": "Not found."})
                user = site.who(self._token())
                if not user:
                    return self._send(401, {"error": "Sign in first."})
                self._get(user, parts[1:], query)
            except (ValueError, KeyError, IndexError):
                self._send(400, {"error": "That request was not understood."})
            except (BrokenPipeError, ConnectionError, socket.timeout):
                pass

        def _get(self, user, parts, query):
            if parts == ["me"]:
                box = site.health.box
                return self._send(200, {
                    "user": user, "host": box.here, "model": s.llm_model, "kubernetes": bool(box.clusters),
                    "hosts": [box.here, *box.agents], "contexts": list(box.clusters)})
            if parts == ["health"]:
                return self._send(200, site.health.state())
            if parts == ["container"]:
                try:
                    return self._send(200, site.container(query.get("host", ""), query.get("name", "")))
                except BadInput as e:
                    return self._send(400, {"error": str(e)})
            if parts == ["changes"]:
                return self._send(200, site.changes(int(query.get("hours", "6"))))
            if parts == ["library"]:
                return self._send(200, site.library())
            if len(parts) == 2 and parts[0] == "library" and parts[1] in ("cases", "runbooks", "reports"):
                text = site.document(parts[1], query.get("id", ""))
                return self._send(200, {"text": text}) if text is not None else self._send(404, {"error": "It is no longer there."})
            if len(parts) >= 3 and parts[0] == "chat" and ID.match(parts[1]):
                chat = site.chat(user, parts[1])
                if not chat:
                    return self._send(404, {"error": "That conversation is gone. Start a new one."})
                if parts[2] == "events":
                    after = max(0, int(query.get("after", "0")))
                    rows, busy = chat.since(after)
                    return self._send(200, {"events": rows, "next": after + len(rows), "busy": busy,
                                            "context": chat.session.box.active})
                if parts[2] == "step" and len(parts) == 4:
                    text = chat.outputs.get(int(parts[3]))
                    return self._send(200, {"text": text if text is not None else "This step returned nothing to show."})
            self._send(404, {"error": "Not found."})

        def do_POST(self):
            try:
                parts, _ = self._path()
                # A page on another site can't set this header, so it can't act for a signed-in person.
                if parts[:1] != ["api"] or self.headers.get("X-Requested-With") != "troubleshooter":
                    return self._send(400, {"error": "That request was not understood."})
                asked = self._body()
                if parts == ["api", "login"]:
                    token, why = site.sign_in(str(asked.get("user", ""))[:64], str(asked.get("password", ""))[:256],
                                              self.client_address[0])
                    if not token:
                        return self._send(403, {"error": why})
                    cookie = f"ts_session={token}; Path=/; Max-Age={SESSION_HOURS * 3600}; HttpOnly; Secure; SameSite=Strict"
                    return self._send(200, {"ok": True}, extra=[("Set-Cookie", cookie)])
                user = site.who(self._token())
                if not user:
                    return self._send(401, {"error": "Sign in first."})
                self._post(user, parts[1:], asked)
            except (ValueError, KeyError, IndexError):
                self._send(400, {"error": "That request was not understood."})
            except (BrokenPipeError, ConnectionError, socket.timeout):
                pass

        def _post(self, user, parts, asked):
            if parts == ["logout"]:
                site.sign_out(self._token())
                return self._send(200, {"ok": True}, extra=[("Set-Cookie", "ts_session=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Strict")])
            if parts == ["chat"]:
                return self._send(200, {"id": site.chat(user).id})
            if parts == ["library", "delete"]:
                cases.remove(s, str(asked.get("id", "")))
                site.log(f"[page, {user}] Deleted the case {strip_control(str(asked.get('id', '')))[:200]}")
                return self._send(200, {"ok": True})
            if parts == ["library", "save"]:
                cases.rewrite(s, str(asked.get("id", "")), str(asked.get("text", "")))
                site.log(f"[page, {user}] Edited the case {strip_control(str(asked.get('id', '')))[:200]}")
                return self._send(200, {"ok": True})
            if len(parts) == 3 and parts[0] == "chat" and ID.match(parts[1]):
                chat = site.chat(user, parts[1])
                if not chat:
                    return self._send(404, {"error": "That conversation is gone. Start a new one."})
                what = parts[2]
                if what == "stop":
                    chat.stopped = chat.busy
                    return self._send(200, {"ok": True})
                if what == "save-case":
                    name = cases.save(s, str(asked.get("text", "")), chat.session.box.active or "")
                    chat.log(f"You saved a case: {name}")
                    chat.add("saved", d="Case saved. Other hosts see it after ./ts next runs on this host.")
                    return self._send(200, {"ok": True})
                if what == "context":
                    if chat.busy:
                        return self._send(409, {"error": "Wait for the answer to finish, or stop it."})
                    try:
                        chat.add("notice", d=chat.session.use_context(str(asked.get("context", ""))))
                    except BadInput as e:
                        return self._send(400, {"error": str(e)})
                    return self._send(200, {"ok": True})
                jobs = {"ask": lambda: chat.ask(question, host), "report": chat.report, "case": chat.case,
                        "new": lambda: (chat.session.reset(), chat.add("new"))}
                if what in jobs:
                    question = str(asked.get("question", "")).strip()[:20000]
                    host = str(asked.get("host", ""))[:100]
                    if what == "ask" and not question:
                        return self._send(400, {"error": "Type a question first."})
                    if not chat.run(jobs[what]):
                        return self._send(409, {"error": "Wait for the answer to finish, or stop it."})
                    return self._send(200, {"ok": True})
            self._send(404, {"error": "Not found."})

    return Handler


def serve(settings, log=lambda line: None):
    why = hosts.not_ready(settings)
    if why:
        print(f"The web page is not served: {why}", flush=True)
        return
    if not users(settings):
        print("The web page is not served: there are no accounts. Add one with ./ts user add NAME, "
              "then docker compose up -d.", flush=True)
        return
    site = Site(settings, log)
    threading.Thread(target=site.health.forever, daemon=True).start()
    cert = "your own certificate" if hosts.own_web_cert(settings) else "the certificate ./ts setup made, so browsers warn"
    print(f"The web page: https://{socket.gethostname()}:{settings.web_port}  ({len(users(settings))} account(s), "
          f"{len(config.agents(settings))} other host(s), {cert})", flush=True)
    hosts.serve(settings, _handler(site), settings.web_port, web=True)
