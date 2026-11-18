"""The terminal chat: ts (chat), ts check (test every connection), ts tools (list what it can do)."""
import dataclasses
import datetime
import json
import os
import shutil
import socket
import sys
import threading
import time

from . import __version__, cases, config, history, hosts, learn, links, llm, watch
from .engine import Session
from .redact import strip_control
from .run import run
from .tools import BadInput, Toolbox, kube_error

TTY = sys.stdout.isatty()
HELP = """Ask in plain English, e.g. "why is uhu-bhr.13.3 unhealthy?"
Follow-up questions keep the conversation going.
  /context        list Kubernetes contexts (* = active)
  /context NAME   switch, for this chat only
  /paste          paste many lines, such as command output
  /report         write this conversation up as an incident report
  /case           save this problem and its fix for later chats
  /traces         the traces Tracer started by itself
  /traces N       open trace N here: read it, ask more, fix it
  /new    forget this conversation, start again
  /tools  list what I can look at and change
  /help   this list
  /quit   leave (Ctrl-D too)
Ctrl-C stops the current answer. Full guide: ./ts --help"""


def paint(code, text):
    return f"\033[{code}m{text}\033[0m" if TTY else text


LOGO = """\
████████╗██████╗  █████╗  ██████╗███████╗██████╗
╚══██╔══╝██╔══██╗██╔══██╗██╔════╝██╔════╝██╔══██╗
   ██║   ██████╔╝███████║██║     █████╗  ██████╔╝
   ██║   ██╔══██╗██╔══██║██║     ██╔══╝  ██╔══██╗
   ██║   ██║  ██║██║  ██║╚██████╗███████╗██║  ██║
   ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝╚══════╝╚═╝  ╚═╝"""
SHADES = (51, 45, 39, 33, 27, 63)   # cyan down to blue, one per line


def intro(settings, box):
    """The start of a chat: the name, then where you are and what it can see."""
    kind = config.role(settings)
    where = {"hub": f"hub, {len(box.agents)} other host(s)", "agent": f"agent of {config.short(settings.hub_host)}",
             "alone": "on its own"}[kind]
    clusters = f"{len(box.clusters)} context(s)" if box.clusters else ("off" if not settings.kubernetes else "no login")
    facts = [("host", f"{box.here} ({where})"),
             ("model", settings.llm_model + ("  (anonymized)" if settings.anonymize else "")), ("kubernetes", clusters)]
    fancy = TTY and shutil.get_terminal_size((80, 24)).columns >= 52 and "UTF" in (sys.stdout.encoding or "").upper()
    if not fancy:
        print(f"Tracer {__version__}  host {box.here}  model {settings.llm_model}" + ("  (anonymized)" if settings.anonymize else ""))
        return
    print()
    for shade, line in zip(SHADES, LOGO.splitlines()):
        print(f"  \033[1;38;5;{shade}m{line}\033[0m")
    trail = ("\033[38;5;240m  ─" + "\033[38;5;45m●\033[38;5;240m─────" * 3 + "\033[38;5;51m▶\033[0m"
             "  \033[2msymptom → evidence → cause → fix\033[0m")
    print(trail)
    print(f"  \033[2mv{__version__} · looks freely, changes only after you say yes\033[0m\n")
    for name, value in facts:
        print(f"  \033[38;5;45m{name:<11}\033[0m{value}")
    print()


class Spinner:
    """Shows that a slow model is still working, until the first text arrives."""

    def __init__(self):
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if not TTY or self._thread:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def _spin(self):
        begun = time.monotonic()
        while not self._stop.wait(1):
            sys.stdout.write(f"\r  thinking... {time.monotonic() - begun:.0f}s ")
            sys.stdout.flush()

    def stop(self):
        if self._thread:
            self._stop.set()
            self._thread.join()
            self._thread = None
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()


class Terminal:
    def __init__(self):
        self.spinner = Spinner()
        self.mid_line = False

    def _break(self):
        self.spinner.stop()
        if self.mid_line:
            print()
            self.mid_line = False

    def text(self, text):
        self.spinner.stop()
        sys.stdout.write(text)
        sys.stdout.flush()
        self.mid_line = not text.endswith("\n")

    def step(self, text):
        self._break()
        print(paint("2", f"  - {strip_control(text)}"))
        self.spinner.start()

    def notice(self, text):
        self._break()
        print(paint("33", f"! {text}"))

    def approve(self, title, reason, command):
        self._break()
        if not sys.stdin.isatty():  # piped input must never be able to answer yes
            print("A change needs approval, but no one is at a terminal. Skipped.")
            return False
        print(paint("1;33", "\n== Approval needed =="))
        print(f"Action:  {strip_control(title)}\nWhy:     {reason}\nCommand: {command}")
        try:
            answer = input("Type yes to run it, anything else to skip: ")
        except EOFError:
            answer = ""
        approved = answer.strip().lower() in ("y", "yes")
        print("Approved.\n" if approved else "Skipped. Nothing was changed.\n")
        return approved

    def done(self):
        self._break()


def make_log(settings):
    """The log is the record of what was approved, so a failure to write it must be seen."""
    warned = []

    def write(line):
        try:
            now = datetime.datetime.now()
            with open(os.path.join(settings.log_dir, f"{now:%Y-%m-%d}.log"), "a", encoding="utf-8") as f:
                f.write(f"{now:%H:%M:%S} {strip_control(line)}\n")
        except OSError as e:
            if not warned:
                warned.append(True)
                print(paint("31", f"! Cannot write the log in {settings.log_dir}: {e}. Actions are NOT being recorded."), file=sys.stderr)
    return write


def chat(settings):
    if not settings.llm_url or not settings.llm_model:
        print("LLM_URL and LLM_MODEL are not set. Fill them in the settings block of compose.yaml.")
        return 1
    window = llm.context_tokens(settings)
    if window:  # about 3 characters a token, leaving room for the reply and the tool list
        room = max(window - settings.llm_max_tokens - 3000, 2000) * 3
        settings = dataclasses.replace(settings, max_context_chars=min(settings.max_context_chars, room))
    ui = Terminal()
    log = make_log(settings)
    session = Session(settings, ui, log)
    keep_history(session.box)
    intro(settings, session.box)
    print(paint("2", "  Type /help for commands, /quit to leave.") + "\n")
    log(f"Chat started with model {settings.llm_model}")
    while True:
        try:
            where = f" [{session.box.active}]" if session.box.active else ""
            question = input(paint("36", f"you{where}> ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            log("Chat ended")
            return 0
        if not question:
            continue
        if question == "/paste":
            question = paste()
            if not question.strip():
                continue
            question = "Output from a command I ran:\n" + question
        if question in ("/quit", "/exit"):
            log("Chat ended")
            return 0
        if question == "/help":
            print(HELP)
        elif question == "/new":
            session.reset()
            log("Started a new conversation")
            print("New conversation.")
        elif question == "/tools":
            list_tools(session.box)
        elif question == "/context" or question.startswith("/context "):
            context(session, question.split()[1:])
        elif question == "/report":
            report(settings, session, ui)
        elif question == "/case":
            case(settings, session, ui, log)
        elif question == "/traces" or question.startswith("/traces "):
            traces(settings, session, question.split()[1:], log)
        else:
            ui.spinner.start()
            session.ask(question)
            ui.done()
            print()


def traces(settings, session, words, log):
    """The auto-traces in the shared folder; one opened here goes on as this chat, with the fix tools."""
    from . import autotrace
    found = autotrace.listing(settings, 20)
    if not found:
        print("No auto-traces yet. They start when the watch finds a new problem\n"
              "(on a hub, or a host that stands alone, with auto-trace on).")
        return
    if not words:
        for n, t in enumerate(found, 1):
            when = time.strftime("%m-%d %H:%M", time.localtime(t.get("started") or 0))
            state = {"done": "", "running": "  (running)"}.get(t.get("state"), f"  ({t.get('state')})")
            print(paint("36", f"{n:>2}") + f"  {when}  {t.get('lane')}{state}")
            print(f"    {short(t.get('problem') or '', 70)}")
            if t.get("link"):
                print(paint("32", f"    <- {short(links.say(t['link']), 70)}"))
            if t.get("state") == "done":
                print(paint("2", f"    likely: {short(t.get('cause') or 'no cause found', 70)}"))
        print(paint("2", "Open one with /traces N."))
        return
    if not words[0].isdigit() or not 1 <= int(words[0]) <= len(found):
        print(f"Give a number from 1 to {len(found)}.")
        return
    record = autotrace.load(settings, found[int(words[0]) - 1]["id"])
    if not record:
        print("That trace is gone.")
        return
    print(paint("1", record.get("problem", "")))
    if record.get("link"):
        print(paint("32", f"<- {links.say(record['link'])}"))
    for e in record.get("events") or []:
        if e.get("t") == "step":
            print(paint("2", f"  {e.get('n')}. {e.get('d')}"))
    said = "".join(e.get("d", "") for e in record.get("events") or [] if e.get("t") == "text").strip()
    print("\n" + (said or "(no answer was written)") + "\n")
    session.reset()
    session.messages = [session.messages[0]] + [m for m in record.get("messages") or [] if m.get("role") != "system"]
    lane = record.get("lane")
    if lane in session.box.clusters:
        print(session.use_context(lane))
    elif lane in session.box.agents:
        session.note = f"(The operator is asking about the host {lane}.)\n"
    log(f"You opened the auto-trace of {record.get('problem')}")
    print(paint("2", "This trace is now this chat: ask more, or ask to fix it (fixes ask you first). /new to leave it."))


def short(text, most):
    text = " ".join(str(text).split())
    return text if len(text) <= most else text[:most - 1] + "…"


def paste():
    """Lines until /end, so pasted output arrives as one message, not one question per line."""
    print("Paste now. Finish with a line saying /end")
    rows = []
    try:
        while True:
            row = input()
            if row.strip() == "/end":
                break
            rows.append(row)
    except (EOFError, KeyboardInterrupt):
        print()
    return "\n".join(rows)


def report(settings, session, ui):
    """The write-up is shown and also kept as a file in the log folder."""
    ui.spinner.start()
    text = session.report()
    ui.done()
    print()
    if not text:
        return
    try:
        print(f"Saved in the log folder: reports/{cases.save_report(settings, text)}")
    except OSError as e:
        print(paint("33", f"! Could not save the report: {e}"))


def case(settings, session, ui, log):
    """The model writes the note, you decide whether it is kept."""
    ui.spinner.start()
    text = session.case()
    ui.done()
    print()
    if not text:
        return
    try:
        answer = input("Save this case for later chats, on every host? Type yes, anything else to drop it: ")
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer.strip().lower() not in ("y", "yes"):
        print("Not saved.")
        return
    try:
        name = cases.save(settings, text, session.box.active or "")
        log(f"You saved a case: {name}")
        print(f"Saved: {cases.title(text)}\nOther hosts see it once you leave this chat.")
    except OSError as e:
        print(paint("33", f"! Could not save the case: {e}"))


def background(settings):
    """What the container does by itself: record what is deployed, and on a hub or an agent, serve."""
    kind = config.role(settings)
    log = make_log(settings)
    # An agent leaves Kubernetes to the hub, which looks at the clusters once for every host.
    box = Toolbox(dataclasses.replace(settings, kubernetes=False) if kind == "agent" else settings)
    jobs = [("The recorder", lambda: history.record_forever(box, settings.record_minutes))]
    if kind == "agent":
        jobs.append(("The agent", lambda: hosts.serve_agent(settings, log)))
    health = None
    # The watch and auto-trace: on a hub for every host, alone for itself. An agent leaves them to its hub.
    if kind == "hub" or (kind == "alone" and settings.auto_trace and settings.llm_url):
        from . import autotrace
        health = autotrace.start(settings, log, mapping=kind == "hub")
        jobs.append(("The watch", health.forever))
        print(f"Watching every {autotrace.HEALTH_EVERY} s. "
              + (f"Auto-trace is on (at most {settings.auto_trace_per_hour} an hour), saved in logs/traces/."
                 if settings.auto_trace and settings.llm_url else "Auto-trace is off."), flush=True)
    if kind == "hub":
        from . import web  # only the hub needs it
        jobs.append(("The web page", lambda: web.serve(settings, log, health)))

    def keep(name, job):
        while True:
            try:
                job()
                return  # it chose to stop (turned off, or not set up yet)
            except Exception as e:
                print(f"{name} stopped ({e}). Starting it again in 30 seconds.", flush=True)
                time.sleep(30)
    print(f"This host runs as: {kind}.", flush=True)
    for name, job in jobs:
        threading.Thread(target=keep, args=(name, job), daemon=True).start()
    threading.Event().wait()  # the container stays up for ./ts whatever the jobs do
    return 0


def keep_history(box):
    """Snapshots for what_changed, taken in the background while the chat is open."""
    def loop():
        while True:
            history.record(box)
            time.sleep(history.EVERY)
    threading.Thread(target=loop, daemon=True).start()


def context(session, words):
    if not session.box.clusters:
        print("No Kubernetes contexts. Log in with kubectl, then run ./ts again.")
    elif not words:
        print(session.box.k8s_contexts())
    else:
        try:
            print(session.use_context(words[0]))
        except BadInput as e:
            print(e)


def list_tools(box):
    for change in (False, True):
        print("\nChanges (ask you first):" if change else "Looks (run freely):")
        for t in box.tools.values():
            if t.change == change:
                print(f"  {t.name}: {t.desc}")


def check_cluster(box, c, report):
    if c.login_tool and not shutil.which(c.login_tool):
        report(f"Context {c.id}", False, f"this kubeconfig logs in with {c.login_tool}, which is not in this "
                                        "container. Make one with scripts/make-kubeconfig.sh")
        return
    active = c.id == box.active
    r = run(box._kubectl(c.id, "get", "--raw", "/version"), timeout=10)
    if not r.ok:
        if active:
            report(f"Context {c.id}", False, kube_error(r.text))
        else:
            print(f"[warn] Context {c.id}: {kube_error(r.text)}")
        return
    nodes = run(box._kubectl(c.id, "get", "nodes", "--no-headers"))
    seen = f"{len(nodes.text.splitlines())} nodes" if nodes.ok else "reachable, no permission to list nodes"
    report(f"Context {c.id}" + (" (active)" if active else ""), True, seen)
    v = run(box._kubectl(c.id, "version", "-o", "json"), parsed=True)
    try:  # only a warning: never let it stop the rest of the check
        ver = json.loads(v.text) if v.ok else {}
        ours, theirs = (int(ver[k]["minor"].rstrip("+")) for k in ("clientVersion", "serverVersion"))
        if abs(ours - theirs) > 1:
            print(f"[warn] Context {c.id}: kubectl 1.{ours} and cluster 1.{theirs} are more than one version apart")
    except (ValueError, KeyError):
        print(f"[warn] Context {c.id}: could not compare kubectl and cluster versions")
    for label, verb in (("read Secrets", "get secrets"), ("delete pods", "delete pods")):
        a = run(box._kubectl(c.id, "auth", "can-i", *verb.split(), "--all-namespaces"))
        said = "yes" if a.text.strip().startswith("yes") else "no"
        warn = label == "read Secrets" and said == "yes" and not c.server
        print(f"       may {label}: {said}" + ("  <- too much: use scripts/make-kubeconfig.sh" if warn else ""))


def check_hosts(settings, box, report):
    """The hub and its agents: is each one set up, and does it answer?"""
    kind = config.role(settings)
    if kind == "alone":
        return
    why = hosts.not_ready(settings)
    if why:
        report("Talking to other hosts", False, why)
        return
    left = hosts.days_left(settings.tls_cert)
    if left is not None and left < 30:
        report("Certificate between hosts", left >= 0, f"{'runs out in ' + str(left) + ' days' if left >= 0 else 'has run out'}. "
               f"Delete certs/troubleshooter-cert.pem, run ./ts setup, then docker compose up -d on every host.")
    if kind == "agent":
        try:
            socket.create_connection(("127.0.0.1", settings.agent_port), timeout=3).close()
            report("Agent", True, f"answers the hub {config.short(settings.hub_host)} on port {settings.agent_port}")
        except OSError as e:
            report("Agent", False, f"nothing listens on port {settings.agent_port} ({e}). Run docker compose up -d.")
        return
    if not box.agents:
        print("[skip] Agents: agent-hosts is empty, so this hub looks at itself only")
    for host in box.agents:
        try:
            started = time.monotonic()
            hosts.ask(settings, host, "_problems", timeout=20)
            report(f"Agent {host}", True, f"answered in {time.monotonic() - started:.1f} s")
        except hosts.Unreachable as e:
            report(f"Agent {host}", False, str(e))
    count = sum(":" in u for u in settings.web_users.split(","))
    try:
        socket.create_connection(("127.0.0.1", settings.web_port), timeout=3).close()
        report("Web page", count > 0, f"https://{socket.gethostname()}:{settings.web_port}, {count} account(s)"
               + ("" if count else ". Add one with: ./ts user add NAME"))
        if hosts.own_web_cert(settings):
            left = hosts.days_left(settings.web_cert)
            report("Web page certificate", left is None or left >= 0,
                   "your own" + ("" if left is None else f", runs out in {left} days" if left >= 0 else
                                 ", HAS RUN OUT: give a new one with ./ts setup --web-cert CERT KEY"))
        else:
            print("[ok] Web page certificate: the one ./ts setup made, so browsers warn. "
                  "To use your own: ./ts setup --web-cert CERT KEY")
    except OSError as e:
        report("Web page", False, f"nothing listens on port {settings.web_port} ({e}). Run docker compose up -d.")


def check(settings):
    """Test each connection the tool depends on, and say plainly what's wrong."""
    failed = 0

    def report(label, ok, detail=""):
        nonlocal failed
        failed += not ok
        print(f"[{'ok' if ok else 'FAIL'}] {label}{': ' + detail if detail else ''}")

    r = run(["docker", "version", "--format", "{{.Server.Version}}"])
    report("Docker", r.ok, r.text[:120])
    box = Toolbox(settings)
    if not settings.kubernetes:
        print("[skip] Kubernetes: turned off (kubernetes: off in compose.yaml)")
    elif not box.clusters:
        print("[skip] Kubernetes: no kubectl login on this host, nothing in ./kube, and no ~/.kube mounted")
    if settings.kubernetes:
        home = os.path.join(settings.kube_home, "config")
        if os.path.isfile(home):
            n = sum(1 for c in box.clusters.values() if c.file == home)
            print(f"[ok] Your ~/.kube: {n} context(s), read again within a minute of a new login")
        else:
            print("[skip] Your ~/.kube: not mounted (kube-home in compose.yaml)")
    for c in box.clusters.values():
        check_cluster(box, c, report)
    report("Host files", os.path.isdir(settings.host_root + "/etc"), settings.host_root)
    report("Log folder", os.access(settings.log_dir, os.W_OK), settings.log_dir)
    if os.path.isdir(settings.runbook_dir):
        print(f"[ok] Runbooks: {len(box._runbooks())} found")
    else:
        print("[skip] Runbooks: no ./runbooks folder mounted")
    print(f"[ok] Cases: {len(cases.all_cases(settings))} saved")
    check_hosts(settings, box, report)
    try:
        ids = llm.models(settings)
        window = llm.context_tokens(settings)
        report("Model server", True, settings.llm_url + (f" ({window} token window)" if window else ""))
        if ids and settings.llm_model not in ids:
            report("Model name", False, f"{settings.llm_model} is not served here. It serves: {', '.join(ids[:8])}")
    except llm.LLMError as e:
        if e.code != 404:
            report("Model server", False, str(e))
            return 1
        # Gateways in front of hosted models often don't offer the list. The chat below is the real test.
        print(f"[warn] Model list: not offered at {settings.llm_url}/models. Testing a chat instead.")
    try:
        tool = [{"type": "function", "function": {
            "name": "overview", "description": "Health of the system.",
            "parameters": {"type": "object", "properties": {}, "required": []}}}]
        started = time.monotonic()
        msg, _ = llm.chat(settings, [{"role": "user", "content": "Check system health using the overview tool."}], tool)
        report("Tool calling", bool(msg.get("tool_calls")),
               f"{time.monotonic() - started:.1f} s" if msg.get("tool_calls") else "the model answered without calling the tool")
    except llm.LLMError as e:
        report("Tool calling", False, str(e))
    if settings.anonymize:
        from . import anonymize
        anonymize.setup(settings)
        anonymize.known(list(box.clusters), "cluster")
        sample = f"{box.here} -> {anonymize.TABLE.hide(box.here)}, 10.1.2.3 -> {anonymize.TABLE.hide('10.1.2.3')}"
        report("Anonymize", True, f"on: {len(anonymize.TABLE.real)} names hidden, e.g. {sample}")
    other = config.for_learning(settings)
    if (other.llm_url, other.llm_model) != (settings.llm_url, settings.llm_model):
        try:
            started = time.monotonic()
            llm.chat(dataclasses.replace(other, llm_reasoning="off"), [{"role": "user", "content": "Reply with the word ready."}])
            report("Learn model", True, f"{other.llm_model} at {other.llm_url}, {time.monotonic() - started:.1f} s")
        except llm.LLMError as e:
            report("Learn model", False, f"{other.llm_model} at {other.llm_url}: {e}")
    return 1 if failed else 0


def show_help():
    with open(os.path.join(os.path.dirname(__file__), "help.txt"), encoding="utf-8") as f:
        print(f.read())


def main():
    settings = config.load()
    command = sys.argv[1] if len(sys.argv) > 1 else "chat"
    if command in ("--help", "-h", "help"):
        show_help()
        return 0
    if command == "check":
        return check(settings)
    if command == "learn":
        return learn.main(settings, sys.argv[2:], make_log(settings))
    if command == "cases-export":  # used by ./ts, which copies cases to the shared folder as you
        if sys.argv[2:3] == ["--sent"]:
            cases.sent(settings, sys.argv[3:])
        else:
            print(json.dumps(cases.waiting(settings)))
        return 0
    if command == "record":  # the container's own start command
        return background(settings)
    if command == "watch":
        return watch.main(settings, Toolbox(settings), sys.argv[2:], make_log(settings))
    if command == "tools":
        list_tools(Toolbox(settings))
        return 0
    if command == "chat":
        try:
            import readline  # noqa: F401  arrow keys and history at the prompt
        except ImportError:
            pass
        return chat(settings)
    print("Usage: ./ts [chat|check|watch|tools|learn /apps/myapp|setup|user add NAME|--help]")
    return 2


if __name__ == "__main__":
    sys.exit(main())
