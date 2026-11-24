# vega catch-up: v1.27.3+k3s1 → v1.28.15+k3s1 → v1.29.15+k3s1 → v1.30.14+k3s2, then uncordon (2026-11-24)

Status: **done, no rollback.** All four nodes are Ready on v1.30.14+k3s2 / containerd 1.7.27-k3s1. vega was **uncordoned at 19:16:35Z** after the last minor and passed an 11-minute watch. enterprise stays cordoned. No minor was skipped.

Authorization: Lee, Nov 24 18:42 UTC, explicitly approved upgrading and uncordoning vega **despite the failed 2026-11-21 load test**, accepting the known hardware-lockup risk (separate read-only investigation, thread 98ab2883). Scope: vega-only upgrades/restarts, a targeted SUC Plan via Git and reconcile, snapshots, uncordon and this journal. Excluded: other nodes, hop 4, Flux, cluster removal, DB/ingress, devbox/Octo/Vaultwarden, secrets, stress tests, repartitioning. Starting main `92d8c37`. `~/ENVIRONMENT.md`, hop 1–3 journals, the prerequisite journal, both SUC Plans and draft PR #3's plan (`origin/leewardbound/k3s-upgrade-plan-1120`, not merged, not edited) were read first.

## Preflight (18:45Z)

- `envcheck`: home/octo readyz ready; SSH vega/enterprise/rigel/macaroni reachable. vega SSH used the existing `setup@10.20.30.112` alias with `StrictHostKeyChecking=yes`; no host-key change. No nova/ziti SSH.
- Nodes: enterprise (control-plane), nova, ziti on v1.30.14+k3s2; **vega agent v1.27.3+k3s1, containerd 1.7.1-k3s1**, cordoned (`node.kubernetes.io/unschedulable` since 2026-11-21T05:57:14Z). Only the 9 DaemonSet pods ran on vega. No pressure anywhere.
- 44/44 Kustomizations, 34/34 HelmReleases (revision baseline captured), 52/52 targets, 9/9 DaemonSets full, 29/29 PVCs Bound, no unhealthy active pods (3 historical hop-2 Failed records retained). API readyz/VIP/direct 200, etcd healthy, enterprise ~2.9 GiB available. Postgres primary `postgres-postgres-6bb6-0` UID `e5480b37-…` 5/5 on nova, SQL `false/off`; PgBouncer 1 pod 2/2 on nova.
- Free bytes / inodes (kubelet stats): enterprise 97.8 GB / 7.15 M; nova nodefs 21.1 GB / 1.45 M, imagefs 805 GB / 56.1 M; ziti nodefs 2.54 GB / 701 k, imagefs 412 GB / 27.6 M; vega 832 GB / 56.7 M.
- vega host: Debian 12, kernel 6.1.0-10, uptime 3 d since Lee's 2026-11-21 16:07Z power-cycle, systemd unit `k3s.service` running `k3s agent` (`KillMode=process`, `Restart=always`), config only `node-ip`, token from file. Cached artifacts: an unused `/usr/local/bin/k3s-v1.29.1+k3s1` (left as is); **no k3s-upgrade images cached on vega** (enterprise has all three), Docker Hub reachable from vega. `kernel.panic=10`; no kernel warnings since boot apart from audio/journald boot noise.
- SUC v0.21.1 controller 1/1 on nova; Plans agent/server Complete at v1.30.14+k3s2, zero Jobs.

## Mechanism: temporary vega-only SUC Plan

`plans/vega-catchup.yaml` (Plan `vega-catchup`), stepped one minor per commit:

- `nodeSelector`: `kubernetes.io/hostname In [vega]` and `control-plane DoesNotExist`. The global agent Plan was **not** touched and still excludes vega; the server Plan was not touched.
- **No `cordon`/`drain`**: SUC v0.21.1 `handle_batch.go` sets `Unschedulable=false` after a successful Job only when the Plan has cordon or drain, so vega stayed cordoned between minors. Job pods tolerate `node.kubernetes.io/unschedulable` by default (`job.go`); the toleration is also explicit in the Plan.
- **No `prepare` step**: k3s-upgrade's `prepare server` waits until the control-plane version equals the Plan version, which can never happen while an agent catches up behind a 1.30 server.
- `concurrency: 1`, no renovate marker. Each commit was preceded by a fresh `origin/main` check, a server-side dry-run and a full health gate, then reconciled with `flux reconcile source git home-kubernetes` and `flux reconcile ks cluster-apps-system-upgrade-controller-plans`.

## Snapshots

Offload uses the established `etcd-snapshot-offload` CronJob (rereads the titan copy and compares SHA-256). Enterprise checksum read independently on the exact file; root:root 0600. Contents and credentials never left enterprise/titan.

| Snapshot | Bytes | SHA-256 (enterprise = titan) |
|---|---|---|
| `pre-vega-catchup-20261124T1848Z-enterprise-1795546110` (saved 18:48:32Z, Job `pre-vega-catchup-20261124t1848z`) | 39,268,384 | `c6673edfbf8db79bebd9a86e891164d1233d9864d67ba92bac2b7cb89903d036` |
| `post-vega-catchup-20261124T1928Z-enterprise-1795548539` (saved 19:28:59Z, Job `post-vega-catchup-20261124t1928z`) | 39,268,384 | `3c153fce3e12f28b39d74d43a1a19a29ed8d50bc3d12379529cac270a60815be` |

## Steps (UTC)

| Step | Main commit | Job hash | kubelet start | Version seen | Plan Complete | containerd |
|---|---|---|---|---|---|---|
| 1 → v1.28.15+k3s1 | `b7e0158` (pushed 18:49:27) | `f9eb9866…` | 18:50:03 | 18:50:11 | 18:50:25 | 1.7.22-k3s1.28 |
| 2 → v1.29.15+k3s1 | `9286fb9` (pushed ~19:00:20) | `59d1797a…` | 19:01:01 | 19:01:06 | 19:01:21 | 1.7.26-k3s1 |
| 3 → v1.30.14+k3s2 | `c3cf0fc` (pushed ~19:11:00) | `a5211fb5…` | 19:11:50 | 19:11:53 | 19:12:32 | 1.7.27-k3s1 |

- Each step behaved like the other agents in hops 1–3: the first SUC pod replaced `/usr/local/bin/k3s`, SIGTERMed the old k3s (pids 718, 317758, …) and ended `Unknown` when the kubelet restarted; the Job's retry pod logged "Binary already been replaced" and succeeded. No manual retry.
- vega's `Ready` condition never transitioned (lastTransitionTime still 2026-11-21T16:07:49Z); no NodeNotReady event. Only `Starting`/`NodeNotSchedulable` (cordon re-asserted, not changed) events per restart, plus the documented startup `InvalidDiskCapacity`/`invalid capacity 0` and `PLEG has yet to be successful` transients and systemd "left-over containerd-shim" notices expected with `KillMode=process`.
- **Boot ID unchanged** (`359e947e…`) in all 87 successful watcher SSH samples 18:47–19:29Z: no reboot, no hang, no kernel log entries. vega load stayed 0.1–0.6.
- One watcher sample at 19:00:47Z recorded "vega ssh fail rc=3": SSH connected, but `systemctl is-active k3s` returned 3 during the step-2 restart. Not a connectivity loss.
- Soak before each next minor: 1.28.15 ≈ 10 min (18:50:25 → 19:00:20), 1.29.15 ≈ 10 min (19:01:21 → 19:11:00), with full gates (44/44 ks, 34/34 hr, unchanged Helm revisions, 52/52 targets, SQL `false/off`, same primary UID, no unhealthy pods, no restart deltas anywhere in the cluster) before each push.
- The other three nodes, their cordon states, the database, ingress and all six HTTP checks were unaffected throughout. One SQL exec sample at 18:48:33Z (during the pre-snapshot save, before any change) returned unknown; all later samples `false/off`.

## Post-1.30 verification and uncordon

- vega v1.30.14+k3s2, kube-proxy version v1.30.14+k3s2, containerd 1.7.27-k3s1, all conditions healthy.
- **CNI**: cilium on vega `OK`, Kubernetes 1.30 Ok, KubeProxyReplacement True, controllers 36/36 healthy, cluster health 4/4 reachable, IPAM 6/254.
- **DNS**: from vega's host `dig @10.43.0.10` resolved `kubernetes.default` to 10.43.0.1. From pod network on vega, `vector-agent` resolved `kubernetes.default` and `grafana.boundcorp.net`, and `loki-canary` resolved and reached `loki-gateway` via service.
- **Storage**: CSINode vega lists `nfs.csi.k8s.io`; csi-nfs-node 3/3. vega holds no PVs.
- **DaemonSets**: all 9 full cluster-wide; vega's 9 DS pods Running with unchanged restart counts and container identities across all three restarts.
- Gate at 19:16:22Z green, then **`kubectl --context home uncordon vega` at 19:16:35Z** (NodeSchedulable 19:16:37Z). enterprise remains cordoned.
- **Watch 19:16:41–19:27:33Z (10 min 52 s)**: 24 watcher samples and 9 full-health samples. Every sample: vega Ready, uncordoned, same boot ID; SQL `false/off`, same primary UID; DNS ok; all HTTP 200; readyz/VIP/etcd ok; rigel route connected where sampled; no restart deltas, no new containers, no crashloops; no eviction/pressure/GC/OOM events (the watcher's `NodeHasNoDiskPressure` substring alerts are the normal "no pressure" event). One full-health sample at 19:17:30Z caught `cluster-apps-system-upgrade-controller` mid-reconcile (triggered by the step-3 source change; finished in 14.8 s, Ready 19:17:32Z).
- **No non-DaemonSet pod was scheduled onto vega during the watch** (no scheduling demand arose; nothing was moved or forced there, no test pod). So vega has not yet carried real workload or load; the hang risk under load is untested by this change.

## Cleanup and final state

- Main commit `aeeadbb` removed `vega-catchup.yaml` and its kustomization entry; Flux (`prune: true`) deleted the Plan. Net Git diff vs `92d8c37` under `plans/` is empty. Zero SUC Jobs remain (900 s TTL).
- Final health 19:29:47Z: 4/4 nodes Ready v1.30.14+k3s2, enterprise cordoned, nova/ziti/vega schedulable; 44/44 ks, 34/34 hr, Helm revisions unchanged from preflight, 52/52 targets, all pods healthy, 9/9 DS, 29/29 PVC, SQL `false/off`, enterprise ~2.8 GiB available. Free bytes/inodes: enterprise 97.7 GB / 7.15 M; nova nodefs 21.1 GB / 1.45 M, imagefs 805 GB / 56.1 M; ziti nodefs 2.55 GB / 701 k, imagefs 412 GB / 27.6 M; vega 831 GB / 56.7 M.
- Left on purpose: vega node labels `plan.upgrade.cattle.io/vega-catchup=a5211fb5…` (from this Plan) and the older `plan.upgrade.cattle.io/agent=e1d892a3…`. Both are inert metadata; removing them is a direct node edit outside this scope. The agent Plan's `NotIn [vega]` exclusion is unchanged, so **future hops will not upgrade vega automatically**: hop 4 needs a decision to include vega in the agent Plan (it is now at the same version as the workers) or repeat a vega-only step. `/usr/local/bin/k3s-v1.29.1+k3s1` on vega untouched.
- Temporary watcher/health processes were stopped. No drain, no app restarts, no other node touched, no Ansible run, no database/ingress/secret/Flux change. Ziti's `/srv` runtime and old-copy rollback tree were not touched.

## Commits

| Main commit | Purpose |
|---|---|
| `b7e0158` | step 1: temporary vega-only Plan at v1.28.15+k3s1 |
| `9286fb9` | step 2: v1.29.15+k3s1 |
| `c3cf0fc` | step 3: v1.30.14+k3s2 |
| `aeeadbb` | remove the temporary Plan |

Sanitized evidence (health samples, watcher log, SUC pod logs, snapshot metadata/checksums): `/home/dev/orca/artifacts/vega-catchup-20261124-task_9317aa08b6c7/`.
