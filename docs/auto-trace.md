# Auto-trace, change → problem links, and anonymize

How it works: the container watches by itself, every 60 seconds.
When it finds a new problem, it writes down the state at once,
looks for a change just before the problem on the same host or
cluster, and then traces the problem itself, look-only. Each trace
is saved as a file, which you read with `./ts` → `/traces` or on
the page.

```
container (ts record, always on)
  ├─ snapshots every 5 min
  └─ watch every 60 s
       → new problem
       → snapshot now (and on the problem's host)
       → link: a change in the 10 min before,
          same host or cluster?
            linked  same container or workload
            maybe   same host, other name
       → auto-trace: look-only, at most 10 an hour,
          the learn model when one is set
       → saved: logs/traces/<host>/<when>-<id>.json

read it:
  ./ts → /traces      list
  ./ts → /traces 3    open as this chat: ask more, fix
  page (hub only)     Map, What changed, Wall
```

Who does it:

- **Alone** (`hub-host` empty): traces itself.
- **Hub:** traces itself, every agent and every cluster.
- **Agent:** traces nothing itself, so nothing is traced twice.
  It answers the hub.

No `./ts` or ssh session has to stay open. Saved traces survive a
container restart.

Diagrams: `diagrams/01-auto-trace.png` and
`diagrams/02-change-problem-link.png`.

## Example

Container `relay` on host-02 moves from image `relay:4` to
`relay:5`, and the new image exits because a setting is missing.
Within about a minute:

```
you> /traces
 1  11-18 05:42  host-02
    docker: relay: Exited (1) 40 seconds ago
    <- 1 min after: container relay: image relay:4 -> relay:5
    likely: relay:5 needs DB_URL, which is not set.
Open one with /traces N.

you> /traces 1
docker: relay: Exited (1) 40 seconds ago
<- 1 min after: container relay: image relay:4 -> relay:5
  1. Looked at what changed in the last 2h
  2. Read logs of container relay
  3. Inspected container relay
Cause: relay:5 needs DB_URL, which is not set.
Fix: set DB_URL for relay, or go back to relay:4.
This trace is now this chat: ask more, or ask to fix it
(fixes ask you first). /new to leave it.
```

## Anonymize

With `anonymize: "on"`, the model never sees host names, cluster
names, IP addresses or `anonymize-words`:

```
web-prod-07   -> hostA
prod-cluster  -> clusterA
site.lan       -> corpA.example
10.20.1.5     -> 198.18.0.5
10.20.1.9     -> 198.18.0.9   (same subnet stays same subnet)
```

- The model's answers and tool calls come back with the real
  names, so you never see the stand-ins.
- What's hidden: this host, the hub, the agents and their
  addresses, Kubernetes contexts, this host's DNS domain, every
  IPv4 address (except 127.x, 0.0.0.0 and 255.x masks) and
  `anonymize-words`.
- Kept: container, pod, workload and image names, because the
  model needs them. Add a name to `anonymize-words` to hide it.
- Secrets are masked either way.
- It covers every model call: chat, the page, auto-trace and
  `./ts learn`.
- `./ts check` shows it is on and how many names are hidden. The
  intro shows "(anonymized)" next to the model.

Use it with a model outside your network. On prod, with the model
inside the network, it can stay off.

## Install

### Step 1: copy the files into `src/troubleshooter/`

On every host:

```
anonymize.py  (new)
autotrace.py  (new)
links.py      (new)
cli.py
config.py
help.txt
history.py
hosts.py
llm.py
tools.py
web.py
webpage.py
```

### Step 2: edit `compose.yaml` in three places

The defaults are: auto-trace on, 10 an hour, 10 minutes,
anonymize off. Add these only to change them.

**Place 1:** in the settings block, after the `llm-ca-file` line:

```yaml
  # "on" = the model never sees host names, cluster names or IPs:
  # they become hostA, clusterA, 198.18.x.x and are swapped back in
  # its answers. Use it with a model outside your network.
  anonymize: &anonymize "off"
  # More words to hide the same way, comma list: a company name,
  # a domain. This host's own DNS domain is hidden already.
  anonymize-words: &anonymize-words ""
```

**Place 2:** in the settings block, after the `web-port` line:

```yaml
  # Auto-trace (a hub, or a host alone): a new problem starts its
  # own look-only trace, with the learn model when one is set.
  # Saved in logs/traces/; read on the page or with ./ts /traces.
  # "off" turns it off.
  auto-trace: &auto-trace "on"
  # Most auto-traces started in an hour.
  auto-trace-per-hour: &auto-trace-per-hour "10"
  # A change this many minutes before a problem is linked to it.
  link-minutes: &link-minutes "10"
```

**Place 3:** under `environment:`, after `WEB_PORT: *web-port`:

```yaml
      AUTO_TRACE: *auto-trace
      AUTO_TRACE_PER_HOUR: *auto-trace-per-hour
      LINK_MINUTES: *link-minutes
      ANONYMIZE: *anonymize
      ANONYMIZE_WORDS: *anonymize-words
```

### Step 3: restart

On every host:

```
docker compose up -d
```

No new image is needed. The container log then says:

```
Watching every 60 s. Auto-trace is on (at most 10 an hour), saved in logs/traces/.
This host runs as: alone.
```

## Using it on the page (hub)

- **Map → Open problems:** each new problem shows
  - its link: green for linked, orange for maybe
  - the trace: waiting, running, "Traced, likely: …",
    "Not traced: 10 already this hour", or failed
  - **Watch it** while it runs, **Open trace** when done
- **Trace, for an auto-trace:**
  - a purple note says Tracer started it by itself
  - anyone signed in can read it and ask more; the
    answers are saved into the trace file
  - **Back to my trace** returns to your own trace
- **What changed:**
  - a green line joins a change to its problem
    (dashed orange for maybe)
  - a purple diamond marks a finished auto-trace;
    select it in the list to open the trace, even
    after a restart
- **Wall screen:** each problem with what its
  auto-trace found, and "◆" lines in the last hour.

## Rules

- One trace at a time, at most `auto-trace-per-hour`.
- Problems already open when the container starts are not traced.
- A problem that clears before its turn is skipped.
- A restart, a state flip, or fewer ready is never a cause:
  that is what the problem looks like.
- A change is only seen at the next snapshot, so one written
  down just after the problem still counts, as "at about the
  same time".
- "maybe" links are shown, but the model is not told they
  are the cause.
- Traces kept: the newest 200, at most 20 MB, per host.

## Data per trace

```
trace file        ~10-40 KB
log lines         ~1 KB
extra snapshot    ~1.5 KB
model input       ~35k tokens (about 6 calls)
model output      ~1-2k tokens
```

`auto-trace-per-hour` caps the model cost: at most about 8M tokens
a day at 10 an hour, if the limit is hit every hour.

## Log lines (plain English)

```
The watch found a new problem: docker: relay: Exited (1)
docker: relay: Exited (1) started 1 min after: container relay: image relay:4 -> relay:5
[auto-trace] Read logs of container relay (71 characters back)
Auto-traced docker: relay: Exited (1) on host-02: likely cause: …
No change on host-03 in the 10 min before host-03 docker: web: Exited (1) (…)
You opened the auto-trace of docker: relay: Exited (1)
```

## Turn it off

Set `auto-trace: "off"` in the settings block, then
`docker compose up -d`. On a hub, links still show on the page.
