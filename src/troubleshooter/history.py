"""What changed: small snapshots of what is deployed, and the difference between then and now.

Kubernetes keeps little history and Docker almost none, so the state is written down while ./ts runs
(a chat or ./ts watch): your login to a cluster only exists then. A snapshot is saved only when it
differs from the last one, so quiet days cost almost nothing. They are compressed, at most 7 days
old, and the folder never passes 50 MB: the oldest are removed first.

Snapshots live in the log folder, which is local to each host. They hold names, images, counts and
states; never settings, environment values or Secrets.
"""
import calendar
import gzip
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

from .run import run
from .tools import CLUSTER_LABEL, VKS_KINDS

EVERY = 300          # seconds between snapshots while a chat or watch runs
KEEP_DAYS = 7
MOST_BYTES = 50_000_000   # the snapshot folder never grows past this; the oldest go first
MOST = 80            # most lines of differences shown
WORKLOADS = "deployments.apps,statefulsets.apps,daemonsets.apps"
CONFIG_COLUMNS = "custom-columns=NS:.metadata.namespace,NAME:.metadata.name,RV:.metadata.resourceVersion"
STAMP = re.compile(r"^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)")


def stamp(text):
    """Seconds since 1970 for a Kubernetes or Docker time (UTC), or None."""
    m = STAMP.match(text or "")
    if not m or m.group(1) == "0001":
        return None
    return calendar.timegm(tuple(int(x) for x in m.groups()) + (0, 0, 0))


def clock(seconds):
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(seconds))


def folder(settings):
    return os.path.join(settings.log_dir, "snapshots")


# ---------- taking a snapshot ----------
def _docker():
    ids = run(["docker", "ps", "-aq", "--no-trunc"])
    if not ids.ok:
        return None
    if not ids.text.split():
        return {}
    r = run(["docker", "inspect", *ids.text.split()], parsed=True)
    try:
        rows = json.loads(r.text) if r.ok else None
    except ValueError:
        rows = None
    if rows is None:
        return None
    found = {}
    for d in rows:
        cfg, state = d.get("Config") or {}, d.get("State") or {}
        if (cfg.get("Labels") or {}).get("troubleshooter.helper"):
            continue
        found[(d.get("Name") or "?").lstrip("/")] = {
            "image": cfg.get("Image"), "build": (d.get("Image") or "")[7:19], "state": state.get("Status"),
            "restarts": d.get("RestartCount", 0), "started": state.get("StartedAt")}
    return found


def _items(box, cid, kind):
    """Everything of a kind the login can list: everywhere, else its own namespace. None if it can't."""
    r = run(box._kubectl(cid, "get", kind, "--all-namespaces", "-o", "json"), parsed=True, timeout=60)
    if not r.ok and "Forbidden" in r.text and box._cluster(cid).namespace:
        r = run(box._kubectl(cid, "get", kind, "-n", box._ns1(cid, None), "-o", "json"), parsed=True, timeout=60)
    try:
        return json.loads(r.text).get("items") or [] if r.ok else None
    except ValueError:
        return None


def _path(obj):
    meta = obj["metadata"]
    return f"{meta.get('namespace')}/{meta['name']}" if meta.get("namespace") else meta["name"]


def _kube(box, cid):
    if not run(box._kubectl(cid, "get", "--raw", "/version"), timeout=10).ok:
        return None
    snap = {}
    loads = _items(box, cid, WORKLOADS)
    if loads is not None:
        snap["workloads"] = {
            f"{o.get('kind', '?').lower()} {_path(o)}": {
                "images": sorted({c.get("image") for c in (((o.get("spec") or {}).get("template") or {}).get("spec") or {})
                                 .get("containers") or []} - {None}),
                "replicas": (o.get("spec") or {}).get("replicas"),
                "ready": (o.get("status") or {}).get("readyReplicas", (o.get("status") or {}).get("numberReady", 0)),
                "version": o["metadata"].get("generation")}
            for o in loads}
    nodes = _items(box, cid, "nodes")
    if nodes is not None:
        snap["nodes"] = {
            o["metadata"]["name"]: {
                "ready": next((c["status"] for c in (o.get("status") or {}).get("conditions") or [] if c["type"] == "Ready"), "?"),
                "kubelet": ((o.get("status") or {}).get("nodeInfo") or {}).get("kubeletVersion")}
            for o in nodes}
    pods = _items(box, cid, "pods")
    if pods is not None:
        counts = {_path(o): sum(c.get("restartCount", 0) for c in (o.get("status") or {}).get("containerStatuses") or [])
                  for o in pods}
        snap["restarts"] = {k: v for k, v in counts.items() if v}
    r = run(box._kubectl(cid, "get", "configmaps", "--all-namespaces", "--no-headers", "-o", CONFIG_COLUMNS), parsed=True, timeout=60)
    if not r.ok and "Forbidden" in r.text and box._cluster(cid).namespace:
        r = run(box._kubectl(cid, "get", "configmaps", "-n", box._ns1(cid, None), "--no-headers", "-o", CONFIG_COLUMNS),
                parsed=True, timeout=60)
    if r.ok:
        rows = [x.split() for x in r.text.splitlines()]
        snap["configmaps"] = {f"{x[0]}/{x[1]}": x[2] for x in rows if len(x) == 3}
    if box._role(cid) == "supervisor":  # VMware VKS: the clusters this Supervisor manages, and their VMs
        vks = {}
        for word, key, read in (
                ("cluster", "clusters", lambda o: f"{(o.get('status') or {}).get('phase')} "
                                                  f"{((o.get('spec') or {}).get('topology') or {}).get('version') or ''}".strip()),
                ("machine", "machines", lambda o: (o.get("status") or {}).get("phase")),
                ("VM", "vms", lambda o: (o.get("status") or {}).get("powerState"))):
            for o in _items(box, cid, VKS_KINDS[key]) or []:
                owner = (o["metadata"].get("labels") or {}).get(CLUSTER_LABEL)
                vks[f"{word} {_path(o)}" + (f" of cluster {owner}" if owner else "")] = read(o)
        snap["vks"] = vks
    return snap


def take(box):
    """The state now. A part that could not be read is None, and is then not compared."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        docker = pool.submit(_docker)
        kube = dict(zip(box.clusters, pool.map(lambda cid: _kube(box, cid), box.clusters)))
        return {"time": int(time.time()), "docker": docker.result(), "k8s": kube}


# ---------- keeping snapshots ----------
def _files(settings):
    """Snapshot files, oldest first. The name is the time taken, so they sort by it."""
    try:
        return sorted(f for f in os.listdir(folder(settings)) if f.endswith((".json.gz", ".json")))
    except OSError:
        return []


def _taken(name):
    """When a snapshot file was taken, from its name."""
    try:
        return calendar.timegm(time.strptime(name[:15], "%Y%m%d-%H%M%S"))
    except ValueError:
        return 0


def _read(settings, name):
    try:
        with (gzip.open if name.endswith(".gz") else open)(os.path.join(folder(settings), name), "rt", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, EOFError):
        return None


def _tidy(settings):
    """Keeps the folder small: nothing older than KEEP_DAYS, and no more than MOST_BYTES in all.

    The oldest go first. The newest file always stays: it is what the next snapshot is compared with.
    """
    sizes = []
    for name in os.listdir(folder(settings)):
        path = os.path.join(folder(settings), name)
        if name.endswith(".tmp") and os.path.getmtime(path) < time.time() - 3600:
            os.unlink(path)  # left behind by a write that was cut short
    for name in _files(settings):
        path = os.path.join(folder(settings), name)
        sizes.append((path, os.path.getsize(path), os.path.getmtime(path)))
    total = sum(size for _, size, _ in sizes)
    for path, size, seen in sizes[:-1]:
        if seen < time.time() - KEEP_DAYS * 86400 or total > MOST_BYTES:
            os.unlink(path)
            total -= size


def _same(a, b):
    return a is not None and b is not None and {**a, "time": 0} == {**b, "time": 0}


def _merge(last, snap):
    """A part that could not be read this time keeps its last known state, so an expired login is not a change."""
    if not last:
        return snap
    kube = {cid: part if part is not None else (last.get("k8s") or {}).get(cid) for cid, part in snap["k8s"].items()}
    return {"time": snap["time"], "docker": snap["docker"] if snap["docker"] is not None else last.get("docker"), "k8s": kube}


def save(settings, snap):
    """Writes the snapshot if anything differs from the last one. True if written."""
    names = _files(settings)
    last = _read(settings, names[-1]) if names else None
    snap = _merge(last, snap)
    try:
        os.makedirs(folder(settings), exist_ok=True)
        if _same(last, snap):
            os.utime(os.path.join(folder(settings), names[-1]))  # the file's time says: still true at this moment
            return False
        final = os.path.join(folder(settings), time.strftime("%Y%m%d-%H%M%S", time.gmtime(snap["time"])) + ".json.gz")
        with gzip.open(final + ".tmp", "wt", encoding="utf-8") as f:
            json.dump(snap, f, separators=(",", ":"))
        os.replace(final + ".tmp", final)
        _tidy(settings)
        return True
    except OSError:
        return False


def record(box):
    """Take a snapshot and keep it. Never raises: recording must not break a chat."""
    try:
        snap = take(box)
        save(box.s, snap)
        return snap
    except Exception:
        return None


def before(settings, when):
    """(snapshot, until): the newest state recorded at or before `when`, and when it was last seen unchanged."""
    for name in reversed(_files(settings)):
        if _taken(name) > when:
            continue
        snap = _read(settings, name)
        if snap:
            try:
                until = os.path.getmtime(os.path.join(folder(settings), name))
            except OSError:
                until = snap["time"]
            return snap, max(until, snap["time"])
    return None, None


def oldest(settings):
    names = _files(settings)
    return _read(settings, names[0]) if names else None


# ---------- comparing ----------
def _table(word, old, new, describe):
    """Lines for one group of named things: what appeared, went, or differs."""
    rows = []
    if old is None or new is None:
        return rows
    word = f"{word} " if word else ""
    for key in sorted(set(old) | set(new)):
        if key not in old:
            text = describe(None, new[key])
            rows.append(f"- new {word}{key}" + (f": {text}" if text else ""))
        elif key not in new:
            rows.append(f"- {word}{key} is gone")
        elif old[key] != new[key]:
            text = describe(old[key], new[key])
            if text:
                rows.append(f"- {word}{key}: {text}")
    return rows


def _fields(names):
    def describe(old, new):
        if old is None:
            return ", ".join(f"{label} {new.get(k)}" for k, label in names.items() if new.get(k) not in (None, "", [], 0))
        return "; ".join(f"{label} {_show(old.get(k))} -> {_show(new.get(k))}" for k, label in names.items()
                         if old.get(k) != new.get(k))
    return describe


def _show(value):
    return ", ".join(value) if isinstance(value, list) else str(value)


def _plain(old, new):
    return f"{new}" if old is None else f"{old} -> {new}"


def _more(old, new):
    return f"restarted {new} times" if old is None else f"restarted {new - old} more times (now {new})" if new > old else ""


def _container(old, new):
    if old is None:
        return f"image {new.get('image')}, {new.get('state')}"
    notes = [f"{label} {old.get(k)} -> {new.get(k)}" for k, label in (("image", "image"), ("state", "state")) if old.get(k) != new.get(k)]
    if old.get("build") != new.get("build") and old.get("image") == new.get("image"):
        notes.append("same image name but a different build of it")
    if new.get("restarts", 0) > old.get("restarts", 0):
        notes.append(f"restarted by Docker {new['restarts'] - old.get('restarts', 0)} more times")
    elif old.get("started") != new.get("started") and new.get("state") == "running":
        notes.append(f"started again at {(new.get('started') or '')[:16].replace('T', ' ')} UTC")
    return "; ".join(notes)


def differences(old, new):
    """Plain lines saying what differs between two snapshots, grouped by where."""
    text = []
    rows = _table("container", old.get("docker"), new.get("docker"), _container)
    if rows:
        text += ["DOCKER", *rows]
    for cid, now in (new.get("k8s") or {}).items():
        then = (old.get("k8s") or {}).get(cid)
        if now is None or then is None:
            continue
        rows = _table("", then.get("workloads"), now.get("workloads"),
                      _fields({"images": "image", "replicas": "wanted", "ready": "ready", "version": "spec version"}))
        rows += _table("node", then.get("nodes"), now.get("nodes"), _fields({"ready": "Ready", "kubelet": "kubelet"}))
        # a pod that went away took its count with it: only say which pods restarted
        rows += [r for r in _table("pod", then.get("restarts"), now.get("restarts"), _more) if not r.endswith(" is gone")]
        rows += _table("configmap", then.get("configmaps"), now.get("configmaps"),
                       lambda a, b: "" if a is None else "its content changed")
        rows += _table("VKS", then.get("vks"), now.get("vks"), _plain)
        if rows:
            text += [f"KUBERNETES {cid}", *rows]
    return text


# ---------- what the cluster itself remembers ----------
def timeline(box, cid, start):
    """Events since `start` that Kubernetes still records: rollouts, container restarts, node changes."""
    rows = []
    sets = _items(box, cid, "replicasets.apps") or []
    by_owner = {}
    for o in sets:
        owner = next((r["name"] for r in o["metadata"].get("ownerReferences") or [] if r.get("kind") == "Deployment"), None)
        if owner:
            by_owner.setdefault((o["metadata"].get("namespace"), owner), []).append(o)

    def images(o):
        return sorted({c.get("image") for c in (((o.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers") or []} - {None})

    def revision(o):
        try:
            return int((o["metadata"].get("annotations") or {}).get("deployment.kubernetes.io/revision", 0))
        except ValueError:
            return 0

    for (ns, owner), group in by_owner.items():
        group.sort(key=revision)
        for last, o in zip([None] + group, group):
            made = stamp(o["metadata"].get("creationTimestamp"))
            if not made or made < start:
                continue
            if last is None:
                what = f"first rollout, image {', '.join(images(o))}"
            elif images(last) != images(o):
                what = f"image {', '.join(images(last))} -> {', '.join(images(o))}"
            else:
                what = "same image, so its settings or spec changed"
            rows.append((made, f"deployment {ns}/{owner} rolled out revision {revision(o)}: {what}"))
    for o in _items(box, cid, "pods") or []:
        for c in (o.get("status") or {}).get("containerStatuses") or []:
            ended = (c.get("lastState") or {}).get("terminated") or {}
            when = stamp(ended.get("finishedAt"))
            if when and when >= start:
                rows.append((when, f"pod {_path(o)} container {c.get('name')} ended ({ended.get('reason')}, exit code "
                                   f"{ended.get('exitCode')}) and was restarted; {c.get('restartCount')} restarts in all"))
    for o in _items(box, cid, "nodes") or []:
        for c in (o.get("status") or {}).get("conditions") or []:
            when = stamp(c.get("lastTransitionTime"))
            if when and when >= start:
                rows.append((when, f"node {o['metadata']['name']}: {c.get('type')} became {c.get('status')} ({c.get('reason')})"))
    return [f"- {clock(when)[11:]} {text}" for when, text in sorted(rows)]


def what_changed(box, since_seconds):
    """The tool: differences from the recorded state before the period, then the cluster's own history."""
    now = record(box) or take(box)
    start = now["time"] - since_seconds
    old, until = before(box.s, start)
    text = []
    if old is None:
        first = oldest(box.s)
        if first is None or first["time"] >= now["time"] - 60:
            text.append("Nothing was recorded before now, so there is no earlier state to compare with. Recording "
                        "has started: it runs while a chat or ./ts watch is open.")
        else:
            old = first
            text.append(f"Nothing was recorded before {clock(start)}. Compared with the oldest record instead, "
                        f"from {clock(first['time'])}.")
    else:
        gap = start - until
        text.append(f"Compared with the state recorded at {clock(old['time'])}"
                    + (f", last seen unchanged at {clock(until)}. Nothing was recording between then and "
                       f"{clock(start)}, so a change in that gap shows here too." if gap > 2 * EVERY else "."))
    if old is not None:
        rows = differences(old, now)
        text += rows[:MOST] if rows else ["No differences in what is deployed."]
        if len(rows) > MOST:
            text.append(f"({len(rows) - MOST} more differences not shown.)")
    missing = [cid for cid, part in now["k8s"].items() if part is None]
    if missing:
        text.append(f"Could not read: {', '.join(missing)} (login refused or not reachable).")
    if now["docker"] is None:
        text.append("Could not read Docker.")
    if box.active and now["k8s"].get(box.active) is not None:
        rows = timeline(box, box.active, start)
        text += ["", f"What context {box.active} itself recorded since {clock(start)}:"]
        text += rows[-MOST:] if rows else ["- no rollouts, container restarts or node changes"]
    started = [(stamp(c.get("started")), n, c) for n, c in (now["docker"] or {}).items()]
    started = sorted((w, n, c) for w, n, c in started if w and w >= start and c.get("state") == "running")
    if started:
        text += ["", f"Docker containers started since {clock(start)}:"]
        text += [f"- {clock(w)[11:]} {n} ({c.get('image')})" for w, n, c in started[-30:]]
    return "\n".join(text)

