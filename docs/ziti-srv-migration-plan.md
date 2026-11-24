# ziti: move container runtime storage from `/var` to `/srv` (plan only)

Status: **PLAN ONLY. Nothing in this document has been executed.** Every command below is marked NOT EXECUTED. No migration, restart, cordon, drain, eviction, mount, unit file, Ansible run, package install, known_hosts edit or storage change was made while writing it.

Requested by Lee, 2026-11-23 23:07 UTC. Base: `origin/main` `84f55fa`. Inputs: `docs/journal/2026-11-20-k3s-upgrade-prereqs.md`, `docs/journal/2026-11-23-k3s-hop2.md`, `docs/journal/2026-11-23-hop3-preparation.md` (§1 storage inspection), draft PR #3 `docs/k3s-upgrade-plan.md` §4.8 at `e4808c9`, the hop-3-prep evidence in `/home/dev/orca/artifacts/hop3-prep-20261123-task_8e5c5990eeae/`, and the fresh read-only inspection below.

Hop 3 is still not authorized. Flux is not changed by this plan.

---

## 1. Summary

| Question | Answer |
|---|---|
| Problem | ziti's container image filesystem (imagefs) and node filesystem (nodefs) are both the separate 12.46 GB `/var` partition (`/dev/sda3`). During hop 2, a cold Jellyfin pull filled it to 0 bytes. Kubelet then evicted Jellyfin, Grafana, the Loki gateway and the Postgres primary. Today 2.78 GB is available. That fails the hop-3 gate of at least 5 GB available. |
| Recommended fix | **Option A: move only the containerd root.** Copy `/var/lib/rancher/k3s/agent/containerd` to `/srv/k3s/agent/containerd` on the same SSD's 445 GB `/srv` partition (`/dev/sda5`, 422 GB available, empty). Bind-mount it back at the original path with a systemd mount unit, and make `k3s-agent` refuse to start without that mount (fail-closed). No k3s config, path, credential or Flux change is needed. |
| Why not full data-dir (nova's pattern) | Option B would also move 13 agent credential files (keys, certificates, kubeconfigs) and the k3s binaries. Those files are not the cause of the incident. The task rules say credential migration needs separate explicit approval. Option B is documented, not recommended. |
| Does it really fix eviction? | Yes. Image layers and writable container layers then sit on `/srv`, and kubelet measures imagefs against `/srv`'s capacity. nova already shows this split in production on the same k3s and containerd versions (§3.4). Pod logs, emptyDirs and k3s binaries stay on `/var`, which becomes a real separate nodefs. With the rollback copy retained, `/var` has about 2.78 GB free; after the approved cleanup, about 10.2 GB. |
| ziti outage | About **10–20 minutes** with ziti NotReady and no containers running. Plan a **90-minute** supervised window that includes gates and the 10-minute watch. Replicated and stateless critical pods are moved to nova first, so ingress, DNS, PgBouncer and MQTT see seconds of disruption, not the whole window (§6). |
| Postgres | The single primary `postgres-postgres-6bb6-0` stays on nova and is not touched. PgBouncer (one replica, currently on **ziti**) gets a short reconnect when it is moved to nova. |
| Physical access | Not strictly required, **only if** a verified out-of-band host channel to ziti exists before the window (B1, §2). There is no BMC/IPMI and no serial console. Strongly recommended: Lee is on site or reachable within about 30 minutes, with a keyboard and monitor for ziti. |
| Verdict | **NOT READY TO EXECUTE.** Blockers B1–B4 in §2 must be resolved and Lee must grant the approval scope in §11. |

---

## 2. Blockers (must be resolved before any execution approval)

| # | Blocker | Why it blocks | How to resolve |
|---|---|---|---|
| **B1** | **No verified out-of-band execution channel to ziti.** `ssh ziti` fails host-key verification, and `kubectl debug node/ziti` stops working as soon as k3s-agent and containerd are stopped. The copy step runs with exactly those stopped. | If anything fails mid-migration (copy, mount, k3s start), nobody can reach the host without a physical console. | Lee verifies ziti's current SSH host key out of band and decides whether to accept it (see below). Then, before the window, prove `ssh ziti sudo -n true` works. **Nobody edits `known_hosts` or bypasses verification without Lee's instruction.** The fallback is a physical console. |
| **B2** | **Route checked 2026-11-24: still a single routing peer.** Read-only devbox sidecar status shows `rigel` connected and advertising `10.20.30.0/24`; every other listed peer advertises no networks. | Current connectivity is good, but rigel going offline during the window still removes devbox’s SSH and kubectl route. | **Open operational gate:** confirm rigel will stay online for the window, or have Lee operate from the LAN. Recheck immediately before execution. |
| **B3** | **Implementation ready for review:** draft [PR #6](https://github.com/boundcorp/cluster-boundcorp-homeops/pull/6), commit `f25460c`, adds the standalone playbook, group vars and templates from §8. | Syntax-check passed locally; fresh nova unit and drop-in bytes match local renders exactly. §3.4’s fences omit blank section separators present in the live files; templates preserve the live bytes. No host Ansible run was made; ansible-lint is not installed. | **Still open:** review and merge PR #6, verify nova SSH separately (ziti verification does not authorize nova host keys), then run `--check --diff --limit nova` expecting zero changes. After migration, check ziti likewise. The playbook requires existing exact mounts and never reloads, mounts, enables or starts services. |
| **B4** | **Tool availability resolved 2026-11-24.** ziti was Ready with DiskPressure=False before a cached-busybox read-only debug probe. `command -v` found python3, cp, tar, findmnt, mountpoint, lsof and systemd-escape, all under `/usr/bin`; cp is GNU coreutils 9.7. | The default cp-plus-python3-manifest method now has every required tool; no package installation is needed. | Lee still approves that method under §11; repeat P10 before execution. No copy or package change was performed. |

**ziti host-key evidence for B1 (public fingerprints only; nothing changed).** These were read with `ssh-keygen -lf` on ziti's `/etc/ssh/ssh_host_*_key.pub`, through an authenticated `kubectl --context home debug node/ziti` pod, on 2026-11-23:

| Type | Current key on ziti | Entry in devbox `known_hosts` for 10.20.30.113 |
|---|---|---|
| ED25519 | `SHA256:j1WeX8uwp/JuQWaBKvUO9M441G6peJUpznh2VQLQrds` | `SHA256:WjHGTBeZojEB+N5Xr5oGC6GPdPHVS/78lvLQfrpxnao` |
| ECDSA | `SHA256:BmVb9ScHI2knFuVUHR67dYbkJeIpv6WdnFIcODjsZvQ` | `SHA256:L7q77RcXCi4gyGO2056WqJGFNWWvAvuFkmsOphP1MYQ` |
| RSA | `SHA256:5cXnOqPZe1f0iznyI1DGjQbUC8xi2ZVc2jpVj9whAlw` | `SHA256:88IBNePRW0pm/pRGIBJ3g/QOktiI/yW0pMMaw8iexqg` |

The mismatch is real: all three keys differ. Whether ziti was reinstalled on 2026-08-07 (its filesystems date from then), which would explain new keys, is Lee's call. Lee can compare these fingerprints on the console. nova's keys were not read.

---

## 3. Verified current state (read-only, 2026-11-23 23:09–23:20 UTC)

Methods: kubelet `stats/summary` and `configz` through the API proxy, and five short-lived privileged busybox `kubectl --context home debug node/...` pods using the cached `busybox:latest` (2.2 MB, `IfNotPresent`): three on ziti and two on nova. Each pod was deleted right after its logs were read. The seven older `node-debugger-nova-*` pods (32 days old) belong to someone else and were left alone. ziti was not under DiskPressure before each pod was created. There was no SSH to nova or ziti. No k3s credential file (`/etc/rancher/k3s/*`, `*.env`, agent keys and certificates) was opened; agent directory listings excluded credential-like names and only counted them. All `du` calls used `-x` and stayed inside the containerd root, `/var/lib/kubelet`, `/var/log` and the k3s data directory, so none crossed into an NFS or PVC mount.

### 3.1 ziti disk layout

One PNY CS900 500 GB SATA SSD. Plain ext4, no LVM, about 1 MiB unpartitioned (hop-3 prep journal). Debian 13, kernel 6.12.101, systemd 257, uptime 31 days, no reboot pending.

| Partition | Mount | Size | Available | `/etc/fstab` |
|---|---|---|---|---|
| sda2 | `/` | 31.2 GB | 25.4 GB | `UUID=d823e70e-… / ext4 errors=remount-ro 0 1` |
| sda3 | `/var` | **12,459,843,584 B** | **2,780,696,576 B** | `UUID=d894efb4-e132-42ee-8d2f-fb871417aea5 /var ext4 defaults 0 2` |
| sda4 | swap | 977 MiB | – | not in fstab; swap is active (`[SWAP]` in lsblk), presumably through systemd's GPT auto-discovery |
| sda5 | `/srv` | 445,098,295,296 B | **422,411,227,136 B** | `UUID=81eebe37-f8c8-48f1-bb90-e50cd4226c25 /srv ext4 defaults 0 2` |

`/srv` facts:
- It is a **local** partition on ziti's only SSD. It is not NFS, not shared and not a PVC.
- It is **persistent** through its fstab entry, with fsck pass 2 and no `nofail`.
- It is **empty**: only `lost+found` exists (2.1 MiB used), last checked at creation on 2026-08-07. Its ext4 features include `ext_attr`, its state is clean, and 27.67 M inodes are free.
- 5% of its blocks (22.7 GB) are reserved for root.
- **Nothing competes for it.** No hostPath, local PV or other workload in this repo points at `/srv` on ziti, and the cluster has **0 local-path PVs**. The local-path provisioner's default path is `/var/lib/rancher/k3s/storage`, which is unused.

Boot behaviour today: `/var` and `/srv` are both plain fstab mounts without `nofail`. If either fails fsck or mount at boot, systemd stops boot in emergency mode. The host is then down until someone fixes it at the console. The migration does not change this, but it adds a k3s dependency on `/srv` (§5).

### 3.2 What uses ziti's `/var` (single-filesystem `du -x`)

| Path | Size | Notes |
|---|---|---|
| `/var/lib/rancher/k3s/agent/containerd` | **7.4 GiB**: `io.containerd.snapshotter.v1.overlayfs` 5.4 G, `io.containerd.content.v1.content` 2.0 G, `metadata.v1.bolt` 7 M, `containerd.log` 4.8 M | 74,161 entries, of which 4,876 are files with more than one hard link. Mode 0700 root:root. |
| `/var/lib/rancher/k3s/data` | 572 MiB | k3s binaries. Three hashed directories plus `current`/`previous`. |
| `/var/lib/rancher/k3s/agent/{etc,pod-manifests}` and 13 credential-like files | about 20 KiB | **Not opened.** These are why Option B needs credential approval. |
| `/var/lib/kubelet` | 2.8 MiB on `/var` | 35 pod mounts sit under it (26 tmpfs, 9 NFS4). `-x` did not descend into them. |
| `/var/log` | 170 MiB | journal 79 M, pod logs 77 M. Kubelet rotation is 10 Mi × 5 per container. |

Kubelet's view from `stats/summary` (`node.fs`, `runtime.imageFs` and `containerFs` all report the same filesystem):

```
node.fs        capacity 12,459,843,584  used 9,023,619,072  available 2,780,971,008
runtime.imageFs capacity 12,459,843,584 used 5,729,607,680  available 2,780,971,008
```

Kubelet `configz` on ziti:
- `evictionHard` imagefs.available 5% and nodefs.available 5%; `evictionMinimumReclaim` 10% for both.
- `evictionPressureTransitionPeriod` 5m.
- Image GC thresholds 85/80%.
- `rootDir` is the default `/var/lib/kubelet`.

On 12.46 GB, eviction starts at about 623 MB available, which is too little for a cold pull and unpack of a large image.

### 3.3 Runtime configuration on ziti

- Unit `k3s-agent.service`: `ExecStart=/usr/local/bin/k3s agent '--node-ip=10.20.30.113'`, with the remaining configuration in `/etc/rancher/k3s/config.yaml` (not read). It has **`KillMode=process`**, `Delegate=yes`, `Restart=always` and `RestartSec=5s`, and no `RequiresMountsFor` or drop-ins.
- k3s's generated containerd configuration (hop-3 prep evidence) has `root = "/var/lib/rancher/k3s/agent/containerd"` and `state = "/run/k3s/containerd"` (tmpfs).
- Processes at inspection time: one `k3s-agent`, one `containerd`, **22 `containerd-shim`**. There were 80 mounts under `/run/k3s/containerd/` and 53 overlay mounts whose `lowerdir` points into the containerd root.
- **`KillMode=process` means `systemctl stop k3s-agent` kills only the k3s main process.** The k3s documentation says shims, and therefore pods, keep running after the service stops. Whether the `containerd` child survives is not proven here. **The plan therefore never assumes that stopping the service stops workloads or containerd** (§7.3 gates).
- The installed `/usr/local/bin/k3s-killall.sh` (dated 2026-08-07) does the following:
  - stops every `k3s*.service`;
  - `kill -9`s the process trees of shims that match `${K3S_DATA_DIR}/data/*/bin/containerd-shim`;
  - unmounts and removes mountpoints under `/run/k3s`, `/var/lib/kubelet/pods`, `/var/lib/kubelet/plugins` and `/run/netns/cni-`;
  - deletes the cni/flannel links (not used here; Cilium is the CNI) and `/var/lib/cni`;
  - removes KUBE-/CNI-/flannel iptables rules.

  It does **not** unmount `/var/lib/rancher/k3s` or anything under it, and it does not kill `containerd` by name. Newer upstream versions of this script may differ, so re-read it at execution time (gate P11).

### 3.4 nova precedent (proves the accounting works)

- `findmnt`: `/var/lib/rancher/k3s` ← `/dev/nvme0n1p7[/k3s]` ext4, bind, private propagation.
- `/etc/systemd/system/var-lib-rancher-k3s.mount`, enabled through `local-fs.target.wants`:
  ```
  [Unit]
  Description=K3s storage on nova srv filesystem
  RequiresMountsFor=/srv/k3s
  Before=k3s-agent.service
  [Mount]
  What=/srv/k3s
  Where=/var/lib/rancher/k3s
  Type=none
  Options=bind
  [Install]
  WantedBy=local-fs.target
  ```
- `/etc/systemd/system/k3s-agent.service.d/storage.conf`:
  ```
  [Unit]
  Requires=var-lib-rancher-k3s.mount
  After=var-lib-rancher-k3s.mount
  [Service]
  ExecStartPre=/usr/bin/mountpoint -q /var/lib/rancher/k3s
  ```
- nova's `stats/summary` shows kubelet keeps the two filesystems apart through the bind mount, on k3s v1.29.15+k3s1 with containerd 1.7.26:
  - `node.fs` (`/var`): capacity 23,275,380,736, available 21,145,960,448.
  - `runtime.imageFs` (`/srv`): capacity 923,869,966,336, available 806,828,765,184.

  This is the evidence that a bind-mounted containerd root gives kubelet a real, separately measured imagefs, rather than hiding bytes.
- None of this is in Git (B3).

### 3.5 Workloads on ziti now

| Pod | Kind | Requests | Notes |
|---|---|---|---|
| `database/postgres-pgbouncer-58cf6df4bf-ph2f6` | Deployment, **1 replica** | – | The only PgBouncer. Its L2 lease `database-postgres-pgbouncer` is held by ziti. |
| `database/pgo-7b9f68898-sbtlq` | Deployment, 1 | – | Operator only; does not affect the database data path. |
| `home/mosquitto-59c76949d4-vn9fs` | Deployment, 1 | – | MQTT for home automation (Zigbee/HA). PVC `mosquitto-config-v1` (NFS). |
| `kube-system/coredns-…-hrqgb` | Deployment, 2 (other on nova) | 100m/128Mi | |
| `networking/ingress-nginx-controller-…-gqnrn` | Deployment, 2 (other on nova), PDB minAvailable 1 | 100m/90Mi | Ingress L2 lease is on **nova**. |
| `kube-system/cilium-operator-…` | 1 | – | |
| `kube-system/csi-nfs-controller-…` | 1 | 5×10m/20Mi | New PVC provisioning only; existing mounts are unaffected. |
| `media/{sonarr,radarr,sabnzbd,filebrowser}` | 1 each | 250Mi each (filebrowser 0) | NFS PVCs. `filebrowser` is the known orphan with `prune: false`. |
| `monitoring/loki-chunks-cache-0` | StatefulSet | **500m / 9830Mi** | Does **not** fit on nova (below). Loki keeps working without it, more slowly. |
| DaemonSets: cilium, cilium-envoy, csi-nfs-node, intel-gpu-plugin, nfd-worker, node-exporter, loki-canary, vector-agent, netbird | – | – | Go down with the node and come back with it. |

Plus terminal Completed or ContainerStatusUnknown records from hop 2, kept as evidence.

**Capacity (no spare capacity assumed).** nova allocatable is 8 CPU / 16,268,448 Ki. Its requests are 2,930m / 6,231,076 Ki (38%), which leaves about 9.57 GiB of memory requests. Actual usage is 63% of memory (`kubectl top`). ziti's non-DaemonSet pods other than the Loki chunk cache request about 1.0 GiB, which fits. `loki-chunks-cache-0` (9,830 Mi) does not fit, so **a full drain is not viable**. enterprise and vega are cordoned and stay cordoned. Recheck this at execution (gate P8).

**Postgres.** The primary `postgres-postgres-6bb6-0` is 5/5 on **nova**. There is one primary and no standby. The latest pgBackRest backups to repo1 are full 2026-11-22 10:19Z and incremental 2026-11-23 10:01Z, both succeeded.

---

## 4. Options compared

| | **A. containerd root only (recommended)** | B. Whole k3s data-dir (nova's pattern) | C. k3s `data-dir` setting / containerd root override | D. Repartition: grow `/var` | E. GC tuning, pruning, image placement only |
|---|---|---|---|---|---|
| Source → target | `/var/lib/rancher/k3s/agent/containerd` → `/srv/k3s/agent/containerd`, bind-mounted back at the source path | `/var/lib/rancher/k3s` → `/srv/k3s`, bind-mounted back | Change `data-dir: /srv/k3s` in config.yaml, or a `config.toml.tmpl` with `root=` | Shrink `/srv` (sda5), move swap, grow sda3 | No move |
| Fixes imagefs headroom | Yes: imagefs is the full 445 GB of `/srv` | Yes | Yes | Yes, as large as chosen | **No.** A stopgap; pruning only defers the next cold pull |
| Credential files moved | **None** | **Yes.** 13 agent credential files plus `agent/etc`; needs explicit approval of source and target paths | Yes (data-dir) | No | No |
| Paths seen by k3s, SUC, killall, kubelet | Unchanged | Unchanged | **Changed.** killall and uninstall default to `/var/lib/rancher/k3s`, and drift appears at the next Ansible or installer run | Unchanged | Unchanged |
| Matches nova | Same mechanism (bind plus a fail-closed drop-in); narrower scope | Exact | No | No | – |
| `/var` after cleanup | About 10.2 GB free (k3s binaries 572 MiB, logs and emptyDirs remain) | About 10.8 GB free | Same as B | Depends on the resize | Unchanged, 2.8 GB |
| Copy size and outage | 7.4 GiB; about 10–20 minutes NotReady | 8.0 GiB; about the same, plus credential handling | Same, plus a config change | Offline partition surgery; tens of minutes to hours; console required | Kubelet restart only |
| Main risks | Bind mount missing at boot (mitigated by fail-closed units); copy fidelity (verified by manifest); stale killall versions (gate) | Same as A, plus credential copy and exposure, and the risk of a fresh empty data-dir if the mount fails without the guard | Configuration drift from upstream defaults; upgrade tooling assumptions | Highest risk of data loss; the SSD is the only disk | Leaves the incident's root cause in place |
| Verdict | **Recommend** | Acceptable only with explicit credential-path approval; no technical gain for this problem | Reject | Reject (unnecessary) | Not a fix; may complement A later |

Scope of A, explicitly:
- **Moved:** only the containerd root, holding image content, snapshots, writable layers, metadata and the containerd log.
- **Not moved:**
  - `/var/lib/kubelet`: nodefs, emptyDirs, PVC mountpoints. PVC data is NFS and is **never** copied.
  - `/var/log`.
  - `/var/lib/rancher/k3s/data`, `agent/etc` and agent credentials.
  - `/etc/rancher`.
  - `/run/k3s/containerd`: state on tmpfs, recreated at start.

Option A's target `/srv/k3s/agent/containerd` deliberately mirrors nova's layout. A later, separately approved Option B would only need to move the small remainder.

---

## 5. Boot and mount dependencies (fail-closed design)

Proposed files on ziti. In Git they would live in Ansible (§8). NOT EXECUTED.

`/etc/systemd/system/var-lib-rancher-k3s-agent-containerd.mount` (this name is what `systemd-escape -p --suffix=mount /var/lib/rancher/k3s/agent/containerd` returned on ziti):
```
[Unit]
Description=k3s containerd root on ziti /srv
RequiresMountsFor=/srv/k3s/agent/containerd
Before=k3s-agent.service

[Mount]
What=/srv/k3s/agent/containerd
Where=/var/lib/rancher/k3s/agent/containerd
Type=none
Options=bind

[Install]
WantedBy=local-fs.target
```

`/etc/systemd/system/k3s-agent.service.d/runtime-storage.conf`:
```
[Unit]
Requires=var-lib-rancher-k3s-agent-containerd.mount
After=var-lib-rancher-k3s-agent-containerd.mount

[Service]
ExecStartPre=/usr/bin/mountpoint -q /var/lib/rancher/k3s/agent/containerd
ExecStartPre=/bin/sh -c 'test "$(/usr/bin/findmnt -n -o SOURCE --target /var/lib/rancher/k3s/agent/containerd)" = "/dev/sda5[/k3s/agent/containerd]"'
```

The second check requires exactly the expected source and fails closed on any mismatch or failed query; there is no mountpoint-only fallback. Read-only probes on 2026-11-24 confirmed nova’s `findmnt -n -o SOURCE --target /var/lib/rancher/k3s` returns `/dev/nvme0n1p7[/k3s]`, while ziti’s `/srv` source is `/dev/sda5`. This pins the device-plus-bracketed-subdirectory format to `/dev/sda5[/k3s/agent/containerd]` for ziti. Recheck the actual bind source at switchover (§7.5); stop if it differs. The bind was not created during prerequisite work.

Behaviour:
- `/srv` missing at boot: fstab already sends boot to emergency mode before k3s starts. **Fail-closed, console required.** This is unchanged from today.
- Bind mount fails, or someone unmounts it: `Requires=` stops k3s-agent, and `ExecStartPre` refuses to start it. ziti stays NotReady and **does not** start containerd on an empty root on `/var`. An empty root would mean re-pulling about 2.2 GB of images onto `/var` and recreating the original DiskPressure. `Restart=always` retries every 5 s; this is harmless and visible in the journal.
- SUC agent upgrades replace `/usr/local/bin/k3s` and restart the unit. The mount unit and drop-in persist. A rerun of the Ansible k3s role could rewrite `k3s-agent.service`, but drop-ins in `k3s-agent.service.d/` survive that. Ansible should own them anyway (§8).
- `k3s-killall.sh` as installed does not touch the bind mount (§3.3). If an installer refresh ever changes it to unmount and `rm -rf` paths under `/var/lib/rancher/k3s`, it would unmount the bind and delete the **empty** mountpoint directory. The `/srv` data stays intact, and k3s then refuses to start until systemd remounts. This is why the old data is **renamed aside, never left shadowed under the mountpoint** (§7.5).

---

## 6. Outage and workload handling

There is no drain, because nova cannot hold `loki-chunks-cache-0`, and no spare capacity is assumed. Instead:

1. **Cordon ziti** (NOT EXECUTED: `kubectl --context home cordon ziti`). This prevents new pods landing on ziti and keeps the migration from racing a reschedule.
2. **Move only the pods whose absence would hurt for the whole window and that fit on nova.** Delete them one at a time; each owning controller recreates the pod on nova. Wait for Ready on nova before the next one:
   - `database/postgres-pgbouncer` (the only replica). DB clients reconnect within seconds. No PDB.
   - `home/mosquitto` (only replica). MQTT clients (zigbee2mqtt, Home Assistant) reconnect.
   - `kube-system/coredns` (ziti replica). If the anti-affinity or spread keeps the replacement Pending, one replica on nova serves; accept it. Never delete the nova replica.
   - `networking/ingress-nginx-controller` (ziti replica). The PDB allows 1. The topology spread uses `nodeTaintsPolicy: Honor`, so a cordoned ziti is not a domain and the replacement fits on nova. The ingress L2 lease is already on nova.
   - `database/pgo`, `kube-system/cilium-operator`, `kube-system/csi-nfs-controller`. Singletons whose outage blocks reconciliation, IPAM or new PVCs.
   - Optional, Lee decides: `media/sonarr`, `radarr`, `sabnzbd`. Otherwise they are down for the window.
   - **Left on ziti and down for the window:** `loki-chunks-cache-0` (does not fit), `media/filebrowser` (the orphan; do not touch it), and every DaemonSet pod.

   The deletions use `kubectl delete pod` without `--force` or grace overrides. PgBouncer goes first, then the SQL check runs before anything else. The primary is never touched.
3. When ziti's runtime stops, Cilium L2 leases held by ziti fail over to another node after the lease expires, about 15 s by default. These include `database-postgres-primary`, `database-postgres-pgbouncer`, `kube-system-kube-dns`, `networking-cloudflared`, `media-sonarr` and a few monitoring and Flux services (§3.5 source). Expect a short blip for LAN clients of those LoadBalancer IPs. Ingress (`10.20.30.200`) is on nova and unaffected.
4. ziti goes NotReady about 40 s after kubelet stops. The pod `not-ready`/`unreachable` NoExecute tolerations default to 300 s. Pods still bound to ziti are marked for eviction after 5 minutes, so the window will probably exceed that. ReplicaSet pods could then be recreated on nova, or stay Pending if they do not fit. That is acceptable and expected; terminating records clear when ziti returns.

Downtime estimate:

| Phase | Estimate | Who is affected |
|---|---|---|
| Moving pods to nova | about 10–60 s each, staggered | PgBouncer clients reconnect; MQTT reconnect; nothing else visible |
| Runtime stop, quiesce, gates | about 2–5 min | Anything left on ziti (Loki chunk cache, filebrowser, optionally the *arr apps) |
| Copy of 7.4 GiB / 74k entries, same SSD | about 2–6 min (estimate; SATA same-disk read+write, many small files) | same |
| Manifest verification (two checksum passes) | about 3–6 min | same |
| Mount, start, kubelet Ready, pods restarted from cached images (no pulls) | about 2–5 min | same |
| **ziti NotReady total** | **about 10–20 min** | |
| Watch, uncordon, gates, ≥10 min stable watch | about 30–45 min | none if healthy |
| **Window to reserve** | **90 min, supervised** | |

The control plane (enterprise, cordoned), the API, the VIP, etcd and the Postgres primary on nova are not in the blast radius.

---

## 7. Runbook (ALL COMMANDS NOT EXECUTED)

Conventions:
- `H$` means a root shell on ziti through the **verified out-of-band channel** from B1 (`ssh ziti` as `setup` with `sudo -n`, or the physical console). It never means a `kubectl debug` pod.
- `K$` means devbox with `kubectl --context home`.
- `DATE` is the window date, e.g. `20261124`.
- Every step has a stop condition. **When in doubt, stop and go to §9 rollback. Do not improvise.**

### 7.1 Preflight gates (the day before and again at T−30 min; any failure is a NO-GO)

| # | Gate | Pass condition |
|---|---|---|
| P1 | B1 resolved | `ssh ziti sudo -n true` succeeds against a **Lee-verified** host key; console fallback identified. |
| P2 | B2 resolved | rigel is online with route `10.20.30.0/24` in devbox `netbird status`, or Lee is operating from the LAN. |
| P3 | B3 resolved | Ansible change merged; `--check --diff --limit nova` shows zero changes. |
| P4 | Cluster health | All 4 nodes Ready; enterprise and vega cordoned; 44/44 ks, 34/34 hr; 52/52 targets; no unhealthy active pods; every DaemonSet full; 29/29 PVCs Bound; API readyz, VIP and direct livez, etcd health; 0 SUC Jobs and both Plans Complete. **No hop 3 or Plan change in flight.** |
| P5 | ziti state | No pressure conditions; the `stats/summary` baseline is recorded (§3.2 numbers). |
| P6 | Fresh backups | (a) A fresh etcd snapshot, offloaded with the existing CronJob and with sha256 equal on enterprise and titan (the cluster-state record before the window). (b) A pgBackRest backup completed successfully within 24 h; if the newest is older, Lee decides on a manual backup first. (c) No copy of node credentials or PVC contents goes into evidence. |
| P7 | Postgres | Primary on nova 5/5; `SELECT pg_is_in_recovery(), current_setting('transaction_read_only')` returns `f, off`; Patroni holds the leader lock. Record its pod UID: it must not change. |
| P8 | Capacity | nova allocatable minus requests is enough for the pods listed in §6.2 (about 1.1 GiB memory). nova has no pressure. |
| P9 | `/srv` | `findmnt /srv` shows `/dev/sda5` ext4 rw; `ls -A /srv` lists only `lost+found` (or only this plan's empty `/srv/k3s`); available ≥ 400 GB; `/srv/k3s` does not exist or is empty. |
| P10 | Tools | `command -v python3 cp tar findmnt mountpoint lsof systemd-escape` all succeed. If python3 is missing, either install `rsync attr` (only with explicit approval) or stop. |
| P11 | killall script | Re-read `/usr/local/bin/k3s-killall.sh` and confirm it still matches §3.3: it does not unmount `/var/lib/rancher/k3s`. |
| P12 | Drop-in source check | Nova’s read-only probe confirmed `/dev/nvme0n1p7[/k3s]`; ziti’s `/srv` source is `/dev/sda5`. §5 now requires exactly `/dev/sda5[/k3s/agent/containerd]`. Verify the actual bind after §7.5 and stop on any mismatch; never fall back to bare `mountpoint -q`. |
| P13 | Watchers | Bounded watchers running: node conditions and cordons; API/VIP; Postgres SQL (`unknown` during transport loss is not the same as read-only); ingress HTTP (hass, esphome, photos, sync, grafana); DNS from a test pod; events for Evicted, ImageGCFailed and FreeDiskSpaceFailed. |
| P14 | Announcement | A quiet time is announced (load-bearing infrastructure rule). Lee is reachable. |

### 7.2 Workload moves (§6)

```
K$ kubectl --context home cordon ziti
K$ kubectl --context home -n database delete pod <pgbouncer pod on ziti>   # wait Ready on nova, SQL check via pgbouncer
K$ kubectl --context home -n home delete pod <mosquitto pod on ziti>       # wait Ready on nova
K$ kubectl --context home -n kube-system delete pod <coredns pod on ziti>  # wait Ready (or accept Pending), DNS test
K$ kubectl --context home -n networking delete pod <ingress pod on ziti>   # wait Ready on nova, ingress 200
K$ kubectl --context home -n database delete pod <pgo pod>
K$ kubectl --context home -n kube-system delete pod <cilium-operator pod> <csi-nfs-controller pod>
# optional (Lee): sonarr, radarr, sabnzbd
```

Stop if any replacement is not Ready on nova within 5 minutes (except a Pending CoreDNS second replica), if the Postgres primary UID changes or SQL fails twice, or if ingress or DNS fail. Then uncordon and abort; nothing on the host has changed yet.

### 7.3 Quiesce the runtime on ziti

```
H$ systemctl stop k3s-agent
H$ /usr/local/bin/k3s-killall.sh            # kills shim trees, unmounts /run/k3s, kubelet pod/plugin mounts, cni netns
```

Then gate Q. **All** of these must hold; do not assume the stop did it.

```
H$ systemctl is-active k3s-agent                          # inactive
H$ pgrep -a -x containerd; pgrep -af containerd-shim; pgrep -af 'k3s (agent|server)'   # all empty
H$ grep -c ' /run/k3s/' /proc/mounts                      # 0
H$ grep -c 'rancher/k3s/agent/containerd' /proc/mounts    # 0 (no overlay lowerdir/upperdir references)
H$ grep -c ' /var/lib/kubelet/pods/' /proc/mounts         # 0 (NFS PVC mounts gone; data stays on titan)
H$ lsof +D /var/lib/rancher/k3s/agent/containerd 2>/dev/null | wc -l   # 0 (bounded: 74k entries, single fs)
```

If `containerd` survives (possible with `KillMode=process`), send `kill -TERM <pid>`, wait 30 s and re-check. **Never** `kill -9` containerd with the bolt database open unless Lee approves; if it does not exit, stop and roll back by simply restarting k3s-agent. If any PVC mount remains, stop. Do not unmount NFS by hand; restart k3s-agent and investigate.

### 7.4 Consistent copy (preserves ownership, mode, timestamps, hard links, xattrs, devices, sparseness)

```
H$ install -d -m 0700 -o root -g root /srv/k3s /srv/k3s/agent
H$ install -d -m 0700 -o root -g root /srv/k3s/agent/containerd
H$ test -z "$(ls -A /srv/k3s/agent/containerd)"            # must be empty
H$ cp -a --preserve=xattr -x --sparse=auto \
      /var/lib/rancher/k3s/agent/containerd/. /srv/k3s/agent/containerd/
```

- `-a` gives `-dR --preserve=all`. Hard links are preserved within the copy set. Overlay whiteout character devices (0:0) and `trusted.overlay.*` xattrs are copied when running as root.
- The explicit `--preserve=xattr` makes an xattr failure a **non-zero exit** instead of a silently ignored one. A non-zero exit means stop.
- `-x` stays on `/var`; no mount can be under the source by now (gate Q).
- **Exclusions:** nothing inside the containerd root is excluded. Nothing outside it is copied: not `/var/lib/kubelet` (PVC mountpoints), not agent credentials, not `/etc/rancher`, not `/run/k3s`.

Manifest verification (python3, read-only; prints only paths and metadata, no contents):

```
H$ cat > /root/ctrd-manifest.py <<'EOF'
import os, sys, stat, hashlib
root = sys.argv[1]; h = hashlib.sha256(); n = 0
for dp, dns, fns in os.walk(root):
    dns.sort(); fns.sort()
    for name in [''] + dns + fns:
        p = os.path.join(dp, name) if name else dp
        st = os.lstat(p); rel = os.path.relpath(p, root)
        xa = sorted((k, os.getxattr(p, k, follow_symlinks=False)) for k in os.listxattr(p, follow_symlinks=False))
        line = [rel, oct(st.st_mode), st.st_uid, st.st_gid, st.st_nlink, st.st_rdev, int(st.st_mtime), repr(xa)]
        if stat.S_ISREG(st.st_mode):
            line.append(st.st_size); f = hashlib.sha256()
            with open(p, 'rb') as fh:
                for b in iter(lambda: fh.read(1 << 20), b''): f.update(b)
            line.append(f.hexdigest())
        elif stat.S_ISLNK(st.st_mode):
            line.append(os.readlink(p))
        h.update(repr(line).encode()); n += 1
print(n, h.hexdigest())
EOF
H$ python3 /root/ctrd-manifest.py /var/lib/rancher/k3s/agent/containerd
H$ python3 /root/ctrd-manifest.py /srv/k3s/agent/containerd
```

Pass condition: identical entry counts (about 74,161) and identical digests. Directory mtimes are included; if they differ only on the top directory (created by `install -d`), the operator must prove that by rerunning with the top entry excluded, not by waving it through. A hard-link check is also covered by `st_nlink`. Any mismatch means stop: run `rm -rf --one-file-system /srv/k3s/agent/containerd` (it is the new copy and nothing references it yet) and roll back per §9 R0.

### 7.5 Switch over (old data renamed aside, never shadowed)

```
H$ mv /var/lib/rancher/k3s/agent/containerd /var/lib/rancher/k3s/agent/containerd.pre-srv-DATE    # same fs: instant rename
H$ install -d -m 0700 -o root -g root /var/lib/rancher/k3s/agent/containerd                       # empty mountpoint, same mode
H$ install -m 0644 <from Git> /etc/systemd/system/var-lib-rancher-k3s-agent-containerd.mount
H$ install -d /etc/systemd/system/k3s-agent.service.d
H$ install -m 0644 <from Git> /etc/systemd/system/k3s-agent.service.d/runtime-storage.conf
H$ systemctl daemon-reload
H$ systemctl enable --now var-lib-rancher-k3s-agent-containerd.mount
```

Mountpoint verification (all must pass before k3s starts):

```
H$ mountpoint /var/lib/rancher/k3s/agent/containerd
H$ findmnt -o TARGET,SOURCE,FSTYPE,OPTIONS /var/lib/rancher/k3s/agent/containerd    # SOURCE /dev/sda5[/k3s/agent/containerd], ext4, rw
H$ stat -f -c '%b %S' /var/lib/rancher/k3s/agent/containerd                         # block count matches /srv, not /var
H$ ls /var/lib/rancher/k3s/agent/containerd/io.containerd.metadata.v1.bolt          # meta.db present
H$ systemctl show k3s-agent -p Requires -p After | grep -c var-lib-rancher-k3s-agent-containerd.mount   # >=1
H$ systemctl is-enabled var-lib-rancher-k3s-agent-containerd.mount                  # enabled
```

### 7.6 Start and validate

```
H$ systemctl start k3s-agent
H$ journalctl -u k3s-agent -n 200 --no-pager | grep -i -E 'containerd|imagefs|evict|error' | tail -50
```

Validation, in order. The node stays **cordoned** until all of these pass:

1. **Runtime.**
   - `pgrep -x containerd` running; `crictl info` ok.
   - `crictl images` lists the same about 29 images as before (no pulls).
   - `crictl imagefsinfo`: mountpoint under `/var/lib/rancher/k3s/agent/containerd/io.containerd.snapshotter.v1.overlayfs`.
   - The kubelet journal has no `invalid capacity 0 on image filesystem` and no `ImageGCFailed`.
2. **Kubelet stats.**
   - `K$ kubectl --context home get --raw /api/v1/nodes/ziti/proxy/stats/summary | jq '.node.fs, .node.runtime.imageFs'`
   - Expect `node.fs.capacityBytes = 12,459,843,584` (still `/var`), and `imageFs.capacityBytes ≈ 445,098,295,296` (`/srv`) with available ≈ 414–422 GB.
   - **Pass/fail:** a reported imagefs capacity of about 12.46 GB means the bind is not in effect, so stop and roll back. nodefs available should be about 2.78 GB until cleanup; it must stay ≥ 2 GB.
3. **Node.**
   - ziti Ready, v1.29.15+k3s1, containerd 1.7.26-k3s1.
   - No Disk, Memory or PID pressure; no taints except the cordon.
   - Every DaemonSet pod on ziti Ready, with **no image pulls** in events: cilium, cilium-envoy, csi-nfs-node, gpu-plugin, nfd, node-exporter, loki-canary, vector-agent and netbird. NetBird `:latest` may re-pull because `latest` defaults to `Always`; that pull is about 22 MB and is acceptable, but note it.
4. **Networking.**
   - Cilium agent on ziti healthy (`cilium status` in-pod, read-only).
   - DNS from a test pod on ziti and on nova resolves `kubernetes.default.svc.cluster.local` → 10.43.0.1 and an external name.
5. **NFS.**
   - `csi-nfs-node` on ziti Ready.
   - After uncordon (step 8), at least one NFS-backed pod scheduled on ziti mounts its PVC. Do not force this; if nothing lands naturally, check the CSI node registration only.
6. **Apps.**
   - 44/44 ks, 34/34 hr, every Helm revision unchanged; 52/52 targets.
   - Ingress `10.20.30.200`: hass, esphome, photos, sync return 200; grafana `/login` 200; Jellyfin `/health` 200.
   - MQTT consumers connected (zigbee2mqtt and HA logs).
7. **Postgres.** Primary pod UID unchanged, still on nova, 5/5; SQL `f/off`; PgBouncer Ready; birdweather polls succeeding.
8. **Uncordon**, NOT EXECUTED: `K$ kubectl --context home uncordon ziti`. Pods that were moved stay on nova; **do not rebalance** as part of this operation.
9. **Stable watch ≥ 10 minutes after uncordon.** No new evictions, restarts, pressure, ImageGCFailed, SQL failures, target losses or ingress/DNS failures. Any regression restarts the clock or triggers rollback per §9.
10. **Post-change etcd snapshot,** offloaded with the sha256 check.

Record the evidence (stats JSON, findmnt output, manifest digests, events) under `/home/dev/orca/artifacts/ziti-srv-migration-DATE/`. This means metadata only: no credentials and no PVC contents.

---

## 8. Durable ownership (Git/Ansible; proposed, not committed here)

There is no Flux change: this is host configuration, and the repo's host layer is Ansible. Proposed change, to be reviewed in its own PR before the window:

- `ansible/playbooks/node-runtime-storage.yaml`: a standalone playbook, **never** part of `cluster-prepare.yaml` or `cluster-installation.yaml`. The latter must not be run against the live cluster; see the CoreDNS caveat in the prerequisites journal. Hosts are `worker`, and each run uses an explicit `--limit`.
- Variables in `ansible/inventory/group_vars/worker/runtime-storage.yaml` (not secret):
  ```yaml
  k3s_runtime_storage:
    nova: { what: /srv/k3s, where: /var/lib/rancher/k3s, unit: var-lib-rancher-k3s.mount, dropin: storage.conf }          # adopt existing
    ziti: { what: /srv/k3s/agent/containerd, where: /var/lib/rancher/k3s/agent/containerd,
            unit: var-lib-rancher-k3s-agent-containerd.mount, dropin: runtime-storage.conf }
  ```
- Templates for the mount unit and the drop-in, byte-identical to §3.4 for nova and §5 for ziti.
- Tasks:
  - template the unit and drop-in;
  - assert `findmnt` SOURCE and that `/srv` is mounted;
  - **no** `daemon-reload` or start handlers, so that writing files never restarts k3s;
  - a `--check --diff` mode is the expected routine use.
- Order:
  1. Merge.
  2. `--check --diff --limit nova` → zero changes (proves adoption).
  3. In the window, place ziti's files exactly from the rendered templates (§7.5).
  4. After the window, `--check --diff --limit ziti` → zero changes.

  Each Ansible run needs SSH, so B1 applies.
- Add the `/srv` dependency to `docs/` (this plan) and the hop-3 storage gate in draft PR #3 when execution is approved. This plan does not edit PR #3.

---

## 9. Rollback (preserves old data and config; nothing is deleted)

| Stage reached | Rollback |
|---|---|
| **R0**: before `mv` (§7.4 failed: copy or manifest mismatch) | Nothing on `/var` has changed. `H$ systemctl start k3s-agent`, then validate as in §7.6 (runtime, stats, apps) and uncordon. The partial `/srv/k3s/agent/containerd` may be removed with `rm -rf --one-file-system` only after confirming with `findmnt -T` that it is on `/dev/sda5` and not mounted anywhere. |
| **R1**: after `mv`, before or after k3s start (mount wrong, stats wrong, apps failing) | 1. `systemctl stop k3s-agent`; `k3s-killall.sh`; gate Q (§7.3). 2. `systemctl disable --now var-lib-rancher-k3s-agent-containerd.mount`; `mountpoint -q /var/lib/rancher/k3s/agent/containerd` must **fail**. 3. `rm /etc/systemd/system/k3s-agent.service.d/runtime-storage.conf` and `/etc/systemd/system/var-lib-rancher-k3s-agent-containerd.mount`; `systemctl daemon-reload`. 4. `rmdir /var/lib/rancher/k3s/agent/containerd` (must be empty; if not, stop). 5. `mv /var/lib/rancher/k3s/agent/containerd.pre-srv-DATE /var/lib/rancher/k3s/agent/containerd`. 6. `systemctl start k3s-agent`, then validate (imagefs back at about 12.46 GB, which is expected), uncordon, and run the 10-minute watch. **Keep `/srv/k3s` as it is** for diagnosis. Images pulled after the switch are lost and get pulled again; that is acceptable, but watch `/var` headroom, which is the original problem. |
| **R2**: host does not boot or `/srv` fails fsck | Physical console. Boot to emergency mode, inspect `/srv` (`journalctl -b`, `fsck -n`). To bring the node up on old data, remove or comment the unit and drop-in, then do R1 steps 4–6. Never run `fsck -y` without Lee. |
| Cluster-level | No Flux, SUC or Kubernetes object changes are made, except the cordon and pod deletions; uncordon reverses the cordon. etcd restore is **not** a rollback path for this node-local operation. |

Config rollback in Git: revert the Ansible PR only after the host has been rolled back, so that Git matches the host.

---

## 10. Cleanup later (separately approved; avoids duplicate disk use)

While both copies exist, about 7.4 GiB is duplicated: the old tree on `/var` and the live tree on `/srv`. `/var` has about 2.78 GB free in that state, which is above the 623 MB eviction threshold but not comfortable.

Proposed cleanup approval, after **at least 24 h** stable and before hop 3 so that `/var` gets its headroom back:

```
H$ findmnt -T /var/lib/rancher/k3s/agent/containerd.pre-srv-DATE -o SOURCE   # must be /dev/sda3 (on /var), NOT a mountpoint
H$ mountpoint -q /var/lib/rancher/k3s/agent/containerd && findmnt -n -o SOURCE /var/lib/rancher/k3s/agent/containerd   # live = /dev/sda5[...]
H$ rm -rf --one-file-system /var/lib/rancher/k3s/agent/containerd.pre-srv-DATE
```

After the cleanup, `/var` should have about 10.2 GB available. Delete `/root/ctrd-manifest.py`.

Until the cleanup is approved, **the old data stays**. Once it is gone, rollback means an image re-pull onto `/var`, which brings back the original risk. Consider GC thresholds (Option E) only afterwards, as a separate change.

---

## 11. Approval scope Lee would need to grant (exact)

To execute, Lee must explicitly approve **all** of the following. Each one is a separate item; approving some does not imply the others.

1. **Prerequisites:**
   - B1: Lee's decision on ziti's host key, with any `known_hosts` update done or instructed by Lee.
   - B2: rigel availability, or Lee operating from the LAN.
   - B3: merge of the Ansible PR and check mode against nova.
   - B4: the copy tooling choice (cp plus python3 manifest by default; optional `apt install rsync attr`).
2. **Window:** date and time, announcement, Lee reachable, physical access available on standby.
3. **Cluster actions:** cordon and uncordon ziti; deleting the specific pods listed in §6.2, one at a time; fresh etcd snapshot and offload Job before and after.
4. **Host actions on ziti only**, through the verified channel:
   - stop `k3s-agent`; run `k3s-killall.sh`; `kill -TERM` a surviving containerd;
   - create `/srv/k3s/agent/containerd` (0700 root);
   - copy **source `/var/lib/rancher/k3s/agent/containerd` → target `/srv/k3s/agent/containerd`**;
   - rename the source to `containerd.pre-srv-DATE`;
   - install the two systemd files from Git; `daemon-reload`; enable the mount unit; start `k3s-agent`;
   - roll back per §9 if a gate fails.
5. **Explicitly not approved by the above:**
   - Option B (any copy of `/var/lib/rancher/k3s` outside `agent/containerd`, including agent credentials). That would need its own source/target-path approval.
   - Any `/etc/rancher` change; any repartitioning or fsck repair.
   - Deleting the old data (§10, separate).
   - GC or eviction tuning; any change on nova or enterprise.
   - Rebalancing moved pods back; Flux changes; hop 3.

---

## 12. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Losing host access mid-operation | Medium, until B1 and B2 are fixed | High: ziti down until a console visit | B1, B2, console on standby; the copy is non-destructive, so a power cycle plus R1 recovers |
| Incomplete copy (xattrs, hard links, whiteouts) | Low | Containers fail or images are corrupt | Explicit `--preserve=xattr`, gate Q, full manifest digest; R1 |
| containerd or shims still running during the copy | Medium (`KillMode=process`) | Inconsistent bolt database or snapshots | Gate Q process, mount and `lsof` checks; never assume |
| Bind mount missing at boot | Low | ziti NotReady (fail-closed) | `Requires` plus `ExecStartPre`; Ansible ownership; documented R2 |
| Imagefs accounting not split | Low (nova proves it works) | The fix would hide bytes | §7.6 step 2 gate; roll back if capacity reads 12.46 GB |
| Postgres disruption | Low (primary untouched) | Client errors | PgBouncer move first plus a SQL gate; primary UID check |
| L2 LB blip for services whose lease ziti holds | High (expected) | Seconds | Announce; watch |
| `/srv` and `/var` on the same SSD | Existing | Both fail together | Unchanged risk; SMART PASSED 2026-11-23, 89% life left |
| Shared-SSD I/O during the copy affects other tenants | None | – | No other tenants: ziti's workloads are already stopped |

---

## 13. Hand-run actions while writing this plan (all read-only, plus own debug pods)

- `kubectl --context home get/describe/top`, `get --raw .../stats/summary` and `.../configz` (ziti, nova), lease and PostgresCluster reads.
- Debug pods created and deleted by this task: `node-debugger-ziti-whm9h`, `node-debugger-nova-sjdsg`, `node-debugger-nova-s9rzh`, `node-debugger-ziti-q9px6`, `node-debugger-ziti-4ppt5`. Their commands were read-only: fstab, findmnt, blkid, `ls`, `du -x`, `/proc/mounts` counts, `ps` counts, unit files without credentials, the killall script, public host-key fingerprints, `command -v`, `tune2fs -l`, and `sudo -l -U setup`.
- `kubectl --context octo -n devbox exec <devbox pod> -c netbird-orca -- netbird status -d` (read-only; only peer names and networks kept).
- `ssh-keygen -F` on devbox `known_hosts` (read-only).
- No SSH to nova or ziti; no `known_hosts` edit; no k3s credential files read; no images pulled except the cached busybox.
