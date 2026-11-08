# Troubleshooter

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

    docker compose up -d
    ./ts check     test every connection
    ./ts           chat
    ./ts tools     list what it can do

In the chat: `/context` lists and switches Kubernetes contexts, `/new` forgets the conversation, `/tools` lists tools, `/quit`
leaves, Ctrl-C stops an answer. A fix shows the exact command and runs only
if you type `yes`. Piped input can never approve.

Settings are in the block at the top of `compose.yaml`.
Secrets go in `.env` (`LLM_API_KEY`, empty for vLLM).

## Runbooks

`./ts learn /apps/myapp` drafts `runbooks/<host>/myapp.md` from the app's
source and what is running. Chat reads it when that app comes up. Each host
has its own folder. Every fact names its file:line, and a fact whose quoted
words aren't in the file is dropped. Review it, then change `Status: DRAFT`
to `Status: REVIEWED`. See `./ts --help`.
Log: `logs/<date>.log`, including every approval and refusal.

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
