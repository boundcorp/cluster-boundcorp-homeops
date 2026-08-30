# Boundcorp HomeOps weekly SRE review

## `$TARGET`

The Boundcorp HomeOps k3s homelab: one control-plane node (`enterprise`), worker nodes (`ziti`, `vega`, `nova`), Titan NFS storage, the Cilium/NetBird network path, Flux-managed Kubernetes workloads, persistent data, PostgreSQL, backups, and observability.

- Project timezone: `America/Los_Angeles`.
- Kubernetes access: `KUBECONFIG="$HOME/.kube/clusters/home" kubectl …` (also configured by `.envrc`).
- Kubernetes writes are prohibited during this review. This repository is Flux/GitOps-managed; remediation must be separately authorized and performed through Git.
- SSH transport reaches the homelab network from this environment. Titan supports read-only SSH as `admin` at `10.20.30.99` when the separately managed approved identity is available; direct Kubernetes-node SSH still requires a separately configured, non-interactive identity. Do not place identity paths, credentials, or keys in this document or a journal.

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

Live review and recovery follow-up completed 2026-08-30T13:02:00-07:00 through 2026-08-30T14:08:00-07:00 America/Los_Angeles. Statuses below are the current snapshot from `docs/sre-audit/journals/2026-08-30.md`; no GitOps auto-fixes were applied.

- Last live review: 2026-08-30T13:02:00-07:00. Review window: 2026-08-23 through 2026-08-30 America/Los_Angeles.
- Evidence/model limitations: required `5.6-luna` was unavailable because this runtime exposed no per-task model selector; investigations used available `openai-codex/gpt-5.6-terra`. Kubernetes Events are not retained for the full weekly window. Direct read-only SSH is verified for `enterprise`; `vega` SSH timed out and kubelet proxy returned 503; `ziti` SSH failed strict host-key verification; Titan SSH stopped before authentication because its trusted host key was unavailable. Prometheus was unavailable during the review window; it was recovered by one out-of-band pod deletion and now passes its readiness check, but historical metrics remain unavailable. No secrets were recorded.
- Nodes and capacity: unhealthy/high overall. `vega` unhealthy/high (Ready Unknown for ~15h38m, unreachable taints, CSI/Cilium/NetBird 3/4); `nova` unhealthy/high (31 retained evictions, 2.76GiB free, 582 image-GC failures); `ziti` degraded/high (40 retained evictions, 2.56GiB free); `enterprise` degraded/moderate (Ready, deliberate cordon, 107% allocatable-memory sample, kube-vip route-loss restart). Evidence source: `kubectl` node/summary queries and enterprise read-only host evidence at 2026-08-30T13:05-13:14-07:00.
- Workloads: unhealthy/high overall. Directly examined controllers included Immich Server, PgBouncer, PGO, ESPHome, Home Assistant, Frigate, Speakr, backup tooling, cert-manager, Flux, Hubble, and others; repeated ziti/nova ephemeral-storage evictions interrupted singleton services. `home/zwave-js-ui` recovered to `1/1` with a ready endpoint after an approved one-time recovery action, but remains degraded/high for the review window. Remaining discovered controllers not individually returned remain unknown.
- Network and exposure: degraded/high. Ingress controller is available. At 2026-08-30T14:08, `prometheus.home.boundcorp.net` returned HTTP 302 and `zwave.home.boundcorp.net` returned HTTP 200 after StatefulSet recreation; a stale Hatchet Ingress still references a missing Service. Cloudflared recovered on nova after a ziti replacement.
- Titan/PVCs: degraded/high for CSI availability; unknown-to-degraded for individual storage integrity. CSI controller is 1/1; node DaemonSet is 3/4 because vega. Share-level samples show NVMe 3.63TiB/95.9% available and HDD 18.18TiB/63.8% available, but inodes, quotas, NAS hardware, snapshots, and RAID/SMART for Titan remain unknown. Several PVCs now have measured usage and explicit backup mappings; destination integrity and isolated restore proof remain absent. `backups/leeward-backups` has an in-window NFS I/O error and no observed automated independent backup mapping.
- PostgreSQL/backups: degraded/high overall. PostgreSQL primary and logical `postgres`, `recipes`, `sonarr`, `birdweather`, `immich`, and `speakr` are currently queryable with no sampled lock contention. PGO, primary, and PgBouncer remain singleton components with recent replacements/evictions; Immich and Speakr retain high application/node-eviction risk. Full and incremental pgBackRest jobs/repository metadata are current, but no isolated restore proof exists.
- Observability/alerts: degraded/high after one-time Prometheus recovery. Prometheus is now `1/1` with ready endpoints and requires scrape/rule/alert verification; Loki remains Failed/InitFailed on missing `monitoring/loki-bucket`; Vector and node-exporter are 0/0; Grafana and Alertmanager are reachable. Slack `#home` contains 126 records/109 alert notifications in the window, including recurring multi-hour node incidents and persistent backup-failure notifications, with incomplete one-to-one firing/resolution pairing.
- Healthy or intentionally healthy units: `games/satisfactory` remains intentionally scaled to zero and Flux-converged; `flux-system/image-automation-controller` and `kustomize-controller` have direct healthy runtime/reconciliation evidence. These are not claims that their dependencies or weekly availability are fully healthy.
- Per-unit status index: the dated journal's `Complete dynamic membership index` is authoritative for every discovered node, controller, Job/CronJob, PVC, PV, and logical database, with each named individually and classified. Each entry includes the evidence limitation and operational risk; unknown is not healthy.
- Immediate focus: preserve and diagnose `vega`, remediate durable ephemeral-storage headroom on `nova` and `ziti`, verify Prometheus after recovery, repair Loki/Vector without fabricating `loki-bucket`, re-probe the two ingress backends, and obtain approved Titan/backup evidence. `$AUTO_FIXES` is None.
- Declarative recovery note: monitoring/ingress pod recovery was performed once out-of-band on 2026-08-30T14:05-14:08-07:00; the evidence and process deviation are recorded in `docs/sre-audit/journals/2026-08-30.md`. Future remediation must use GitOps.

## Journals

Each live review creates or appends `docs/sre-audit/journals/YYYY-MM-DD.md` using the project timezone and the evidence-first SRE Review journal structure. Configuration-time discovery does not create a journal.
