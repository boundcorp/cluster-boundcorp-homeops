# k3s upgrade plan: v1.27.3+k3s1 → v1.36.6+k3s1

Status: **PLAN ONLY, nothing executed.** Written 2026-11-19 from read-only checks of the `home` cluster, read-only SSH to `enterprise` and `vega`, this repo, and upstream release notes. Nothing in the cluster or on any node was changed.

Every step that changes the cluster, a node, or Git `main` needs Lee's approval at the gates marked **🔒 APPROVAL**. The k3s control plane, etcd, ingress, Flux and NetBird are load-bearing infrastructure, so none of this is auto-approved under the Sandbox tier.

---

## 0. TL;DR

| Item | Recommendation |
|---|---|
| **Target** | **v1.36.6+k3s1**, the latest patch of the 1.36 minor |
| **Why not v1.37.2 (k3s `stable`)?** | No released Cilium, cert-manager or Crunchy PGO version lists k8s 1.37 support yet. 1.37 also moves embedded etcd to 3.7. Make it a follow-up hop once those projects ship support. |
| **Fallback stop point** | **v1.35.10+k3s1** if the ingress-nginx decision (§5.3) is not made in time. Upstream supports 1.35 until about Feb 2027. |
| **Hops** | **9** server hops, one minor at a time: 1.28.15 → 1.29.15 → 1.30.14+k3s2 → 1.31.14 → 1.32.13 → 1.33.13+k3s2 → 1.34.13 → 1.35.10 → 1.36.6 |
| **Mechanism** | The existing system-upgrade-controller (SUC) Plans, driven by Git commits (GitOps). Manual binary swap over SSH is the fallback for `enterprise` and `vega` only. |
| **Flux** | Flux has to move in lockstep: v2.0.1 only supports k8s ≤1.27, and Flux 2.10 needs ≥1.35. Bump it via `kubernetes/flux/config/flux.yaml` between hops. **Do not** `kubectl apply -k kubernetes/bootstrap` (it is pinned to v2.9.6, which needs k8s ≥1.34). |
| **Hard prerequisites** | Off-node etcd snapshot plus a restore drill; Plan fixes so SUC doesn't uncordon `enterprise`; uncordon `vega`; fix the kube-vip interface; Flux to 2.2 |
| **Estimated downtime** | Control-plane API: about 2–5 min per hop, roughly 20–45 min total. Apps: about 2–5 min per singleton per drain, and nova-pinned home devices (Frigate, Z-Wave) about 5–15 min per hop. Wall-clock: about 3–4 evenings (§9). |

---

## 1. Current state (observed 2026-11-19)

### 1.1 Nodes

| Node | Role | Status | k3s | OS / kernel | Capacity | Live usage |
|---|---|---|---|---|---|---|
| enterprise (10.20.30.111) | control-plane, sole embedded-etcd member | Ready, **cordoned** since 2026-08-04 | v1.27.3+k3s1 | Debian 12, 6.1.0-10, cgroup v2 | 4 CPU, 7.6 GiB (5.6 GiB allocatable), no swap | 943m CPU; 6.9 GiB = **119% of allocatable**, about 2.1 GiB `available`. k3s-server RSS is 1.6 GiB at about 133% CPU. |
| vega (.112) | agent | Ready, **cordoned** since 2026-08-30 | v1.27.3+k3s1 | Debian 12, 6.1.0-10, cgroup v2 | 12 CPU, 31 GiB | about idle (105m, 9.6 GiB mostly page cache). k3s up since 2026-10-22, 0 restarts. |
| ziti (.113) | agent | Ready | v1.27.3+k3s1 | Debian 13, 6.12 | 8 CPU, 16 GiB | 30 pods; **memory requests 69%** |
| nova (.114) | agent | Ready | v1.27.3+k3s1 | Debian 13, 6.12 | 8 CPU, 16 GiB | 72 pods; memory 77% used, requests 37% (5.9 GiB) |

All nodes run containerd 1.7.1-k3s1. The API server certificate is valid until 2027-04-16 and the CAs until 2034.

### 1.2 How k3s is installed and managed

- **Install method:** the Ansible role `xanmanning.k3s` (`ansible/inventory/group_vars/*`, `ansible/playbooks/cluster-installation.yaml`).
  - Evidence on enterprise and vega:
    - The systemd unit `/etc/systemd/system/k3s.service` uses `KillMode=process` and is named `k3s.service` on **both** server and agent. It is not the get.k3s.io `k3s-agent.service` layout.
    - `/usr/local/bin/k3s-killall.sh` and `k3s-uninstall.sh` are present.
    - The hard-linked `k3s-v1.29.1+k3s1` binary is left over from an earlier role run. `kubectl`, `crictl` and `ctr` on the nodes are hard links to that 1.29.1 binary, while `/usr/local/bin/k3s` itself is still the v1.27.3 binary.
  - nova and ziti were re-joined on 2026-08-07 with a manual pinned agent install (see `docs/journals/2026-08-07-*`). Their unit layout was not verified because their host keys changed and SSH is off-limits.
- **enterprise `/etc/rancher/k3s/config.yaml`** (keys only): `cluster-init`, `cluster-cidr`, `disable` (6 items: coredns, flannel, local-storage, metrics-server, servicelb, traefik), `disable-cloud-controller`, `disable-kube-proxy`, `disable-network-policy`, `docker`, `etcd-expose-metrics`, `flannel-backend`, `kube-apiserver-arg` (1: `anonymous-auth=true`), `kube-controller-manager-arg` (1), `kube-scheduler-arg` (1), `node-ip`, `service-cidr`, `tls-san` (1: the VIP), `write-kubeconfig-mode`.
  - There are **no `etcd-arg` and no `etcd-snapshot-*`/`etcd-s3*` keys**, so k3s's default snapshot schedule applies.
  - `config.yaml.d/` is empty.
- **vega config.yaml:** `node-ip` only. The server URL and token are in the unit's `ExecStart`, redacted here.
- **containerd:** no custom `config.toml.tmpl` / `config-v3.toml.tmpl` on enterprise or vega, so the containerd 2.0 switch at 1.31 needs no template migration there. nova and ziti are unverified.
- **Channel:** none. Versions are pinned explicitly.
- **⚠ Version-pin drift.** `ansible/inventory/group_vars/kubernetes/main.yaml` pins `k3s_release_version: v1.29.1+k3s1`, but the cluster runs v1.27.3.
  - Any run of the k3s role, **including `cluster-kube-vip.yaml`**, which includes the role, would jump nodes straight to 1.29 and skip 1.28.
  - Re-pin to v1.27.3+k3s1 as the first commit (prerequisite P1).
- **system-upgrade-controller:** deployed via Flux (`kubernetes/apps/system-upgrade/`) at **v0.11.0**, with Plans `server` and `agent` both at `v1.27.3+k3s1`. The controller pod is Running on enterprise. **The Plans are usable** with these fixes:
  1. **Server plan has `cordon: true`.** SUC (v0.11.0 and HEAD) sets `Unschedulable=false` after a successful job when `cordon` or `drain` is set (`pkg/upgrade/handle_batch.go`). So the first server hop would **uncordon enterprise** and repeat the 2026-08-04 recovery storm. **Remove `cordon: true` from the server plan**; enterprise is already cordoned.
  2. **Agent plan has no `drain`/`cordon`.** It upgrades workers in place with only a k3s restart. Add a `drain` block (below). Because of (1), SUC will then uncordon each worker afterwards. That is intended for nova and ziti, and for vega only once we've decided to uncordon it (P4).
  3. Both Plans select nodes by label, so the agent plan covers vega, ziti and nova at `concurrency: 1`. The order is not controllable. The agent plan's `prepare: [prepare, server]` init step blocks agents until the server plan has completed, which is a good safety net.
  4. Bump SUC itself (latest v0.21.1). See §5.

### 1.3 Flux

- **Running:** distribution v2.0.1 (helm-controller v0.35.0, kustomize v1.0.1, source v1.0.1), self-managed via `kubernetes/flux/config/flux.yaml` (OCIRepository `flux-manifests` tag `v2.0.1`).
- **CLI on devbox:** v2.9.6. `flux get hr` fails with `helm.toolkit.fluxcd.io/v2: no matches` because the 2.9 CLI only speaks `v2` while the cluster serves only `v2beta1`. **Use the CLI that matches the cluster version during the campaign**, e.g. download per-step CLIs to the scratchpad, or use `kubectl get hr -A`.
- **`kubernetes/bootstrap/kustomization.yaml`** was bumped to `flux2/manifests/install?ref=v2.9.6` in 87a6f54 and was "pending manual apply". **Do not apply it.**
  - Flux 2.9 needs k8s 1.34–1.36 and has removed `helm v2beta1`, `source v1beta1` and `v1beta2`, which this repo still uses.
  - It would also fight `flux.yaml`, which pins the OCI tag at v2.0.1.
  - Flux upgrades in this plan go through `flux.yaml`. Leave `bootstrap/` alone until the end, then re-align it to the final version.
- **Repo Flux API usage:**

  | API | Objects | Notes |
  |---|---|---|
  | `helm.toolkit.fluxcd.io/v2beta1` | 35 | |
  | `source.toolkit.fluxcd.io/v1beta2` | 21 | |
  | `source.toolkit.fluxcd.io/v1beta1` | 3 | `flux/repositories/helm/vector.yaml`, `rook-ceph-charts.yaml`, and `apps/kube-system/external-snapshotter/app/crds.yaml` |
  | `kustomize.toolkit.fluxcd.io/v1beta2` | 1 | |
  | `notification.toolkit.fluxcd.io/v1` | 1 | |

  - Cluster storedVersions are still `v2beta1` for HelmRelease and `v1beta2` for most sources. These must be migrated before Flux 2.7, which removes them.

### 1.4 Deprecated / removed Kubernetes APIs

- **Core Kubernetes APIs:** no change is needed for 1.28–1.36.
  - Live `apiserver_requested_deprecated_apis` shows only:
    - `componentstatuses v1` (deprecated, not removed; a kubectl/monitoring read)
    - `cilium.io/v2alpha1 ciliumnodeconfigs` (Cilium itself)
    - Flux `kustomize v1beta2` and `source v1beta1`
  - The rendered manifests of every HelmRelease (`helm get manifest`) use only GA APIs: v1, apps/v1, rbac/v1, networking/v1, admissionregistration/v1, apiextensions/v1, apiregistration/v1, storage/v1, monitoring.coreos.com/v1.
  - The repo has no `flowcontrol`, `policy/v1beta1`, `batch/v1beta1`, or `autoscaling/v2beta*`.
  - The only Kubernetes API removals in range are flowcontrol `v1beta2` (1.29) and `v1beta3` (1.32), and nothing here uses them.
- **pluto/kubent:** not installed on devbox. Nothing was installed for this plan; the metric plus the manifest scan above cover the same ground.
- **Objects that must change before a specific hop:**

| Before | Object(s) | Change |
|---|---|---|
| Flux 2.3 (after hop 1) | 35 HelmReleases | `helm.toolkit.fluxcd.io/v2beta1` → `v2` (`flux migrate`, see §4) |
| Flux 2.6/2.7 (after hops 4–5) | 3 `source v1beta1`, 21 `source v1beta2`, 1 `kustomize v1beta2`; cluster storedVersions | → `source.toolkit.fluxcd.io/v1` and `kustomize v1`; run `flux migrate` in-cluster. **Must be done before Flux 2.7.** |
| hop 6 (1.33) | `external-dns` chart 1.13.0 (app 0.13.5) | ≥ app 0.18 (EndpointSlices); the latest is chart 1.24.0 / app 0.24.0 |
| Cilium 1.19/1.20 bump | `CiliumL2AnnouncementPolicy` (v2alpha1, the only served version) and `CiliumLoadBalancerIPPool` (v2alpha1 in repo; v2 is already served) | Move the IP pool to `cilium.io/v2` now. Recheck the L2 policy's API in the Cilium 1.19/1.20 upgrade notes when bumping. Both objects are in the repo and in the Ansible template `custom-cilium-l2.yaml.j2`. |
| hop 8 (1.35) | every node | cgroup v2 required (`failCgroupV1=true` default). enterprise and vega are cgroup2fs. Debian 13 nova/ziti default to v2 but are **unverified**. |
| hop 9 (1.36) | `gitRepo` volumes | removed in 1.36; none found in the repo |

---

## 2. Target and hop sequence

Latest patch per minor, from `https://update.k3s.io/v1-release/channels` on 2026-11-19:

| Hop | k3s | Notes |
|---|---|---|
| 1 | v1.28.15+k3s1 | |
| 2 | v1.29.15+k3s1 | k3s removed `rotate-keys` and `multi-cluster-cidr` flags (unused here) |
| 3 | v1.30.14+k3s2 | Cilium 1.18 is officially in range from here |
| 4 | v1.31.14+k3s1 | **containerd 1.7 → 2.x** (k3s ≥1.31.6). `k3s etcd-snapshot --server/--token` renamed to `--etcd-server/--etcd-token`. |
| 5 | v1.32.13+k3s1 | |
| 6 | v1.33.13+k3s2 | **embedded etcd 3.5.26 → 3.6.14**. Upstream etcd now advises 3.5 ≥ 3.5.32 before 3.6, and no k3s 1.32 build has that. It is very likely fine for a single-member cluster, but it is **the highest-risk hop**: fresh snapshot, restore drill already done, hold point. |
| 7 | v1.34.13+k3s1 | |
| 8 | v1.35.10+k3s1 | cgroup v1 is fatal by default. This is the fallback stop point. |
| 9 | v1.36.6+k3s1 | **Target.** Needs the ingress decision (§5.3). |
| (10) | v1.37.2+k3s1 | **Deferred.** Needs Cilium ≥1.21, cert-manager ≥1.22 and a PGO release listing 1.37. etcd 3.6.14 → 3.7.1 (source ≥3.6.11 is OK). Static pods may no longer reference Secrets or ConfigMaps; kube-vip's manifest uses hostPath only, so that's fine. |

- **Server before agents, one minor at a time.** That is the k3s documented rule ("do not skip intermediate minor versions").
- The kubelet skew policy (n-3) would technically let agents skip minors. This plan **upgrades agents on every hop** anyway: it keeps nodes uniform, keeps each blast radius small, and is what the SUC agent Plan does naturally.
- If time gets tight, an acceptable compromise is to let agents trail the server by **at most one minor**, i.e. upgrade agents every second hop. Never run agents more than 2 minors behind, and never newer than the server.

### Add-on moves interleaved with hops

Each row is a separate PR or commit. Do them while the cluster sits at the stated k8s version, and let them settle before the next hop.

| When (cluster at) | Flux (via `flux.yaml` tag) | Other add-ons |
|---|---|---|
| 1.27 (before hop 1) | 2.0.1 → **2.2.x** (supports 1.26–1.28; no API removals) | SUC v0.11.0 → v0.21.1 (or the newest that runs on 1.27). external-snapshotter / source v1beta1 cleanup. Cilium IP pool → v2. |
| 1.28 (after hop 1) | → **2.3.x** (1.28–1.30). Migrate repo HelmReleases to `helm.toolkit.fluxcd.io/v2`. | |
| 1.29 | → **2.4.x** (1.29–1.31) | |
| 1.30 | → **2.5.x** (1.30–1.32) | metrics-server chart → app 0.7.x |
| 1.31 | → **2.6.x** (1.31–1.33). `flux migrate -v 2.6 -f .` on the repo, then `flux migrate` in-cluster (storedVersions → v1/v2) | Cilium 1.18 → **1.19** latest patch (1.31–1.34). cert-manager 1.17 → next minors one at a time (per cert-manager's support table). metrics-server → 0.8.x |
| 1.32 | → **2.7.x** (1.32–1.34). Removes `v1beta1` and helm `v2beta1`, so it fails if the migration was skipped. | cert-manager continue |
| 1.32 (before hop 6) | | **external-dns → chart 1.24.x / app ≥0.18** (required for 1.33) |
| 1.33 | → **2.8.x** (1.33–1.35) | Cilium 1.19 → **1.20.x** (1.33–1.36). cert-manager → **1.21.x** (1.33–1.36). |
| 1.34 | → **2.9.x** (1.34–1.36) | metrics-server → 0.9.x (1.34+). NFD → 0.17+ (CRDs separately). intel-gpu-plugin → 0.36.x. |
| 1.35 | → **2.10.x** (1.35–1.37) | ingress decision executed (§5.3) |
| 1.36 (done) | re-align `kubernetes/bootstrap/kustomization.yaml` to the running Flux version | kube-prometheus-stack 47 → current (separate project, see §5.6) |

---

## 3. Prerequisites (pre-flight, all at v1.27.3)

Do these in order. ☐ = not done.

- ☐ **P0 🔒 APPROVAL #1: approve the plan and a maintenance calendar.** Lee picks the windows (suggested: 3–4 evenings).
- ☐ **P1 Re-pin Ansible to reality** (Git only, no cluster effect). Set `k3s_release_version: "v1.27.3+k3s1"` in `ansible/inventory/group_vars/kubernetes/main.yaml`, then bump it in the same commit as each hop. Until then **do not run any Ansible playbook** (`cluster-kube-vip.yaml` includes the k3s role).
- ☐ **P2 Off-node etcd backup in place and a restore drill done** (§6).
  - The snapshots that exist today are local-only on enterprise's disk, the same disk class that failed in August.
  - 🔒 **APPROVAL #2**: the backup CronJob (kube-system) or the `etcd-s3` config change.
- ☐ **P3 Fix the SUC Plans** (Git):
  - Remove `cordon: true` from `plans/server.yaml`.
  - Add the following to `plans/agent.yaml`:
    ```yaml
    drain:
      force: true            # bare pods (none expected) would block otherwise
      deleteLocalData: true  # emptyDir; data on NFS PVCs is unaffected   (field name: deleteEmptydirData on newer SUC)
      ignoreDaemonSets: true
      skipWaitForDeleteTimeout: 60
      timeout: 900s
    ```
  - Bump SUC to a current release and confirm it runs on 1.27.
  - Leave both Plans' `version` at `v1.27.3+k3s1` in this commit, so nothing upgrades yet.
  - Verify with `kubectl --context home -n system-upgrade get plans -o yaml` that `status.latestVersion` is v1.27.3+k3s1 and that no Jobs are created.
- ☐ **P4 🔒 APPROVAL #3: uncordon `vega`.** See §7.2. This is required: nova's drain does not fit on ziti alone.
  - Do a 24 h soak first: Ready the whole time, no new kernel or disk errors via `ssh vega journalctl -k -p err --since -24h`.
  - Then `kubectl --context home uncordon vega`.
- ☐ **P5 🔒 APPROVAL #4: kube-vip interface fix** (§8).
  - Set `vip_interface: enp3s0` in `ansible/playbooks/templates/kube-vip-static-pod.yaml.j2`.
  - Put the identical edit into `/var/lib/rancher/k3s/agent/pod-manifests/kube-vip-static-pod.yaml` on enterprise. Make it by hand, not with the playbook (P1).
  - Expect a VIP blip of about 10 s.
- ☐ **P6 Flux 2.0.1 → 2.2.x** (Git: `flux.yaml` OCI tag). Verify all Kustomizations and HelmReleases are Ready (`kubectl --context home get ks,hr -A`).
- ☐ **P7 Health baseline:**
  - All 4 nodes Ready.
  - DaemonSets (Cilium, CSI NFS, NetBird) at 4/4.
  - No Pending or CrashLoop pods.
  - Prometheus up. It was recreated today at 04:14Z and is now 2/2, so the Succeeded-phase pod from the August eviction is gone.
  - Record `kubectl --context home get pods -A -o wide > baseline-<date>.txt` (scratchpad, not the repo).
- ☐ **P8 (nice to have, Lee):** re-verify the nova and ziti SSH host keys out of band. This is the only way to do manual agent fallback or rollback on those two. Also confirm cgroup v2 and the absence of a containerd template there: `stat -fc %T /sys/fs/cgroup` and `ls /var/lib/rancher/k3s/agent/etc/containerd/`.
- ☐ **P9 Pre-download** every k3s binary and `sha256sum-amd64.txt` for v1.28.15 … v1.36.6 to vega's local disk. This allows a manual fallback and rollback even if GitHub is unreachable.
- ☐ **P10 Announce** a quiet window, since this restarts ingress and the database pods. Pause Frigate/HA automations if Lee prefers.

---

## 4. Per-hop procedure (repeat for hops 1–9)

Set `V` to the hop's version, e.g. `V=v1.28.15+k3s1`. All kubectl/flux commands use `--context home`.

### 4.1 Pre-hop gate (read-only, about 5 min)

```sh
kubectl --context home get nodes -o wide                     # all Ready, all on previous version
kubectl --context home get ks,hr -A | grep -v True           # expect nothing (or only known-bad, e.g. loki)
kubectl --context home get pods -A | grep -Ev 'Running|Completed'   # expect nothing new vs baseline
kubectl --context home get ds -A | awk '$3!=$5'              # DaemonSets fully available
ssh enterprise 'free -m; uptime'                             # need ≥1.5 GiB "available", load < 4
```

### 4.2 Snapshot (enterprise, about 2 min)

```sh
ssh enterprise "sudo k3s etcd-snapshot save --name pre-${V//+/-}"
ssh enterprise 'sudo ls -la /var/lib/rancher/k3s/server/db/snapshots/ | tail -3'
# then copy off-node (§6.2), and verify the sha256 at the destination
```

### 4.3 🔒 APPROVAL per hop: Lee's go for hop N

Show the diff, then:

**Commit A (server only):**
- Bump `version: "$V"` in `kubernetes/apps/system-upgrade/system-upgrade-controller/plans/server.yaml`.
- Bump `k3s_release_version` in the Ansible group_vars.
- Push, then `flux --context home reconcile ks cluster-apps-system-upgrade-controller-plans -n flux-system --with-source`. Reconciling Flux is part of the approved hop.

Watch:
```sh
kubectl --context home -n system-upgrade get plans,jobs,pods -w
ssh enterprise 'sudo journalctl -u k3s -f --since now'      # expect stop/start; etcd ready; "Running kube-apiserver"
```

- The API (direct and VIP) is unavailable for about 1–5 min while k3s restarts. Expect it toward the high end on hop 6, because etcd 3.6 migrates the data directory.
- Running pods keep running because of `KillMode=process`.

**Server verification (gate before agents):**
```sh
kubectl --context home get nodes            # enterprise shows $V, Ready, still SchedulingDisabled
kubectl --context home get --raw /readyz?verbose | tail -3
kubectl --context home get --raw /livez
ssh enterprise 'sudo k3s etcd-snapshot ls | tail -2; free -m'   # etcd answers; memory sane
kubectl --context home -n kube-system get pods -o wide | grep -E 'kube-vip|cilium-operator|coredns'
```

**Commit B (agents):**
- Bump `version: "$V"` in `plans/agent.yaml`, push, reconcile the same ks.
- SUC drains and upgrades one worker at a time (vega, ziti, nova in any order), then uncordons each.
- Watch that drained pods reschedule and that nothing stays Pending for more than 5 min, especially PgBouncer/Postgres (PGO), ingress-nginx, cert-manager, and Flux controllers.
- Expected interruptions while **nova** drains:
  - `frigate` is pinned to nova (Coral) and `zwave-js-ui` needs the USB stick on nova, so both are down until nova returns, about 5–15 min.
  - Jellyfin needs a GPU node, and any agent works for it.

### 4.4 Post-hop verification (about 10 min), before declaring the hop done

```sh
kubectl --context home get nodes                                   # all 4 on $V, Ready; enterprise still cordoned; vega/ziti/nova schedulable
kubectl --context home get ks,hr -A | grep -v True                  # no new failures
kubectl --context home get pods -A | grep -Ev 'Running|Completed'
kubectl --context home get ds -A                                    # cilium, csi-nfs-node, netbird, node-exporter 4/4
kubectl --context home -n database get postgrescluster,pods         # PGO cluster healthy, pgbouncer Ready
kubectl --context home get certificate -A | grep -v True            # cert-manager OK
kubectl --context home get --raw /metrics | grep apiserver_requested_deprecated_apis   # nothing new with removed_release <= next hop
curl -sk -o /dev/null -w '%{http_code}\n' https://10.20.30.200 -H 'Host: <an internal app host>'   # ingress serves
kubectl --context home -n monitoring get pods | grep prometheus     # Prometheus 2/2
```

Then:
- Do the interleaved add-on step for this version (§2 table), with its own reconcile and verification.
- Wait at least 30 min of stability (or until the next window) before the next hop.
- Record the hop in `docs/journal/`.

### 4.5 Manual fallback (enterprise / vega only)

Use this if SUC misbehaves. **Do not** use the `get.k3s.io` installer on agents: it would create `k3s-agent.service` next to the role's `k3s.service` (see the 2026-08 journals). Swap the binary instead:

```sh
V=v1.28.15+k3s1; U=${V//+/%2B}
curl -fsSLo /tmp/k3s        https://github.com/k3s-io/k3s/releases/download/$U/k3s
curl -fsSLo /tmp/sha256.txt https://github.com/k3s-io/k3s/releases/download/$U/sha256sum-amd64.txt
(cd /tmp && grep ' k3s$' sha256.txt | sha256sum -c -)
sudo cp -a /usr/local/bin/k3s /usr/local/bin/k3s.prev          # keep previous for rollback
sudo install -m 0755 /tmp/k3s /usr/local/bin/k3s
sudo systemctl restart k3s
```

For agents, run `kubectl --context home drain <node> --ignore-daemonsets --delete-emptydir-data` first and `uncordon` afterwards. nova and ziti need P8 before this path works.

---

## 5. Add-on compatibility

Sources: upstream release notes and READMEs, gathered 2026-11-19. Items marked *unverified* need a recheck at execution time.

| Component | Deployed | Supports (deployed) | Needed for 1.36 | Action |
|---|---|---|---|---|
| **Flux** | 2.0.1 | ≤1.27 | 2.9.x or 2.10.x | Step through 2.2 → 2.10 per §2. Migrate APIs (`flux migrate`) before 2.7. Any 2.x → 2.x jump is officially allowed, but each Flux minor only supports a 3-minor k8s window, so it has to track the hops. **Must move before hop 1** (2.0 stops at 1.27). |
| **Cilium** | chart 1.18.16 | 1.30–1.33 (already out of range on 1.27, but working) | 1.20.x (1.33–1.36) | 1.19 at k8s 1.31–1.33, then 1.20 at 1.33–1.34. One Cilium minor at a time; run the pre-flight check per the Cilium upgrade guide. `k8sServiceHost` = VIP, so it depends on kube-vip (§8). |
| **cert-manager** | v1.17.2 (EOL) | 1.29–1.33 | v1.21.x (1.33–1.36) | Step minors 1.18 → 1.21 between hops 4 and 7. Check CRD upgrade notes per minor. |
| **ingress-nginx** | chart 4.15.1 / controller 1.15.1 | 1.31–1.35 | **none: project retired, repo archived 2026-03** | See §5.3. 🔒 decision |
| **kube-vip** | v0.6.0 static pod (Ansible template) | works; old | any (v1.3.2 latest) | P5: set `vip_interface`. Bump to v1.x as a separate, later change (§8). |
| **NFS CSI** (csi-driver-nfs) | 4.13.5 | 1.21+ | same | Optional bump to 4.13.6. Titan exports are unaffected. |
| **intel-gpu-plugin** | image 0.27.0 (app-template 1.5.1) | old but device-plugin v1beta1 API is stable | 0.36.x lists 1.36 | Bump at about 1.34. Not a blocker. 64/93 restarts are worth a look. |
| **node-feature-discovery** | chart 0.13.3 | 1.24+ | 0.17+ recommended (0.20 built on 1.37 libs) | Bump at about 1.34; CRDs are applied separately (`nfd-api-crds.yaml`). The worker has 537/915 restarts, so investigate during the bump. Not a blocker. |
| **kube-prometheus-stack** | 47.1.0 (operator ≈0.66, Prometheus 2.45) | `>=1.25` | same | Not a blocker. The 47 → 93 jump is its own project: CRDs are not upgraded by Helm, plus Prometheus 3.0, Grafana 11/13 and value-schema changes. The Ansible-installed PodMonitor/PrometheusRule/ServiceMonitor CRDs are at v0.66.0. Schedule after the campaign. |
| **metallb** | not deployed | – | – | LB IPs come from Cilium L2 announcements |
| **NetBird** | DaemonSet `netbirdio/netbird:latest`, hostNetwork | no k8s API dependency | – | Pin a version (latest v0.83.1) so node restarts don't pull a surprise client. Not a blocker. |
| **cloudflared** | 2026.10.3 | no k8s dependency | – | none (2026.11.1 available) |
| **external-dns** | chart 1.13.0 / app 0.13.5 | old | **app ≥0.18 required for 1.33+** | Bump before hop 6 |
| **system-upgrade-controller** | v0.11.0 | unverified matrix | v0.21.1 | Bump in P3. Note the uncordon behaviour. |
| metrics-server | chart 3.10.0 (app 0.6.x) | 1.25+ | 0.8 (1.31+) / 0.9 (1.34+) | Bump at 1.30 and 1.34 |
| coredns | chart 1.49.0 | unverified | 1.50.x | Optional |
| Crunchy PGO | 5.8.9 | 1.30–1.36 | same (6.0.x also OK) | none for 1.36; **blocks 1.37** |
| k8s-gateway / reloader / local-path | 3.7.2 / 1.0.30 / 0.0.24 | old, generic APIs | – | Optional bumps |

### 5.3 ingress-nginx decision (🔒 APPROVAL #5, needed before hop 9)

ingress-nginx has been retired upstream (best-effort maintenance ended March 2026; the repo is archived). 1.15.1 is the final release, officially tested up to k8s 1.35, and it receives no further security fixes. Options:

1. **Stop the campaign at 1.35.10** and keep ingress-nginx until a migration is done. Lowest risk now, but 1.35 reaches upstream EOL around Feb 2027.
2. **Proceed to 1.36 on ingress-nginx 1.15.1 unsupported.** It uses only `networking.k8s.io/v1` and `admissionregistration/v1`, and probably works. You accept the unpatched-ingress risk.
3. **Migrate ingress before hop 9 (recommended long-term)** to:
   - (a) Cilium Ingress / Gateway API, since Cilium is already the CNI and L2 announcer, or
   - (b) Traefik.

   Either is a separate PR series touching every app's Ingress. Use a parallel controller on a new LB IP, then cut over `10.20.30.200`.

Recommendation: **(1) as the default stop point, with (3) planned.** Take hop 9 only after the migration, or choose (2) explicitly.

---

## 6. etcd backup and rollback

### 6.1 What exists today

- k3s default scheduled snapshots: every 12 h (00:00/12:00), retention 5, **local only** at `enterprise:/var/lib/rancher/k3s/server/db/snapshots/`. There are five 38 MB snapshots from 2026-11-17 to 2026-11-19.
- One 111 MB manual `pre-drain-recovery-20260804-0901-enterprise-…` snapshot.
- No `etcd-s3` configuration and no off-node copy.
- A 2026-08-03 archive of `/etc/rancher/k3s` plus the server token exists on rigel (`/home/linked/enterprise-recovery/`). It is old but still useful for the token.
- Snapshots contain every Secret in plaintext: k3s secrets-encryption is not enabled in config.yaml. Treat every copy as sensitive, with a restricted share and mode 600.
- Titan: SSH to the NAS is rejected (by design per ENVIRONMENT.md), so copies have to arrive over NFS from inside the cluster or from a node.

### 6.2 Off-node copies (P2), pick one (🔒 APPROVAL #2)

| Option | How | Pros | Cons |
|---|---|---|---|
| **A. GitOps CronJob → Titan (recommended)** | CronJob in `kube-system`, `nodeName: enterprise` (bypasses the cordon without uncordoning), hostPath `/var/lib/rancher/k3s/server/db/snapshots` **read-only** plus `/var/lib/rancher/k3s/server/token` read-only, writing to a PVC on `nfs-titan-hdd` (restricted). Runs at 00:30/12:30 and copies new snapshots with sha256 files. Trigger on demand before each hop with `kubectl --context home -n kube-system create job --from=cronjob/etcd-snapshot-offload pre-hop-N`. | Titan, as requested. Through Git. No host config change. Tiny footprint on enterprise. | Needs a privileged-ish pod (hostPath root files). PVC content is readable by anyone with cluster access to that namespace. |
| B. k3s native S3 | Add `etcd-s3`, `etcd-s3-bucket`, `etcd-s3-folder`, `etcd-s3-region`, plus credentials to enterprise config (the creds belong in a root-only file, not this repo). Reuse the AWS account already used by `pvc-backup`/pgBackRest. Takes effect on the next k3s restart, i.e. hop 1. | Off-site, native retention, `k3s etcd-snapshot ls` shows both. | Not Titan. Host config change. Credentials on the node. |
| C. Manual, per hop (minimum) | `ssh enterprise 'sudo cat <snap>' > scratch` then `ssh vega 'umask 077; cat > ~/etcd-snapshots/<snap>'`, plus sha256 | No cluster change | Not Titan. Manual. Passes through devbox, so delete the scratch copy afterwards. |

Recommendation: **A plus C for the pre-hop-6 snapshot.** Keep A afterwards as permanent DR. Also copy `/var/lib/rancher/k3s/server/token` once (encrypted at rest), because a restore on a rebuilt node needs it.

### 6.3 Restore drill (part of P2)

- Prove the snapshot is readable without touching the live cluster.
- **Option 1 (preferred, zero cluster impact):** on vega, in a throwaway directory, run the matching k3s binary's bundled etcd tooling, `k3s etcd-snapshot` list/inspect against a copy. Or at minimum check `etcdutl snapshot status <file>` from an etcd release tarball, which validates hash, revision and key count.
- **Option 2 (fuller):** restore into a disposable single-node k3s VM or container with `--cluster-reset --cluster-reset-restore-path` and the copied token, then `kubectl get ns`. Do not join it to anything, and destroy it afterwards because it contains the cluster's secrets.

### 6.4 Rollback procedure for a failed server hop

Kubernetes does not support in-place downgrades. Rollback means restoring the pre-hop snapshot with the previous binary. 🔒 **APPROVAL #6:** Lee decides that the hop has failed and that we restore.

1. In Git, revert commit A (and B if made) so that Flux and SUC do not immediately re-upgrade. If the API is up, also `flux --context home suspend ks cluster-apps-system-upgrade-controller-plans -n flux-system`.
2. On enterprise:
   ```sh
   sudo systemctl stop k3s
   sudo install -m 0755 /usr/local/bin/k3s.prev /usr/local/bin/k3s     # or the pre-downloaded previous version from vega
   sudo k3s server --config /etc/rancher/k3s/config.yaml \
        --cluster-reset --cluster-reset-restore-path=/var/lib/rancher/k3s/server/db/snapshots/pre-<V>-enterprise-<ts>
   # wait for "Managed etcd cluster membership has been reset, restart without --cluster-reset flag now"
   sudo systemctl start k3s
   ```
   - A restore may require the original token. k3s reads it from the existing data dir; on a rebuilt node, pass `--token` from the saved token file.
   - `k3s-killall.sh` is **not** needed and would kill every workload on the node.
3. If agents were already upgraded and are newer than the restored server, roll them back too (the kubelet must not be newer than the API server):
   - Use SUC with the reverted agent Plan, or the manual binary swap (vega directly; nova and ziti need P8).
   - Agents that are still on the old version need nothing.
4. Verify as in §4.4. Workloads that changed after the snapshot (scale-outs, new Secrets from cert-manager) re-converge via Flux and controllers. Any cert issued in the gap is re-issued.

For the etcd 3.5 → 3.6 hop (6), rollback is **only** by snapshot restore. etcd 3.5 cannot open a 3.6 data dir that has been written to, which is why the pre-hop-6 snapshot gets two off-node copies.

Agent-only failure: drain the node, put back the previous binary, restart k3s, uncordon. No etcd involvement.

---

## 7. Capacity and topology

### 7.1 enterprise (single control plane)

- Only node-level pods run on it (all expected):
  - DaemonSets: cilium, cilium-envoy, csi-nfs-node, intel-gpu-plugin, node-feature-discovery-worker, node-exporter, loki-canary, vector-agent, netbird
  - The `kube-vip-enterprise` static pod
  - The `system-upgrade-controller` Deployment
- Requests total 130m CPU and 70 Mi, yet actual memory is 119% of allocatable. The control plane itself (k3s-server about 1.6 GiB RSS plus etcd) dominates, which allocatable doesn't account for.
- Today's live CPU was 23% (load 1.3–2.2). The 87% figure from earlier checks is consistent with periodic spikes; Prometheus history was unavailable for confirmation.
- **Can the server hop be done safely?** Yes, with conditions. The risk is a k3s restart plus apiserver storage migration spiking memory with no swap and about 2 GiB headroom, which could end in an OOM kill or slow start. August also showed CPU starvation causing leader-election loss loops.
- Mitigations, in order of value:
  1. **Keep enterprise cordoned** (hence P3: SUC must not uncordon it).
  2. Pre-hop gate: ≥1.5 GiB `available`, load < 4. Abort the window if not.
  3. Optional (Git): keep `loki-canary` and `vector-agent` off enterprise via node affinity. That saves roughly 150–250 MiB but loses enterprise logs. Probably not worth it; leave vector, drop loki-canary.
  4. Move `system-upgrade-controller` to a worker (Deployment affinity). The SUC *jobs* still run on enterprise for the server Plan.
  5. **Hardware (best, cheapest real fix):** add RAM to enterprise (16 GiB). Lee to check whether the box supports it. Ideally do this before the campaign.

### 7.2 vega: uncordon? **Yes, before the campaign (P4, 🔒)**

- vega has 12 CPU / 31 GiB, is idle, has been Ready since its 2026-10-22 reboot with 0 k3s restarts, and has been cordoned since the 2026-08-30 NotReady incident.
- The root cause of that incident was never established (open incident `docs/journal/2026-08-30-vega-notready-and-node-storage.md`), hence the 24 h soak in P4.
- Without vega, draining **nova** (72 pods, 5.9 GiB requested) onto **ziti** (69% memory requested, 16 GiB) does not fit. Pods would sit Pending, or ziti would hit the same ephemeral-storage/memory pressure that evicted half the cluster on 2026-08-30.
- With vega schedulable, every agent drain has ample room.
- If Lee prefers not to uncordon:
  - Run the campaign **without agent drains**: remove `drain` and use only k3s restarts, where pods keep running thanks to `KillMode=process`.
  - Accept that containerd 1.7 → 2.x (hop 4) restarts containers in place.
  - Keep vega cordoned and let SUC upgrade it, which requires removing `drain` so SUC doesn't uncordon it.

### 7.3 Second control-plane node: **later, not during this campaign**

| Option | Pros | Cons |
|---|---|---|
| **A. Stay single-CP for the campaign; add HA afterwards (recommended)** | Simplest hops (one etcd member, one restore path). Snapshots give recovery. | Single point of failure continues for weeks |
| B. Add one server now (2 members) | Second copy of data | **Worse availability**: 2-member etcd needs both for quorum. Doubles the server-hop work. etcd 3.5 → 3.6 with mixed members is riskier. |
| C. Add two servers now (3 members, e.g. vega plus one) | Real HA during the campaign | Converting an agent to a server means a re-install (no in-place promotion). Ansible role history includes the 2026-08-07 self-promotion accident. Much more work and risk up front. |
| D. Later: move the control plane to vega (add vega as server, remove enterprise's member) | Control plane on 12 CPU / 31 GiB instead of 4 / 7.6. Fixes the capacity issue permanently. | etcd member add/remove on a single-member cluster. Do it on a healthy, current cluster with snapshots, not mid-campaign. |

Recommendation: **A now**, then a follow-up project for **3 servers** (enterprise, vega, plus nova or new hardware), or **D** if a third box isn't available. 🔒 Lee decides; this is outside the scope of the upgrade go-ahead.

---

## 8. kube-vip restarts (424)

- **Evidence:**
  - The previous container exited 1 at 2026-11-01 23:47:28Z with `level=fatal msg="crash: default route deleted and the default interface may be invalid"`.
  - Startup logs say `No interface is specified for VIP in config, auto-detecting default Interface`.
  - The 2026-08-30 SRE review saw the same in-window "default-route-loss" restart.
- **Mechanism:**
  - kube-vip (v0.6.0, and still in v1.3.x in a softer form) runs `MonitorDefaultInterface` **only when `vip_interface` is not set**, and exits when the default route on that link is deleted.
  - enterprise's IPv4 default route is static (`/etc/network/interfaces`), but `iface enp3s0 inet6 auto` also installs an **IPv6 default route learned from router advertisements, with an expiry** (`proto ra … expires 1760sec`). When an RA is late or the router re-advertises, that route is removed and re-added, and kube-vip crashes.
  - Additional restarts in Aug 2026 came from API/etcd outages (lease loss), which is expected behaviour.
  - NetBird's host-network `wt0` route manipulation is a less likely contributor.
  - Confidence: high on the IPv6-RA hypothesis (code path plus config). It was not proven at runtime: the journal grep on enterprise was aborted because it was too heavy for the node.
- **Impact:**
  - Each crash drops the VIP `10.20.30.250` for about 10–15 s.
  - Cilium's `k8sServiceHost` and every agent's server URL point at the VIP, so each crash is a brief cluster-wide API outage for node agents and Cilium. This also adds noise to helm-controller webhook failures.
- **Fix before the upgrade? Yes (P5).** It's cheap and it removes a confounder from hop verification:
  1. Add the following to the kube-vip env in `ansible/playbooks/templates/kube-vip-static-pod.yaml.j2` and to the live static-pod file on enterprise (edited directly, not through the playbook, see P1):
     ```yaml
     - name: vip_interface
       value: enp3s0
     ```
  2. Optional, Lee's call: stop accepting IPv6 RA default routes on enterprise (`accept_ra_defrtr=0`), if IPv6 egress from the control plane isn't needed.
  3. Later, separately: bump kube-vip v0.6.0 → v1.3.x, keeping `vip_interface` set.
- **Verify:** the restart count stays flat for 7 days.

---

## 9. Downtime estimate

| Event | Per occurrence | Count | Total |
|---|---|---|---|
| API unavailable (server k3s restart) | 1–3 min (hop 6: up to about 5–10 min for etcd 3.6 data migration) | 9 | about 20–45 min |
| VIP blip (P5) | about 10 s | 1 | – |
| Singleton app restart from agent drains (HA, ESPHome, Mosquitto, Immich, PgBouncer/Postgres, cert-manager, Flux controllers, Grafana…) | 1–5 min per pod | about 3 drains per hop | each app is down about 1–5 min, 1–3 times per hop |
| nova-pinned device apps (Frigate, Z-Wave JS) | 5–15 min | 9 | about 45–135 min total |
| Ingress | 2 replicas (nova, ziti): about 0 if both are not drained at once, which `concurrency: 1` ensures | | about 0 |
| Add-on bumps (Cilium especially, a rolling agent restart) | brief per-node datapath restarts | about 2 | seconds per pod |

Wall-clock (cumulative interruption is shorter):
- Prerequisites: 1–2 sessions, plus a 24 h vega soak.
- Hops: about 45–75 min each including verification, plus interleaved add-on PRs.
- Suggested grouping:
  - Evening 1: P-steps plus hops 1–2
  - Evening 2: hops 3–5
  - Evening 3: hop 6 alone (etcd 3.6), plus 7
  - Evening 4: hops 8–9, or stop at 8

---

## 10. Risks

| # | Risk | Likelihood / impact | Mitigation |
|---|---|---|---|
| 1 | **Single control-plane / etcd node.** A failed hop, OOM or disk fault means a total control-plane outage. Snapshots today are on the same disk only. | med / **critical** | P2 off-node snapshots plus restore drill; pre-hop snapshot every hop; rollback §6.4; RAM upgrade |
| 2 | **etcd 3.5 → 3.6 at hop 6.** Upstream now wants ≥3.5.32 first; k3s 1.32.13 ships 3.5.26. | low-med / critical | Extra snapshot with 2 off-node copies; hold point; read k3s 1.33.x release notes and issues at execution time; abort and restore if etcd fails to start |
| 3 | **SUC uncordons enterprise** (`cordon: true` plus handle_batch uncordon), re-creating the August scheduling storm on a 4-CPU/7.6 GiB node | high if unfixed / high | P3: remove `cordon` from the server Plan; verify the cordon after each hop |
| 4 | **Flux / k8s version lockstep.** Flux 2.0 is unsupported beyond 1.27; the 2.7/2.8 API removals break reconciliation if the migration is skipped; the stale `bootstrap/` v2.9.6 being applied early would break Flux | med / high (GitOps stalls, including for rollback) | §2 table; matching CLI per step; never apply `bootstrap/` mid-campaign; `flux migrate` before 2.7 |
| 5 | **enterprise memory headroom** (119% of allocatable, about 2 GiB available, no swap) leading to OOM or leader-election loops during k3s start | med / high | Pre-hop gate; keep cordoned; move SUC controller off; RAM upgrade |
| 6 | **ingress-nginx retired**: no supported release for 1.36 and no security fixes | certain / med | Stop at 1.35, or migrate ingress (§5.3) |
| 7 | **Drain capacity**: without vega, nova's drain doesn't fit on ziti; no PodDisruptionBudgets exist anywhere, so singletons go down on every drain | high / med | P4 uncordon vega; announce windows; `concurrency: 1` |
| 8 | **Add-ons already outside support** (Cilium 1.18 on 1.27–1.29, cert-manager 1.17 EOL) | low-med / high (CNI) | Cilium and cert-manager step-ups at the stated versions; one Cilium minor at a time with pre-flight |
| 9 | **kube-vip crash during a hop** confuses verification or extends the API outage | med / med | P5 before starting |
| 10 | **nova/ziti unverifiable over SSH** (host keys changed): no manual fallback, rollback or cgroup check on the two busiest nodes | med / med | P8 by Lee; rely on SUC; agent rollback via SUC only |
| 11 | containerd 2.x at hop 4 with an unknown custom template on nova/ziti | low / med | P8 check; full drain at hop 4 |
| 12 | Ansible pin 1.29.1: an accidental playbook run skips a minor | low / high | P1 |
| 13 | Prometheus 2.45 / kube-state-metrics on very new k8s: metrics gaps | med / low | Accept; kube-prometheus-stack modernisation after the campaign |

---

## 11. Where Lee's approval is required

| # | Gate | What is approved |
|---|---|---|
| 1 | P0 | This plan, the target (1.36.6 vs the 1.35.10 stop point), and the maintenance windows |
| 2 | P2 | etcd off-node mechanism (CronJob to Titan, S3, or manual) and the restore drill |
| 3 | P4 | Uncordoning vega (or choosing the no-drain variant) |
| 4 | P5 | kube-vip static-pod change on enterprise (VIP blip), and optional IPv6 RA change |
| – | P3 / P6 / add-ons | Each Git PR to `main` touching SUC, Flux, Cilium, cert-manager, external-dns or ingress (infra tier: diff shown first) |
| 5 | §5.3 | ingress-nginx: stop at 1.35, run unsupported on 1.36, or migrate first |
| per hop | §4.3 | Go for hop N, server commit A, then go for agents commit B (two approvals per hop; Lee can batch them as "go for hop N end-to-end") |
| 6 | §6.4 | Declaring a hop failed and restoring etcd from snapshot |
| 7 | §7.3 | Any second control-plane / HA work (separate project) |

Without approval, workers may do only read-only checks, plus preparing PRs as drafts.

---

## 12. Out of scope / follow-ups

- Hop 10 to 1.37.x, once Cilium 1.21, cert-manager 1.22 and a PGO release with 1.37 ship.
- Control-plane HA (§7.3).
- kube-prometheus-stack 47 → current; Loki/Vector health; Flux notification Alerts (none exist).
- kube-vip v1.x; NetBird image pin; NFD restart counts.
- Remove the stale `k3s-v1.29.1+k3s1` hard-link set left by the Ansible role once the role is re-pinned (cosmetic).
- Enable k3s secrets-encryption, so etcd snapshots don't hold plaintext Secrets.
