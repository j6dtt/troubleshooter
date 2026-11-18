# VKS scope, cluster cards, and your own logins

How it works: Tracer reads your `~/.kube/config` (mounted
read-only) as well as `./kube`, and reads them again within a
minute whenever they change. Each context is checked by what it
is: the Supervisor, a vSphere namespace, or a cluster seen from
inside.

```
kubectl vsphere login ...   (on the hub host)
   → ~/.kube/config changes
   → watch, next round (≤ 60 s):
       files changed? read the contexts again
   → log: "The Kubernetes logins changed:
           added prod1"
   → new card on the page, no restart
```

## Scope

```
vCenter, ESXi hosts         ┐
Supervisor control plane    │ not Tracer's:
Supervisor system namespaces│ the vSphere admin's
NSX, storage arrays         ┘
─────────────────────────────── Tracer's edge
vSphere namespaces          ┐
  VKS Cluster, Machine, VM  │ looked at
  claims (PVCs), events     │ (look-only on the page)
VKS workload clusters       │
  nodes, pods, workloads    ┘
Docker hosts (hub + agents)
```

- Give Tracer a login with namespace rights. Never a vCenter
  admin account: the key would sit in a shared folder, and
  Tracer passes logs to the model.
- When a problem points below a namespace (a VM that never gets an
  address, no host with enough memory), Tracer says so and stops
  there.

## The cards (Map → Kubernetes)

```
Supervisor          login ok, or expired
  (named by its      a namespace user sees nothing
   address)          across it: not a problem

vSphere namespace   "2 VKS cluster(s) (prod1, …),
  (team-a)          14 VMs, 6 claims"
                    problems: a claim not Bound,
                    a vSphere Pod in trouble
                    buttons: each VKS cluster's card

VKS cluster         from the Supervisor: control plane,
  (team-a/          worker pools, machines, their VMs
   prod1)           button "Inside:" → the cluster's own
                    card, when you have a login to it

Kubernetes          from inside: nodes, pods in every
  (prod1)            namespace
```

- Select a button on a card: the page scrolls to that card and
  outlines it for a moment.
- **Not problems:**
  - "no permission" (Forbidden)
  - restarts whose last one was over an hour ago
  - pods that failed over a day ago and were left behind (Evicted,
    a job's Error). The card counts them as "N old failed pod(s)
    left over".
- **Login expired:** orange dashed border, "login expired" in the
  corner. Not a problem, not traced. Log in again; the card is back
  within a minute.
- Two contexts on the same vSphere namespace make one card.
- A tool asked in a chat still tells the model about old restarts
  and when they were ("restarted 9 times, last 40 days ago").

## Auto-trace

- Problems a new login brings are shown, not traced: they were there
  before Tracer could see them. Same as problems open at start.
- Problems that start after that are traced as usual.

## Install

### Step 1: copy the files into `src/troubleshooter/`

On the hub (and any host alone that watches clusters):

```
tools.py
watch.py
autotrace.py
engine.py
history.py
web.py
webpage.py
config.py
cli.py
help.txt
```

### Step 2: edit `compose.yaml` in two places

**Place 1:** in the settings block, after the line
`# scripts/make-kubeconfig.sh. Empty = no Kubernetes.`:

```yaml
  # Your own ~/.kube (hub, or a host alone): every context that
  # kubectl vsphere login writes there is watched, with no restart;
  # a new login shows on the page within a minute. Full path, not ~
  # (under sudo, ~ is root's). It is your login: everyone on the
  # page sees with your rights. Example: "/home/you/.kube".
  # The default is an empty folder made here: nothing is read.
  kube-home: &kube-home "/home/you/.kube"
```

**Place 2:** under `volumes:`, after `- ./kube:/kube:ro`:

```yaml
      - type: bind
        source: *kube-home
        target: /kube-home
        read_only: true
        bind:
          create_host_path: true
```

On agents, leave `kube-home` as `"./kube-home"`.

### Step 3: restart once

```
docker compose up -d
./ts check
```

`./ts check` then shows:

```
[ok] Your ~/.kube: 6 context(s), read again within a minute of a new login
```

From then on, log in with `kubectl vsphere login` as usual. No
restart is needed.

## Things to know

- Tokens from `kubectl vsphere login` last about 10 hours. For
  watching all the time, put a service-account kubeconfig in
  `./kube`. A file there wins over a context of the same name in
  `~/.kube`.
- Logins that run a program to log in (exec plugins) don't work in
  the container. vSphere logins don't use one.
- If your kubeconfig names a CA file by path, that path must also
  be inside `~/.kube`. vSphere logins embed it or skip the check.
