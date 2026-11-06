"""What the model can do. Read tools run freely; change tools only build a Change for approval.

Every argument is checked here and passed as one fixed argv item. There is no shell and no
free-form command tool, so a bad argument (or a poisoned log line) can't widen what runs.
"""
import json
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .redact import clean
from .run import run

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,252}$")
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", ".tox", ".mypy_cache",
             ".pytest_cache", "dist", "build", "target", ".gradle", ".idea", "vendor"}
READ_LINES = 300          # most lines of a file returned at once
MAX_FILE = 20_000_000     # larger files are read from the end only
IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-/:@]{0,254}$")
NET_TEST = """
import socket, sys, time
host, port = sys.argv[1], int(sys.argv[2])
try:
    found = sorted({a[4][0] for a in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
except OSError as e:
    print(f"DNS failed for {host}: {e}")
    sys.exit()
print(f"DNS: {host} is {', '.join(found)}")
began = time.monotonic()
try:
    socket.create_connection((host, port), timeout=5).close()
    print(f"TCP: connected to port {port} in {(time.monotonic() - began) * 1000:.0f} ms")
except OSError as e:
    print(f"TCP: could not connect to port {port}: {e}")
"""
SINCE = re.compile(r"^(\d{1,5})([smhd])$")
NO_NAMESPACE = ("nodes", "namespaces", "persistentvolumes", "storageclasses")
SECRET_KIND = re.compile(r"^secrets?(\.|$)", re.I)
SCALABLE = ("deployments", "statefulsets")
RESTARTABLE = ("deployments", "statefulsets", "daemonsets")
SECRET_FILE = re.compile(r"(^|/)(\.env[^/]*|.*\.(key|pem|p12|pfx|jks|keystore|kdbx)|id_(rsa|dsa|ecdsa|ed25519)|"
                         r"shadow|gshadow|credentials?(\.\w+)?|\.netrc|\.pgpass|kubeconfig|client\.config)$", re.I)
REAL_FS = ("ext2", "ext3", "ext4", "xfs", "btrfs", "zfs", "vfat", "nfs", "nfs4", "cifs", "ceph", "f2fs")
UNITS = {"docker": ("docker.service", "containerd.service"),
         "kubernetes": ("kubelet.service", "k3s.service", "k3s-agent.service", "rke2-server.service",
                        "rke2-agent.service", "containerd.service", "snap.microk8s.daemon-kubelite.service",
                        "snap.microk8s.daemon-containerd.service", "snap.microk8s.daemon-k8s-dqlite.service")}
CHECKS = {
    "processes": "ps aux 2>/dev/null || for p in /proc/[0-9]*; do printf '%s ' \"${p#/proc/}\"; "
                 "tr '\\000' ' ' < \"$p/cmdline\"; echo; done",
    "disk": "df -h",
    "memory": "cat /sys/fs/cgroup/memory.max /sys/fs/cgroup/memory.current 2>/dev/null; head -3 /proc/meminfo",
    "listening_ports": "ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null || cat /proc/net/tcp",
    "dns_config": "cat /etc/resolv.conf /etc/hosts",
}


def kube_error(text):
    """kubectl repeats itself and prefixes noise; the last line says what went wrong."""
    if re.search(r"Unauthorized|must be logged in", text):
        return "the login was refused: the token has expired or is wrong."
    return (text.strip().splitlines() or ["no answer"])[-1][:200]


class BadInput(Exception):
    """A tool was called wrongly. The message goes back to the model so it can correct itself."""


@dataclass
class Change:
    title: str
    argv: list


@dataclass
class P:
    type: str
    desc: str
    required: bool = False
    enum: tuple = ()
    default: object = None


@dataclass
class Tool:
    name: str
    desc: str
    say: str
    fn: object
    params: dict = field(default_factory=dict)
    change: bool = False
    needs_kube: bool = False
    per_cluster: bool = True

    def schema(self):
        props = {}
        for k, p in self.params.items():
            props[k] = {"type": p.type, "description": p.desc}
            if p.enum:
                props[k]["enum"] = list(p.enum)
        return {"type": "function", "function": {
            "name": self.name, "description": self.desc,
            "parameters": {"type": "object", "properties": props,
                           "required": [k for k, p in self.params.items() if p.required]}}}

    def check(self, args):
        if not isinstance(args, dict):
            raise BadInput("Arguments must be a JSON object.")
        extra = set(args) - set(self.params)
        if extra:
            raise BadInput(f"Unknown argument(s): {', '.join(sorted(extra))}.")
        out = {}
        for k, p in self.params.items():
            v = args.get(k)
            if v is None or v == "":
                if p.required:
                    raise BadInput(f"Missing required argument: {k}.")
                out[k] = p.default
                continue
            if p.type == "integer":
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    raise BadInput(f"{k} must be a whole number.") from None
            elif p.type == "boolean":
                v = v if isinstance(v, bool) else str(v).lower() == "true"
            else:
                v = str(v)
            if p.enum and v not in p.enum:
                raise BadInput(f"{k} must be one of: {', '.join(p.enum)}.")
            out[k] = v
        return out

    def describe(self, args):
        try:
            return self.say.format(**{k: ("" if v is None else v) for k, v in args.items()})
        except (KeyError, IndexError):
            return self.say


def name(value, what="name"):
    if not NAME.match(value or ""):
        raise BadInput(f"Invalid {what}: {value!r}.")
    return value


def k8s_kind(value):
    """Any resource kind, custom ones too, except Secrets."""
    if SECRET_KIND.match(name(value, "kind")):
        raise BadInput("Secrets are never read.")
    return value


def since(value):
    """'30m', '2h', '1d' -> Go-style duration ('d' isn't understood by docker or kubectl)."""
    m = SINCE.match(value or "")
    if not m:
        raise BadInput("since must look like 30m, 2h or 1d.")
    n, unit = int(m[1]), m[2]
    return f"{n * 24}h" if unit == "d" else f"{n}{unit}"


def lines(n, default=200, most=2000):
    return max(1, min(int(n or default), most))


def grep(text, pattern):
    """Keep lines containing any of the |-separated words, ignoring case."""
    if not pattern:
        return text
    words = [w.lower() for w in pattern.split("|") if w]
    kept = [ln for ln in text.splitlines() if any(w in ln.lower() for w in words)]
    return "\n".join(kept) if kept else f"No lines contain: {pattern}"


@dataclass
class Cluster:
    id: str
    file: str
    context: str
    login_tool: str = ""  # a program the kubeconfig runs to log in; must exist in this container
    server: str = ""      # set for your own login: the kubectl proxy that ./ts started on the host
    namespace: str = ""   # the context's own namespace, used when none is given


def find_clusters(kube_dir):
    """Your current login (through ./ts), then every context of every file in the folder.

    One file with one context is named after the file.
    """
    found = {}
    for row in filter(None, os.environ.get("KUBE_PROXIES", "").split(";")):
        context, port, namespace = (row.split("|") + ["", ""])[:3]
        cid = re.sub(r"[^A-Za-z0-9_.\-]", "-", context)
        found[cid] = Cluster(cid, "", context, server=f"http://127.0.0.1:{port}", namespace=namespace)
    try:
        files = sorted(e.path for e in os.scandir(kube_dir) if e.is_file() and not e.name.startswith("."))
    except OSError:
        return found
    for path in files:
        r = run(["kubectl", "config", "view", "--kubeconfig", path, "-o", "json"], parsed=True)
        if not r.ok:
            continue
        cfg = json.loads(r.text)
        users = {u["name"]: (u.get("user", {}).get("exec") or {}).get("command", "") for u in cfg.get("users") or []}
        contexts = cfg.get("contexts") or []
        stem = os.path.splitext(os.path.basename(path))[0]
        for c in contexts:
            cid = stem if len(contexts) == 1 else f"{stem}.{c['name']}"
            cid = re.sub(r"[^A-Za-z0-9_.\-]", "-", cid)
            found[cid] = Cluster(cid, path, c["name"], users.get(c.get("context", {}).get("user"), ""))
    return found


class Toolbox:
    def __init__(self, settings, clusters=None):
        self.s = settings
        self.clusters = find_clusters(settings.kube_dir) if clusters is None else clusters
        ids = tuple(self.clusters)
        current = re.sub(r"[^A-Za-z0-9_.\-]", "-", os.environ.get("KUBE_CURRENT", ""))
        self.active = current if current in self.clusters else next(iter(ids), None)
        which = P("string", "Context (default: the active one)", False, ids)
        self.tools = {}
        for t in self._define():
            if (t.needs_kube and not ids) or (t.change and not settings.allow_changes):
                continue
            if t.needs_kube and t.per_cluster:
                t.params = {"cluster": which, **t.params}
            self.tools[t.name] = t

    def schemas(self):
        return [t.schema() for t in self.tools.values()]

    def _guard(self, target):
        for pattern in self.s.protected_names:
            if pattern == target or (pattern.endswith("*") and target.startswith(pattern[:-1])):
                raise BadInput(f"{target} is protected (PROTECTED_NAMES) and can't be changed from here.")
        return target

    def out(self, text):
        return clean(text, self.s.tool_output_chars)

    def _cluster(self, cid):
        cid = cid or self.active
        if cid not in self.clusters:
            raise BadInput(f"No such context. Available: {', '.join(self.clusters)}.")
        return self.clusters[cid]

    def _ns1(self, cluster, namespace):
        return name(namespace or self._cluster(cluster).namespace or "default", "namespace")

    def k8s_contexts(self):
        """Every context, which is active, and whether its login works right now."""
        def state(cid):
            r = run(self._kubectl(cid, "get", "--raw", "/version"), timeout=10)
            return "ok" if r.ok else kube_error(r.text)
        with ThreadPoolExecutor(max_workers=8) as pool:
            states = list(pool.map(state, self.clusters))
        rows = []
        for c, st in zip(self.clusters.values(), states):
            mark = "* " if c.id == self.active else "  "
            rows.append(f"{mark}{c.id}" + (f" (namespace {c.namespace})" if c.namespace else "") + f": {st}")
        return "\n".join(rows)

    def k8s_use_context(self, context):
        """Only for this chat: the kubeconfig and other terminals are not touched."""
        c = self._cluster(context)
        self.active = c.id
        return f"Active context is now {c.id}. Your kubeconfig was not changed."

    def _kubectl(self, cid, *args):
        c = self._cluster(cid)
        where = ["--server", c.server] if c.server else ["--context", c.context]
        return ["kubectl", "--kubeconfig", c.file or "/dev/null", *where, "--request-timeout=20s", *args]

    def _ns(self, namespace):
        return ["--all-namespaces"] if not namespace else ["-n", name(namespace, "namespace")]

    # ---------- whole-system view ----------
    def overview(self, all_clusters=False):
        chosen = list(self.clusters) if all_clusters else [self.active] if self.active else []
        parts = [self._host_summary(), self._docker_summary(), self._swarm_summary()]
        with ThreadPoolExecutor(max_workers=8) as pool:  # one dead cluster must not hold up the rest
            parts += pool.map(self._k8s_summary, chosen)
        return "\n\n".join(parts)

    def _meminfo(self):
        info = {}
        with open("/proc/meminfo") as f:
            for row in f:
                k, v = row.split(":", 1)
                info[k] = int(v.split()[0]) // 1024
        return info

    def _disks(self):
        """Real filesystems of the host, read from the mount table (df hides bind-mounted duplicates)."""
        root = self.s.host_root.rstrip("/")
        rows, seen = [], set()
        with open("/proc/self/mounts") as f:
            mounts = [ln.split()[:3] for ln in f]
        for dev, point, fstype in mounts:
            point = point.replace("\\040", " ")
            if not (point == root or point.startswith(root + "/")) or point in seen or fstype not in REAL_FS:
                continue
            if re.search(r"/snap/|/docker/|/kubelet/|/containerd/", point) or dev.startswith("/dev/loop"):
                continue
            seen.add(point)
            try:
                v = os.statvfs(point)
            except OSError:
                continue
            if v.f_blocks:
                used = 100 - v.f_bavail * 100 // v.f_blocks
                gb = 2 ** 30 / v.f_frsize
                rows.append((used, f"{point[len(root):] or '/'}: {used}% used, "
                                   f"{v.f_bavail / gb:.1f} GB free of {v.f_blocks / gb:.1f} GB"))
        return rows

    def _host_summary(self):
        with open("/proc/loadavg") as f:
            load = " ".join(f.read().split()[:3])
        with open("/proc/uptime") as f:
            days = float(f.read().split()[0]) / 86400
        m = self._meminfo()
        text = [f"HOST ({os.cpu_count()} CPUs, up {days:.1f} days)", f"load: {load}",
                f"memory: {m['MemAvailable']} MB free of {m['MemTotal']} MB; "
                f"swap used {m['SwapTotal'] - m['SwapFree']} of {m['SwapTotal']} MB"]
        return "\n".join(text + [d for _, d in sorted(self._disks(), reverse=True)[:8]])

    def _docker_rows(self):
        r = run(["docker", "ps", "-a", "--format", "{{json .}}"])
        if not r.ok:
            raise RuntimeError(r.text)
        return [json.loads(x) for x in r.text.splitlines() if x.startswith("{")]

    def _docker_summary(self):
        try:
            rows = self._docker_rows()
        except RuntimeError as e:
            return f"DOCKER: not reachable: {e}"
        bad = [f"- {c['Names']}: {c['Status']}" for c in rows
               if c["State"] != "running" or "unhealthy" in c["Status"] or "Restarting" in c["Status"]]
        running = sum(c["State"] == "running" for c in rows)
        return "\n".join([f"DOCKER: {running} of {len(rows)} containers running; {len(bad)} need a look"] + bad[:40])

    def _swarm_summary(self):
        state = run(["docker", "info", "--format", "{{.Swarm.LocalNodeState}} {{.Swarm.ControlAvailable}}"]).text
        if not state.startswith("active"):
            return "SWARM: not active"
        if not state.endswith("true"):
            return "SWARM: this host is a worker; node and service details are only on a manager"
        text = ["SWARM"]
        nodes = run(["docker", "node", "ls", "--format", "{{.Hostname}} {{.Status}} {{.Availability}}"]).text
        down = [n for n in nodes.splitlines() if " Ready Active" not in n]
        text.append(f"nodes: {len(nodes.splitlines())}, not ready: {len(down)}")
        text += [f"- {n}" for n in down]
        services = run(["docker", "service", "ls", "--format", "{{.Name}} {{.Replicas}}"]).text
        for row in services.splitlines():
            m = re.search(r"(\d+)/(\d+)", row)
            if m and m[1] != m[2]:
                text.append(f"- service short of replicas: {row}")
        text.append(f"services: {len(services.splitlines())}")
        return "\n".join(text)

    def _k8s_summary(self, cid):
        """Forbidden is normal for a namespace user: say so and show what the login can see."""
        r = run(self._kubectl(cid, "get", "nodes", "-o", "json"), parsed=True)
        if not r.ok and "Forbidden" not in r.text:
            return f"KUBERNETES {cid}: not reachable: {kube_error(r.text)}"
        nodes = json.loads(r.text)["items"] if r.ok else []
        text = []
        for n in nodes:
            ready = next((c["status"] for c in n["status"].get("conditions", []) if c["type"] == "Ready"), "?")
            pressure = [c["type"] for c in n["status"].get("conditions", [])
                        if c["type"] != "Ready" and c["status"] == "True"]
            if ready != "True" or pressure:
                text.append(f"- node {n['metadata']['name']}: Ready={ready} {' '.join(pressure)}")
        scope = "all namespaces"
        pods = run(self._kubectl(cid, "get", "pods", "--all-namespaces", "-o", "json"), parsed=True)
        if not pods.ok and "Forbidden" in pods.text:
            scope = f"namespace {self._ns1(cid, None)} only"
            pods = run(self._kubectl(cid, "get", "pods", "-n", self._ns1(cid, None), "-o", "json"), parsed=True)
        total = 0
        if pods.ok:
            items = json.loads(pods.text)["items"]
            total = len(items)
            for p in items:
                why = self._pod_trouble(p)
                if why:
                    text.append(f"- pod {p['metadata']['namespace']}/{p['metadata']['name']}: {why}")
        else:
            text.append(f"- pods: {kube_error(pods.text)}")
        count = f"{len(nodes)} nodes" if r.ok else "nodes hidden (no permission)"
        return "\n".join([f"KUBERNETES {cid}: {count}, {total} pods in {scope}; {len(text)} need a look"] + text[:40])

    @staticmethod
    def _pod_trouble(pod):
        status = pod.get("status", {})
        phase = status.get("phase")
        if phase == "Succeeded":
            return ""
        notes = [] if phase == "Running" else [phase or "unknown phase"]
        for c in status.get("containerStatuses", []):
            waiting = c.get("state", {}).get("waiting", {}).get("reason")
            if waiting:
                notes.append(waiting)
            elif not c.get("ready"):
                notes.append(f"{c['name']} not ready")
            if c.get("restartCount", 0) >= 5:
                notes.append(f"{c['name']} restarted {c['restartCount']} times")
        return ", ".join(notes)

    # ---------- Docker ----------
    def docker_list(self, include_stopped):
        fmt = "table {{.Names}}\t{{.Image}}\t{{.Status}}"
        return run(["docker", "ps", "--format", fmt] + (["-a"] if include_stopped else [])).text

    def docker_inspect(self, container):
        r = run(["docker", "inspect", name(container, "container")], parsed=True)
        if not r.ok:
            return r.text
        d = json.loads(r.text)[0]
        cfg, host, state = d.get("Config", {}), d.get("HostConfig", {}), d.get("State", {})
        labels = cfg.get("Labels") or {}
        managed = ("Swarm service" if "com.docker.swarm.service.name" in labels else
                   "Docker Compose" if "com.docker.compose.project" in labels else "plain docker run")
        slim = {"ManagedBy": managed, "Image": cfg.get("Image"), "State": state, "RestartCount": d.get("RestartCount"),
                "Env": cfg.get("Env"), "Cmd": cfg.get("Cmd"), "Entrypoint": cfg.get("Entrypoint"),
                "Healthcheck": cfg.get("Healthcheck"), "Mounts": d.get("Mounts"),
                "Ports": d.get("NetworkSettings", {}).get("Ports"),
                "Networks": list((d.get("NetworkSettings", {}).get("Networks") or {})),
                "RestartPolicy": host.get("RestartPolicy"), "Memory": host.get("Memory"),
                "NanoCpus": host.get("NanoCpus"), "NetworkMode": host.get("NetworkMode"),
                "Labels": {k: v for k, v in (cfg.get("Labels") or {}).items() if "compose" in k}}
        return json.dumps(slim, indent=1)

    def docker_logs(self, container, tail, since_, match):
        argv = ["docker", "logs", "--timestamps", "--tail", str(lines(tail))]
        if since_:
            argv += ["--since", since(since_)]
        return grep(run(argv + [name(container, "container")]).text, match) or "The log is empty for that period."

    def docker_stats(self, container):
        fmt = "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.PIDs}}"
        return run(["docker", "stats", "--no-stream", "--format", fmt] +
                   ([name(container, "container")] if container else []), timeout=40).text

    def docker_events(self, since_):
        r = run(["docker", "events", "--since", since(since_ or "1h"), "--until", "0s", "--format",
                 "{{.Time}} {{.Type}} {{.Action}} {{.Actor.Attributes.name}}"], timeout=20)
        rows = [re.sub(r"^\d+", lambda m: time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(int(m[0]))), ln)
                for ln in r.text.splitlines() if not re.search(r" exec_(create|start|die)", ln)]
        return "\n".join(rows[-300:]) or "No events in that period."

    def docker_network(self, network):
        if not network:
            return run(["docker", "network", "ls", "--format", "table {{.Name}}\t{{.Driver}}\t{{.Scope}}"]).text
        r = run(["docker", "network", "inspect", name(network, "network")], parsed=True)
        if not r.ok:
            return r.text
        d = json.loads(r.text)[0]
        return json.dumps({"Driver": d.get("Driver"), "Internal": d.get("Internal"), "IPAM": d.get("IPAM", {}).get("Config"),
                           "Containers": {v["Name"]: v.get("IPv4Address") for v in (d.get("Containers") or {}).values()}}, indent=1)

    def docker_disk(self):
        return run(["docker", "system", "df"], timeout=60).text

    def container_check(self, container, check):
        return run(["docker", "exec", name(container, "container"), "sh", "-c", CHECKS[check]]).text

    def docker_info(self):
        r = run(["docker", "info", "--format", "{{json .}}"], parsed=True)
        if not r.ok:
            return r.text
        d = json.loads(r.text)
        keep = ("ServerVersion", "OperatingSystem", "KernelVersion", "NCPU", "MemTotal", "DockerRootDir", "Driver",
                "DriverStatus", "LoggingDriver", "CgroupDriver", "CgroupVersion", "DefaultRuntime", "LiveRestoreEnabled",
                "Containers", "ContainersRunning", "ContainersPaused", "ContainersStopped", "Images",
                "HttpProxy", "HttpsProxy", "NoProxy", "Warnings", "ServerErrors")
        info = {k: d[k] for k in keep if d.get(k) not in (None, "", [])}
        info["Swarm"] = (d.get("Swarm") or {}).get("LocalNodeState")
        info["InsecureRegistries"] = (d.get("RegistryConfig") or {}).get("InsecureRegistryCIDRs")
        info["Mirrors"] = (d.get("RegistryConfig") or {}).get("Mirrors")
        path = f"{self.s.host_root}/etc/docker/daemon.json"
        if os.path.isfile(path):
            with open(path, encoding="utf-8", errors="replace") as f:
                info["daemon.json"] = f.read()[:3000]
        else:
            info["daemon.json"] = "not present: the engine runs on its defaults"
        return json.dumps(info, indent=1)

    def docker_images(self, image):
        if not image:
            return run(["docker", "images", "--format", "table {{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.CreatedSince}}\t{{.Size}}"]).text
        if not IMAGE.match(image):
            raise BadInput(f"Invalid image: {image!r}.")
        r = run(["docker", "image", "inspect", image], parsed=True)
        if not r.ok:
            return r.text
        d = json.loads(r.text)[0]
        cfg = d.get("Config") or {}
        slim = {"Id": d.get("Id"), "Tags": d.get("RepoTags"), "Created": d.get("Created"), "Size": d.get("Size"),
                "Platform": f"{d.get('Os')}/{d.get('Architecture')}", "Layers": len((d.get("RootFS") or {}).get("Layers") or []),
                "User": cfg.get("User"), "Entrypoint": cfg.get("Entrypoint"), "Cmd": cfg.get("Cmd"), "Env": cfg.get("Env"),
                "WorkingDir": cfg.get("WorkingDir"), "ExposedPorts": list(cfg.get("ExposedPorts") or {}),
                "Healthcheck": cfg.get("Healthcheck"), "Labels": cfg.get("Labels")}
        used = run(["docker", "ps", "-a", "--filter", f"ancestor={image}", "--format", "{{.Names}}"]).text
        history = run(["docker", "history", "--format", "{{.Size}}\t{{.CreatedBy}}", image]).text
        lines_ = "\n".join(ln[:160] for ln in history.splitlines()[:25])
        return f"{json.dumps(slim, indent=1)}\n\nUsed by: {used.replace(chr(10), ', ') or 'no containers'}\n\nLayers, newest first:\n{lines_}"

    def _volume_users(self):
        ids = run(["docker", "ps", "-aq"]).text.split()
        users = {}
        if ids:
            r = run(["docker", "inspect", *ids], parsed=True)
            for c in json.loads(r.text) if r.ok else []:
                for m in c.get("Mounts") or []:
                    if m.get("Type") == "volume":
                        users.setdefault(m.get("Name"), []).append(c["Name"].lstrip("/"))
        return users

    def docker_volumes(self, volume):
        users = self._volume_users()
        if volume:
            r = run(["docker", "volume", "inspect", name(volume, "volume")], parsed=True)
            if not r.ok:
                return r.text
            d = json.loads(r.text)[0]
            return json.dumps({"Driver": d.get("Driver"), "Mountpoint": d.get("Mountpoint"), "Created": d.get("CreatedAt"),
                               "Labels": d.get("Labels"), "Options": d.get("Options"),
                               "UsedBy": users.get(volume, [])}, indent=1)
        r = run(["docker", "system", "df", "-v", "--format", "{{json .Volumes}}"], timeout=90, parsed=True)
        if not r.ok:
            return r.text
        rows = sorted(json.loads(r.text) or [], key=lambda v: v.get("Name", ""))
        return "\n".join(f"{v['Name'][:40]}: {v.get('Size', '?')}, used by "
                         f"{', '.join(users.get(v['Name'], [])) or 'nothing'}" for v in rows) or "No volumes."

    def container_net_test(self, container, host, port):
        """From a temporary helper sharing the container's network and DNS, so bare images can be tested too."""
        if not 1 <= port <= 65535:
            raise BadInput("port must be 1 to 65535.")
        return run(["docker", "run", "--rm", "--pull", "never", "--network", f"container:{name(container, 'container')}",
                    "--read-only", "--cap-drop", "ALL", "--memory", "64m", "--label", "troubleshooter.helper=true",
                    self.s.helper_image, "python", "-c", NET_TEST, name(host, "host"), str(port)], timeout=30).text

    # ---------- Swarm ----------
    def swarm_status(self):
        nodes = run(["docker", "node", "ls"])
        return f"{nodes.text}\n\n{run(['docker', 'service', 'ls']).text}" if nodes.ok else nodes.text

    def swarm_tasks(self, service):
        return run(["docker", "service", "ps", "--no-trunc", name(service, "service")]).text

    # ---------- Kubernetes ----------
    def k8s_kinds(self, cluster):
        return run(self._kubectl(cluster, "api-resources", "--verbs=get", "-o", "name")).text

    def k8s_get(self, cluster, kind, namespace, resource):
        k8s_kind(kind)
        if resource:
            r = run(self._kubectl(cluster, "get", kind, name(resource, "resource"), "-o", "json", *self._named_ns(cluster, kind, namespace)), parsed=True)
            if not r.ok:
                return r.text
            obj = json.loads(r.text)
            meta = obj.get("metadata", {})
            meta.pop("managedFields", None)
            (meta.get("annotations") or {}).pop("kubectl.kubernetes.io/last-applied-configuration", None)
            return json.dumps(obj, indent=1)
        return run(self._kubectl(cluster, "get", kind, "-o", "wide", *(self._ns(namespace) if kind not in NO_NAMESPACE else []))).text

    def _named_ns(self, cluster, kind, namespace):
        if kind in NO_NAMESPACE:
            return []
        return ["-n", self._ns1(cluster, namespace)]

    def k8s_describe(self, cluster, kind, resource, namespace):
        k8s_kind(kind)
        return run(self._kubectl(cluster, "describe", kind, name(resource, "resource"), *self._named_ns(cluster, kind, namespace))).text

    def k8s_logs(self, cluster, pod, namespace, container, previous, tail, since_, match):
        args = ["logs", name(pod, "pod"), "-n", self._ns1(cluster, namespace),
                "--timestamps", f"--tail={lines(tail)}"]
        if container:
            args += ["-c", name(container, "container")]
        else:
            args.append("--all-containers=true")
        if previous:
            args.append("--previous")
        if since_:
            args.append(f"--since={since(since_)}")
        return grep(run(self._kubectl(cluster, *args)).text, match) or "The log is empty for that period."

    def k8s_events(self, cluster, namespace, warnings_only):
        args = ["get", "events", "--sort-by=.lastTimestamp", *self._ns(namespace)]
        if warnings_only:
            args.append("--field-selector=type=Warning")
        r = run(self._kubectl(cluster, *args))
        return "\n".join(r.text.splitlines()[-150:])

    def k8s_top(self, cluster, what, namespace):
        args = ["top", what] + (self._ns(namespace) if what == "pods" else [])
        r = run(self._kubectl(cluster, *args))
        return r.text if r.ok else f"No usage figures (metrics-server may be missing): {r.text[:200]}"

    def pod_check(self, cluster, pod, namespace, check, container):
        args = ["exec", name(pod, "pod"), "-n", self._ns1(cluster, namespace)]
        if container:
            args += ["-c", name(container, "container")]
        return run(self._kubectl(cluster, *args, "--", "sh", "-c", CHECKS[check])).text

    # ---------- Host ----------
    def host_status(self):
        return self._host_summary()

    def host_processes(self, sort):
        key = {"cpu": "-pcpu", "memory": "-rss"}[sort or "cpu"]
        r = run(["ps", "-eo", "pid,ppid,user,pcpu,pmem,etime,stat,comm", f"--sort={key}"])
        return "\n".join(r.text.splitlines()[:30])

    def host_ports(self):
        return run(["ss", "-ltnu"]).text

    def host_network(self):
        return "\n".join(run(a).text for a in (["ip", "-br", "addr"], ["ip", "route"]))

    def host_logs(self, source, since_, match, errors_only, tail):
        root = self.s.host_root
        journals = [d for d in ("/var/log/journal", "/run/log/journal") if os.path.isdir(root + d) and os.listdir(root + d)]
        n = lines(tail, 200, 1000)
        if journals and os.path.exists(f"{root}/usr/bin/journalctl"):
            # The host's own journalctl: always matches its journal format, and keeps systemd out of the image.
            argv = ["chroot", root, "/usr/bin/journalctl", "-D", journals[0], "--no-pager", "-n", str(n), "-o", "short-iso"]
            if source == "kernel":
                argv.append("-k")
            argv += [f"_SYSTEMD_UNIT={u}" for u in UNITS.get(source, ())]  # exact: wildcards scan the whole journal
            if errors_only:
                argv += ["-p", "err"]
            if since_:
                argv += ["--since", f"-{since(since_)}"]
            r = run(argv, timeout=60)
            return grep(r.text, match) or "No matching log lines."
        for f in {"kernel": ("kern.log", "messages")}.get(source, ("syslog", "messages")):
            path = f"{root}/var/log/{f}"
            if os.path.isfile(path):
                return grep(run(["tail", "-n", str(n), path]).text, match)
        return "No host log could be found."

    # ---------- Network, from this host ----------
    def net_check(self, host, port):
        if not 1 <= port <= 65535:
            raise BadInput("port must be 1 to 65535.")
        started = time.monotonic()
        try:
            with socket.create_connection((name(host, "host"), port), timeout=5):
                return f"Connected to {host}:{port} in {(time.monotonic() - started) * 1000:.0f} ms."
        except OSError as e:
            return f"Could not connect to {host}:{port}: {e}"

    def dns_lookup(self, host):
        try:
            found = sorted({a[4][0] for a in socket.getaddrinfo(name(host, "host"), None)})
            return f"{host} resolves to: {', '.join(found)}"
        except OSError as e:
            return f"{host} does not resolve: {e}"

    def http_check(self, url):
        if not re.match(r"^https?://[^\s]+$", url):
            raise BadInput("url must start with http:// or https://")
        started = time.monotonic()
        ctx = ssl._create_unverified_context() if url.startswith("https") else None
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "troubleshooter"})
            with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
                body = r.read(800).decode("utf-8", "replace")
                code = r.status
        except urllib.error.HTTPError as e:
            code, body = e.code, e.read(800).decode("utf-8", "replace")
        except (urllib.error.URLError, OSError) as e:
            return f"GET {url} failed: {getattr(e, 'reason', e)}"
        return f"GET {url}: HTTP {code} in {(time.monotonic() - started) * 1000:.0f} ms{' (certificate not checked)' if ctx else ''}\n{body}"

    # ---------- App files ----------
    def _path(self, path):
        """Only inside FILE_ROOTS and never a secret file, after following links."""
        root = self.s.host_root.rstrip("/")
        real = os.path.realpath(root + "/" + path.lstrip("/"))
        inside = any(real == root + r.rstrip("/") or real.startswith(root + r.rstrip("/") + "/") for r in self.s.file_roots)
        if not inside:
            raise BadInput(f"{path} is outside the allowed folders: {', '.join(self.s.file_roots)}.")
        if SECRET_FILE.search(real):
            raise BadInput(f"{path} is a secret file and is never read.")
        return real

    def list_files(self, path):
        real = self._path(path)
        rows = []
        for e in sorted(os.scandir(real), key=lambda e: e.name)[:300]:
            size = "" if e.is_dir() else f" {e.stat(follow_symlinks=False).st_size} bytes"
            rows.append(e.name + ("/" if e.is_dir() else "") + size)
        return "\n".join(rows) or "Empty folder."

    def read_file(self, path, tail, match, start_line=None, end_line=None):
        real = self._path(path)
        if not os.path.isfile(real):
            raise BadInput(f"{path} is not a file.")
        size = os.path.getsize(real)
        with open(real, "rb") as f:
            f.seek(max(0, size - MAX_FILE))
            text = f.read().decode("utf-8", "replace")
        if "\x00" in text[:4000]:
            return "That is a binary file."
        if self._is_kubeconfig(text):
            raise BadInput(f"{path} is a kubeconfig and is never read.")
        rows = text.splitlines()
        if match:
            hits = [f"{i}: {ln}" for i, ln in enumerate(rows, 1) if grep(ln, match) == ln]
            return "\n".join(hits[-READ_LINES:]) or f"No lines contain: {match}"
        if tail:
            first = max(0, len(rows) - min(tail, READ_LINES))
            return "\n".join(f"{i}: {ln}" for i, ln in enumerate(rows[first:], first + 1))
        start = max(1, start_line or 1)
        stop = min(end_line or len(rows), start + READ_LINES - 1, len(rows))
        body = "\n".join(f"{i}: {ln}" for i, ln in enumerate(rows[start - 1:stop], start))
        if stop < len(rows):
            body += f"\n[Lines {start}-{stop} of {len(rows)}. Use start_line={stop + 1} for more.]"
        return body or f"The file has {len(rows)} lines."

    @staticmethod
    def _is_kubeconfig(text):
        return bool(re.search(r"^kind:\s*Config\s*$", text, re.M))

    def search_code(self, path, words, file_ending):
        """Words in text files under a folder. Never follows links or opens secret files."""
        real = self._path(path)
        root = self.s.host_root.rstrip("/")
        terms = [w.lower() for w in words.split("|") if w]
        if not terms:
            raise BadInput("words is empty.")
        hits, files, deadline = [], 0, time.monotonic() + 20
        for folder, dirs, names in os.walk(real):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for fname in sorted(names):
                full = os.path.join(folder, fname)
                if file_ending and not fname.endswith(file_ending):
                    continue
                if os.path.islink(full) or not os.path.isfile(full) or SECRET_FILE.search(full) \
                        or os.path.getsize(full) > 2_000_000:
                    continue
                if len(hits) >= 200 or files >= 20_000 or time.monotonic() > deadline:
                    return "\n".join(hits) + "\n[Stopped early. Narrow the folder or the words.]"
                files += 1
                try:
                    with open(full, "rb") as f:
                        text = f.read().decode("utf-8", "replace")
                except OSError:
                    continue
                if "\x00" in text[:4000] or self._is_kubeconfig(text):
                    continue
                for i, ln in enumerate(text.splitlines(), 1):
                    if any(t in ln.lower() for t in terms):
                        hits.append(f"{full[len(root):]}:{i}: {ln.strip()[:200]}")
        return "\n".join(hits[:200]) or f"Nothing found in {files} files."

    # ---------- Changes: each returns a Change; the engine asks before running it ----------
    def docker_restart(self, container):
        c = self._guard(name(container, "container"))
        return Change(f"Restart container {c}", ["docker", "restart", c])

    def docker_start(self, container):
        c = self._guard(name(container, "container"))
        return Change(f"Start container {c}", ["docker", "start", c])

    def docker_stop(self, container):
        c = self._guard(name(container, "container"))
        return Change(f"Stop container {c}", ["docker", "stop", c])

    def swarm_restart(self, service):
        v = self._guard(name(service, "service"))
        return Change(f"Restart all tasks of Swarm service {v}", ["docker", "service", "update", "--force", "--detach", v])

    def swarm_scale(self, service, replicas):
        v = self._guard(name(service, "service"))
        return Change(f"Scale Swarm service {v} to {replicas}", ["docker", "service", "scale", "--detach", f"{v}={replicas}"])

    def k8s_delete_pod(self, cluster, pod, namespace):
        p, n, c = self._guard(name(pod, "pod")), self._guard(self._ns1(cluster, namespace)), self._cluster(cluster)
        return Change(f"On cluster {c.id}: delete pod {n}/{p} (its controller makes a new one)",
                      self._kubectl(c.id, "delete", "pod", p, "-n", n, "--wait=false"))

    def k8s_restart(self, cluster, kind, resource, namespace):
        r, n, c = self._guard(name(resource, "resource")), self._guard(self._ns1(cluster, namespace)), self._cluster(cluster)
        return Change(f"On cluster {c.id}: rolling restart of {kind} {n}/{r}",
                      self._kubectl(c.id, "rollout", "restart", f"{kind}/{r}", "-n", n))

    def k8s_scale(self, cluster, kind, resource, namespace, replicas):
        r, n, c = self._guard(name(resource, "resource")), self._guard(self._ns1(cluster, namespace)), self._cluster(cluster)
        return Change(f"On cluster {c.id}: scale {kind} {n}/{r} to {replicas}",
                      self._kubectl(c.id, "scale", f"{kind}/{r}", f"--replicas={replicas}", "-n", n))

    # ---------- Catalogue ----------
    def _define(self):
        cont = P("string", "Container name", True)
        tail = P("integer", "Newest lines to return (default 200)")
        when = P("string", "Only this recent, e.g. 30m, 2h, 1d")
        match = P("string", "Keep only lines containing these words, separated by |")
        ns = P("string", "Namespace (default: all when listing, else 'default')")
        ns1 = P("string", "Namespace (default: the context's own, else 'default')")
        kind = P("string", "Resource kind, e.g. pods, deployments, or a custom one from k8s_kinds", True)
        res = P("string", "Resource name", True)
        pod = P("string", "Pod name", True)
        svc = P("string", "Swarm service name", True)
        chk = P("string", "What to look at", True, tuple(CHECKS))
        reason = P("string", "One sentence: why this fixes the problem, with the evidence", True)
        reps = P("integer", "Number of replicas", True)

        def t(n, desc, say, fn, params=None, **kw):
            return Tool(n, desc, say, fn, params or {}, **kw)

        def reasoned(fn):
            return lambda reason=None, **a: fn(**a)

        return [
            t("overview", "Health of the host, Docker, Swarm and the active Kubernetes context in one call. Start here.",
              "Checked overall health", self.overview,
              {"all_clusters": P("boolean", "Check every context, not just the active one")}),
            t("k8s_contexts", "List Kubernetes contexts, which is active (*), and whether each login works.",
              "Listed Kubernetes contexts", self.k8s_contexts, needs_kube=True, per_cluster=False),
            t("k8s_use_context", "Switch the active context for this chat. The kubeconfig is not changed.",
              "Switched to context {context}", self.k8s_use_context,
              {"context": P("string", "Context name", True, tuple(self.clusters))}, needs_kube=True, per_cluster=False),
            t("docker_list", "List containers with image and status.", "Listed containers",
              lambda include_stopped: self.docker_list(include_stopped),
              {"include_stopped": P("boolean", "Also show stopped containers")}),
            t("docker_inspect", "A container's config and state: exit code, OOM, health log, env, mounts, ports, restart policy.",
              "Inspected container {container}", self.docker_inspect, {"container": cont}),
            t("docker_logs", "A container's logs.", "Read logs of container {container}",
              lambda container, tail, since, match: self.docker_logs(container, tail, since, match),
              {"container": cont, "tail": tail, "since": when, "match": match}),
            t("docker_stats", "CPU, memory and process counts, now.", "Read container resource use {container}",
              self.docker_stats, {"container": P("string", "Container name (default: all)")}),
            t("docker_events", "Recent starts, stops, deaths, OOM kills and health changes.", "Read Docker events",
              lambda since: self.docker_events(since), {"since": when}),
            t("docker_network", "List networks, or show one network's containers and addresses.", "Looked at Docker network {network}",
              self.docker_network, {"network": P("string", "Network name (default: list all)")}),
            t("docker_disk", "Disk used by images, containers and volumes.", "Read Docker disk use", self.docker_disk),
            t("container_check", "Look inside a running container with a fixed read-only check.",
              "Checked {check} inside container {container}", self.container_check, {"container": cont, "check": chk}),
            t("docker_info", "Engine settings and warnings: version, storage and logging drivers, cgroups, root folder, proxies, daemon.json.",
              "Read Docker engine settings", self.docker_info),
            t("docker_images", "List images with age and size, or show one: config, layers and which containers use it.",
              "Looked at Docker image {image}", self.docker_images, {"image": P("string", "Image (default: list all)")}),
            t("docker_volumes", "List volumes with size and which containers use them, or show one.",
              "Looked at Docker volume {volume}", self.docker_volumes, {"volume": P("string", "Volume (default: list all)")}),
            t("container_net_test", "Test DNS and a TCP connection as seen from inside a running container. Works on any image.",
              "Tested {host}:{port} from inside container {container}", self.container_net_test,
              {"container": cont, "host": P("string", "Host name or address", True), "port": P("integer", "Port", True)}),
            t("swarm_status", "Swarm nodes and services.", "Read Swarm status", self.swarm_status),
            t("swarm_tasks", "A service's tasks with their errors and placement.", "Read tasks of Swarm service {service}",
              self.swarm_tasks, {"service": svc}),
            t("k8s_kinds", "All resource kinds on this cluster, including custom ones (e.g. Calico, cert-manager).",
              "Listed Kubernetes resource kinds", self.k8s_kinds, needs_kube=True),
            t("k8s_get", "List a kind of resource, or show one in full. Secrets are not available.",
              "Listed Kubernetes {kind} {name}", lambda cluster, kind, namespace, name: self.k8s_get(cluster, kind, namespace, name),
              {"kind": kind, "namespace": ns, "name": P("string", "One resource (default: list)")}, needs_kube=True),
            t("k8s_describe", "Describe a resource, including conditions and recent events.", "Described {kind} {name}",
              lambda cluster, kind, name, namespace: self.k8s_describe(cluster, kind, name, namespace),
              {"kind": kind, "name": res, "namespace": ns1}, needs_kube=True),
            t("k8s_logs", "A pod's logs. previous=true shows the crashed run before the last restart.", "Read logs of pod {pod}",
              lambda cluster, pod, namespace, container, previous, tail, since, match:
              self.k8s_logs(cluster, pod, namespace, container, previous, tail, since, match),
              {"pod": pod, "namespace": ns1, "container": P("string", "Container (default: all)"),
               "previous": P("boolean", "Logs of the previous run"), "tail": tail, "since": when, "match": match},
              needs_kube=True),
            t("k8s_events", "Cluster events, newest last.", "Read Kubernetes events", self.k8s_events,
              {"namespace": ns, "warnings_only": P("boolean", "Only warnings", default=True)}, needs_kube=True),
            t("k8s_top", "CPU and memory of nodes or pods.", "Read Kubernetes resource use", self.k8s_top,
              {"what": P("string", "nodes or pods", True, ("nodes", "pods")), "namespace": ns}, needs_kube=True),
            t("pod_check", "Look inside a running pod with a fixed read-only check.", "Checked {check} inside pod {pod}",
              self.pod_check, {"pod": pod, "namespace": ns1, "check": chk, "container": P("string", "Container")}, needs_kube=True),
            t("host_status", "Load, memory, swap and disks of the machine running this tool, not of cluster nodes.", "Read host status", self.host_status),
            t("host_processes", "Top host processes.", "Read top host processes", self.host_processes,
              {"sort": P("string", "cpu or memory", enum=("cpu", "memory"))}),
            t("host_ports", "Ports listening on the host.", "Read listening host ports", self.host_ports),
            t("host_network", "Host interfaces and routes, including VPN tunnels.", "Read host interfaces and routes", self.host_network),
            t("host_logs", "Logs of the machine running this tool, not of cluster nodes: OOM kills and disk errors (kernel), Docker or Kubernetes daemons, or all.", "Read {source} host logs",
              lambda source, since, match, errors_only, tail: self.host_logs(source, since, match, errors_only, tail),
              {"source": P("string", "Which log", True, ("system", "kernel", "docker", "kubernetes")),
               "since": when, "match": match, "errors_only": P("boolean", "Only errors"), "tail": tail}),
            t("net_check", "Can this host open a TCP connection?", "Tested TCP to {host}:{port}", self.net_check,
              {"host": P("string", "Host name or address", True), "port": P("integer", "Port", True)}),
            t("dns_lookup", "Resolve a name from this host.", "Looked up {host}", self.dns_lookup,
              {"host": P("string", "Host name", True)}),
            t("http_check", "GET a URL from this host: status, time, start of the body.", "Fetched {url}", self.http_check,
              {"url": P("string", "http:// or https:// URL", True)}),
            t("list_files", "List an app folder.", "Listed folder {path}", self.list_files,
              {"path": P("string", "Host path", True)}),
            t("read_file", "Read source code, config or a log, with line numbers, up to 300 lines at a time. Secret files are refused.",
              "Read file {path} {start_line}", self.read_file,
              {"path": P("string", "Host path", True), "start_line": P("integer", "First line (default 1)"),
               "end_line": P("integer", "Last line"), "tail": P("integer", "Only the last N lines"), "match": match}),
            t("search_code", "Find words in the text files under a folder: gives file:line and the line. Use it to find where a "
              "function, error message or setting is.", "Searched {path} for {words}", self.search_code,
              {"path": P("string", "Host folder", True), "words": P("string", "Words to find, ignoring case, separated by |", True),
               "file_ending": P("string", "Only files ending with this, e.g. .py")}),
            t("docker_restart", "Restart a container. Asks the operator first.", "Restart container {container}",
              reasoned(self.docker_restart), {"container": cont, "reason": reason}, change=True),
            t("docker_start", "Start a stopped container. Asks the operator first.", "Start container {container}",
              reasoned(self.docker_start), {"container": cont, "reason": reason}, change=True),
            t("docker_stop", "Stop a container. Asks the operator first.", "Stop container {container}",
              reasoned(self.docker_stop), {"container": cont, "reason": reason}, change=True),
            t("swarm_restart", "Restart a service's tasks. Asks the operator first.", "Restart Swarm service {service}",
              reasoned(self.swarm_restart), {"service": svc, "reason": reason}, change=True),
            t("swarm_scale", "Change a service's replicas. Asks the operator first.", "Scale Swarm service {service}",
              reasoned(self.swarm_scale), {"service": svc, "replicas": reps, "reason": reason}, change=True),
            t("k8s_delete_pod", "Delete a pod so its controller replaces it. Asks the operator first.", "Delete pod {pod}",
              reasoned(self.k8s_delete_pod), {"pod": pod, "namespace": ns1, "reason": reason}, change=True, needs_kube=True),
            t("k8s_restart", "Rolling restart. Asks the operator first.", "Restart {kind} {name}",
              reasoned(lambda cluster, kind, name, namespace: self.k8s_restart(cluster, kind, name, namespace)),
              {"kind": P("string", "Kind", True, RESTARTABLE), "name": res, "namespace": ns1, "reason": reason},
              change=True, needs_kube=True),
            t("k8s_scale", "Change replicas. Asks the operator first.", "Scale {kind} {name}",
              reasoned(lambda cluster, kind, name, namespace, replicas: self.k8s_scale(cluster, kind, name, namespace, replicas)),
              {"kind": P("string", "Kind", True, SCALABLE), "name": res, "namespace": ns1, "replicas": reps, "reason": reason},
              change=True, needs_kube=True),
        ]
