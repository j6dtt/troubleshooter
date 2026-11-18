"""The watch, kept running in the container, and the traces Tracer starts by itself.

Runs on a hub (for every host and cluster, and the web page shows it) and on a host that stands alone
(for itself). An agent leaves it to its hub. Each auto-trace is saved as a file in
logs/traces/<host>/, so the page, ./ts /traces and a restart all see the same traces.
"""
import collections
import dataclasses
import glob
import json
import os
import queue
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import cases, config, history, hosts, links, llm, watch
from .engine import Session
from .redact import strip_control
from .tools import Toolbox

WAIT = 25                 # seconds a request for events waits for news
HEALTH_EVERY = 60
EVENTS_KEPT = 2000
AUTO = "auto-trace"            # the "user" of the traces Tracer starts by itself; everyone may open them
AUTO_KEPT = 50
CAUSE = re.compile(r"^\s*[*_#\s]*(?:likely |root |probable )?cause[*_]*\s*:[*_]*\s*(.+)$", re.I | re.M)
TRACES_KEPT = 200
TRACES_BYTES = 20 * 1024 * 1024
SAVING = threading.Lock()


def cause_in(text):
    """The model's "Cause:" line, or "" when it found none."""
    found = [m.strip(" *_`") for m in CAUSE.findall(text or "")]
    last = found[-1] if found else ""
    return "" if re.match(r"not (found|yet|known|established)", last, re.I) else last


class Chat:
    """One conversation. It is also the engine's UI: what the engine says becomes a list of events."""

    def __init__(self, cid, user, settings, log):
        self.id, self.user, self.s = cid, user, settings
        self.events, self.outputs, self.steps = [], {}, 0
        self.cond = threading.Condition()
        self.busy, self.stopped, self.used = False, False, time.time()
        self.after = None  # called when each piece of work ends: an auto-trace saves itself
        self.log = lambda line: log(f"[{user}] {line}" if user == AUTO else f"[page, {user}] {line}")
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
                    self.events.append({"t": "done"})
                if self.after:  # saved before anyone waiting on busy goes on
                    try:
                        self.after()
                    except Exception as e:
                        self.log(f"Could not save this trace: {e}")
                with self.cond:
                    self.busy = False
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

    def __init__(self, settings, log, mapping=True):
        self.s, self.log = settings, log
        self.box = Toolbox(dataclasses.replace(settings, allow_changes=False))
        self.box.log = log
        self.lock = threading.Lock()
        self.open, self.lines, self.checked, self.failed = {}, [], 0, ""
        self.hosts, self.auto = [], None
        self.mapping = mapping  # every host's containers, for the page's map; not needed without the page
        # One file per host: hosts that stand alone share the log folder.
        self.path = os.path.join(settings.log_dir, f"watch-events-{self.box.here}.jsonl")
        self.events = self._load()

    def _load(self):
        for path in (self.path, os.path.join(self.s.log_dir, "watch-events.jsonl")):  # the second: before 0.x
            try:
                with open(path, encoding="utf-8") as f:
                    return [json.loads(row) for row in f.read().splitlines()[-EVENTS_KEPT:] if row.strip()]
            except (OSError, ValueError):
                continue
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
                seen, found = pool.submit(watch.survey, self.box), pool.submit(self._hosts if self.mapping else list)
                seen, found = seen.result(), found.result()
        except Exception as e:
            with self.lock:
                self.failed = str(e)
            return
        now, new = int(time.time()), []
        where = lambda k: watch.lane(k, self.box.here, self.box.agents)
        added = []
        with self.lock:
            first = not self.checked
            for k, text in seen["problems"].items():
                if k not in self.open:
                    # A login added since the last round brings problems that were there already: shown, not traced.
                    known = first or getattr(self.box, "first_seen", {}).get(where(k), 0) > self.checked
                    self.open[k] = {"text": text, "since": now, "known": known, "lane": where(k)}
                    if not known:
                        added.append(k)
                        new.append({"time": now, "kind": "new", "text": text, "lane": where(k)})
                        self.log(f"The watch found a new problem: {text}")
                else:
                    self.open[k]["text"] = text
            for k in [k for k in self.open if k not in seen["problems"]]:
                new.append({"time": now, "kind": "ok", "text": self.open.pop(k)["text"], "lane": where(k)})
            self.lines, self.hosts, self.checked, self.failed = seen["lines"], found, now, ""
            fresh = [(k, dict(self.open[k])) for k in added]
        if fresh:
            found_links = self._link(fresh, now)
            with self.lock:
                for k, link in found_links.items():
                    if k in self.open:
                        self.open[k]["link"] = link
                for e in new:
                    e["link"] = next((found_links[k] for k, p in fresh if p["text"] == e["text"] and k in found_links), None)
                    if e["link"]:
                        self.log(f"{e['text']} started {links.say(e['link'])}")
        with self.lock:
            self.events += new
        if new:
            self._keep(new)
        for k, p in fresh:
            if self.auto and not k.endswith(" agent"):  # an agent that does not answer can't be traced through it
                self.mark(k, self.auto.offer(k, {**p, "link": found_links.get(k)}))

    def _link(self, fresh, now):
        """A fresh snapshot of the lanes with new problems, then each problem's change, if any."""
        lanes, box = {p["lane"] for _, p in fresh}, self.box
        since = now - self.s.link_minutes * 60 - 3600
        changes = []
        if lanes & {box.here, *box.clusters}:
            history.record(box)
            changes += [{**e, "lane": box.here if e["where"] == "Docker" else e["where"]} for e in history.events(self.s, since)]
        for host in lanes & set(box.agents):
            try:
                hosts.ask(self.s, host, "_record", timeout=60)
                changes += [{**e, "lane": host} for e in json.loads(hosts.ask(self.s, host, "_events", {"since": since}, timeout=30))]
            except (hosts.Unreachable, ValueError):
                pass
        found = {}
        for k, p in fresh:
            link = links.find({"time": now, "lane": p["lane"], "text": p["text"]}, changes, self.s.link_minutes)
            if link:
                found[k] = link
            else:
                mine = [c for c in changes if c["lane"] == p["lane"]]
                self.log(f"No change on {p['lane']} in the {self.s.link_minutes} min before {p['text']} "
                         f"({len(mine)} recorded change(s) there in the last hour, the newest at "
                         f"{time.strftime('%H:%M:%S', time.localtime(max((c['time'] for c in mine), default=0)))})")
        return found

    def mark(self, k, trace):
        """Where the problem's auto-trace stands: waiting, running, done, limit or failed."""
        if trace is None:
            return
        with self.lock:
            if k in self.open:
                self.open[k]["trace"] = trace

    def still_open(self, k):
        with self.lock:
            return k in self.open

    def note(self, event):
        with self.lock:
            self.events.append(event)
        self._keep([event])

    def forever(self):
        while True:
            self.round()
            time.sleep(HEALTH_EVERY)

    def state(self):
        with self.lock:
            return {"checked": self.checked, "every": HEALTH_EVERY, "failed": self.failed,
                    "problems": sorted(({"text": p["text"], "since": p["since"], "known": p["known"], "lane": p["lane"],
                                         "link": p.get("link"), "trace": p.get("trace")}
                                        for p in self.open.values()), key=lambda p: -p["since"]),
                    "lines": self.lines, "hosts": self.hosts, "events": self.events[-60:][::-1],
                    "clusters": [{"name": c["where"], "kind": c["kind"], "line": c["line"], "problems": c["problems"],
                                  "role": c.get("role", ""), "expired": c.get("expired", False),
                                  "context": c.get("context", "")}
                                 for c in self.lines if c["kind"] in ("Kubernetes", "VKS cluster")]}

    def problem_events(self, since):
        with self.lock:
            return [e for e in self.events if e["time"] >= since]


class AutoTrace:
    """A new problem starts its own look-only trace: one at a time, at most auto-trace-per-hour an hour."""

    def __init__(self, settings, log, health):
        self.s, self.log, self.health = settings, log, health
        self.started, self.waiting = [], queue.Queue()
        self.chats = collections.OrderedDict()  # the newest, ready to be read and asked more
        self.lock = threading.Lock()
        threading.Thread(target=self._work, daemon=True).start()

    def offer(self, k, problem):
        """Called for each new problem. What to show for it now, or None when auto-trace is off."""
        s = self.s
        if not s.auto_trace or s.auto_trace_per_hour <= 0 or not s.llm_url:
            return None
        now = time.time()
        self.started = [t for t in self.started if now - t < 3600]
        if len(self.started) >= s.auto_trace_per_hour:
            self.log(f"Did not auto-trace {problem['text']}: {s.auto_trace_per_hour} were already started this hour")
            return {"state": "limit", "most": s.auto_trace_per_hour}
        self.started.append(now)
        self.waiting.put((k, problem))
        return {"state": "waiting"}

    def _work(self):
        while True:
            k, problem = self.waiting.get()
            if not self.health.still_open(k):
                continue  # it cleared before its turn
            try:
                self.run(k, problem)
            except Exception as e:  # one failed trace must not stop the next
                self.health.mark(k, {"state": "failed", "why": str(e)[:200]})
                self.log(f"The auto-trace of {problem['text']} failed: {e}")

    def keep(self, chat):
        with self.lock:
            self.chats[chat.id] = chat
            self.chats.move_to_end(chat.id)
            while len(self.chats) > AUTO_KEPT:
                self.chats.popitem(last=False)

    def find(self, cid):
        """A trace by its id: kept in memory, or read back from its file (after a restart, or from ./ts)."""
        with self.lock:
            found = self.chats.get(cid)
        if found:
            return found
        record = load(self.s, cid)
        if not record:
            return None
        chat = restore(fit(config.for_learning(self.s)), record, self.log)
        self.keep(chat)
        return chat

    def question(self, problem):
        link = problem.get("link")
        text = [f"Auto-trace. The watch found a new problem at {time.strftime('%H:%M', time.localtime(problem['since']))} "
                f"on {problem['lane']}: {problem['text']}"]
        if link and link["strength"] == "linked":
            text.append(f"It started {links.say(link)}. That change names the same thing: check it first.")
        elif link:
            text.append(f"It started {links.say(link)[7:]}. That change is on the same host but names something else: "
                        "check it, don't assume it is the cause.")
        text.append("Find the likely cause. Look only: what changed on that host or cluster, the logs and state of the "
                    "thing, and past cases. Use at most 6 tool calls. End with a Cause: line and a Fix: line.")
        return "\n".join(text)

    def run(self, k, problem):
        settings = fit(config.for_learning(self.s))  # the learn model when one is set, so people keep the chat model
        chat = Chat(secrets.token_urlsafe(12), AUTO, settings, self.log)
        record = {"id": chat.id, "host": chat.session.box.here, "problem": problem["text"], "lane": problem["lane"],
                  "since": problem["since"], "link": problem.get("link"), "started": int(time.time()),
                  "model": settings.llm_model, "state": "running"}
        chat.record = record
        chat.after = lambda: save(self.s, chat)
        self.keep(chat)
        self.health.mark(k, {"state": "running", "id": chat.id})
        host = problem["lane"] if problem["lane"] in chat.session.box.agents else ""
        if problem["lane"] in chat.session.box.clusters:
            chat.session.use_context(problem["lane"])
        save(self.s, chat)
        chat.run(lambda: chat.ask(self.question(problem), host))
        with chat.cond:
            while chat.busy:
                chat.cond.wait(10)
        said = "".join(e.get("d", "") for e in chat.events if e["t"] == "text")
        cause = cause_in(said)
        record.update(state="done", cause=cause, fix=fix_in(said), finished=int(time.time()))
        save(self.s, chat)
        self.health.mark(k, {"state": "done", "id": chat.id, "cause": cause})
        self.health.note({"time": int(time.time()), "kind": "traced", "lane": problem["lane"], "id": chat.id,
                          "text": f"{problem['text']}: {('likely ' + cause) if cause else 'no cause found'}"})
        self.log(f"Auto-traced {problem['text']} on {problem['lane']}: "
                 f"{('likely cause: ' + cause) if cause else 'no cause found'}")


FIX = re.compile(r"^\s*[*_#\s]*fix[*_]*\s*:[*_]*\s*(.+)$", re.I | re.M)


def fix_in(text):
    found = [m.strip(" *_`") for m in FIX.findall(text or "")]
    last = found[-1] if found else ""
    return "" if re.match(r"not (found|yet|known)", last, re.I) else last


def fit(settings):
    """Leave room in the model's window for its reply and the tool list (about 3 characters a token)."""
    window = llm.context_tokens(settings) if settings.llm_url else None
    if window:
        room = max(window - settings.llm_max_tokens - 3000, 2000) * 3
        settings = dataclasses.replace(settings, max_context_chars=min(settings.max_context_chars, room))
    return dataclasses.replace(settings, allow_changes=False)


# --- the trace files: logs/traces/<host>/<when>-<id>.json ---

def folder(s, host=None):
    return os.path.join(s.log_dir, "traces", host or config.here())


def save(s, chat):
    """Write the trace as it stands now, then keep the folder to TRACES_KEPT files and TRACES_BYTES."""
    record = dict(chat.record, events=chat.events, outputs={str(n): t for n, t in chat.outputs.items()},
                  messages=chat.session.messages, context=chat.session.box.active or "", steps=chat.steps)
    where = folder(s, record["host"])
    os.makedirs(where, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(record["started"]))
    path = os.path.join(where, f"{stamp}-{record['id']}.json")
    with SAVING:  # the trace's own thread and the watch may save at the same moment
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(record, f)
        os.replace(path + ".tmp", path)
        _tidy(where)


def _tidy(where):
    files = sorted(glob.glob(os.path.join(where, "*.json")), reverse=True)  # newest first, by name
    total = 0
    for n, path in enumerate(files):
        try:
            total += os.path.getsize(path)
            if n >= TRACES_KEPT or total > TRACES_BYTES:
                os.remove(path)
        except OSError:
            pass


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            found = json.load(f)
        return found if isinstance(found, dict) and found.get("id") else None
    except (OSError, ValueError):
        return None


def load(s, cid):
    if not re.match(r"^[A-Za-z0-9_-]{8,64}$", cid or ""):
        return None
    for path in glob.glob(os.path.join(s.log_dir, "traces", "*", f"*-{cid}.json")):
        return _read(path)
    return None


def listing(s, most=30):
    """The newest traces of every host in the shared folder, without their steps."""
    paths = glob.glob(os.path.join(s.log_dir, "traces", "*", "*.json"))
    paths.sort(key=lambda p: os.path.basename(p), reverse=True)
    found = []
    for path in paths[:most]:
        record = _read(path)
        if record:
            found.append({k: record.get(k) for k in ("id", "host", "lane", "problem", "since", "link", "state",
                                                     "cause", "fix", "started", "model")})
    return found


def restore(settings, record, log):
    """A saved trace as a chat again: its events for the page, its messages so people can ask more."""
    chat = Chat(record["id"], AUTO, settings, log)
    chat.events = list(record.get("events") or [])
    chat.outputs = {int(n): t for n, t in (record.get("outputs") or {}).items()}
    chat.steps = int(record.get("steps") or len(chat.outputs))
    if record.get("messages"):
        chat.session.messages = record["messages"]
    if record.get("context") in chat.session.box.clusters:
        chat.session.box.active = record["context"]
    chat.record = {k: v for k, v in record.items() if k not in ("events", "outputs", "messages", "context", "steps")}
    chat.after = lambda: save(settings, chat)
    return chat


def start(settings, log, mapping):
    """The watch and auto-trace, running in the background. Returns the watch, for the page to show."""
    health = Health(settings, log, mapping)
    health.auto = AutoTrace(dataclasses.replace(settings, allow_changes=False), log, health)
    return health
