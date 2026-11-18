# Auto-trace and change → problem links

How it works: when the hub's watch finds a new problem, the hub
writes down the state at once, looks for a change just before the
problem on the same host or cluster, and then traces the problem
itself, look-only. You read the result on the page.

```
watch finds a new problem (hub, every 60 s)
  → hub writes the state down now
     (asks the problem's host to do the same)
  → link: a change in the 10 min before,
     same host or cluster?
       linked  same container or workload
       maybe   same host, other name
       none
  → auto-trace: look-only, at most 10 an hour,
     the learn model when one is set
  → Map, What changed, Console, log
```

Diagrams: `diagrams/01-auto-trace.png` and
`diagrams/02-change-problem-link.png`.

## Example

Container `relay` on host-02 moves from image `relay:4` to `relay:5`,
and the new image exits because a setting is missing. Within about
a minute the Map shows:

```
host-02 docker: relay: Exited (1)
← 1 min after: container relay: image relay:4 -> relay:5
Traced, likely: relay:5 needs DB_URL, which is not set.
  [Open trace]  [Trace this]
```

## Install

### Step 1: copy the files into `src/troubleshooter/`

On every host (agents answer the new "write the state down now"
request):

```
links.py      (new)
config.py
help.txt
history.py
hosts.py
web.py
webpage.py
```

### Step 2: compose.yaml (optional)

The defaults are on, 10 an hour, 10 minutes. Add these only to
change them.

**Place 1:** in the settings block, after the `web-port` line:

```yaml
  # Auto-trace (hub only): a new problem starts its own look-only
  # trace, with the learn model when one is set. "off" turns it off.
  auto-trace: &auto-trace "on"
  # Most auto-traces started in an hour.
  auto-trace-per-hour: &auto-trace-per-hour "10"
  # A change this many minutes before a problem is linked to it.
  link-minutes: &link-minutes "10"
```

**Place 2:** under `environment:`, after `WEB_PORT: *web-port`:

```yaml
      AUTO_TRACE: *auto-trace
      AUTO_TRACE_PER_HOUR: *auto-trace-per-hour
      LINK_MINUTES: *link-minutes
```

### Step 3: restart

On every host:

```
docker compose up -d
```

No new image is needed.

## Using it on the page

- **Map → Open problems:** each new problem shows
  - its link: green for linked, orange for maybe
  - the trace: waiting, running, "Traced, likely: …",
    "Not traced: 10 already this hour", or failed
  - **Watch it** while it runs, **Open trace** when done
- **Trace, for an auto-trace:**
  - a purple note says Tracer started it by itself
  - anyone signed in can read it and ask more
  - **Back to my trace** returns to your own trace
- **What changed:**
  - a green line joins a change to its problem
    (dashed orange for maybe)
  - a purple diamond marks a finished auto-trace;
    select it in the list to open the trace
- **Console:** "▲ problem ← change" and
  "◆ auto-trace: …" lines in the live feed.

## Rules

- Only on the hub, one trace at a time.
- Problems already open when the page starts are not traced.
- A problem that clears before its turn is skipped.
- A restart, a state flip, or fewer ready is never a cause:
  that is what the problem looks like.
- A change is only seen at the next snapshot, so one written
  down just after the problem still counts, as "at about the
  same time".
- "maybe" links are shown, but the model is not told they
  are the cause.

## Log lines (plain English)

```
The page's watch found a new problem: host-02 docker: relay: Exited (1)
host-02 docker: relay: Exited (1) started 1 min after: container relay: image relay:4 -> relay:5
Auto-traced host-02 docker: relay: Exited (1) on host-02: likely cause: …
No change on host-03 in the 10 min before host-03 docker: web: Exited (1) (…)
```

## Turn it off

Set `auto-trace: "off"` in the settings block, then
`docker compose up -d` on the hub. Links still show.
