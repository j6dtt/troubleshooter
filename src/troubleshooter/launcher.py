"""Runs on the host, as you, and starts the troubleshooter in its container. ./ts calls it.

It lives here, not in ./ts, so updates are .py files only. Uses only the standard library and works
on Python 3.6 or newer, as found on older hosts.

  - Starts one kubectl proxy per context on 127.0.0.1, as you, so the container can use your logins.
    They stop when this ends.
  - ./ts setup: makes the key and certificate the hosts use to talk to each other, into .env and
    ./certs. ./ts user: the accounts of the web page, into .env. Neither is ever shown.
  - ./ts login: renews every kubectl vsphere login with one password. The password goes to kubectl
    only, for that moment; it is never stored or shown.
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
WCP = re.compile(r"^wcp:(\[[^\]]+\]|[^:]+):(.+)$")  # the user kubectl vsphere login writes: wcp:<server>:<name>
KUBE_HOME = re.compile(r'^\s*kube-home:\s*&kube-home\s+"([^"]*)"', re.M)
REFUSED = re.compile(r"password|credential|unauthori[sz]ed|invalid|locked|denied", re.I)
SOON = 2 * 3600      # a login that ends within this is said when ./ts starts


def safe(text):
    """Same rule as learn.app_name in the container: one name for one file."""
    return re.sub(r"[^A-Za-z0-9._-]", "-", text)


def quiet(argv):
    """Output of a command, or "" if it fails or is missing."""
    try:
        return subprocess.check_output(argv, stderr=subprocess.DEVNULL, universal_newlines=True)
    except (OSError, subprocess.CalledProcessError):
        return ""


def token_end(token):
    """When a login token ends (epoch seconds), as the token itself says; 0 when it does not say."""
    try:
        part = str(token).split(".")[1]
        end = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)).decode("utf-8")).get("exp")
        return float(end) if isinstance(end, (int, float)) and not isinstance(end, bool) and end > 0 else 0
    except (IndexError, ValueError, AttributeError, TypeError):
        return 0


def kube_view(raw=False, env=None):
    """The kubeconfig as kubectl reads it. raw=True has the tokens: used only for when they end."""
    try:
        out = subprocess.check_output(["kubectl", "config", "view", "-o", "json"] + (["--raw"] if raw else []),
                                      stderr=subprocess.DEVNULL, universal_newlines=True, env=env)
        return json.loads(out) or {}
    except (OSError, subprocess.CalledProcessError, ValueError):
        return {}


def login_ends(env=None):
    """{context: when its login ends}, only for the logins that say."""
    cfg = kube_view(raw=True, env=env)
    ends = {u.get("name"): token_end((u.get("user") or {}).get("token") or "") for u in cfg.get("users") or []}
    found = {}
    for c in cfg.get("contexts") or []:
        end = ends.get((c.get("context") or {}).get("user")) or 0
        if end:
            found[c.get("name")] = end
    return found


def span(seconds):
    seconds = max(0, int(seconds))
    return "{} min".format(max(1, seconds // 60)) if seconds < 5400 else "{} h".format(int(round(seconds / 3600.0)))


def ending_soon(ends, now=None):
    """One line about logins that have ended or end soon, or "" when none do."""
    now = now or time.time()
    over = sorted(c for c, end in ends.items() if end <= now)
    soon = sorted((end, c) for c, end in ends.items() if now < end <= now + SOON)
    parts = []
    if over:
        parts.append("The login to {} has ended.".format(", ".join(over)))
    if soon:
        parts.append("The login to {} ends in {}.".format(", ".join(c for _, c in soon), span(soon[0][0] - now)))
    return " ".join(parts) + " ./ts login renews every login with one password." if parts else ""


def login_plan(cfg):
    """What kubectl vsphere login wrote in a kubeconfig, as what to log in to again:
    ([{server, user, insecure, contexts, namespaces}] for each Supervisor, [{name, user}] for each VKS cluster).
    A Supervisor's contexts share one cluster entry: its own (named after the server) and one per vSphere
    namespace. A VKS cluster has one context, named after the cluster."""
    servers = {c.get("name"): c.get("cluster") or {} for c in cfg.get("clusters") or []}
    users = {u.get("name"): WCP.match(u.get("name") or "") for u in cfg.get("users") or []}
    groups = {}
    for c in cfg.get("contexts") or []:
        ctx = c.get("context") or {}
        who = users.get(ctx.get("user"))
        if who and ctx.get("cluster") in servers:
            groups.setdefault((ctx["cluster"], ctx["user"]), []).append((c.get("name") or "", ctx.get("namespace") or ""))
    supervisors, clusters = [], []
    for (entry, user), members in sorted(groups.items()):
        who, server = users[user], servers[entry]
        host = who.group(1).strip("[]")
        address = re.sub(r"^https?://|[:/]\d*/?$", "", server.get("server") or "")
        names = [name for name, _ in members]
        if len(members) > 1 or any(space for _, space in members) or names[0] in (host, address, entry):
            supervisors.append({"server": host, "user": who.group(2), "insecure": bool(server.get("insecure-skip-tls-verify")),
                                "contexts": sorted(names), "namespaces": sorted({(name, space) for name, space in members if space})})
        else:
            clusters.append({"name": names[0], "user": who.group(2)})
    return supervisors, clusters


def vsphere_login(server, user, password, insecure, env, namespace="", cluster=""):
    """(True, "") or (False, why). The password is given to kubectl in its environment, never on its command line."""
    argv = ["kubectl", "vsphere", "login", "--server=" + server, "--vsphere-username", user]
    if insecure:
        argv.append("--insecure-skip-tls-verify")
    if cluster:
        argv += ["--tanzu-kubernetes-cluster-namespace", namespace, "--tanzu-kubernetes-cluster-name", cluster]
    try:
        p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             universal_newlines=True, env=dict(env, KUBECTL_VSPHERE_PASSWORD=password))
        try:
            out = p.communicate(timeout=90)[0]
        except subprocess.TimeoutExpired:
            p.kill()
            p.communicate()
            return False, "no answer within 90 seconds"
    except OSError as e:
        return False, str(e)
    if p.returncode == 0:
        return True, ""
    said = [row.strip() for row in (out or "").replace(password, "***").splitlines() if row.strip()]
    return False, said[-1][:300] if said else "kubectl vsphere login failed with exit code {}".format(p.returncode)


def login_files():
    """[(label, environment)] of each kubeconfig to renew: yours, and kube-home's when that is another file."""
    mine = (os.environ.get("KUBECONFIG") or "").split(os.pathsep)[0] or os.path.join(os.path.expanduser("~"), ".kube", "config")
    found = [(mine, dict(os.environ))]
    try:
        with io.open(os.path.join(HOME, "compose.yaml"), encoding="utf-8") as f:
            m = KUBE_HOME.search(f.read())
    except (OSError, UnicodeDecodeError):
        m = None
    if m and m.group(1):
        other = os.path.join(os.path.abspath(os.path.join(HOME, os.path.expanduser(m.group(1)))), "config")
        if os.path.isfile(other) and os.path.realpath(other) != os.path.realpath(mine):
            found.append((other, dict(os.environ, KUBECONFIG=other)))
    return found


def login(args):
    """./ts login: every kubectl vsphere login again, with one password for each user name."""
    if kubernetes_off():
        print('Kubernetes is "off" in compose.yaml: there are no logins to renew.')
        return 1
    if not shutil.which("kubectl") or not (shutil.which("kubectl-vsphere") or quiet(["kubectl", "vsphere", "version"])):
        print("This needs kubectl and its vsphere plugin (kubectl-vsphere) on this host.")
        return 1
    passwords, done, failed, stop = {}, 0, 0, False
    for label, env in login_files():
        cfg = kube_view(env=env)
        supervisors, clusters = login_plan(cfg)
        if not supervisors and not clusters:
            print("{}: no kubectl vsphere login found there. Log in once by hand; ./ts login renews it from then on.".format(label))
            continue
        print("Logins to renew in {}:".format(label))
        for s in supervisors:
            print("  Supervisor {} as {}  (contexts: {})".format(s["server"], s["user"], ", ".join(s["contexts"])))
        for c in clusters:
            print("  VKS cluster {} as {}".format(c["name"], c["user"]))
        current = (cfg.get("current-context") or "")
        ok_supervisors = []
        for s in supervisors:
            if s["user"] not in passwords:
                passwords[s["user"]] = getpass.getpass("Password for {}: ".format(s["user"]))
            good, why = vsphere_login(s["server"], s["user"], passwords[s["user"]], s["insecure"], env)
            if good:
                done += 1
                ok_supervisors.append(s)
                print("Logged in to Supervisor {}.".format(s["server"]))
                continue
            failed += 1
            print("Could not log in to Supervisor {}: {}".format(s["server"], why))
            if REFUSED.search(why):
                stop = True
                print("Stopped here: trying the same password again elsewhere could lock the account.")
                break
        if not stop and clusters:
            homes = cluster_homes(ok_supervisors, env)
            for c in clusters:
                home = homes.get(c["name"])
                if not home:
                    failed += 1
                    print("VKS cluster {}: no vSphere namespace this login can see holds it. Log in to it by hand.".format(c["name"]))
                    continue
                s, namespace = home
                good, why = vsphere_login(s["server"], s["user"], passwords[s["user"]], s["insecure"], env, namespace, c["name"])
                if good:
                    done += 1
                    print("Logged in to VKS cluster {} (vSphere namespace {}).".format(c["name"], namespace))
                else:
                    failed += 1
                    print("Could not log in to VKS cluster {}: {}".format(c["name"], why))
        if current and done:  # kubectl vsphere login switches the current context: put yours back
            subprocess.call(["kubectl", "config", "use-context", current], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        if stop:
            break
    passwords.clear()
    if done:
        ends = [end for _, env in login_files() for end in login_ends(env).values() if end > time.time()]
        until = " They last until about {}.".format(time.strftime("%H:%M", time.localtime(min(ends)))) if ends else ""
        print("{} login{} renewed.{} The page shows them within a minute; no restart.".format(done, "" if done == 1 else "s", until))
    if failed:
        print("{} login{} not renewed.".format(failed, "" if failed == 1 else "s"))
    return 0 if done and not failed else 1


def cluster_homes(supervisors, env):
    """{VKS cluster: (its Supervisor, its vSphere namespace)}, asked from each namespace a fresh login can see."""
    found = {}
    for s in supervisors:
        cfg = kube_view(env=env)  # read again: a login can bring new namespaces
        fresh = next((x for x in login_plan(cfg)[0] if x["server"] == s["server"] and x["user"] == s["user"]), s)
        for context, namespace in fresh["namespaces"]:
            try:
                names = subprocess.check_output(
                    ["kubectl", "--context", context, "--request-timeout=20s", "get", "clusters.cluster.x-k8s.io", "-n", namespace,
                     "-o", "jsonpath={.items[*].metadata.name}"], stderr=subprocess.DEVNULL, universal_newlines=True, env=env)
            except (OSError, subprocess.CalledProcessError):
                continue
            for name in names.split():
                found.setdefault(name, (s, namespace))
    return found


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
        ends = login_ends()
        said = ending_soon(ends)
        if said:
            print(said)
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
                parts.append("{}|{}|{}|{};".format(context, port, namespace, int(ends.get(context) or 0)))
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
        # Dots, not "$": Docker Compose reads "$" in .env as the start of a variable.
        users[name] = "pbkdf2.{}.{}.{}".format(ROUNDS, salt, done.hex())
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
    if args[:1] == ["login"]:
        return login(args)
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
