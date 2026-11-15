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

- [ ] 13. Web page. Best after item 8: a change timeline reads well on
      a page.
- [ ] 14. vCenter read-only tools: ESXi hosts, datastores, alarms,
      tasks. Needs a read-only vCenter account.
- [ ] 15. NSX read-only tools, only if the network kinds on the
      Supervisor leave gaps. Needs NSX API access.
- [x] 16. `learn-llm-url` and `learn-llm-model`: one model for chat and
      another for `./ts learn`.

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
