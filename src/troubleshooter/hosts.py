"""Several hosts: the hub asks, and an agent answers about its own host.

Every host runs the same code. The one named in hub-host is the hub; the others are agents. An agent
listens on agent-port and runs only the fixed read tools, only for the hub:

  - the traffic is encrypted, and the hub accepts no certificate but the one in ./certs;
  - the caller must send the shared key (AGENT_KEY in .env) and must be the hub's address;
  - nothing is changed: an agent has no fix tools, no Kubernetes tools and no shell.

./ts setup makes the certificate and the keys.
"""
import base64
import dataclasses
import hmac
import http.server
import json
import os
import socket
import ssl
import time
import urllib.error
import urllib.request

from . import config
from .redact import strip_control

NOT_ASKED = {"list_runbooks", "read_runbook", "past_cases"}   # shared by every host: the hub reads them itself
MOST_BODY = 200_000
KEY_FILE = "/tmp/troubleshooter-tls-key.pem"
WEB_KEY_FILE = "/tmp/troubleshooter-web-key.pem"


class Unreachable(Exception):
    """A host did not answer, with the reason in plain words."""


def not_ready(s):
    """Why this host can't talk to the others yet, or "" if it can."""
    if not s.agent_key:
        return "AGENT_KEY is missing from .env. Run ./ts setup, then docker compose up -d."
    if not s.tls_key:
        return "TLS_KEY is missing from .env. Run ./ts setup, then docker compose up -d."
    if not os.path.isfile(s.tls_cert):
        return f"The certificate {s.tls_cert} is missing. Run ./ts setup."
    return ""


def _key_file(encoded, path):
    """A private key is kept in .env, not in the shared folder; ssl wants it as a file."""
    pem = base64.b64decode(encoded)
    try:
        with open(path, "rb") as f:
            if f.read() == pem:
                return path
    except OSError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    return path


def own_web_cert(s):
    """True if the page has a certificate of your own (./ts setup --web-cert)."""
    return bool(s.web_tls_key) and os.path.isfile(s.web_cert)


def server_context(s, web=False):
    """Between hosts: always the certificate ./ts setup made. For the page: your own, if you gave one."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    if web and own_web_cert(s):
        ctx.load_cert_chain(s.web_cert, _key_file(s.web_tls_key, WEB_KEY_FILE))
    else:
        ctx.load_cert_chain(s.tls_cert, _key_file(s.tls_key, KEY_FILE))
    return ctx


def days_left(path):
    """Days until a certificate file runs out, or None if that can't be read."""
    try:
        found = ssl._ssl._test_decode_cert(path)  # the only parser in the standard library
        return int((ssl.cert_time_to_seconds(found["notAfter"]) - time.time()) // 86400)
    except Exception:
        return None


def client_context(s):
    """Trusts exactly our own certificate, whatever host name it is served from."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(s.tls_cert)
    return ctx


# ---------- the hub's side ----------
def ask(s, host, tool, args=None, timeout=90):
    """Run a tool on another host and return its text. Raises Unreachable."""
    known = config.agents(s)
    if host not in known:
        raise Unreachable(f"{host} is not one of the agent hosts ({', '.join(known) or 'none set'}).")
    why = not_ready(s)
    if why:
        raise Unreachable(why)
    body = json.dumps({"tool": tool, "args": args or {}}).encode()
    request = urllib.request.Request(
        f"https://{known[host]}:{s.agent_port}/run", data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + s.agent_key})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),  # never through a web proxy
                                         urllib.request.HTTPSHandler(context=client_context(s)))
    try:
        with opener.open(request, timeout=timeout) as r:
            return str(json.load(r).get("text", ""))
    except urllib.error.HTTPError as e:
        try:
            said = json.loads(e.read(2000)).get("text", "")
        except ValueError:
            said = ""
        raise Unreachable(f"{host} refused (HTTP {e.code}): {said or e.reason}") from None
    except ssl.SSLError as e:
        raise Unreachable(f"{host}: the encrypted connection failed ({e.reason or e}). Both hosts need the same "
                          f"certificate and TLS_KEY.") from None
    except (OSError, ValueError) as e:
        reason = getattr(e, "reason", e)
        raise Unreachable(f"{host} did not answer on port {s.agent_port}: {reason}") from None


# ---------- the agent's side ----------
def hub_addresses(s):
    """The addresses a request may come from: the hub's."""
    where = config.address(s.hub_host)
    found = set()
    try:
        for info in socket.getaddrinfo(where, None):
            found.add(info[4][0])
    except OSError:
        pass
    return found


def _plain(address):
    return address[7:] if address.startswith("::ffff:") else address


class Agent:
    """Answers the hub with this host's own read tools."""

    def __init__(self, settings, log=lambda line: None):
        from .tools import Toolbox  # late: tools needs this module too
        self.s, self.log = settings, log
        # No fix tools and no Kubernetes here: the hub looks at clusters once, for every host.
        self.box = Toolbox(dataclasses.replace(settings, allow_changes=False, kubernetes=False, hub_host=""), clusters={})
        self.allowed, self.checked = set(), 0.0

    def may_call(self, address):
        if time.monotonic() - self.checked > 300 or _plain(address) not in self.allowed:
            self.allowed, self.checked = hub_addresses(self.s), time.monotonic()
        return _plain(address) in self.allowed

    def run(self, tool, args):
        """(ok, text) for one request from the hub."""
        from . import history, watch
        from .tools import BadInput
        if tool == "_problems":
            return True, json.dumps(watch.local_problems(self.box))
        if tool == "_survey":
            return True, json.dumps(watch.local_survey(self.box))
        if tool == "_record":  # the hub found a new problem here: write the state down now, so the change shows
            history.record(self.box)
            return True, "recorded"
        if tool == "_map":
            return True, json.dumps(watch.local_map(self.box))
        if tool == "_events":
            return True, json.dumps(history.events(self.s, int(args.get("since", 0))))
        if tool == "_coverage":
            return True, json.dumps(history.coverage(self.s, int(args.get("since", 0))))
        found = self.box.tools.get(tool)
        if found is None or tool in NOT_ASKED:
            return False, f"Refused: {tool} is not a tool this agent answers."
        try:
            checked = found.check(args)
            say = found.describe(checked).strip()
            text = self.box.out(str(found.fn(**checked)) or "(no output)")
            self.log(f"The hub asked: {say} ({len(text)} characters back)")
            return True, text
        except BadInput as e:
            self.log(f"Refused a {tool} request from the hub: {e}")
            return False, f"Refused: {e}"
        except Exception as e:  # one failing tool must not take the agent down
            self.log(f"{tool}, asked by the hub, failed: {e}")
            return True, self.box.out(f"The tool failed: {e}")


class Secure(http.server.BaseHTTPRequestHandler):
    """An https request: the handshake happens here, in the request's own thread."""
    timeout = 60
    protocol_version = "HTTP/1.1"
    server_version = "troubleshooter"
    sys_version = ""

    def setup(self):
        self.request.settimeout(self.timeout)
        self.request.do_handshake()
        super().setup()

    def log_message(self, *a):
        pass


def _handler(agent):
    class Handler(Secure):
        timeout = 30

        def _send(self, code, ok, text):
            body = json.dumps({"ok": ok, "text": text}).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def do_POST(self):
            caller = self.client_address[0]
            sent = self.headers.get("Authorization", "")
            if not hmac.compare_digest(sent.encode(), ("Bearer " + agent.s.agent_key).encode()):
                agent.log(f"Refused a request from {caller}: wrong key")
                return self._send(401, False, "wrong key")
            if not agent.may_call(caller):
                agent.log(f"Refused a request from {caller}: it is not the hub ({agent.s.hub_host})")
                return self._send(403, False, f"{caller} is not the hub's address. If the hub has several "
                                              f"addresses, write hub-host as name=address.")
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if self.path != "/run" or not 0 < size <= MOST_BODY:
                    return self._send(400, False, "bad request")
                asked = json.loads(self.rfile.read(size))
                tool, args = strip_control(str(asked.get("tool", ""))), asked.get("args") or {}
                if not isinstance(args, dict):
                    return self._send(400, False, "bad request")
            except (ValueError, OSError):
                return self._send(400, False, "bad request")
            ok, text = agent.run(tool, args)
            self._send(200, ok, text)

        def do_GET(self):
            self._send(404, False, "not found")

    return Handler


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        pass  # a scanner or a dropped connection is not worth a traceback


def serve(settings, handler, port, web=False):
    """An https server. The handshake is done per connection, so a slow caller can't hold up the rest."""
    httpd = Server(("", port), handler)
    httpd.socket = server_context(settings, web).wrap_socket(httpd.socket, server_side=True, do_handshake_on_connect=False)
    httpd.serve_forever()


def serve_agent(settings, log=lambda line: None):
    why = not_ready(settings)
    if why:
        print(f"The agent is not listening: {why}", flush=True)
        return
    print(f"Agent: answering the hub {config.short(settings.hub_host)} on port {settings.agent_port}.", flush=True)
    serve(settings, _handler(Agent(settings, log)), settings.agent_port)
