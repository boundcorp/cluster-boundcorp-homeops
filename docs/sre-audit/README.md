# Boundcorp HomeOps weekly SRE review

## `$TARGET`

The Boundcorp HomeOps k3s homelab: one control-plane node (`enterprise`), worker nodes (`ziti`, `vega`, `nova`), Titan NFS storage, the Cilium/NetBird network path, Flux-managed Kubernetes workloads, persistent data, PostgreSQL, backups, and observability.

- Project timezone: `America/Los_Angeles`.
- Kubernetes access: `KUBECONFIG="$HOME/.kube/clusters/home" kubectl …` (also configured by `.envrc`).
- Kubernetes writes are prohibited during this review. This repository is Flux/GitOps-managed; remediation must be separately authorized and performed through Git.
- SSH transport reaches the homelab network from this environment, but this session has no accepted SSH identity for `enterprise` and cannot resolve hostname `titan`. Node and Titan host-level checks therefore require a separately configured, non-interactive SSH identity. Do not place identity paths, credentials, or keys in this document or a journal.

## `$CADENCE`

Weekly, with a rolling seven-day lookback, after the Sunday 02:00 local PostgreSQL full backup has completed. Journal dates and timestamps use `America/Los_Angeles`.

## `$SUBAGENT_MODEL`

`5.6-luna`

Every live investigation must request this model explicitly and launch independent units concurrently. If the runtime cannot provide it, record the constraint rather than silently substituting another model.

## `$RESOURCES`

At the start of every live review, enumerate dynamic membership. Dispatch one investigation for every discovered workload controller, logical database, backup workflow, Bound PVC, and node. A resource-group result does not replace examination of its members.

| Unit | Membership / evidence source | Required review checks |
|---|---|---|
| Kubernetes control plane and Flux | Control-plane node `enterprise`; `kube-system`; Flux `GitRepository`, `Kustomization`, and `HelmRelease` objects | API-server, scheduler, controller-manager, etcd, k3s service, node pressure, failed reconciliation, stuck releases, pending resources, recent events, and control-plane metrics. `enterprise` being cordoned must be compared to its taints/scheduling intent, not automatically marked unhealthy. |
| Each node | `enterprise`, `ziti`, `vega`, `nova` | Kubernetes Ready/pressure/taint/cordon state; kubelet/Cilium/CSI/NetBird DaemonSet readiness; CPU/memory/disk/network saturation; kernel and storage errors; SMART/NVMe/RAID status; OS/update state; and node-specific workload impact. |
| Every Kubernetes workload | Every `Deployment`, `StatefulSet`, `DaemonSet`, `Job`, and `CronJob` from `kubectl get deploy,statefulset,daemonset,job,cronjob -A`; terminal Jobs are recorded with their completion result before exclusion | Desired/current/available replicas, pod readiness, restart/OOM history, pending/failed pods, warning events, deployment generation, CronJob freshness/history, resource requests/limits, and relevant application metrics/logs. A deliberately scaled-to-zero workload must have its intent confirmed from GitOps configuration before it is not treated as degraded. |
| Network and service exposure | Cilium, Hubble, CoreDNS, ingress-nginx, Cloudflared, external-dns, k8s-gateway, NetBird, Cilium L2 resources, Services/EndpointSlices/Ingresses/Certificates | Network-policy and CNI health, node-to-node reachability, ingress/endpoints, DNS, certificate validity, tunnel health, external DNS reconciliation, load-balancer availability, and dependency correlation for user-facing workloads. |
| Titan NFS and CSI | Titan at `10.20.30.99`; NFS exports `/volume1/k8s` (`nfs-titan-nvme`) and `/volume2/k8s-hdd` (`nfs-titan-hdd`); CSI NFS controller and node DaemonSet | Export reachability, CSI node/controller readiness, mount errors/timeouts/staleness, per-share capacity/inodes/latency, RAID/filesystem and disk health, NAS system logs, snapshots/replication, and impact on every NFS-backed workload. NFS’s `soft` mount option requires particular attention to I/O error evidence. |
| Every PVC/PV | Every Bound claim/PV from `kubectl get pvc,pv -A`; presently all sampled claims use `nfs-titan-nvme` or `nfs-titan-hdd` | Bound state, StorageClass/share/path, actual capacity and inode use at Titan, growth, mount errors, reclaim behavior, workload/data classification, backup mapping, newest backup and restore proof. A Bound claim and its requested size do not prove free space, quota enforcement, or backup validity. |
| PGO control plane and shared PostgreSQL | `database/pgo`, `database/postgres`; logical databases `postgres`, `recipes`, `sonarr`, `birdweather`, `immich`, `speakr`; PgBouncer and pgMonitor exporter | Operator/CR conditions, primary and PgBouncer availability, PostgreSQL/Patroni health, connection pressure, database size/growth, locks/long transactions, WAL/archive state, query availability without sensitive results, exporter health, and storage pressure. Each logical database is a distinct investigation unit. |
| PostgreSQL backup repository | `database/postgres` pgBackRest repository in object storage and generated full/incremental backup CronJobs | Repository status, newest full/incremental backup age, archive continuity, schedule/job outcomes, retention, destination availability, and approved isolated restore evidence. `pgbackrest info` is repository metadata, not a recovery proof. |
| Non-PGO backups | Every backup-related CronJob/Job, initially `backups/pvc-backup`, `database/immich-library-backup`, and `media/jellyfin-backup` | Last schedule, success/failure, job logs without secrets, destination freshness/content/size, source-to-backup mapping, and isolated restore evidence. Identify data-bearing PVCs with no mapped backup. |
| Capacity and hardware | Every node plus Titan NFS NVMe/HDD pools, Kubernetes capacity and scheduling metrics | CPU/memory/disk/network headroom, disk I/O/latency/errors, filesystem/inode capacity, RAID/degradation, thermal/fan/power signals where exposed, pod scheduling headroom, and trend direction. |
| Prometheus, Grafana, Alertmanager, and logs | Prometheus/Alertmanager in `monitoring`, Grafana, Loki, Vector, node-problem-detector, node-exporter configuration, kube-state-metrics, application scrape objects, dashboards, rules | Active target/scrape/rule health; Prometheus storage/retention; Alertmanager active/silenced alerts; Grafana datasource, provisioning, and panel-query health; log ingestion/queryability; and dashboard coverage. Map every active service to Kubernetes/node metrics and, when it exposes application metrics, to a healthy scrape and useful dashboard/panel. An unmatched service is an observability gap, not a healthy service. |
| Alert history and quality | `#home` in the `boundcorp` Slack archive, Alertmanager and PrometheusRules | Retrieve firing/resolved pairs for the rolling week; inspect active/silenced alerts, repeated symptoms, alert duration, missing resolves, noisy rules, dashboard links, and scrape state during alerts. Retrieve a full Slack thread for every message with `thread_ts`. |

## `$EXTRA_INSTRUCTIONS`

### Evidence collection

All commands are read-only. Do not run `kubectl apply`, `helm install`, `helm upgrade`, reconciliation, restarts, package installation, storage commands with mutation, or restores during a review.

```sh
# Cluster, dynamic workload, Flux, storage, and recent-event inventory
KUBECONFIG="$HOME/.kube/clusters/home" kubectl get nodes,deploy,statefulset,daemonset,job,cronjob,pod -A -o wide
KUBECONFIG="$HOME/.kube/clusters/home" kubectl get events -A --sort-by=.lastTimestamp
KUBECONFIG="$HOME/.kube/clusters/home" kubectl get gitrepositories,kustomizations,helmreleases -A
KUBECONFIG="$HOME/.kube/clusters/home" kubectl get postgresclusters,pvc,pv -A -o wide

# PGO health and backup metadata (actual pod name is dynamically discovered).
KUBECONFIG="$HOME/.kube/clusters/home" kubectl get postgresclusters -A -o json
KUBECONFIG="$HOME/.kube/clusters/home" kubectl -n database exec <postgres-pod> -c database -- pgbackrest info --output=json

# Prometheus scrape state and observability inventory.
KUBECONFIG="$HOME/.kube/clusters/home" kubectl get configmap -A -l grafana_dashboard=1
KUBECONFIG="$HOME/.kube/clusters/home" kubectl get servicemonitor,podmonitor,probe,prometheusrule -A
KUBECONFIG="$HOME/.kube/clusters/home" kubectl -n monitoring exec <prometheus-pod> -c prometheus -- wget -qO- 'http://localhost:9090/api/v1/targets?state=active'
```

### SSH evidence and access limitation

Use the approved SSH identity and host aliases when they are available. Collect only necessary operational evidence, for example filesystem/inode capacity, RAID state, `smartctl`/`nvme` health, kernel storage/network errors, service status, and device error counters.

At access verification (2026-08-12T10:54:52-07:00), a separately managed identity successfully reached Titan as `admin` at `10.20.30.99`. Direct Kubernetes-node SSH remains unverified: the documented `setup` account on `enterprise` rejected the current identity and its tested key. Titan SMART exists but requires privileged access; passwordless privilege was not available, so physical-drive SMART health remains **unknown**. The live review must name any continuing missing access and its risk; it must not guess users or record private-key material.

### Backup validity standard

- Successful CronJob completion and `pgbackrest info` establish only job/repository metadata health.
- A backup is **recoverability-verified** only after a documented restore to an isolated, disposable target succeeds and the restored service/data integrity is safely checked.
- Never run a restore test against production, overwrite a PVC, alter an object store, or create test infrastructure without separate explicit authorization.
- For every PVC, record its source workload/data class, Titan share/path, actual usage, backup mapping, newest successful backup, off-host/copy status, and restore proof. Do not infer coverage from a PVC name, scheduled CronJob, or NFS availability.

### Alert-history query

Alertmanager is configured to route to `#home`. Use the local archive and do not include raw private messages, credentials, tokens, connection strings, database contents, object keys, or unnecessary hardware identifiers in journals.

```sh
slack-history-db sql 'SELECT m.timestamp, m.thread_ts, json_extract(m.blocks, "$.attachments[0].title"), json_extract(m.blocks, "$.attachments[0].text") FROM messages m JOIN channels c ON c.id=m.channel_id AND c.workspace=m.workspace WHERE m.workspace="boundcorp" AND c.name="home" AND m.timestamp >= datetime("now", "-7 days") ORDER BY m.timestamp ASC;'
```

For every returned `thread_ts`, run `slack-history-db thread <thread_ts> -w boundcorp` before drawing conclusions.

### Assessment rules

- Classify every examined unit as **healthy**, **degraded**, **unhealthy**, or **unknown**. `unknown` must state exactly which evidence/access is missing and the operational consequence.
- Keep observed facts separate from inferences. Prefer bounded metrics, timestamps, links/query references, and relevant logs over assertions.
- Compare the current window against the preceding journal. Establish availability, latency, saturation, RPO/RTO, and growth baselines only from observed evidence; never invent them.
- Correlate node reachability, CNI/NFS symptoms, pod lifecycle, alert history, scrape failures, Flux conditions, database state, and application availability before proposing a cause.
- Assess healthy workloads deliberately: readiness, recent events, relevant metrics/logs, active alerts, dependency state, and baseline trend are all required.

## `$AUTO_FIXES`

None. The review is strictly read-only. Every restart, configuration/deployment/alert change, package update, storage action, reconcile, backup/restore action, data operation, or access-control change requires explicit authorization outside this record and must use the project’s GitOps process where applicable.

## `$CURRENT_SERVICES_AND_STATUSES`

No live SRE investigation has been performed yet. This is a configuration-time, read-only inventory. All statuses below are **unknown / baseline pending**, not health assertions; the first live review must update every discovered unit with status, check time, evidence source, and baseline observations.

- Last inventory/access check: 2026-08-12T10:54:52-07:00.
- Verified evidence sources: `~/.kube/clusters/home`, repository manifests, Prometheus local target API, `#home` Slack archive, and read-only Titan SSH access. Direct Kubernetes-node SSH remains unverified with the current identity.
- Cluster inventory: `enterprise`, `nova`, `vega`, and `ziti` all reported Ready with no Kubernetes MemoryPressure, DiskPressure, or PIDPressure; `enterprise` remains SchedulingDisabled. Kubernetes Metrics API showed current CPU/memory observations for every node, but direct host hardware/OS/log evidence remains baseline pending.
- Storage inventory: all sampled claims are NFS CSI claims on Titan. Both `nfs-titan-nvme` (`/volume1/k8s`) and `nfs-titan-hdd` (`/volume2/k8s-hdd`) are in use. Titan verified `/volume1` at 144 GiB used of 3.7 TiB (4%) on healthy `md1` RAID1 and `/volume2` at 6.7 TiB used of 19 TiB (37%) on healthy `md2` RAID5. Significant data claims include the 4 TiB Immich library, 1 TiB Frigate media, 10 TiB Slushmedia data, 2 TiB backups, 25 GiB Prometheus, shared PostgreSQL, and home/media application data. Titan inode, SMART/NVMe, and snapshot/replication state remain baseline pending because the supplied admin identity lacks non-interactive privileged diagnostics.
- PGO inventory: one `database/postgres` cluster with the six logical databases listed under `$RESOURCES`. pgBackRest repository status was `ok` when sampled; the newest displayed incremental backup metadata completed on 2026-08-12. No isolated restore-test evidence is recorded.
- Backup workflow inventory: active CronJobs included `pvc-backup`, `immich-library-backup`, PostgreSQL full/incremental backups, `jellyfin-backup`, and `jellyfin-restarter`. Completion, destination validity, and coverage/restore baselines remain pending.
- Observability inventory: Prometheus had 41 active targets with no unhealthy target at the sampling instant. Grafana had 30 provisioned dashboard ConfigMaps. Scrape/dashboard coverage and per-node metrics baseline pending. The deployed node-exporter and Vector Agent DaemonSets reported 0 desired/current due to their configured selectors; a live review must determine intent and resulting node-metric/log coverage rather than assume failure or adequacy.
- Alert-history context: `#home` showed prolonged/repeated events during the seven-day discovery window, including PostgreSQL pgBackRest job/staleness alerts, `ziti` reachability, NFS/Cilium-related DaemonSet rollout symptoms, `k8s-gateway` pending/non-ready symptoms, backup Job failures, and target-down alerts. This is context for the first live review, not a root-cause conclusion.

## Journals

Each live review creates or appends `docs/sre-audit/journals/YYYY-MM-DD.md` using the project timezone and the evidence-first SRE Review journal structure. Configuration-time discovery does not create a journal.
