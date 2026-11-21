"""Change -> problem links: did a problem start right after a change on the same host or cluster?

No model is used. A change counts for a problem when it was in the same lane (host or cluster), before
the problem, and at most link-minutes before it:

  linked   the change names the same container, workload or machine as the problem
  maybe    same host or cluster, another name: worth a look, not a cause

A change is only seen at the next snapshot, so it happened somewhere between "after" (the last time
the old state was seen) and "time" (when the new state was written down).
"""
import re

KINDS = ("new container ", "container ", "new deployment ", "deployment ", "new statefulset ", "statefulset ",
         "new daemonset ", "daemonset ", "new pod ", "pod ", "new node ", "node ", "new configmap ", "configmap ",
         "new vks ", "vks ")
VKS = re.compile(r"^(cluster|machine|vm) ")
# What a problem looks like, not what caused it: a state flip, restarts, fewer ready.
SYMPTOM = re.compile(r"^(state |started again at |restarted |ready |Ready )")


def subject(text):
    """What a change is about: "container relay: image ..." -> "relay"."""
    head = text.split(":", 1)[0].strip()
    low = head.lower()
    for kind in KINDS:
        if low.startswith(kind):
            head = head[len(kind):]
            break
    head = VKS.sub("", head, count=1) if low.startswith(("vks ", "new vks ")) else head
    return head.split(" of cluster ")[0].strip()


def names(text, name=None):
    """The names a change is about. `name` is the row's own, when it has one; else it is read from the text."""
    found = name or subject(text)
    return {n.lower() for n in (found, found.split("/")[-1]) if len(n) >= 3}


def is_symptom(text):
    """True when every part of a change only shows a problem (a new image or a gone container is a real change)."""
    if ":" not in text:
        return False  # "container x is gone"
    parts = [p.strip() for p in text.split(":", 1)[1].split(";") if p.strip()]
    if text.lower().startswith("new ") and not (parts and parts[0].startswith("restarted")):
        return False
    return all(SYMPTOM.match(p) for p in parts)


def find(problem, changes, minutes):
    """The change that best explains a problem, or None.

    problem: {"time", "lane", "text"}; changes: [{"time", "after"?, "lane", "text"}].
    Returns {"strength": "linked" or "maybe", "text", "time", "gap"} (gap in seconds, 0 = at the same time).
    """
    window, best = minutes * 60, None
    said = problem["text"].lower()
    for c in changes:
        if c.get("lane") != problem.get("lane") or c.get("repeats"):
            continue  # another lane, or something that keeps changing by itself: not this problem's cause
        if c["symptom"] if "symptom" in c else is_symptom(c["text"]):
            continue
        after = min(c.get("after", c["time"]), c["time"])
        if after > problem["time"] or c["time"] < problem["time"] - window:
            continue
        strong = any(n in said for n in names(c["text"], c.get("name")))
        gap = max(0, problem["time"] - c["time"])
        rank = (strong, -gap)
        if best is None or rank > best[0]:
            best = (rank, c, strong, gap)
    if best is None:
        return None
    _, c, strong, gap = best
    return {"strength": "linked" if strong else "maybe", "text": c["text"], "time": c["time"], "gap": gap}


def say(link):
    """One line for people: "1 min after: image relay:4 -> relay:5"."""
    when = "at about the same time as" if link["gap"] < 60 else f"{round(link['gap'] / 60)} min after"
    return f"{'maybe: ' if link['strength'] == 'maybe' else ''}{when}: {link['text']}"
