"""The terminal chat: ts (chat), ts check (test every connection), ts tools (list what it can do)."""
import dataclasses
import datetime
import json
import os
import shutil
import sys
import threading
import time

from . import __version__, config, llm
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
  /new    forget this conversation, start again
  /tools  list what I can look at and change
  /help   this list
  /quit   leave (Ctrl-D too)
Ctrl-C stops the current answer. Full guide: ./ts --help"""


def paint(code, text):
    return f"\033[{code}m{text}\033[0m" if TTY else text


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
                print(paint("31", f"! Cannot write the log in {settings.log_dir}: {e}. Actions are NOT being recorded."))
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
    print(paint("1", f"Troubleshooter {__version__}") + f"  model: {settings.llm_model}")
    print("Type /help for commands.\n")
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
        else:
            ui.spinner.start()
            session.ask(question)
            ui.done()
            print()


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
    if not box.clusters:
        print("[skip] Kubernetes: no kubectl login on this host, and nothing in ./kube")
    for c in box.clusters.values():
        check_cluster(box, c, report)
    report("Host files", os.path.isdir(settings.host_root + "/etc"), settings.host_root)
    report("Log folder", os.access(settings.log_dir, os.W_OK), settings.log_dir)
    try:
        ids = llm.models(settings)
        window = llm.context_tokens(settings)
        report("Model server", True, settings.llm_url + (f" ({window} token window)" if window else ""))
        if ids and settings.llm_model not in ids:
            report("Model name", False, f"{settings.llm_model} is not served here")
    except llm.LLMError as e:
        report("Model server", False, str(e))
        return 1
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
    if command == "tools":
        list_tools(Toolbox(settings))
        return 0
    if command == "chat":
        try:
            import readline  # noqa: F401  arrow keys and history at the prompt
        except ImportError:
            pass
        return chat(settings)
    print("Usage: ./ts [chat|check|tools|--help]")
    return 2


if __name__ == "__main__":
    sys.exit(main())
