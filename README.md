# Tracer

Terminal chat with an AI model that can inspect Docker, Swarm, Kubernetes,
the host and app files, and fix things once you approve.

    you ── ./ts ── engine ── model (OpenAI-compatible URL)
                     ├─ look tools: run freely
                     └─ fix tools: ask you first

Full guide with examples: `./ts --help`.

`./ts` is two lines that start `src/troubleshooter/launcher.py` with the
host's `python3` (3.6 or newer). Everything is in `src/`, so an update is
`.py` files and `help.txt`, never `./ts` itself.

## Use

    cp compose.example.yaml compose.yaml   once; then edit its settings block
    docker compose up -d
    ./ts check     test every connection
    ./ts           chat
    ./ts tools     list what it can do

In the chat: `/context` lists and switches Kubernetes contexts, `/new` forgets the conversation, `/tools` lists tools, `/quit`
leaves, Ctrl-C stops an answer. A fix shows the exact command and runs only
if you type `yes`. Piped input can never approve.

Settings are in the block at the top of `compose.yaml`, your own copy of
`compose.example.yaml`. It is never in git, so host names and ports stay local.
Secrets go in `.env` (`LLM_API_KEY`, empty for vLLM).

## Runbooks

`./ts learn /apps/myapp` drafts `runbooks/<host>/myapp.md` from the app's
source and what is running. Chat reads it when that app comes up. Each host
has its own folder. Every fact names its file:line, and a fact whose quoted
words aren't in the file is dropped. Review it, then change `Status: DRAFT`
to `Status: REVIEWED`. See `./ts --help`.
Log: `logs/<date>.log`, including every approval and refusal.

## What changed, watch and report

- **What changed:** the state is written down every 5 minutes: Docker all the
  time in the background, Kubernetes while a chat or `./ts watch` is open
  (images, replica counts, restarts, nodes, configmap versions, containers,
  VKS machines and VMs). Ask "what changed in the last 2
  hours?" to compare then with now. Snapshots are in the log folder under
  `snapshots/`: compressed, kept 7 days, never more than 50 MB in all (the
  oldest go first), with no settings values or Secrets.
- **`./ts watch`:** checks health every 60 seconds (`--every N` to change) and
  prints only what is NEW or OK AGAIN. No model is used. Leave it running to
  always have a history for "what changed".
- **`/report`:** in a chat, writes up the findings as an incident report and
  saves it in the log folder under `reports/`.
- **`/case`:** when a problem is solved, saves a short note (symptom, cause,
  fix, how to recognise it) after you confirm it. Later chats search these
  first and still check the live system. Notes are in `runbooks/_cases/<host>/`,
  shared by every host that runs from the same folder; edit or delete the
  files to correct them.

## Several hosts and the web page

Every host runs the same code from one shared folder. One setting, `hub-host`,
names the hub; the hosts in `agent-hosts` are agents.

    browser ── https ── HUB: the page, the Kubernetes watch and
                        history, its own Docker and host
                         │  encrypted, shared key, hub only
                        AGENTS: their own Docker, host and app files

- **Agents** listen on `agent-port` (8001) and run only the fixed read tools,
  only for the hub: no fixes, no Kubernetes, no shell.
- **`./ts setup`** makes the shared key and the certificate once; they go to
  `.env` and `certs/` and are never shown.
- **The page** is at `https://<hub>:<web-port>` (9001). It looks only; fixes
  stay in `./ts`. Accounts: `./ts user add NAME`, kept in `.env` as hashes.
- **Your own certificate for the page:** `./ts setup --web-cert CERT KEY`, so
  browsers don't warn. Hosts keep talking to each other with the one
  `./ts setup` made.
- **`./ts` on the hub** can look at the other hosts ("why is relay unhealthy
  on host-02?"). On an agent host it sees only that host.
- **Docker only:** `kubernetes: "off"` removes every cluster tool and mention.

The page's files live in `webpage.py`, so an update is `.py` files only, and
nothing is loaded from the internet.

## Kubernetes and VKS

Log in as usual, then run `./ts`. It starts on your current kubectl context
and can reach every context in your kubeconfig:

    you log in ── kubectl proxy per context ── ./ts ── clusters
                  (host, only while the chat is open)

In the chat, `/context` lists contexts (`*` = active, with whether each login
works) and `/context NAME` switches. You can also just ask: "switch to the
Supervisor". Switching lasts for this chat only and never changes your
kubeconfig or other terminals. The prompt shows the active context, and every
approval names it.

`/context` marks a Supervisor context ("supervisor"). That is where VMware
keeps each VKS cluster, its machines and their VMs. Ask "why is node X
missing?" or "is the upgrade stuck?" from any context: `vks_cluster_health`
finds the Supervisor itself and lists only what is not ready, with VMware's
reason. For storage, `vks_volume_check` follows a claim (PVC) from its pod to
the volume, the attach, and the Supervisor's side. See [ROADMAP.md](ROADMAP.md) for what comes next.

Contexts can be workload clusters or the Supervisor, with your permissions. If
a login is refused, log in again in another terminal; no restart needed. New
contexts appear the next time you run `./ts`. Needs kubectl on this host.

While the chat is open, anyone logged in to this host can reach those local
proxy ports and act as you. The app blocks Secrets and system namespaces, but
Kubernetes only limits it to your own rights. `pod_check` is not available
through the proxy.

**On shared hosts, or for tight limits:** put a
kubeconfig per cluster in `./kube`, using `scripts/make-kubeconfig.sh <name>
[--look-only]`. This makes a service account, and Kubernetes itself refuses it
Secrets and anything outside `k8s/*.yaml`. To cut access: `kubectl delete
namespace troubleshooter`.

Cluster nodes are VMs: the `host_` tools see only the Docker host running this.

## Safety

- It never runs shell commands, but it can suggest them. Commands it suggests
  are not checked by anything: read them before you run them.
- `llm-verify-tls` turns the model server's certificate check on or off. It is
  off by default, so traffic to the model can be intercepted on that network.
- No shell tool. Each tool runs one fixed command with checked arguments.
- Masked before the model, screen or log: passwords, keys, tokens.
- Never read: Kubernetes Secrets, `.env`, key files, anything outside
  `file-roots`.
- `protected-names` can't be changed, even if approved: by default the
  system namespaces, including `vmware-system-*`.
- `allow-changes: "false"` removes the fix tools.
- Masking is pattern-based, and the Docker socket and kubeconfig give this
  container admin rights on Docker. Treat access to `./ts` as Docker admin
  access. On clusters, `k8s/*.yaml` sets the real limit.

The model can be wrong. Check its evidence before approving a fix.

## Air-gapped install

    scripts/make-bundle.sh      on dev: tests, then dist/*.tar

The bundle script stops if Trivy finds any HIGH or CRITICAL issue in the image.
Copy the tar over and follow `INSTALL.txt` inside. It has no model weights
and no secrets, and pulls nothing at install or run time.

## Tests

    PYTHONPATH=src python3 -m unittest discover -s tests

## Layout

- `src/troubleshooter/tools.py`: every tool and its argument checks
- `engine.py`: conversation loop, no terminal code (a web page can reuse it)
- `cli.py`: the terminal; `llm.py`: model client; `redact.py`: masking
