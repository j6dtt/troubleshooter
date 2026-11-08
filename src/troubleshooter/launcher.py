"""Runs on the host, as you, and starts the troubleshooter in its container. ./ts calls it.

It lives here, not in ./ts, so updates are .py files only. Uses only the standard library and works
on Python 3.6 or newer, as found on older hosts.

  - Starts one kubectl proxy per context on 127.0.0.1, as you, so the container can use your logins.
    They stop when this ends.
  - ./ts learn: saves the runbook as you, in runbooks/<this host>/, so it works on a shared folder
    where the container's root can't write. Stopping it stops the work in the container too.
"""
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

HOME = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HELP = os.path.join(HOME, "src", "troubleshooter", "help.txt")
CONTEXTS = '{range .contexts[*]}{.name}{"\\t"}{.context.namespace}{"\\n"}{end}'
SERVING = re.compile(r"127\.0\.0\.1:(\d+)")


def safe(text):
    """Same rule as learn.app_name in the container: one name for one file."""
    return re.sub(r"[^A-Za-z0-9._-]", "-", text)


def quiet(argv):
    """Output of a command, or "" if it fails or is missing."""
    try:
        return subprocess.check_output(argv, stderr=subprocess.DEVNULL, universal_newlines=True)
    except (OSError, subprocess.CalledProcessError):
        return ""


class Proxies:
    def __init__(self):
        self.procs, self.folder, self.current, self.found = [], None, "", ""

    def start(self):
        if not shutil.which("kubectl"):
            return
        rows = [r.split("\t") for r in quiet(["kubectl", "config", "view", "-o", "jsonpath=" + CONTEXTS]).splitlines() if r]
        if not rows:
            return
        self.current = quiet(["kubectl", "config", "current-context"]).strip()
        # In the foreground first: a login plugin may need to ask for a password.
        if self.current and subprocess.call(["kubectl", "get", "--raw", "/version"], stdout=subprocess.DEVNULL) != 0:
            print("Not logged in to {}. Log in from another terminal; no need to restart.".format(self.current))
        self.folder = tempfile.mkdtemp()
        parts = []
        for n, row in enumerate(rows):
            context, namespace = row[0], (row[1] if len(row) > 1 else "")
            log = os.path.join(self.folder, str(n))
            with open(log, "w") as out:
                proc = subprocess.Popen(["kubectl", "proxy", "--context", context, "--port=0", "--address=127.0.0.1"],
                                        stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            self.procs.append(proc)
            port = self._port(proc, log)
            if port:
                parts.append("{}|{}|{};".format(context, port, namespace))
            else:
                print("Skipping context {}: its kubectl proxy did not start.".format(context))
        self.found = "".join(parts)

    @staticmethod
    def _port(proc, log):
        for _ in range(50):
            with open(log) as f:
                m = SERVING.search(f.read())
            if m:
                return m.group(1)
            if proc.poll() is not None:
                return None
            time.sleep(0.1)
        return None

    def stop(self):
        for proc in self.procs:
            if proc.poll() is None:
                proc.terminate()
        for proc in self.procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        if self.folder:
            shutil.rmtree(self.folder, ignore_errors=True)


def exec_args(proxies, *extra):
    return ["docker", "compose", "exec", *extra, "-e", "KUBE_PROXIES=" + proxies.found,
            "-e", "KUBE_CURRENT=" + proxies.current, "troubleshooter", "ts"]


class Stopped(Exception):
    pass


class Signals:
    """Ctrl-C while the container runs reaches the container too: note it and let the container
    answer first. Any other time, and on SIGTERM, stop at once."""

    def __init__(self):
        self.waiting, self.stopped = False, False
        signal.signal(signal.SIGINT, self._on)
        signal.signal(signal.SIGTERM, self._on)

    def _on(self, number, frame):
        self.stopped = True
        if number == signal.SIGTERM or not self.waiting:
            raise Stopped()

    def call(self, argv, **kw):
        self.waiting = True
        try:
            return subprocess.call(argv, **kw)
        finally:
            self.waiting = False


def learn(signals, proxies, args):
    target = next((a for a in args if a.startswith("/")), "none")
    host, app = safe(os.uname()[1]), safe(os.path.basename(target.rstrip("/")) or "none")
    folder = os.path.join("runbooks", host)
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as e:
        print("Cannot write to ./{}: {}".format(folder, e))
        return 1
    run_id = str(os.getpid())
    tmp = os.path.join(folder, ".{}.{}".format(app, run_id))
    code, saved = 1, False
    try:
        with open(tmp, "w") as out:
            code = signals.call(exec_args(proxies, "-T", "-e", "LEARN_RUN=" + run_id) + args, stdout=out)
        if code == 0 and not signals.stopped and os.path.getsize(tmp):
            final = os.path.join(folder, app + ".md")
            if os.path.exists(final):
                shutil.copy2(final, final + ".bak")
            os.chmod(tmp, 0o644)
            os.chmod(folder, 0o755)
            os.rename(tmp, final)
            saved = True
            print("Saved " + final)
    finally:
        if not saved:
            if signals.stopped:  # stop it in the container too, or it keeps using the model
                subprocess.call(["docker", "compose", "exec", "-T", "troubleshooter", "sh", "-c",
                                 "kill $(cat /tmp/learn.{0}) 2>/dev/null; rm -f /tmp/learn.{0}".format(run_id)],
                                stderr=subprocess.DEVNULL)
            if os.path.exists(tmp):
                os.unlink(tmp)
    if signals.stopped:
        return 130
    return 0 if saved else (code or 1)


def main():
    args = sys.argv[1:]
    os.chdir(HOME)
    if args[:1] in (["-h"], ["--help"], ["help"]):
        with open(HELP) as f:
            sys.stdout.write(f.read())
        return 0
    signals = Signals()
    proxies = Proxies()
    try:
        proxies.start()
        if args[:1] == ["learn"]:
            return learn(signals, proxies, args)
        code = signals.call(exec_args(proxies) + args)
        return 130 if signals.stopped and code else code
    except Stopped:
        return 130
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)  # finish cleaning up even if Ctrl-C is pressed again
        proxies.stop()


if __name__ == "__main__":
    sys.exit(main())
