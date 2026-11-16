# Roadmap

What comes next, in build order. Done items are ticked.

## Phase 1: VKS and Kubernetes depth

- [x] 1. Supervisor label: `/context` and `k8s_contexts` mark a context
      that manages VKS clusters, so "switch to supervisor" always works.
- [x] 2. `vks_cluster_health`: one call walks cluster, control plane,
      worker pools, machines, node VMs and warning events, and reports
      only what is not ready, with the reason.
- [x] 3. `vks_volume_check`: follows a claim (PVC) from its pod to the
      volume, the attach, and the Supervisor's side, with VMware's error.
- [x] 4. VKS instructions for the model: which context and kinds to
      check for each symptom.
- [x] 5. `pod_net_test`: DNS and TCP test from inside a pod.
- [x] 6. `k8s_workload_logs`: logs of all pods of a workload in one call.
- [x] 7. `k8s_service_check`: service, endpoints, pods and nodes in one
      call, and where the chain breaks.

## Phase 2: memory

- [x] 8. "What changed?": a small snapshot every 5 minutes (restarts,
      images, replicas, node and VM state) and a diff on request.
      Covers rollout history too.
- [x] 9. Case memory: `/case` saves a solved problem as symptom, cause,
      fix in the shared folder; later chats search it first.
- [ ] 10. Runbooks in real use: review the first `./ts learn` drafts
      from production and tune what they get wrong.

## Phase 3: proactive

- [x] 11. `./ts watch`: checks health on a timer and prints only when
      something goes bad.
- [x] 12. `/report`: the chat's findings as an incident write-up with
      timeline, evidence, cause and fix.

## Phase 4: reach

- [x] 13. Web page: see "Web page: built" below.
- [ ] 14. vCenter read-only tools: ESXi hosts, datastores, alarms,
      tasks. Needs a read-only vCenter account.
- [ ] 15. NSX read-only tools, only if the network kinds on the
      Supervisor leave gaps. Needs NSX API access.
- [x] 16. `learn-llm-url` and `learn-llm-model`: one model for chat and
      another for `./ts learn`.

## Web page: built

One web page for everything, same code on every host.

    browser -> HUB host: the page, the Kubernetes watch, one
               Kubernetes history, the cluster logins, its own
               Docker and host
            -> AGENT hosts: answer for their own Docker, host,
               app folders and logs; record their own Docker

- [x] `kubernetes` on/off, for Docker-only sites.
- [x] Agent on tcp/8001: encrypted, shared key, answers only the
      hub, fixed read tools only.
- [x] `hub-host` and `agent-hosts`; a host choice on the Docker and
      host tools; `./ts` and `./ts watch` on the hub cover every host.
- [x] `./ts setup` (keys and certificate) and `./ts user` (accounts).
- [x] The page, with sign-in, on tcp/9001. It looks only.
- [x] `./ts setup --web-cert`: your own certificate for the page.
- [x] A. Chat, with each step's full output beside it.
- [x] C. Health: open problems first, then everything watched.
- [x] B. What changed, as a timeline.
- [x] D. Library: cases, runbooks and reports; cases can be edited
      and deleted.

Tested on one dev host only, with a second process standing in for
another host. Still to prove on production:

- [ ] Real traffic between separate hosts, and the hub-address check
      on hosts with several addresses.
- [ ] The two service-account kubeconfigs in `kube/` on the hub.
- [ ] Root-squashed NFS: the certificate must be readable by the
      container; the keys are in `.env`, not in the shared folder.

Ideas for later:

- Fixes from the page, with approval and a record of who approved.
- The timeline's recording bar per cluster, not one for everything.

## Also done

- [x] Fix tools refuse a Supervisor context.
- [x] Background recorder: Docker snapshots with no chat open.
- [x] Snapshots compressed, folder capped at 50 MB.

## Housekeeping

- [ ] Rebuild the bundle (the current one predates the GPT-5.4 fixes).
- [ ] Run the launcher on Python 3.6 (only syntax-checked so far).
- [x] Supervisor label, `vks_cluster_health`, `what_changed` and
      `./ts watch` confirmed on a real VKS Supervisor (healthy clusters).
- [ ] See `vks_cluster_health` and `vks_volume_check` report a real
      fault: both were built against canned kubectl answers.
- [ ] Try `pod_net_test`, `k8s_workload_logs` and `k8s_service_check`
      on a real cluster: dev has none.
