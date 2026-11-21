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

from . import cases, config, history, hosts, tools, webpage
from . import autotrace
from .autotrace import AUTO, Chat, Health, AutoTrace, WAIT, cause_in  # noqa: F401  (tests use them here)
from .engine import Session
from .redact import mask, strip_control
from .tools import NAME, BadInput, Toolbox

SESSION_HOURS = 12
MOST_CHATS = 40
MOST_BODY = 300_000
ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def users(settings):
    return dict(u.split(":", 1) for u in settings.web_users.split(",") if ":" in u)


def account_whole(stored):
    """False when a password hash was cut short. Older ./ts wrote it with "$", and Docker Compose read
    "$" plus letters in .env as a variable, so part of the hash went missing."""
    parts = re.split(r"[$.]", stored or "")
    return len(parts) == 4 and parts[0] == "pbkdf2" and len(parts[2]) == 32 and len(parts[3]) == 64


def password_ok(stored, given):
    try:
        kind, rounds, salt, want = re.split(r"[$.]", stored)
        if kind != "pbkdf2":
            return False
        got = hashlib.pbkdf2_hmac("sha256", given.encode("utf-8"), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(got.hex(), want)
    except (ValueError, TypeError):
        return False


class Site:
    """Everything the page's requests share."""

    def __init__(self, settings, log, health=None):
        self.s = autotrace.fit(settings)  # the page looks only
        self.log = log
        self.sessions, self.chats, self.fails = {}, {}, {}
        self.lock = threading.Lock()
        # The watch and auto-trace run in the container's background; the page shows them.
        self.health = health or autotrace.start(settings, log, mapping=True)
        self.health.mapping = True
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
        if not found and cid and self.health.auto:
            found = self.health.auto.find(cid)  # an auto-trace: everyone signed in may read it and ask more
        with self.lock:
            if found and found.user in (user, AUTO):
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
        box.refresh()
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
                   "kind": {"new": "problem", "traced": "traced"}.get(e["kind"], "cleared"), "link": e.get("link"),
                   "id": e.get("id")} for e in self.health.problem_events(since)]
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

    # --- pods, for the map: a namespace's pods, and one pod with its log ---
    def _space(self, cluster, namespace):
        box = self.health.box
        if cluster not in box.clusters:
            raise BadInput(f"{cluster} is not one of the clusters.")
        if not NAME.match(namespace or ""):
            raise BadInput("That is not a namespace name.")
        return box

    @staticmethod
    def _workload(pod):
        """(kind, name) of what runs the pod: a ReplicaSet's pods belong to its Deployment."""
        meta = pod.get("metadata") or {}
        owner = next(iter(meta.get("ownerReferences") or []), None)
        if not owner:
            return "pod", meta.get("name", "?")
        kind, name = owner.get("kind", "?"), owner.get("name", "?")
        hash_ = (meta.get("labels") or {}).get("pod-template-hash")
        if kind == "ReplicaSet" and hash_ and name.endswith("-" + hash_):
            return "deployment", name[:-len(hash_) - 1]
        if kind == "Job" and re.search(r"-\d{8,}$", name):
            return "cronjob", re.sub(r"-\d{8,}$", "", name)
        return kind.lower(), name

    def pods(self, cluster, namespace):
        box = self._space(cluster, namespace)
        got, problem = box._json(cluster, "pods", "-n", namespace)
        if problem:
            raise BadInput(f"Could not list the pods: {problem}")
        now, groups = time.time(), {}
        for p in got.get("items") or []:
            status = p.get("status") or {}
            boxes = status.get("containerStatuses") or []
            restarts = sum(c.get("restartCount", 0) for c in boxes)
            last = max([tools.when(((c.get("lastState") or {}).get("terminated") or {}).get("finishedAt")) for c in boxes] or [0])
            left = box._leftover(p, now)
            trouble = "" if left else box._pod_trouble(p, now, recent=True)   # as the watch: old restarts are history
            ready = status.get("phase") == "Succeeded" or (bool(boxes) and all(c.get("ready") for c in boxes))
            waiting = next((c["state"]["waiting"].get("reason") for c in boxes if (c.get("state") or {}).get("waiting")), "")
            kind, name = self._workload(p)
            groups.setdefault((kind, name), []).append({
                "name": p["metadata"]["name"], "state": waiting or status.get("phase", "?"), "ready": ready,
                "restarts": restarts, "last_restart": last or None, "trouble": trouble, "left_over": left,
                "node": (p.get("spec") or {}).get("nodeName", "")})
        out = []
        for (kind, name), items in groups.items():
            items.sort(key=lambda x: (not x["trouble"], x["name"]))
            out.append({"kind": kind, "name": name, "pods": items,
                        "ready": sum(x["ready"] for x in items), "trouble": sum(bool(x["trouble"]) for x in items)})
        out.sort(key=lambda g: (-g["trouble"], g["kind"], g["name"]))
        return {"cluster": cluster, "namespace": namespace, "workloads": out,
                "count": sum(len(g["pods"]) for g in out)}

    def pod(self, cluster, namespace, name, previous=False):
        box = self._space(cluster, namespace)
        if not NAME.match(name or ""):
            raise BadInput("That is not a pod name.")
        p, problem = box._json(cluster, "pod", name, "-n", namespace)
        if problem:
            raise BadInput(f"Could not read the pod: {problem}")
        status, spec = p.get("status") or {}, p.get("spec") or {}
        boxes = status.get("containerStatuses") or []
        exit_code = next((((c.get("lastState") or {}).get("terminated") or {}).get("exitCode") for c in boxes
                          if (c.get("lastState") or {}).get("terminated")), None)
        text = box.k8s_logs(cluster, name, namespace, None, bool(previous), 200, None, None)
        kind, owner = self._workload(p)
        return {"name": name, "namespace": namespace, "cluster": cluster, "workload": f"{kind} {owner}",
                "state": next((c["state"]["waiting"].get("reason") for c in boxes if (c.get("state") or {}).get("waiting")),
                              status.get("phase", "?")),
                "trouble": box._pod_trouble(p, recent=True), "restarts": sum(c.get("restartCount", 0) for c in boxes),
                "exit_code": exit_code, "node": spec.get("nodeName", ""), "started": status.get("startTime"),
                "images": sorted({c.get("image", "") for c in spec.get("containers") or []}),
                "logs": box.out(str(text)), "previous": bool(previous)}

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
                    "user": user, "host": box.here, "model": s.llm_model if s.web_show_model else "", "kubernetes": bool(box.clusters),
                    "hosts": [box.here, *box.agents], "contexts": list(box.clusters)})
            if parts == ["health"]:
                return self._send(200, {**site.health.state(), "contexts": list(site.health.box.clusters)})
            if parts == ["container"]:
                try:
                    return self._send(200, site.container(query.get("host", ""), query.get("name", "")))
                except BadInput as e:
                    return self._send(400, {"error": str(e)})
            if parts in (["pods"], ["pod"]):
                try:
                    if parts == ["pods"]:
                        return self._send(200, site.pods(query.get("cluster", ""), query.get("namespace", "")))
                    return self._send(200, site.pod(query.get("cluster", ""), query.get("namespace", ""), query.get("name", ""),
                                                    query.get("previous") == "1"))
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
                                            "context": chat.session.box.active, "auto": chat.user == AUTO})
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


def serve(settings, log=lambda line: None, health=None):
    why = hosts.not_ready(settings)
    if why:
        print(f"The web page is not served: {why}", flush=True)
        return
    if not users(settings):
        print("The web page is not served: there are no accounts. Add one with ./ts user add NAME, "
              "then docker compose up -d.", flush=True)
        return
    site = Site(settings, log, health)
    if not health:  # serve() on its own, as in a test: the page runs its own watch
        threading.Thread(target=site.health.forever, daemon=True).start()
    cert = "your own certificate" if hosts.own_web_cert(settings) else "the certificate ./ts setup made, so browsers warn"
    print(f"The web page: https://{socket.gethostname()}:{settings.web_port}  ({len(users(settings))} account(s), "
          f"{len(config.agents(settings))} other host(s), {cert})", flush=True)
    hosts.serve(settings, _handler(site), settings.web_port, web=True)
