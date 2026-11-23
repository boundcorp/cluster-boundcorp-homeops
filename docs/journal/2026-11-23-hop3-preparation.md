# Hop 3 preparation: disks, Jellyfin GPU and readiness (2026-11-23)

Scope: Lee's 20:53/20:54 UTC requests, clarified by the coordinator after the dispatch text was truncated. Disk inspection only, one GitOps Jellyfin rollout, an offloaded etcd snapshot, and readiness documentation. No hop 3 or k3s/SUC version change is authorized. Enterprise and vega remain cordoned. The hop-2 incident and GPU failure are recorded in `2026-11-23-k3s-hop2.md`; whether the GPU permission defect predates hop 2 remains unknown.

## 1. Ziti storage inspection (20:58–21:03 UTC)

`~/ENVIRONMENT.md` was read first. `envcheck` was absent from PATH; `/home/dev/bin/envcheck` passed home/API and documented SSH routes. No SSH to nova/ziti, host-key edits, host writes, pruning or storage changes. Read-only node-exporter mounts, kubelet stats/configz and temporary privileged BusyBox node-debug pods were used. Each debug pod was deleted immediately after inspection; ziti DiskPressure was False before creating either ziti pod.

| Host | Physical storage and layout | Effective k3s/image filesystem |
|---|---|---|
| ziti | One PNY CS900 500GB SATA SSD (465.8 GiB); sda1 EFI 976 MiB, sda2 root 29.7 GiB, sda3 `/var` 11.9 GiB, sda4 active swap 977 MiB, sda5 `/srv` 422.2 GiB. Plain ext4; no LVM tools or mapper volumes. No unused physical disks/partitions (zero-sized nbd devices are virtual). | `/var` filesystem capacity 12,459,843,584 bytes; available 2,786,013,184 bytes (2.59 GiB), about 9.02 GB used. `/` has about 24 GiB free; `/srv` has about 394 GiB available, 2.1 MiB used. |
| nova | One KingFast NVMe, 953.9 GiB; root 55.5 GiB, `/var` 22.2 GiB, swap 976 MiB, `/srv` 875.2 GiB. No LVM tools. | **Already bind-mounts `/srv/k3s` at `/var/lib/rancher/k3s`**. Imagefs capacity 923,869,966,336 bytes; available 806,831,136,768 bytes. `/var` independently has 21.16 GB available. |
| enterprise | Patriot P210 128GB SATA, root partition 110.3 GiB, EFI 512 MiB; `/var` and `/srv` are on root. No LVM tools. | Root about 109 GiB filesystem, 11 GiB used, 92 GiB available. Read-only sfdisk reports ~976 MiB free using the current GPT and warns its backup GPT/PMBR reflect the old disk size. No partition-table repair attempted. |

Ziti `sfdisk --list-free` proves **only 1,056,256 bytes (~1 MiB) unpartitioned**, after sda5. `/var` is immediately followed by swap and `/srv`; it cannot simply grow into contiguous free space. Its problem is partition allocation, not lack of total SSD capacity. Nova's live bind mount provides a directly relevant precedent for using the large `/srv` partition.

K3s uses the default `/var/lib/rancher/k3s`; generated containerd config explicitly has `root = "/var/lib/rancher/k3s/agent/containerd"`, `state = "/run/k3s/containerd"`. Ziti has no separate bind mount there. `du -x` reports about 8.0 GiB under `/var/lib/rancher`: 7.4 GiB containerd and 572 MiB k3s binaries/data. Kubelet CRI imagefs accounting reports 5.73 GB, which is a different accounting scope from on-disk `du` and compressed image sizes. Logs total about 172 MiB (journal 79 MiB, pod logs 77 MiB); cache 101 MiB. Inodes are plentiful (~700k free of 780k). This is runtime/image capacity, not a multi-GB log runaway.

29 CRI images report ~2.204 GB summed image sizes (shared/compressed layers are not unique filesystem occupancy). Largest: Jellyfin 751 MB, Cilium 255 MB, PGO operator 199 MB, ingress 116 MB, radarr 86 MB, Vector/sonarr ~81 MB each. Jellyfin's cached image remains on ziti after hop 2; it was not pruned. Configz on both workers: image-GC high/low **85/80%**; hard eviction imagefs/nodefs available **5%**; minimum reclaim **10%**; pressure transition **5 min**. At ziti's size, these are ~1.87/2.49 GB available for high/low GC and only ~0.623 GB at eviction: too little margin for concurrent upgrade/cold-unpack churn.

SMART on ziti: **PASSED**, 33 C, CRC/PHY errors 0, raw read errors 0, reported life left 89, 50,693 power-on hours, 170 unsafe shutdowns, vendor bad-block field 0/22. These are a read-only health snapshot, not a guarantee against failure. No tests were started. Nova's actual NVMe path `/dev/nvme0n1` has zero unpartitioned bytes; SMART PASSED, critical warning 0, 38 C, percentage used 4%, available spare 78%, media/data-integrity errors and error-log entries 0. The initial generic `/dev/sda` probe failed because nova has no SATA disk; the correct path was then inspected. Its `/srv/k3s` bind mount was confirmed with findmnt.

### Options for Lee (none implemented)

| Option | Cost / risk | Expected interruption / physical presence |
|---|---|---|
| Grow an LV or `/var` into free space | No LV or useful unallocated space exists. Rebuilding/moving swap and `/srv` can create room, but partition/filesystem surgery is the highest-risk option and unnecessary here. | Offline/recovery window, likely tens of minutes to hours; recovery console/physical access advisable. |
| **Use existing `/srv` for k3s, following nova's bind-mount pattern (recommended)** | No hardware cost; controlled copy preserving ownership/xattrs/hardlinks, quiesced runtime, persistent mount ordering, and verification/rollback required. Keep the ordinary k3s path so SUC and containerd paths still agree. Do not copy a live runtime or leave both trees active. | Reserve a supervised 15–30+ min worker maintenance window; k3s/container workloads may need interruption. No physical visit expected if a separately approved debug/host-management route is reliable; verified SSH/console fallback strongly recommended. |
| Add/replace SSD | Hardware cost; still requires migration/layout correction. One SATA disk is observed, with no unused device; do not assume a spare NVMe slot from software inventory. Larger SSD alone does not enlarge `/var`. | Lee physically installs hardware; planned shutdown/migration, typically 30–60+ min. Confirm exact chassis/slot support before buying. |
| Earlier GC thresholds plus `crictl rmi --prune` | No hardware cost; can remove unused images but leaves active-image floor and future cold-pull risk. A proposed 70/60% policy would reclaim sooner, but measured capacity gates still govern. No tuning/prune was run. | Kubelet config/restart requires separate approval; prune causes future downloads. No physical visit expected. A stopgap, not capacity resolution. |
| Keep large images off ziti | App nodeAffinity is low risk but reduces failover options. Do this for Jellyfin in part 2; other large apps need individual review. A broad taint could evict/block other workloads and is not authorized. | Jellyfin's one authorized rollout only; no physical visit. Does not recover its already cached image or satisfy storage headroom by itself. |

The recommended migration is a **separate Lee-approved infrastructure operation**, with a concrete runbook and safe workload handling before execution. Current 2.79 GB free fails the proposed **5,000,000,000 bytes minimum imagefs available** pre-hop gate; no further agent upgrade should start on this storage layout without resolving the gate and cold-pull/unpack headroom.

Evidence retained outside Git: `/home/dev/orca/artifacts/hop3-prep-20261123-task_8e5c5990eeae/` (node/pod/Flux/revision baseline, disk inspection, CRI inventory, and original draft plan).

## 2. Jellyfin GPU repair (`d6acf50`, 21:06–21:07 UTC)

Confirmed **both nova and ziti** renderD128 devices are root:GID **992**, mode 0660; the old Jellyfin process had groups 568,44,1000,10000 and bundled vainfo failed to open the device. The app-template 3.1.0 HelmRelease's existing `controllers.jellyfin.pod.securityContext` already rendered the old supplemental groups, so this chart supports the fix directly.

Added 992 while keeping every existing group, and added **required nodeAffinity `kubernetes.io/hostname In [nova]`**, preserving the Intel-GPU nodeSelector. This makes the next scheduling decision require nova and prevents another Jellyfin cold pull on ziti. The existing failed hop-2 pod/cached image on ziti remains as evidence; neither was deleted/pruned. No other app, host permission or GPU plugin changed.

Rendered the exact cached chart with resolved live values plus the two proposed changes, and server-side dry-ran every rendered object in namespace media. The first dry-run omitted the namespace and received the ingress duplicate-host rejection; rerunning with `-n media` passed PVC, Service, Deployment and Ingress. It caused no live writes. The Git diff contains only the group and affinity changes. Fresh pull before the commit, push to main, automatic Flux source/kustomization reconciliation, then **only** `flux --context home reconcile helmrelease jellyfin --namespace media --timeout=60s` (no force or other manual Flux reconcile).

Expected Helm revision **4 → 5**. Deployment successfully rolled out once; new pod `jellyfin-7544c9487-f2jwp` is 1/1 on nova with 0 restarts, groups 568,44,992,1000,10000. Verification:

- `test -r /dev/dri/renderD128` and `test -w` both true.
- Bundled `/usr/lib/jellyfin-ffmpeg/vainfo --display drm --device /dev/dri/renderD128` initializes Intel iHD successfully and lists H264/HEVC/MPEG2/VP8 decode/encode profiles.
- Generated two seconds of 640×360/30fps test video, encoded using `h264_vaapi`, then ran `-hwaccel vaapi -hwaccel_device /dev/dri/renderD128 -hwaccel_output_format vaapi` and `h264_vaapi` to a null sink: **60 frames decoded, 60 encoded, zero decode errors, exit 0**. The temporary sample in this pod's `/tmp` was removed by a shell trap. This proves bundled FFmpeg device/decode/encode operation, not an end-to-end authenticated user playback session.
- Ingress `https://jellyfin.boundcorp.net/health` HTTP **200**, including the final check.

Whether this permission failure predates hop 2 remains **unknown**. Rollback is a reviewed revert of `d6acf50` through Git/Flux; it would remove the permission fix and the safety pin, so investigate rather than casually reverting. No rollback was needed.

## 3. Hop-3 readiness only

### Snapshot (21:08 UTC)

Explicitly authorized on-demand `ssh enterprise 'sudo -n k3s etcd-snapshot save --name hop3-prep-20261123T2108Z'`, then `kubectl --context home -n kube-system create job --from=cronjob/etcd-snapshot-offload hop3-prep-20261123t2108z`. The Job completed. The source was independently checksummed over SSH; the offload procedure reread/checksummed the titan copy and required equality before its final rename.

| Snapshot | Size | SHA-256 (enterprise = titan) |
|---|---|---|
| `hop3-prep-20261123T2108Z-enterprise-1795468092` | 37,687,328 bytes | `aa069a2b6c69f59ede2e63092b4fc8bb1feea4fa6b21455b1e948362ac97ba01` |

Source mode 0600; titan target root:root 0700 and snapshot mode 0600. No snapshot contents or credentials were printed/copied to devbox. The offload's normal token handling remained inside its existing protected workflow. Snapshot and completed offload Job retained. A new pre-hop snapshot is required at the actual upgrade window, after any prerequisite changes.

### API / compatibility evidence

Repo scan, live API discovery and live/Prometheus `apiserver_requested_deprecated_apis` were checked. The [API migration guide](https://kubernetes.io/docs/reference/using-api/deprecation-guide/) lists no built-in beta/GA resource API removal for 1.30. The [1.30 release announcement](https://kubernetes.io/blog/2024/04/17/kubernetes-v1-30-release/#deprecations-and-removals) identifies removal of `SecurityContextDeny`; neither repository nor enterprise config enables it. No removal blocker was found. The sole deprecated-request series is HelmRelease v2beta1 with **empty `removed_release`**, a Flux issue rather than Kubernetes 1.30 removal. Local Kustomize API syntax, OCIRepository v1beta2 and PGO CRD v1beta1 are not removed Kubernetes built-in APIs. These metrics describe observed requests since server restart; recheck intermittent clients at execution time.

| Actually running | Assessment for 1.30 / primary evidence |
|---|---|
| Cilium 1.18.16 | In the 1.30–1.33 test matrix: [pinned documentation](https://github.com/cilium/cilium/blob/v1.18.16/Documentation/network/kubernetes/compatibility.rst). |
| cert-manager 1.17.2 | Compatible 1.29–1.33, although upstream 1.17 is EOL: [release matrix](https://cert-manager.io/docs/releases/#old-cert-manager-releases). |
| ingress-nginx chart 4.15.1 / controller 1.15.1 | Matrix is **1.31–1.35**, excluding 1.30. Existing exception must be explicitly reconfirmed by Lee for hop 3, or an ingress change separately reviewed: [upstream table](https://github.com/kubernetes/ingress-nginx#supported-versions-table). Working on 1.29 is not 1.30 certification. |
| PGO chart/operator 5.8.9 | 5.8.x matrix includes 1.30–1.36: [supported platforms](https://access.crunchydata.com/documentation/postgres-operator/latest/overview/supported-platforms). Compatibility gives no HA guarantee. |
| NFS CSI 4.13.5 | Driver matrix 1.21+: [upstream](https://github.com/kubernetes-csi/csi-driver-nfs#container-images--kubernetes-compatibility). Current CSI DaemonSets/controller, PVC mounts healthy; no CSI edit. |
| kube-prometheus-stack 47.1.0 / operator 0.66.0 / Prometheus 2.45.0 | [Chart](https://github.com/prometheus-community/helm-charts/blob/kube-prometheus-stack-47.1.0/charts/kube-prometheus-stack/Chart.yaml) minimum ≥1.16.0-0, correcting the old plan's ≥1.25 shorthand. [Operator](https://github.com/prometheus-operator/prometheus-operator/blob/v0.66.0/Documentation/compatibility.md) uses client-go 0.27.2; later-version compatibility is best effort, not explicit 1.30 certification. 52/52 targets currently up; preserve post-node metrics gates. |
| SUC controller 0.21.1 / job kubectl 1.30.3 | Uses core/batch/apps/RBAC GA APIs and Plan v1; kubectl matches target 1.30. No formal 1.30 test guarantee found; successful operation on 1.29/hop 2 is observed evidence. [Pinned manifests](https://github.com/rancher/system-upgrade-controller/tree/v0.21.1/manifests). Plans remain Complete at 1.29.15, agent cordon-only/concurrency 1/postCompleteDelay 300s, vega excluded. |
| Flux 2.3.0 | **Supports 1.28–1.30**, so compatible with hop 3: [2.3 announcement](https://fluxcd.io/blog/2024/05/flux-v2.3.0/). No upgrade technically required for 1.30. |

The campaign recommends the exact next Flux step **2.4.0** (only upstream 2.4 release, supports 1.29–1.31 per [announcement](https://fluxcd.io/blog/2024/09/flux-v2.4.0/)), requiring separate Lee approval or explicit deferral. No Flux change made. That operation needs an exact artifact/render/server dry-run, patch/feature-flag review, before/after Helm revision baseline with zero unintended upgrades, all six controllers/ks/hr Ready, and CRD storage-version rollback review. The pre-2.7 `flux migrate`/storedVersions/managedFields cleanup and orphan filebrowser remain separate later gates.

Vega 1.27 is exactly n−3 at API server 1.30 and allowed by [upstream skew policy](https://kubernetes.io/releases/version-skew-policy/). Before hop 4 (1.31), Lee must approve removal (recommended), or a separately scoped idle upgrade to at least 1.28, preferably 1.30+. It stays cordoned and SUC-excluded; no action on vega in this task.

### Draft plan update

Updated only `docs/k3s-upgrade-plan.md` on **PR #3's branch** `leewardbound/k3s-upgrade-plan-1120`, commit **`e4808c9`**; [PR #3](https://github.com/boundcorp/cluster-boundcorp-homeops/pull/3) verified **still draft, unmerged**, head matches. New §4.8 records:

- ≥5,000,000,000 available imagefs bytes plus measured cold-pull/unpack headroom before A/every agent, and stop on capacity/pressure/eviction failures. No binary downgrade as disk remediation; separately approved storage migration needs a quiesced rollback plan.
- Single Patroni primary/no standby; Lee explicitly accepts server API/DCS demotion/outage risk. Watch Patroni during A and check SQL `pg_is_in_recovery()=false`, `transaction_read_only=off` after each node. Exec transport failures during restart are unknown results, not proof of read-only mode. Require clean recovery before the next node.
- Hop-3 intended order A server, B1 **nova only** (exclude vega,ziti), then B2 **ziti only** after capacity/DB/GPU gates; concurrency alone is not isolation. Keep full 300 s postCompleteDelay and ≥10 min stability gates.
- Kube-vip mirror UID/restartCount/startedAt/termination/logs all matter: hop 2 retained its UID and restarted the container once, exact prior exit cause still uncertain. Direct/VIP probe failures alone do not prove a server restart.
- Jellyfin required nova affinity and a review of other large image placements/rollout surge demand before execution.
- Explicitly evidenced recovered leader-election exits are classified separately, but >3 affected previously healthy pods still escalates/holds; exit 137, admission errors, evictions, recurrent crashes or DB failure remain material regressions. Existing time/memory/etcd/cordon stop thresholds preserved; no automatic restore/downgrade.

### Final sweep (21:13–21:14 UTC)

- Enterprise/nova/ziti Ready **v1.29.15+k3s1**; vega Ready **v1.27.3+k3s1**. Enterprise/vega still cordoned, nova/ziti schedulable, vega still SUC-excluded. No Memory/Disk/PID pressure, including **ziti DiskPressure False**.
- **44/44 Kustomizations, 34/34 HelmReleases Ready**, only Jellyfin's revision changed (4 → 5). All other Helm revisions match baseline. All active application pods ready; no new running-pod restart deltas besides the intended Jellyfin replacement; all 9 DaemonSets full, **29/29 PVCs Bound**. Historical failed/evicted pods remain untouched.
- **52/52 Prometheus targets up**. DNS from the new Jellyfin pod resolves `kubernetes.default.svc.cluster.local` to 10.43.0.1. Jellyfin `/health` and Grafana `/login` ingress HTTP **200**.
- API readyz ok; etcd `/health` true. Enterprise memory available 3,076 MiB, load 1.19. Kube-vip same mirror UID `dac857b1…`, restartCount **1**, started **19:53:34Z** (no new restart).
- Postgres primary **5/5**, read-only SQL **false/off**, `pg_stat_replication` rows **0** (no standby). Patroni logs show continuing leader lock. PgBouncer **2/2**.
- Both SUC Plans Complete, applying empty, version **v1.29.15+k3s1**; **0 SUC Jobs**. Ansible pin, Plans, k3s versions and all infrastructure configuration are unchanged.
- Ziti imagefs available **2,778,148,864 bytes**, capacity **12,459,843,584**; CRI imagefs used **5,729,607,680**. No new evictions, ImageGCFailed or pressure events during preparation. The two new warnings were the old Jellyfin pod's readiness connection refusal during its intended termination (21:06:28) and CronJob controller `UnexpectedJob` for the explicitly created manual offload Job (21:08:19); the Job completed successfully.
- This task's four node-debug pods were deleted (two ziti, two nova); unrelated pre-existing debug pods were left alone. The FFmpeg sample was removed. Snapshot/Job and sanitized evidence retained. No other pod/PVC deletion, host change, prune, restart, drain, cordon change, Flux upgrade, or hop 3.

**NO-GO for hop 3.** Ziti storage/cold-pull headroom remains the technical blocker and needs Lee's separately approved capacity operation (recommend existing `/srv`, no hardware purchase). Lee also needs to decide Flux 2.4.0 timing versus explicit deferral, reconfirm the ingress/legacy monitoring lifecycle exceptions and single-primary DB outage risk, and approve the actual hop/window with fresh snapshot/stability checks. Jellyfin GPU is fixed; Flux 2.3.0 itself is compatible with 1.30. Vega removal/idle upgrade is due before hop 4, not a hop-3 technical blocker.

Main commits: **`38c6099`** disk assessment, **`d6acf50`** Jellyfin permission/placement fix, plus this final journal commit. Draft-plan commit: **`e4808c9`**. No merge of PR #3.
