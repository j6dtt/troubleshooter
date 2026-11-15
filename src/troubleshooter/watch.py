"""./ts watch: checks health on a timer and speaks only when something goes bad or comes good.

No model is used: the same checks as the overview tool, compared with the round before. It also
keeps the snapshots that what_changed compares against, so leaving it open gives the chat a history.
"""
import datetime
import os
import re
import time

from . import history

EVERY = 60       # seconds between rounds; ./ts watch --every N changes it
SHORTEST = 15
STILL = 3600     # say "still watching" this often, so a quiet screen is not mistaken for a dead one
AGE = re.compile(r"\d+|\b(about|less than|an?|seconds?|minutes?|hours?|days?|weeks?|months?|ago|up)\b")


def key(where, line):
    """The same problem keeps one key while its counters and ages move on."""
    return where + "|" + " ".join(AGE.sub(" ", line.lower()).split())


def problems(box):
    """{key: text} of everything that needs a look right now."""
    found = {}
    try:
        for used, text in box._disks():
            if used >= 90:
                where = text.split(":")[0]
                found[f"host|disk {where}"] = f"host: disk {text}"
        m = box._meminfo()
        if m["MemAvailable"] * 20 < m["MemTotal"]:
            found["host|memory"] = f"host: memory low, {m['MemAvailable']} MB free of {m['MemTotal']} MB"
    except (OSError, KeyError, ValueError):
        pass
    for part in box.overview(all_clusters=True).split("\n\n"):
        rows = part.splitlines()
        if not rows:
            continue
        where = rows[0].split(":")[0].split(" (")[0]
        if "not reachable" in rows[0]:
            found[where + "|unreachable"] = f"{where.lower()}: {rows[0].split(':', 1)[1].strip()}"
        for row in rows[1:]:
            if row.startswith("- "):
                found[key(where, row)] = f"{where.lower()}: {row[2:]}"
    for cid in box.clusters:
        if box.roles.get(cid) != "supervisor" and box._role(cid) != "supervisor":
            continue
        try:  # VMware VKS: the clusters this Supervisor manages
            cluster = ""
            for row in box.vks_cluster_health(cid, None, None).splitlines():
                if row.startswith("VKS CLUSTER "):
                    cluster = row.split(":")[0][12:].split(" (")[0]
                elif row.lstrip().startswith("- ") and cluster:
                    found[key(f"vks {cid} {cluster}", row)] = f"VKS cluster {cluster}: {row.strip()[2:]}"
        except Exception:
            pass
    return found


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

    places = "host, Docker" + (f" and {len(box.clusters)} Kubernetes context(s)" if box.clusters else "")
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
