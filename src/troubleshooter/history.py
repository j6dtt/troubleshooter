"""What changed: small snapshots of what is deployed, and the difference between then and now.

Kubernetes keeps little history and Docker almost none, so the state is written down while ./ts runs
(a chat or ./ts watch): your login to a cluster only exists then. A snapshot is saved only when it
differs from the last one, so quiet days cost almost nothing. They are compressed, at most 7 days
old, and the folder never passes 50 MB: the oldest are removed first.

Snapshots live in logs/snapshots/<host>: the log folder is shared by every host, so each keeps its own. They hold names, images, counts and
states; never settings, environment values or Secrets.
"""
import calendar
import gzip
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

from . import config, links
from .run import run
from .tools import CLUSTER_LABEL, VKS_KINDS

EVERY = 300          # seconds between snapshots while a chat or watch runs
KEEP_DAYS = 7
MOST_BYTES = 50_000_000   # the snapshot folder never grows past this; the oldest go first
MOST = 80            # most lines of differences shown
WORKLOADS = "deployments.apps,statefulsets.apps,daemonsets.apps"
CONFIG_COLUMNS = ("custom-columns=NS:.metadata.namespace,NAME:.metadata.name,RV:.metadata.resourceVersion,"
                  "MADE:.metadata.creationTimestamp")
# Namespaces run by Kubernetes and VMware, not by your apps: their settings churn all day
SYSTEM = re.compile(r"^(kube-|vmware-system-|tkg-|tanzu-|svc-|secretgen-|kapp-|cert-manager$|ingress-nginx$|gpu-operator$)")
CHURN = 4            # the same thing changing this often in the period shown is one line with a count
SLACK = 120          # seconds two clocks may differ: the source's and this host's
BOOKKEEPING = re.compile(r"\.app(-change-[a-z0-9]{4,6})?$")
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
    """This host's own snapshots. The log folder is shared, so one host must never read another's as its own."""
    return os.path.join(settings.log_dir, "snapshots", config.here())


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
        cfg, state, labels = d.get("Config") or {}, d.get("State") or {}, (d.get("Config") or {}).get("Labels") or {}
        if labels.get("troubleshooter.helper"):
            continue
        found[(d.get("Name") or "?").lstrip("/")] = {
            "image": cfg.get("Image"), "build": (d.get("Image") or "")[7:19], "state": state.get("Status"),
            "restarts": d.get("RestartCount", 0), "started": state.get("StartedAt"),
            # Docker's own times and exit code: when and how, not only what
            "created": d.get("Created"), "finished": state.get("FinishedAt"), "exit": state.get("ExitCode"),
            # a one-off run (docker run --rm, docker compose run): it comes and goes by design
            "oneoff": bool((d.get("HostConfig") or {}).get("AutoRemove")) or str(labels.get("com.docker.compose.oneoff")) == "True"}
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


def _born(obj):
    """When Kubernetes says the object was made (seconds), or None."""
    return stamp((obj.get("metadata") or {}).get("creationTimestamp"))


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
                "version": o["metadata"].get("generation"), "born": _born(o)}
            for o in loads}
    nodes = _items(box, cid, "nodes")
    if nodes is not None:
        snap["nodes"] = {
            o["metadata"]["name"]: {
                "ready": next((c["status"] for c in (o.get("status") or {}).get("conditions") or [] if c["type"] == "Ready"), "?"),
                "kubelet": ((o.get("status") or {}).get("nodeInfo") or {}).get("kubeletVersion"), "born": _born(o),
                "ready_at": next((stamp(c.get("lastTransitionTime")) for c in (o.get("status") or {}).get("conditions") or []
                                  if c["type"] == "Ready"), None)}
            for o in nodes}
    pods = _items(box, cid, "pods")
    if pods is not None:
        counts, last = {}, {}
        for o in pods:
            boxes = (o.get("status") or {}).get("containerStatuses") or []
            count = sum(c.get("restartCount", 0) for c in boxes)
            if not count:
                continue
            counts[_path(o)] = count
            ends = [e for e in (((c.get("lastState") or {}).get("terminated") or {}) for c in boxes) if e]
            if ends:  # why and when its last run ended, as the pod itself says: gone once the pod is deleted
                end = max(ends, key=lambda e: stamp(e.get("finishedAt")) or 0)
                last[_path(o)] = {"at": stamp(end.get("finishedAt")), "why": end.get("reason"), "code": end.get("exitCode")}
        snap["restarts"], snap["restart_last"] = counts, last
    r = run(box._kubectl(cid, "get", "configmaps", "--all-namespaces", "--no-headers", "-o", CONFIG_COLUMNS), parsed=True, timeout=60)
    if not r.ok and "Forbidden" in r.text and box._cluster(cid).namespace:
        r = run(box._kubectl(cid, "get", "configmaps", "-n", box._ns1(cid, None), "--no-headers", "-o", CONFIG_COLUMNS),
                parsed=True, timeout=60)
    if r.ok:
        rows = [x.split() for x in r.text.splitlines()]
        # kapp's own notes of each reconcile ("x.app", "x.app-change-ab12c") change all day and say nothing
        rows = [x for x in rows if len(x) == 4 and not BOOKKEEPING.search(x[1])]
        snap["configmaps"] = {f"{x[0]}/{x[1]}": x[2] for x in rows}
        snap["configmaps_born"] = {f"{x[0]}/{x[1]}": stamp(x[3]) for x in rows}
    if box._role(cid) == "supervisor":  # VMware VKS: the clusters this Supervisor manages, and their VMs
        vks, born = {}, {}  # None as soon as one kind can't be read: an empty list would read as "all gone", then "all new"
        for word, key, read in (
                ("cluster", "clusters", lambda o: (f"{(o.get('status') or {}).get('phase')} "
                                                   f"{((o.get('spec') or {}).get('topology') or {}).get('version') or ''}".strip()
                                                   + (" paused" if (o.get("spec") or {}).get("paused") else ""))),
                ("machine", "machines", lambda o: (o.get("status") or {}).get("phase")),
                ("VM", "vms", lambda o: (o.get("status") or {}).get("powerState"))):
            found = _items(box, cid, VKS_KINDS[key]) if vks is not None else None
            if found is None:
                vks = None
                continue
            for o in found:
                owner = (o["metadata"].get("labels") or {}).get(CLUSTER_LABEL)
                key = f"{word} {_path(o)}" + (f" of cluster {owner}" if owner else "")
                vks[key], born[key] = read(o), _born(o)
        snap["vks"], snap["vks_born"] = vks, born if vks is not None else None
    return snap


def take(box):
    """The state now. A part that could not be read is None, and is then not compared."""
    box.refresh()
    with ThreadPoolExecutor(max_workers=8) as pool:
        docker = pool.submit(_docker)
        kube = dict(zip(box.clusters, pool.map(lambda cid: _kube(box, cid), box.clusters)))
        return {"time": int(time.time()), "host": config.here(), "docker": docker.result(), "k8s": kube}


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
            snap = json.load(f)
    except (OSError, ValueError, EOFError):
        return None
    if not isinstance(snap, dict) or snap.get("host") not in (None, config.here()):
        return None  # another host's: never compare it with this one
    return snap


def _tidy(settings):
    """Keeps the folder small: nothing older than KEEP_DAYS, and no more than MOST_BYTES in all.

    The oldest go first. The newest file always stays: it is what the next snapshot is compared with.
    """
    shared = os.path.dirname(folder(settings))
    for name in os.listdir(shared):  # from before each host had its own folder: every host's, mixed up
        if name.endswith((".json.gz", ".json", ".tmp")) and os.path.isfile(os.path.join(shared, name)):
            try:
                os.unlink(os.path.join(shared, name))
            except OSError:
                pass
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
    return a is not None and b is not None and {**a, "time": 0, "host": 0} == {**b, "time": 0, "host": 0}


def _keep(known, part):
    """One cluster's part, with what could not be read this time (pods, VKS, ...) kept as last known."""
    if part is None or not known:
        return part if part is not None else known
    return {**{k: v for k, v in known.items() if v is not None}, **{k: v for k, v in part.items() if v is not None}}


def _merge(last, snap):
    """A part that could not be read this time keeps its last known state, so an expired login is not a change."""
    if not last:
        return snap
    known = last.get("k8s") or {}
    # A context this snapshot does not cover (taken with no login open) keeps its last known state too.
    kube = {**known, **{cid: _keep(known.get(cid), part) for cid, part in snap["k8s"].items()}}
    return {"time": snap["time"], "host": snap.get("host", last.get("host")), "docker": snap["docker"] if snap["docker"] is not None else last.get("docker"), "k8s": kube}


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
def _rows(word, old, new, describe, born, since, when=None, aspect="state"):
    """One row for each named thing that appeared, went or differs.

    A row is {"line", "when", "subject", "aspect"}. `when` is the source's own time for the change, or None
    when only the two records say that it changed.
    born = {name: when the source says it was made}. A thing is "new" only if it was made after the older
    record (`since`): one that is older was there all along and only could not be seen, so it is no change and
    gets no row. Without a made-time it is "first seen", never "new". "Gone" is only said when both lists
    were read."""
    rows = []
    if old is None or new is None:
        return rows
    lead = f"{word} " if word else ""
    for key in sorted(set(old) | set(new)):
        subject = lead + key
        if key not in old:
            text = describe(None, new[key])
            made = (born or {}).get(key)
            if made is None:
                rows.append({"line": f"- first seen: {subject}" + (f" ({text})" if text else ""), "when": None,
                             "subject": subject, "aspect": "first seen"})
            elif since is None or made >= since - SLACK:
                rows.append({"line": f"- new {subject}" + (f": {text}" if text else ""), "when": made,
                             "subject": subject, "aspect": "new"})
        elif key not in new:
            rows.append({"line": f"- {subject} is gone", "when": None, "subject": subject, "aspect": "gone"})
        elif old[key] != new[key]:
            text = describe(old[key], new[key])
            if text:
                rows.append({"line": f"- {subject}: {text}", "when": when(old[key], new[key]) if when else None,
                             "subject": subject, "aspect": aspect})
    return rows


def _fields(names):
    def describe(old, new):
        if old is None:
            return ", ".join(f"{label} {_show(new.get(k))}" for k, label in names.items() if new.get(k) not in (None, "", [], 0))
        return "; ".join(f"{label} {_show(old.get(k))} -> {_show(new.get(k))}" for k, label in names.items()
                         if old.get(k) != new.get(k))
    return describe


def _show(value):
    return ", ".join(value) if isinstance(value, list) else str(value)


def _plain(old, new):
    return f"{new}" if old is None else f"{old} -> {new}"


def _cluster_state(text):
    """("Provisioned", "v1.33.6", paused) from a VKS cluster's recorded state "phase version [paused]"."""
    words = str(text).split()
    paused = words[-1:] == ["paused"]
    words = words[:-1] if paused else words
    return (words[0] if words else "?"), (words[1] if len(words) > 1 else ""), paused


def _vks_cluster(old, new):
    """Which part of a VKS cluster's state changed: its phase, its version, or a pause."""
    if old is None:
        return f"{new}"
    (phase, version, paused), (phase2, version2, paused2) = _cluster_state(old), _cluster_state(new)
    notes = ([f"phase {phase} -> {phase2}"] if phase != phase2 else []) \
        + ([f"version {version or 'unknown'} -> {version2 or 'unknown'}"] if version != version2 else []) \
        + ([("paused: VMware won't repair, scale or upgrade it" if paused2 else "unpaused")] if paused != paused2 else [])
    return "; ".join(notes)


def _rollout(old, new):
    """A workload's own change: a new image, or the same image with other settings. Scaling is said apart."""
    if old is None:
        return f"image {_show(new.get('images'))}"
    if old.get("images") != new.get("images"):
        return f"image {_show(old.get('images'))} -> {_show(new.get('images'))}"
    if old.get("version") != new.get("version") and old.get("replicas") == new.get("replicas"):
        return "its settings or spec changed, same image"
    return ""


def _scale(old, new):
    if old is None or old.get("replicas") == new.get("replicas"):
        return ""
    return f"scaled from {old.get('replicas')} to {new.get('replicas')}"


def _container(old, new):
    if old is None:
        return f"image {new.get('image')}, {new.get('state')}"
    notes = [f"{label} {old.get(k)} -> {new.get(k)}" for k, label in (("image", "image"), ("state", "state")) if old.get(k) != new.get(k)]
    if old.get("state") != new.get("state") and new.get("state") in ("exited", "dead") and new.get("exit") is not None:
        notes[-1] += f" (exit code {new['exit']})"
    if old.get("build") != new.get("build") and old.get("image") == new.get("image"):
        notes.append("same image name but a different build of it")
    if new.get("restarts", 0) > old.get("restarts", 0):
        notes.append(f"restarted by Docker {new['restarts'] - old.get('restarts', 0)} more times")
    elif old.get("started") != new.get("started") and new.get("state") == "running":
        notes.append(f"started again at {(new.get('started') or '')[:16].replace('T', ' ')} UTC")
    return "; ".join(notes)


def _container_when(old, new):
    """Docker's own time for what changed on a container, or None."""
    if new.get("state") != old.get("state") and new.get("state") in ("exited", "dead"):
        return stamp(new.get("finished"))
    if old.get("started") != new.get("started"):
        return stamp(new.get("started"))
    return None


def _restarts(then, now, last):
    """A row for each pod that restarted since the older record, with why and when its last run ended.
    A pod that went away took its count with it, so "gone" is never said here."""
    rows = []
    if then is None or now is None:
        return rows
    for pod in sorted(now):
        count, before = now[pod], then.get(pod, 0)
        if count <= before:
            continue
        info = (last or {}).get(pod) or {}
        text = f"restarted {count - before} more times (now {count})" if before else f"restarted {count} times"
        if info.get("why") or info.get("code") is not None:
            text += f"; the last run ended {info.get('why') or 'with no reason given'}, exit code {info.get('code')}"
        rows.append({"line": f"- pod {pod}: {text}", "when": info.get("at"), "subject": f"pod {pod}", "aspect": "restart"})
    return rows


def _system(key):
    """True for "kind ns/name" or "ns/name" in a namespace Kubernetes or VMware runs."""
    return bool(SYSTEM.match(key.split(" ")[-1].split("/")[0])) if "/" in key else False


def _yours(table):
    return table if table is None else {k: v for k, v in table.items() if not _system(k)}


def _both(old, new):
    """The two tables cut down to the names in both: for a pass that must not say "new" or "gone" again."""
    if old is None or new is None:
        return None, None
    return {k: v for k, v in old.items() if k in new}, {k: v for k, v in new.items() if k in old}


OWN = re.compile(r"^(- (?:new |first seen: )?VKS cluster \S+/(\S+)) of cluster \2(?=$|[: ])")   # "... prod1 of cluster prod1"
UPGRADE = re.compile(r"(: |; )(image |kubelet |same image name but a different build)")
SYMPTOMS = ("state", "restart", "ready")   # what a problem looks like, not what caused it


def changes(old, new, quiet=False):
    """[row]: what differs between two records.

    A row is {"where", "line", "when", "subject", "name", "aspect", "symptom", "about", "key"}:
      when     the source's own time (Docker's or Kubernetes'), kept only when it falls between the two
               records; None = sometime between them
      about    ["rollout" | "restart" | "ready", subject] when the cluster keeps its own dated line for it
      key      names "the same thing changing again", for folding repeats; None for a new image or version,
               which is never folded

    quiet=True is the page's log: only what could explain a problem, one row per thing that happened. There,
    a workload's image or settings and its scale are rows of their own; how many pods are ready is left out
    (a symptom: it shows as a problem); things only "first seen" and one-off containers are left out; and in
    the namespaces Kubernetes and VMware run themselves only a new image, a new workload or a gone one is
    kept. Settings (configmaps) of those namespaces are never compared: they rewrite themselves all day."""
    since, until = old.get("time"), new.get("time")
    found = []

    def add(where, rows, about=None):
        for r in rows:
            when = r["when"]
            sure = when is not None and (since is None or when >= since - SLACK) and (until is None or when <= until + SLACK)
            aspect = "upgrade" if r["aspect"] not in ("new", "gone", "first seen") and UPGRADE.search(r["line"]) else r["aspect"]
            found.append({
                "where": where, "line": OWN.sub(r"\1", r["line"]), "when": when if sure else None, "subject": r["subject"],
                "name": links.subject(r["subject"] + ":"), "aspect": aspect, "symptom": aspect in SYMPTOMS,
                "about": [about[aspect], r["subject"]] if about and aspect in about else None,
                "key": None if aspect == "upgrade" else f"{r['subject']} " + ("comes and goes" if aspect in ("new", "gone") else aspect)})

    was, docker = old.get("docker"), new.get("docker")
    if quiet and was is not None and docker is not None:  # one-off runs (docker run --rm, compose run) come and go
        once = {n for table in (was, docker) for n, c in table.items() if c.get("oneoff")}
        was, docker = ({n: c for n, c in table.items() if n not in once} for table in (was, docker))
    add("DOCKER", _rows("container", was, docker, _container,
                        {n: stamp(c.get("created")) for n, c in (docker or {}).items()}, since, when=_container_when))
    for cid, now in (new.get("k8s") or {}).items():
        then = (old.get("k8s") or {}).get(cid)
        if now is None or then is None:
            continue
        where = f"KUBERNETES {cid}"
        loads, nodes = now.get("workloads"), now.get("nodes")
        born = {k: v.get("born") for k, v in (loads or {}).items()}
        if quiet:
            rollout = {"upgrade": "rollout", "spec": "rollout", "new": "rollout"}   # only a Deployment has such a line
            rows = _rows("", then.get("workloads"), loads, _rollout, born, since, aspect="spec")
            rows = [r for r in rows if not _system(r["subject"]) or r["aspect"] in ("new", "gone") or ": image " in r["line"]]
            add(where, [r for r in rows if r["subject"].startswith("deployment ")], about=rollout)
            add(where, [r for r in rows if not r["subject"].startswith("deployment ")])
            add(where, _rows("", *_both(_yours(then.get("workloads")), _yours(loads)), _scale, born, since, aspect="scale"))
        else:
            add(where, _rows("", then.get("workloads"), loads,
                             _fields({"images": "image", "replicas": "wanted", "ready": "ready", "version": "spec version"}),
                             born, since))
        rows = _rows("node", then.get("nodes"), nodes, _fields({"ready": "Ready", "kubelet": "kubelet"}),
                     {k: v.get("born") for k, v in (nodes or {}).items()}, since,
                     when=lambda a, b: b.get("ready_at") if a.get("ready") != b.get("ready") else None, aspect="ready")
        add(where, rows, about={"ready": "ready"})
        add(where, _restarts(then.get("restarts"), now.get("restarts"), now.get("restart_last")), about={"restart": "restart"})
        # older records still hold kapp's reconcile notes: left out of both sides, so they are neither gone nor new
        was, maps = ({k: v for k, v in table.items() if not BOOKKEEPING.search(k)} if table is not None else None
                     for table in (_yours(then.get("configmaps")), _yours(now.get("configmaps"))))
        add(where, _rows("configmap", was, maps,
                         lambda a, b: "" if a is None else "its content changed",
                         now.get("configmaps_born") or {}, since, aspect="content"))
        vks_then, vks_now, vks_born = then.get("vks"), now.get("vks"), now.get("vks_born") or {}
        for mine, describe in ((lambda key: key.startswith("cluster "), _vks_cluster),
                               (lambda key: not key.startswith("cluster "), _plain)):
            a, b = ({key: v for key, v in table.items() if mine(key)} if table is not None else None for table in (vks_then, vks_now))
            add(where, _rows("VKS", a, b, describe, vks_born, since, aspect="vks"))
    return [r for r in found if r["aspect"] != "first seen"] if quiet else found


def differences(old, new):
    """Plain lines saying what differs between two snapshots, grouped by where."""
    text, last = [], None
    for r in changes(old, new):
        if r["where"] != last:
            text.append(r["where"])
            last = r["where"]
        text.append(r["line"] + (f" (made {clock(r['when'])})" if r["aspect"] == "new" and r["when"] else ""))
    return text


# ---------- what the cluster itself remembers ----------
def timeline(box, cid, start):
    """Lines for what Kubernetes itself still records since `start`."""
    return [f"- {clock(when)[11:]} {text}" for when, text, _ in timeline_rows(box, cid, start)]


def timeline_rows(box, cid, start):
    """[(when, text, about)] since `start`: rollouts, container restarts, node changes. `about` names the thing
    as changes() does (["rollout", "deployment ns/name"]), so the same happening is not said twice."""
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
            rows.append((made, f"deployment {ns}/{owner} rolled out revision {revision(o)}: {what}",
                         ["rollout", f"deployment {ns}/{owner}"]))
    for o in _items(box, cid, "pods") or []:
        for c in (o.get("status") or {}).get("containerStatuses") or []:
            ended = (c.get("lastState") or {}).get("terminated") or {}
            when = stamp(ended.get("finishedAt"))
            if when and when >= start:
                rows.append((when, f"pod {_path(o)} container {c.get('name')} ended ({ended.get('reason')}, exit code "
                                   f"{ended.get('exitCode')}) and was restarted; {c.get('restartCount')} restarts in all",
                             ["restart", f"pod {_path(o)}"]))
    for o in _items(box, cid, "nodes") or []:
        for c in (o.get("status") or {}).get("conditions") or []:
            when = stamp(c.get("lastTransitionTime"))
            # Ready either way, or a pressure that began: "MemoryPressure became False" at every boot says nothing
            if when and when >= start and (c.get("type") == "Ready" or c.get("status") == "True"):
                rows.append((when, f"node {o['metadata']['name']}: {c.get('type')} became {c.get('status')} ({c.get('reason')})",
                             ["ready", f"node {o['metadata']['name']}"] if c.get("type") == "Ready" else None))
    return sorted(rows, key=lambda row: row[:2])


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
                        "has started: the container keeps it going every record-minutes.")
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



def record_forever(box, minutes, sleep=time.sleep, rounds=None):
    """The container's own job: snapshots with no chat open. It sees Docker, and clusters that have a
    kubeconfig file in ./kube; your own kubectl login is only there while ./ts runs."""
    if minutes <= 0:
        print("Background recording is off (record-minutes is 0).", flush=True)
        return
    print(f"Recording what is deployed every {minutes} minutes, for what_changed.", flush=True)
    done = 0
    while rounds is None or done < rounds:
        record(box)
        done += 1
        sleep(minutes * 60)


# ---------- for the timeline ----------
_MEMO = {}


def events(settings, since):
    """The page's log since `since`: each difference between one record and the next, oldest first.

    {"time", "exact", "after", "seen", "where", "text", "name", "symptom", "about", "key", "repeats"?}:
      time, exact  the source's own time when it has one (exact); else when the newer record was taken
      after, seen  the older state was last seen at `after`, the newer one at `seen`: it changed in between
    With no source time and nothing recording in between, the text says so: a window, never a guessed moment."""
    names = _files(settings)
    start = next((i for i, n in enumerate(names) if _taken(n) >= since), len(names))
    names = names[max(start - 1, 0):]
    found, last = [], None
    for name in names:
        if last is not None:
            pair = (last, name)
            if pair not in _MEMO:
                old, new = _read(settings, last), _read(settings, name)
                _MEMO[pair] = changes(old, new, quiet=True) if old and new else []
                if len(_MEMO) > 5000:
                    _MEMO.pop(next(iter(_MEMO)))
            seen = _taken(name)
            try:  # the old state was last seen then: the change happened after it
                after = min(int(os.path.getmtime(os.path.join(folder(settings), last))), seen)
            except OSError:
                after = _taken(last)
            for r in _MEMO[pair]:
                when, text = r["when"], r["line"][2:]
                if int(when or seen) < since:
                    continue  # the source dates it before the period asked for
                if when is None and seen - after > 2 * EVERY:
                    text += f" (sometime between {clock(after)[5:16]} and {clock(seen)[11:16]} UTC: nothing was recording then)"
                found.append({"time": int(when or seen), "exact": when is not None, "after": after, "seen": seen,
                              "where": "Docker" if r["where"] == "DOCKER" else r["where"].replace("KUBERNETES ", "", 1),
                              "text": text, "name": r["name"], "symptom": r["symptom"], "about": r["about"], "key": r["key"]})
        last = name
    found = _calm(found)
    found.sort(key=lambda e: e["time"])
    return found[-500:]


def _calm(found):
    """The same thing changing over and over is one line with a count, not many: a workload scaled up and down
    all day, a pod in a crash loop, a setting that something keeps rewriting. A new image is never folded."""
    groups = {}
    for e in found:
        if e.get("key"):
            groups.setdefault((e["where"], e["key"]), []).append(e)
    folded = set()
    for rows in groups.values():
        if len(rows) < CHURN:
            continue
        rows.sort(key=lambda e: e["time"])
        first, newest = rows[0], rows[-1]
        folded.update(id(e) for e in rows[:-1])
        same_day = clock(first["time"])[:10] == clock(newest["time"])[:10]
        newest["repeats"] = len(rows)
        newest["text"] += (f" ({len(rows)} such changes since {clock(first['time'])[11 if same_day else 5:16]} UTC; "
                           "only the newest is shown)")
    return [e for e in found if id(e) not in folded]


def fold(found, cluster):
    """One line per thing that happened. A change the cluster also records itself (a rollout, a container
    restart, a node's Ready) is left to the cluster's own line, which has the reason and the cluster's time.
    The change stays when the cluster no longer remembers it, e.g. the pod is gone."""
    marks = {}
    for e in cluster:
        if e.get("about"):
            marks.setdefault((e.get("lane"), *e["about"]), []).append(e["time"])
    kept = []
    for e in found:
        times = marks.get((e.get("lane"), *e["about"])) if e.get("about") else None
        if times and any(e.get("after", e["time"]) - SLACK <= t <= e.get("seen", e["time"]) + SLACK for t in times):
            continue
        kept.append(e)
    return kept


def coverage(settings, since):
    """[[from, to]]: the stretches of time when the state was being written down."""
    spans = []
    for name in _files(settings):
        try:
            seen = os.path.getmtime(os.path.join(folder(settings), name))
        except OSError:
            continue
        taken = _taken(name)
        if seen < since:
            continue
        if spans and taken - spans[-1][1] <= 2 * EVERY:
            spans[-1][1] = max(spans[-1][1], seen)
        else:
            spans.append([max(taken, since), max(seen, taken)])
    return [[int(a), int(b)] for a, b in spans]
