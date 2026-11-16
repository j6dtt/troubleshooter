"""Runs on the host, as you, and starts the troubleshooter in its container. ./ts calls it.

It lives here, not in ./ts, so updates are .py files only. Uses only the standard library and works
on Python 3.6 or newer, as found on older hosts.

  - Starts one kubectl proxy per context on 127.0.0.1, as you, so the container can use your logins.
    They stop when this ends.
  - ./ts setup: makes the key and certificate the hosts use to talk to each other, into .env and
    ./certs. ./ts user: the accounts of the web page, into .env. Neither is ever shown.
  - After a chat: copies the cases you saved with /case to runbooks/_cases/<host>/, as you, so
    every host that runs from this folder sees them.
  - ./ts watch: stopping it, or losing its terminal, stops the watch in the container too.
  - ./ts learn: saves the runbook as you, in runbooks/<this host>/, so it works on a shared folder
    where the container's root can't write. Stopping it stops the work in the container too.
"""
import base64
import getpass
import hashlib
import io
import json
import os
import re
import secrets
import ssl
import shutil
import signal
import subprocess
import sys
import tempfile
import time

HOME = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
START = os.getcwd()  # where ./ts was typed: file names given to it are meant from there
HELP = os.path.join(HOME, "src", "troubleshooter", "help.txt")
ENV = os.path.join(HOME, ".env")
CERT = os.path.join(HOME, "certs", "troubleshooter-cert.pem")
WEB_CERT = os.path.join(HOME, "certs", "web-cert.pem")
OFF = re.compile(r'^\s*kubernetes:\s*&kubernetes\s+"?(off|false|no|0)"?\s*(#.*)?$', re.I | re.M)
USER = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
ROUNDS = 200000
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


def watch(signals, proxies, args):
    """Runs until stopped. Whatever ends this, the watch in the container is ended too."""
    run_id = str(os.getpid())
    extra = ["-e", "WATCH_RUN=" + run_id] + ([] if sys.stdin.isatty() else ["-T"])
    try:
        code = signals.call(exec_args(proxies, *extra) + args)
        return 130 if signals.stopped and code else code
    finally:
        subprocess.call(["docker", "compose", "exec", "-T", "troubleshooter", "sh", "-c",
                         "kill $(cat /tmp/watch.{0}) 2>/dev/null; rm -f /tmp/watch.{0}".format(run_id)],
                        stderr=subprocess.DEVNULL)


def share_cases():
    """Cases saved in the chat wait in the container's log folder; write them to the shared folder as you."""
    try:
        found = json.loads(quiet(["docker", "compose", "exec", "-T", "troubleshooter", "ts", "cases-export"]) or "{}")
    except ValueError:
        return
    if not isinstance(found, dict):
        return
    save = found.get("save") if isinstance(found.get("save"), dict) else {}
    done = []

    def place(key):
        host, _, name = key.partition("/")
        if safe(host) != host or safe(name) != name or name.startswith(".") or not name.endswith(".md") or host.startswith("."):
            return None, None
        return os.path.join("runbooks", "_cases", host), name
    for key in found.get("delete") or []:  # removed on the web page
        folder, name = place(str(key))
        try:
            if folder and os.path.isfile(os.path.join(folder, name)):
                os.unlink(os.path.join(folder, name))
            if folder:
                done.append(key)
        except OSError as e:
            print("Could not remove a case from ./{}: {}".format(folder, e))
    for key in sorted(save):
        folder, name = place(key)
        if not folder:
            continue
        tmp = os.path.join(folder, ".{}.{}".format(name, os.getpid()))
        try:
            os.makedirs(folder, exist_ok=True)
            os.chmod(os.path.dirname(folder), 0o755)
            os.chmod(folder, 0o755)
            with io.open(tmp, "w", encoding="utf-8") as out:
                out.write(save[key])
            os.chmod(tmp, 0o644)
            os.rename(tmp, os.path.join(folder, name))
            done.append(key)
        except (OSError, TypeError) as e:
            print("Could not copy a case to ./{}: {}. It stays on this host.".format(folder, e))
            if os.path.exists(tmp):
                os.unlink(tmp)
    if done:
        quiet(["docker", "compose", "exec", "-T", "troubleshooter", "ts", "cases-export", "--sent"] + done)
        print("Updated {} case(s) for every host in ./runbooks/_cases/".format(len(done)))


def kubernetes_off():
    """The setting is read from compose.yaml here, so no cluster login is touched when it is off."""
    try:
        with io.open(os.path.join(HOME, "compose.yaml"), encoding="utf-8") as f:
            return bool(OFF.search(f.read()))
    except (OSError, UnicodeDecodeError):
        return False


def env_read():
    """The lines of .env, and {name: value} of what they set."""
    try:
        with io.open(ENV, encoding="utf-8") as f:
            rows = f.read().splitlines()
    except (OSError, UnicodeDecodeError):
        rows = []
    found = {}
    for row in rows:
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$", row)
        if m:
            found[m.group(1)] = m.group(2).strip()
    return rows, found


def env_set(name, value):
    """Sets one line of .env, as you, readable by you only."""
    rows, _ = env_read()
    line = "{}={}".format(name, value)
    for n, row in enumerate(rows):
        if re.match(r"^\s*{}=".format(re.escape(name)), row):
            rows[n] = line
            break
    else:
        rows.append(line)
    tmp = ENV + ".{}.tmp".format(os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with io.open(fd, "w", encoding="utf-8") as out:
        out.write("\n".join(rows) + "\n")
    os.rename(tmp, ENV)


def web_cert(args):
    """./ts setup --web-cert CERT KEY: your own certificate for the page, so browsers don't warn.
    ./ts setup --web-cert remove: back to the one ./ts setup made."""
    if args == ["remove"]:
        try:
            if os.path.exists(WEB_CERT):
                os.unlink(WEB_CERT)
            env_set("WEB_TLS_KEY", "")
        except OSError as e:
            print("Could not remove it: {}".format(e))
            return 1
        print("Removed. The page goes back to the certificate ./ts setup made. On the hub: docker compose up -d")
        return 0
    if len(args) != 2:
        print("Usage: ./ts setup --web-cert CERT.pem KEY.pem   or   ./ts setup --web-cert remove")
        return 2
    cert, key = (os.path.join(START, a) for a in args)
    try:
        with open(key, "rb") as f:
            secret = f.read()
        with open(cert, "rb") as f:
            public = f.read()
    except OSError as e:
        print("Could not read {}: {}".format(e.filename, e.strerror))
        return 1
    if b"BEGIN CERTIFICATE" not in public:
        print("{} is not a certificate in PEM form (it should start with -----BEGIN CERTIFICATE-----).".format(args[0]))
        return 1
    if b"PRIVATE KEY" not in secret:
        print("{} is not a private key in PEM form.".format(args[1]))
        return 1
    if b"ENCRYPTED" in secret:
        print("The key has a passphrase, and the page starts with nobody there to type it.\n"
              "Make a copy without one, then give that:\n  openssl pkey -in {} -out key-nopass.pem".format(args[1]))
        return 1
    try:
        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(cert, key)
    except (ssl.SSLError, OSError, ValueError) as e:
        print("The certificate and the key do not belong together, or one is damaged ({}).".format(getattr(e, "reason", None) or e))
        return 1
    try:
        os.makedirs(os.path.dirname(WEB_CERT), exist_ok=True)
        with open(WEB_CERT + ".tmp", "wb") as out:
            out.write(public)
        os.chmod(WEB_CERT + ".tmp", 0o644)
        os.rename(WEB_CERT + ".tmp", WEB_CERT)
        env_set("WEB_TLS_KEY", base64.b64encode(secret).decode("ascii"))
    except OSError as e:
        print("Could not write the certificate or .env: {}".format(e))
        return 1
    print("The page will use your certificate (copied to certs/web-cert.pem; its key is in .env, not shown).\n"
          "Hosts still talk to each other with the certificate ./ts setup made.\n"
          "Next, on the hub: docker compose up -d")
    return 0


def setup(args):
    """The shared key and the certificate that let the hub and the agents talk, encrypted."""
    if args[1:2] == ["--web-cert"]:
        return web_cert(args[2:])
    if args[1:]:
        print("Usage: ./ts setup   or   ./ts setup --web-cert CERT.pem KEY.pem")
        return 2
    _, have = env_read()
    did = []
    if not have.get("AGENT_KEY"):
        env_set("AGENT_KEY", secrets.token_urlsafe(32))
        did.append("the shared key (AGENT_KEY in .env)")
    if not have.get("TLS_KEY") or not os.path.isfile(CERT):
        if not shutil.which("openssl"):
            print("openssl is not on this host. Run ./ts setup on a host that has it: the folder is shared.")
            return 1
        folder = tempfile.mkdtemp()
        key = os.path.join(folder, "key.pem")
        try:
            os.makedirs(os.path.dirname(CERT), exist_ok=True)
            made = subprocess.call(
                ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650", "-sha256",
                 "-subj", "/CN=troubleshooter", "-keyout", key, "-out", CERT + ".tmp"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if made != 0:
                print("openssl could not make the certificate.")
                return 1
            with open(key, "rb") as f:
                env_set("TLS_KEY", base64.b64encode(f.read()).decode("ascii"))
            os.chmod(CERT + ".tmp", 0o644)
            os.rename(CERT + ".tmp", CERT)
        except OSError as e:
            print("Could not write the certificate or .env: {}".format(e))
            return 1
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        did.append("the certificate (certs/troubleshooter-cert.pem, good for 10 years) and its key (TLS_KEY in .env)")
    if not did:
        print("Nothing to do: the key and the certificate are already there.")
        return 0
    print("Made " + " and ".join(did) + ".")
    print("Next, on every host: docker compose up -d   then   ./ts check")
    return 0


def user(args):
    """./ts user add NAME | remove NAME | list: the accounts of the web page."""
    _, have = env_read()
    users = dict(u.split(":", 1) for u in have.get("WEB_USERS", "").split(",") if ":" in u)
    if args[1:2] == ["list"]:
        print("\n".join(sorted(users)) or "No accounts yet. Add one with: ./ts user add NAME")
        return 0
    if len(args) != 3 or args[1] not in ("add", "remove") or not USER.match(args[2]):
        print("Usage: ./ts user add NAME | ./ts user remove NAME | ./ts user list\n"
              "A name is up to 32 letters, digits, dots, dashes or underscores.")
        return 2
    name = args[2]
    if args[1] == "remove":
        if name not in users:
            print("There is no account named {}.".format(name))
            return 1
        del users[name]
    else:
        first = getpass.getpass("Password for {} (10 characters or more): ".format(name))
        if len(first) < 10:
            print("Too short. Nothing was changed.")
            return 1
        if getpass.getpass("Again: ") != first:
            print("The two did not match. Nothing was changed.")
            return 1
        salt = secrets.token_hex(16)
        done = hashlib.pbkdf2_hmac("sha256", first.encode("utf-8"), bytes.fromhex(salt), ROUNDS)
        users[name] = "pbkdf2${}${}${}".format(ROUNDS, salt, done.hex())
    try:
        env_set("WEB_USERS", ",".join("{}:{}".format(n, h) for n, h in sorted(users.items())))
    except OSError as e:
        print("Could not write .env: {}".format(e))
        return 1
    print("{} {}. On the hub host: docker compose up -d".format("Removed" if args[1] == "remove" else "Saved", name))
    return 0


def main():
    args = sys.argv[1:]
    os.chdir(HOME)
    if args[:1] in (["-h"], ["--help"], ["help"]):
        with open(HELP) as f:
            sys.stdout.write(f.read())
        return 0
    if args[:1] == ["setup"]:
        return setup(args)
    if args[:1] == ["user"]:
        return user(args)
    signals = Signals()
    proxies = Proxies()
    try:
        share_cases()  # also those saved from the web page, or in a chat that was cut off
        if not kubernetes_off():
            proxies.start()
        if args[:1] == ["learn"]:
            return learn(signals, proxies, args)
        if args[:1] == ["watch"]:
            return watch(signals, proxies, args)
        code = signals.call(exec_args(proxies) + args)
        if args[:1] in ([], ["chat"]):
            share_cases()
        return 130 if signals.stopped and code else code
    except Stopped:
        return 130
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)  # finish cleaning up even if Ctrl-C is pressed again
        proxies.stop()


if __name__ == "__main__":
    sys.exit(main())
