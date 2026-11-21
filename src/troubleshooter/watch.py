"""./ts watch: checks health on a timer and speaks only when something goes bad or comes good.

No model is used: the same checks as the overview tool, compared with the round before. It also
keeps the snapshots that what_changed compares against, so leaving it open gives the chat a history.
"""
import datetime
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

from . import history, hosts
from .tools import EXPIRED, read_namespace_line

EVERY = 60       # seconds between rounds; ./ts watch --every N changes it
SHORTEST = 15
STILL = 3600     # say "still watching" this often, so a quiet screen is not mistaken for a dead one
AGE = re.compile(r"\d+|\b(about|less than|an?|seconds?|minutes?|hours?|days?|weeks?|months?|ago|up)\b")


def key(where, line):
    """The same problem keeps one key while its counters and ages move on. The name it is about stays as it is."""
    name, colon, rest = line.partition(": ")
    if not colon:
        name, rest = "", line
    return where + "|" + name + "|" + " ".join(AGE.sub(" ", rest.lower()).split())


KINDS = {"HOST": "Host", "DOCKER": "Docker", "SWARM": "Swarm", "KUBERNETES": "Kubernetes"}


def _section(found, part, lines=None, where=""):
    """One part of the overview: its problems go into `found`, its summary line into `lines`."""
    rows = part.splitlines()
    if not rows:
        return
    name = rows[0].split(":")[0].split(" (")[0]
    before = len(found)
    if "not reachable" in rows[0]:
        found[name + "|unreachable"] = f"{name.lower()}: {rows[0].split(':', 1)[1].strip()}"
    for row in rows[1:]:
        if row.startswith("- "):
            found[key(name, row)] = f"{name.lower()}: {row[2:]}"
    if lines is not None:
        kind = KINDS.get(name.split()[0], name.title())
        lines.append({"kind": kind, "where": name.split(" ", 1)[1] if kind == "Kubernetes" and " " in name else where,
                      "line": rows[0].split(":", 1)[1].strip() if ":" in rows[0] else "active",
                      "problems": len(found) - before})


def local_survey(box):
    """This machine: {"problems": {key: text}, "lines": [a summary line per thing watched]}."""
    found, lines = {}, []
    try:
        m = box._meminfo()
        with open("/proc/loadavg") as f:
            load = f.read().split()[0]
        for used, text in box._disks():
            if used >= 90:
                found[f"host|disk {text.split(':')[0]}"] = f"host: disk {text}"
        if m["MemAvailable"] * 20 < m["MemTotal"]:
            found["host|memory"] = f"host: memory low, {m['MemAvailable']} MB free of {m['MemTotal']} MB"
        lines.append({"kind": "Host", "where": "", "line": f"load {load}, {m['MemAvailable']} MB memory free of {m['MemTotal']} MB",
                      "problems": len(found)})
    except (OSError, KeyError, ValueError, IndexError):
        pass
    for part in (box._docker_summary(), box._swarm_summary()):
        if part != "SWARM: not active":
            _section(found, part, lines)
    return {"problems": found, "lines": lines}


def local_map(box):
    """This machine for the page's map: each container, and how busy and full the host is."""
    found = {"containers": [], "load": None, "memory": None, "disks": [], "error": ""}
    try:
        rows = box._docker_rows()
    except RuntimeError as e:
        rows, found["error"] = [], f"Docker is not reachable: {e}"
    detail = history._docker() or {}
    for c in rows:
        name, status = c.get("Names", "?"), c.get("Status", "")
        if detail and name not in detail:
            continue  # a short-lived helper of this tool
        d = detail.get(name, {})
        state = ("restarting" if "Restarting" in status or c.get("State") == "restarting"
                 else "unhealthy" if "unhealthy" in status else c.get("State") or "?")
        found["containers"].append({"name": name, "state": state, "status": status, "image": c.get("Image") or d.get("image"),
                                    "restarts": d.get("restarts", 0), "started": d.get("started")})
    found["containers"].sort(key=lambda c: (c["state"] == "running", c["name"]))
    try:
        m = box._meminfo()
        with open("/proc/loadavg") as f:
            found["load"] = float(f.read().split()[0])
        found["cpus"] = os.cpu_count() or 1
        found["memory"] = [m["MemTotal"] - m["MemAvailable"], m["MemTotal"]]
        found["disks"] = [[used, text.split(":")[0]] for used, text in box._disks()]
    except (OSError, KeyError, ValueError, IndexError):
        pass
    return found


def lane(k, here, agents):
    """Which host or cluster a problem's key is about: the page draws one lane for each."""
    first = k.split(" ", 1)[0]
    if first == here or first in agents:
        return first
    if k.startswith("KUBERNETES "):
        return k.split("|")[0].split(" ")[1]
    if k.startswith("vks "):
        return k.split(" ")[1]
    return here


def local_problems(box):
    return local_survey(box)["problems"]


def kube_survey(box):
    """Every context, and the VKS clusters a Supervisor manages. The kubeconfigs are read again first when one
    changed, so a new login is watched from the next round on. Two contexts on the same vSphere namespace are
    looked at once."""
    box.refresh()
    found, lines, ids, spaces = {}, [], [], set()
    for cid, c in list(box.clusters.items()):
        if getattr(c, "namespace", "") and box._role(cid) == "supervisor":
            if (c.api or cid, c.namespace) in spaces:
                continue  # the same vSphere namespace through another context: one card is enough
            spaces.add((c.api or cid, c.namespace))
        ids.append(cid)
    with ThreadPoolExecutor(max_workers=8) as pool:
        parts = list(pool.map(box._k8s_summary, ids))
    kinds = {cid: box.kind_of(cid) for cid in ids}
    for cid, part in zip(ids, parts):
        _section(found, part, lines)
        if lines and lines[-1]["kind"] == "Kubernetes":
            lines[-1]["role"] = kinds[cid]
            lines[-1]["expired"] = part.splitlines()[0].endswith(EXPIRED)
            lines[-1]["expires"] = getattr(box.clusters.get(cid), "expires", 0) or 0   # 0: the token has no end, or does not say
            # a namespace where this login sees nothing: not healthy, just unknown
            rows = part.splitlines()
            spaces = next((r for r in rows[1:] if r.startswith("NAMESPACES ")), "")
            if spaces:
                lines[-1]["spaces"], lines[-1]["more_spaces"] = read_namespace_line(spaces)
            lines[-1]["blind"] = all(w in part.splitlines()[0] for w in ("VKS clusters hidden", "VMs hidden", "claims hidden"))
    seen = set()
    for cid in ids:
        if box.roles.get(cid) != "supervisor":
            continue
        c = box.clusters[cid]
        where = (getattr(c, "api", "") or cid, getattr(c, "namespace", ""))
        if where in seen:
            continue
        seen.add(where)
        try:
            cluster, count = "", {}
            for row in box.vks_cluster_health(cid, None, None).splitlines():
                if row.startswith("VKS CLUSTER "):
                    cluster = row.split(":")[0][12:].split(" (")[0]
                    count[cluster] = [row.split(":", 1)[1].strip(), 0]
                elif row.lstrip().startswith("- ") and cluster:
                    found[key(f"vks {cid} {cluster}", row)] = f"VKS cluster {cluster}: {row.strip()[2:]}"
                    count[cluster][1] += 1
            lines += [{"kind": "VKS cluster", "where": name, "line": text, "problems": n, "role": "vks", "context": cid,
                       "paused": text.startswith("paused")}
                      for name, (text, n) in count.items() if not any(x["where"] == name for x in lines)]
        except Exception as e:  # say it: otherwise the VKS cards would just vanish
            if getattr(box, "log", None):
                box.log(f"Could not check the VKS clusters through {cid}: {e}")
    return {"problems": found, "lines": lines}


def survey(box):
    """Everything watched, in one pass: here, the clusters, and on the hub the other hosts."""
    agents = getattr(box, "agents", None) or {}
    here = f"{box.here} " if agents else ""
    local, kube = local_survey(box), kube_survey(box)
    found = {here + k: here + v for k, v in local["problems"].items()}
    found.update(kube["problems"])
    lines = [{**row, "where": row["where"] or box.here} for row in local["lines"]] + kube["lines"]

    def other(host):
        try:
            return host, json.loads(hosts.ask(box.s, host, "_survey", timeout=45))
        except (hosts.Unreachable, ValueError) as e:
            return host, {"problems": {"agent": f"its agent did not answer: {e}"},
                          "lines": [{"kind": "Host", "where": host, "line": f"not reachable: {e}", "problems": 1}]}
    if agents:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for host, got in pool.map(other, agents):
                found.update({f"{host} {k}": f"{host} {v}" for k, v in (got.get("problems") or {}).items()})
                lines += [{**row, "where": row.get("where") or host} for row in got.get("lines") or []]
    return {"problems": found, "lines": lines}


def problems(box):
    """{key: text} of everything that needs a look right now."""
    return survey(box)["problems"]


def main(settings, box, argv, log, sleep=time.sleep, rounds=None):
    """./ts leaves a note of this process, so it can stop the watch if its terminal goes away."""
    note = f"/tmp/watch.{os.environ['WATCH_RUN']}" if os.environ.get("WATCH_RUN", "").isdigit() else None
    if note:
        with open(note, "w") as f:
            f.write(str(os.getpid()))
    try:
        return _watch(settings, box, argv, log, sleep, rounds)
    finally:
        if note:
            os.unlink(note)


def _watch(settings, box, argv, log, sleep, rounds):
    every = EVERY
    if argv[:1] == ["--every"] and len(argv) > 1 and argv[1].isdigit():
        every = max(int(argv[1]), SHORTEST)
    elif argv:
        print("Usage: ./ts watch [--every SECONDS]")
        return 2

    def say(text):
        print(f"{datetime.datetime.now():%H:%M:%S} {text}", flush=True)

    places = "host, Docker" + (f", {len(box.clusters)} Kubernetes context(s)" if box.clusters else "") \
        + (f" and {len(box.agents)} other host(s)" if getattr(box, "agents", None) else "")
    print(f"Watching {places} every {every} s. Only changes are shown. Ctrl-C stops.", flush=True)
    log(f"Watch started, every {every} seconds")
    known, last_record, last_word, done = None, 0.0, time.monotonic(), 0
    try:
        while rounds is None or done < rounds:
            try:
                now = problems(box)
            except Exception as e:
                say(f"This round of checks failed ({e}). Trying again.")
                now = known
            if known is None and now is not None:
                for text in now.values():
                    say(f"PROBLEM   {text}")
                    log(f"Watch found: {text}")
                if not now:
                    say("All quiet: nothing needs a look.")
                last_word = time.monotonic()
            elif now is not None:
                for k in now.keys() - known.keys():
                    say(f"NEW       {now[k]}")
                    log(f"Watch found a new problem: {now[k]}")
                for k in known.keys() - now.keys():
                    say(f"OK AGAIN  {known[k]}")
                    log(f"Watch saw this clear: {known[k]}")
                if now.keys() != known.keys():
                    last_word = time.monotonic()
                elif time.monotonic() - last_word >= STILL:
                    say(f"Still watching: {len(now)} open problem(s)." if now else "Still watching: all quiet.")
                    last_word = time.monotonic()
            known = now if now is not None else known
            if time.monotonic() - last_record >= history.EVERY:
                history.record(box)
                last_record = time.monotonic()
            done += 1
            if rounds is None or done < rounds:
                sleep(every)
    except KeyboardInterrupt:
        print()
    log("Watch ended")
    return 0
