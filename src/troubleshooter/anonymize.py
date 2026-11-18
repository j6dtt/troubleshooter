"""Anonymize: host names, cluster names, IP addresses and chosen words are swapped for stand-ins
before anything reaches the model, and swapped back in what it answers.

  web-prod-07     -> hostA            (this host, the hub and the agents, and their addresses)
  prod-cluster    -> clusterA         (Kubernetes contexts)
  site.lan, acme   -> corpA.example, nameA   (anonymize-words, and this host's DNS domain)
  10.20.1.5       -> 198.18.0.5       (each /24 gets its own stand-in /24; the last number is kept,
                                       so "same subnet" still reads as the same subnet)

The model only ever sees the stand-ins, so the tools it calls name them too: they are swapped back
before a tool runs. The table lives in memory and only grows, so a name keeps its stand-in for as long
as the container runs. Loopback, 0.0.0.0 and masks (255.x) are left as they are.
"""
import json
import re
import socket
import threading

NOTE = ("\n\nNames such as hostA, clusterA, nameA and corpA.example, and addresses in 198.18.0.0/15, stand for "
        "real ones that are hidden from you. Use them exactly as written, in answers and in tool calls.")
IPV4 = re.compile(r"(?<![\w.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\w]|\.\d)")
STAND_IP = re.compile(r"(?<![\w.])198\.(18|19)\.(\d{1,3})\.(\d{1,3})(?![\w]|\.\d)")
EDGE = r"(?<![A-Za-z0-9])({})(?![A-Za-z0-9])"


def letters(n):
    """0 -> A, 25 -> Z, 26 -> AA."""
    text = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        text = chr(65 + r) + text
    return text


class Table:
    def __init__(self):
        self.lock = threading.Lock()
        self.real, self.stand = {}, {}      # real (lower case) -> stand-in, stand-in -> real
        self.counts = {"host": 0, "cluster": 0, "name": 0, "corp": 0}
        self.nets, self.back = {}, {}       # "10.20.1" -> 3, 3 -> "10.20.1"
        self._hide = self._show = None

    def known(self, names, kind):
        """Names to hide: kind is host, cluster or name (a word with a dot is hidden as a domain)."""
        with self.lock:
            for name in names:
                name = (name or "").strip().strip(".")
                if len(name) < 3 or name.lower() in self.real or IPV4.fullmatch(name) or name in ("localhost", "localdomain"):
                    continue
                k = "corp" if kind == "name" and "." in name else kind
                stand = f"{k}{letters(self.counts[k])}" + (".example" if k == "corp" else "")
                self.counts[k] += 1
                self.real[name.lower()], self.stand[stand] = stand, name
                self._hide = self._show = None

    def _patterns(self):
        if self._hide is None:
            reals = sorted(self.real, key=len, reverse=True)
            stands = sorted(self.stand, key=len, reverse=True)
            self._hide = re.compile(EDGE.format("|".join(map(re.escape, reals))), re.I) if reals else None
            self._show = re.compile(EDGE.format("|".join(map(re.escape, stands)))) if stands else None
        return self._hide, self._show

    def _net(self, prefix):
        if prefix not in self.nets:
            n = len(self.nets)
            self.nets[prefix], self.back[n] = n, prefix
        n = self.nets[prefix]
        return f"198.{18 + n // 256}.{n % 256}"

    def hide(self, text):
        if not text:
            return text
        with self.lock:
            names, _ = self._patterns()
            if names:
                text = names.sub(lambda m: self.real[m.group(1).lower()], text)

            def ip(m):
                parts = [int(x) for x in m.groups()]
                if max(parts) > 255 or parts[0] in (0, 127, 255) or (parts[0] == 198 and parts[1] in (18, 19)):
                    return m.group(0)
                return f"{self._net('.'.join(m.groups()[:3]))}.{m.group(4)}"
            return IPV4.sub(ip, text)

    def show(self, text):
        if not text:
            return text
        with self.lock:
            _, stands = self._patterns()
            if stands:
                text = stands.sub(lambda m: self.stand[m.group(1)], text)

            def ip(m):
                n = (int(m.group(1)) - 18) * 256 + int(m.group(2))
                return f"{self.back[n]}.{m.group(3)}" if n in self.back else m.group(0)
            return STAND_IP.sub(ip, text)


TABLE = Table()


def known(names, kind):
    TABLE.known(names, kind)


_SET = set()


def setup(s):
    """The names every host knows: itself, the hub and agents, their DNS domain and anonymize-words."""
    from . import config
    key = (s.hub_host, tuple(config.agents(s)), s.anonymize_words)
    if key in _SET:
        return
    _SET.add(key)
    names = [config.here(), *(e.split("=")[0] for e in [s.hub_host, *config.agents(s)] if e)]
    addresses = [e.split("=", 1)[1] for e in [s.hub_host, *config.agents(s)] if "=" in (e or "")]
    known(names + addresses, "host")
    fqdn = socket.getfqdn()
    if "." in fqdn and not fqdn.endswith((".localdomain", ".local")):
        known([fqdn], "host")
        known([fqdn.split(".", 1)[1]], "name")
    known([w for w in re.split(r"[,\s]+", s.anonymize_words or "") if w], "name")


def _each(message, change):
    out = dict(message)
    if isinstance(out.get("content"), str):
        out["content"] = change(out["content"])
    if out.get("tool_calls"):
        out["tool_calls"] = [{**c, "function": {**c["function"], "arguments": change(c["function"].get("arguments") or "")}}
                             for c in out["tool_calls"]]
    return out


def hide_messages(messages):
    out = [_each(m, TABLE.hide) for m in messages]
    if out and out[0].get("role") == "system":
        out[0]["content"] = (out[0].get("content") or "") + NOTE
    return out


def hide_tools(tools):
    return json.loads(TABLE.hide(json.dumps(tools))) if tools else tools


def show_message(message):
    return _each(message, TABLE.show)


class Streamed:
    """Shows streamed text with the real names: holds back the last word until it is complete."""

    def __init__(self, on_text):
        self.on_text, self.held = on_text, ""

    def __call__(self, piece):
        text = self.held + piece
        cut = max(text.rfind(" "), text.rfind("\n"), text.rfind("\t"))
        self.held = text[cut + 1:]
        if cut >= 0:
            self.on_text(TABLE.show(text[:cut + 1]))

    def flush(self):
        if self.held:
            self.on_text(TABLE.show(self.held))
            self.held = ""
