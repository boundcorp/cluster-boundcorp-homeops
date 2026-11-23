# k3s upgrade plan: v1.27.3+k3s1 → v1.36.6+k3s1

Status: **hop 1 done (1.28.15); hop-2 prerequisites done. Next gate: hop 2 (1.28 → 1.29), §11 and §4.7.**
- Written 2026-11-19 from read-only checks of the `home` cluster, read-only SSH to `enterprise` and `vega`, this repo, and upstream release notes.
- Updated 2026-11-23:
  - Prerequisites done 2026-11-20 to 2026-11-23 (§3). Full record: `docs/journal/2026-11-20-k3s-upgrade-prereqs.md`.
  - **vega is unavailable**; it is excluded from the campaign (§7.2).
  - Drain capacity recomputed for two workers (§7.2), with downtime figures to match (§9).
  - Hop-1 readiness check and exact procedure added (§4.6).
  - **Hop 1 done** 2026-11-23 17:25–17:31Z, no rollback: enterprise, ziti and nova on v1.28.15+k3s1, vega still v1.27.3+k3s1. Record: `docs/journal/2026-11-23-k3s-hop1.md`.
  - **Hop-2 prerequisites done** 2026-11-23 18:14–19:05Z (§3, "Hop-2 prerequisites"): stale k3s `coredns` HelmChart removed, ingress-nginx at 2 replicas with a PDB, Flux 2.3.0 with every repo manifest on HelmRelease v2 / source v1, SUC agent `postCompleteDelay`. Record: `docs/journal/2026-11-20-k3s-upgrade-prereqs.md`, last section.
  - Hop-2 readiness and the revised procedure added (§4.7).

Every step that changes the cluster, a node, or Git `main` needs Lee's approval at the gates marked **🔒 APPROVAL**. The k3s control plane, etcd, ingress, Flux and NetBird are load-bearing infrastructure, so none of this is auto-approved under the Sandbox tier.

---

## 0. TL;DR

| Item | Recommendation |
|---|---|
| **Target** | **v1.36.6+k3s1**, the latest patch of the 1.36 minor |
| **Why not v1.37.2 (k3s `stable`)?** | No released Cilium, cert-manager or Crunchy PGO version lists k8s 1.37 support yet. 1.37 also moves embedded etcd to 3.7. Make it a follow-up hop once those projects ship support. |
| **Fallback stop point** | **v1.35.10+k3s1** if the ingress-nginx decision (§5.3) is not made in time. Upstream supports 1.35 until about Feb 2027. |
| **Hops** | **9** server hops, one minor at a time: 1.28.15 → 1.29.15 → 1.30.14+k3s2 → 1.31.14 → 1.32.13 → 1.33.13+k3s2 → 1.34.13 → 1.35.10 → 1.36.6 |
| **Mechanism** | The system-upgrade-controller (SUC, now v0.21.1) Plans, driven by Git commits (GitOps). Manual binary swap over SSH is the fallback for `enterprise` only (vega is out; nova/ziti host keys are unverified). |
| **Workers** | **nova and ziti only.** vega hung hard under load on 2026-11-21 and stays cordoned. It is excluded from the agent Plan (A0, `e80d04b`) and **remove it from the cluster before hop 4** (kubelet skew, §7.2). |
| **Flux** | Flux has to move in lockstep. It is now **v2.3.0** (supports 1.28–1.30), and Flux 2.10 needs ≥1.35. Bump it via `kubernetes/flux/config/flux.yaml` between hops. **Do not** `kubectl apply -k kubernetes/bootstrap` (it is pinned to v2.9.6, which needs k8s ≥1.34). |
| **Hard prerequisites** | **Done:** off-node etcd snapshots to titan plus a restore drill; Plan fixes so SUC doesn't uncordon `enterprise`; the kube-vip interface fix; Flux 2.2.3; SUC v0.21.1 on a worker (§3). Hop 1 done with vega excluded and cordon-only agents. Hop-2 prerequisites done: Flux 2.3.0 plus HelmRelease v2, ingress-nginx ×2 with a PDB, stale `coredns` HelmChart gone, SUC agent pause (§3). |
| **Agent mode** | With two 16 GiB workers, **neither can absorb the other's drain** (§7.2). Recommended: **cordon-only agent upgrades** (no drain; pods keep running through the k3s restart), with drain plus a scale-down only at hop 4 (containerd 2). The alternative is to drain every hop and scale down Loki and Immich (and optionally speakr and *arr) each time. |
| **Estimated downtime** | Control-plane API: about 2–5 min per hop, roughly 20–45 min total. Cordon-only agents: about 0 app downtime per hop, apart from a ~1 min kubelet gap per node. Drain variant: Loki/Immich down about 20–40 min per hop, each singleton 1–5 min twice per hop, and Frigate/Z-Wave 5–15 min per hop. Wall-clock: about 3–4 evenings (§9). |

---

## 1. Current state (observed 2026-11-19, updated 2026-11-23)

### 1.1 Nodes

Usage figures are from 2026-11-23 03:30Z (`kubectl top nodes`, `kubectl describe nodes`).

| Node | Role | Status | k3s | OS / kernel | Capacity | Live usage |
|---|---|---|---|---|---|---|
| enterprise (10.20.30.111) | control-plane, sole embedded-etcd member | Ready, **cordoned** since 2026-08-04 | v1.27.3+k3s1 | Debian 12, 6.1.0-10, cgroup v2 | 4 CPU, 7.6 GiB (5.6 GiB allocatable), no swap | 936m CPU; 6.7 GiB = **119% of allocatable**, 2.0 GiB `available`, load 1.2. Requests are only 130m / 70 Mi (DaemonSets). |
| vega (.112) | agent | Ready, **cordoned, unavailable**. Hard hang under load on 2026-11-21; see §7.2. | v1.27.3+k3s1 | Debian 12, 6.1.0-10, cgroup v2 | 12 CPU, 31 GiB | Idle: only the 9 DaemonSet pods, and no PVs bound to it. Rebooted by hand 2026-11-21 16:07Z. |
| ziti (.113) | agent | Ready | v1.27.3+k3s1 | Debian 13, 6.12 | 8 CPU, 16 GiB (15.5 GiB allocatable) | 14 non-DS pods. Memory requests **10.7 GiB (68%)**, 9.6 GiB of it `loki-chunks-cache-0`. Working set 7.0 GiB (45%). Runs the Postgres primary. |
| nova (.114) | agent | Ready | v1.27.3+k3s1 | Debian 13, 6.12 | 8 CPU, 16 GiB (15.5 GiB allocatable) | 41 non-DS pods. Memory requests 5.9 GiB (37%). Working set **13.0 GiB (83%)**. Runs Frigate (Coral), Z-Wave (USB) and now the SUC controller. |

**After hop 1 (2026-11-23):** enterprise, ziti and nova are v1.28.15+k3s1 (containerd 1.7.22-k3s1.28); vega is v1.27.3+k3s1 (containerd 1.7.1-k3s1), still cordoned and excluded. The table above is the pre-hop-1 snapshot. The API server certificate is valid until 2027-04-16 and the CAs until 2034.

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
- **Version pin (fixed in P1, `20bdb52`).** `ansible/inventory/group_vars/kubernetes/main.yaml` used to pin `k3s_release_version: v1.29.1+k3s1` while the cluster runs v1.27.3. Any run of the k3s role, **including `cluster-kube-vip.yaml`**, would have jumped nodes straight to 1.29. It is now pinned to v1.27.3+k3s1. Bump it in the same commit as each server hop.
- **system-upgrade-controller:** deployed via Flux (`kubernetes/apps/system-upgrade/`), now at **v0.21.1** (`3d97f22`, `673b2b7`). The controller runs on a worker (nova at the moment): the upstream control-plane affinity is overridden and vega is excluded. Plans `server` and `agent` are both at `v1.27.3+k3s1`, `Complete=True`, no Jobs.
  1. **Server plan: `cordon: true` removed (P3, `002191f`).** SUC (v0.11.0 through v0.21.1) sets `Unschedulable=false` after a successful job whenever `cordon` or `drain` is set (`pkg/upgrade/handle_batch.go`). With `cordon: true` the first server hop would have **uncordoned enterprise** and repeated the 2026-08-04 recovery storm. enterprise stays cordoned by hand.
  2. **Agent plan: `drain` added (P3).** Because of (1), SUC uncordons each worker afterwards. With only two workers, drains do not fit (§7.2), so hop 1 should switch the agent plan to `cordon: true` without `drain` (§4.6, commit A0). Either way SUC uncordons the node afterwards, which is fine for nova and ziti.
  3. **The agent plan still selects vega.** vega has no control-plane label, so the agent plan would drain or cordon vega, upgrade it, and then **uncordon** it. That violates "vega stays cordoned". Exclude it in Git (`kubernetes.io/hostname NotIn [vega]`) before any agent version bump (§4.6, A0).
  4. **Agent order is deterministic.** SUC sorts candidates by sha256(node UID + plan UID + latestHash), so it can be computed in advance (§4.3). The agent plan's `prepare: [prepare, server]` init step blocks agents until the server plan has completed, which is a good safety net.
  5. **Plan durations must be strings** (e.g. `timeout: "900s"`). SUC ≥ v0.16.3 decodes an int into an int32 `IntOrString`, so 900 s in ns overflows and the controller stops listing Plans (seen and fixed on 2026-11-23).
  6. **Agent sequencing (checked 2026-11-23 in the v0.21.1 source and the hop-1 controller log).** When an agent's Job completes, SUC writes the plan-hash label and `Unschedulable=false` in **one** node update; with `concurrency: 1` the next node is only selected once the previous one carries the hash. Hop 1's log: `JobComplete` on ziti at 17:30:07.066, nova's Job created at 17:30:07.089. So the two agents were never cordoned together; the "~10 s overlap" in the hop-1 journal was the 13 s watcher sampling. The real gap was that ziti had only ~40 s after its kubelet restart before nova was cordoned. Fixed with `postCompleteDelay: "300s"` on the agent Plan (`ccddf12`): the node is uncordoned at once, but its hash label (and therefore the next node's Job) waits 5 min. It is not part of the hash. The hop procedure additionally gates per node via `nodeSelector` (§4.7).

### 1.3 Flux

- **Running:** distribution **v2.3.0** since 2026-11-23 (`52aab2b`): helm-controller v1.0.1, kustomize/source/notification v1.3.0, image-reflector v0.32.0, image-automation v0.38.0. It was v2.0.1 when this plan was written and v2.2.3 from P6 (`d2ab9e9`) through hop 1. It is self-managed via `kubernetes/flux/config/flux.yaml` (OCIRepository `flux-manifests` tag `v2.3.0`). 2.3.0 is the only 2.3.x release. The HelmRelease CRD serves v2 (storage), v2beta2 and v2beta1 (deprecated).
- **CLI on devbox:** v2.9.6. It does not match the cluster: `flux reconcile ks` works, but it can't read OCIRepository, and `flux get hr` is unreliable. **Use `kubectl get ks,hr -A`**, or a CLI matching the cluster version, downloaded per step to the scratchpad.
- **`kubernetes/bootstrap/kustomization.yaml`** was bumped to `flux2/manifests/install?ref=v2.9.6` in 87a6f54 and was "pending manual apply". **Do not apply it.**
  - Flux 2.9 needs k8s 1.34–1.36 and has removed `helm v2beta1`, `source v1beta1` and `v1beta2`, which this repo still uses.
  - It would also fight `flux.yaml`, which pins the OCI tag (now v2.3.0).
  - Flux upgrades in this plan go through `flux.yaml`. Leave `bootstrap/` alone until the end, then re-align it to the final version.
- **Repo Flux API usage** (migrated 2026-11-23, `8b7e0c0` sources, `73c7055` `bfdfb73` `c1a027e` `ebd2372` `a2ba928` HelmReleases; zero Helm upgrades):

  | API | Objects | Notes |
  |---|---|---|
  | `helm.toolkit.fluxcd.io/v2` | 35 files | was v2beta1; plus 7 `ks.yaml` healthChecks |
  | `source.toolkit.fluxcd.io/v1` | 22 HelmRepository + 1 GitRepository | were v1beta2 / v1beta1 |
  | `source.toolkit.fluxcd.io/v1beta2` | 1 | OCIRepository `flux-manifests`: the only version Flux 2.3 serves (v1 arrives in 2.6) |
  | `kustomize.toolkit.fluxcd.io/v1` | all | `external-snapshotter-crds` was v1beta2 |
  | `notification.toolkit.fluxcd.io/v1` | 1 | |

  - **Still needed before Flux 2.7** (which drops the beta versions): run `flux migrate` in-cluster to rewrite stored objects, then confirm CRD `status.storedVersions` hold only `v1`/`v2`. Today they are HelmRelease `[v2beta1, v2beta2, v2]`, HelmRepository and HelmChart `[v1beta2, v1]`, Alert/Provider `[v1beta2, v1beta3]`, ImageUpdateAutomation `[v1beta1, v1beta2]`. kustomize-controller skips unchanged objects, so the migrated HRs still carry a `kustomize-controller` managedFields entry for v2beta1 until their spec next changes.
  - The orphan HelmRelease `media/filebrowser` (removed from Git in `87a6f54`, still live because `cluster-apps` has `prune: false`) is not covered by a repo migration. Delete or migrate it before 2.7 (🔒 Lee).

### 1.4 Deprecated / removed Kubernetes APIs

- **Core Kubernetes APIs:** no change is needed for 1.28–1.36.
  - Live `apiserver_requested_deprecated_apis` shows only:
    - `componentstatuses v1` (deprecated, not removed; a kubectl/monitoring read)
    - `cilium.io/v2alpha1 ciliumnodeconfigs` (Cilium itself)
    - Flux `kustomize v1beta2` and `source v1beta1`
  - The rendered manifests of every HelmRelease (`helm get manifest`) use only GA APIs: v1, apps/v1, rbac/v1, networking/v1, admissionregistration/v1, apiextensions/v1, apiregistration/v1, storage/v1, monitoring.coreos.com/v1.
  - The repo has no `flowcontrol`, `policy/v1beta1`, `batch/v1beta1`, or `autoscaling/v2beta*`.
  - The only Kubernetes API removals in range are flowcontrol `v1beta2` (1.29) and `v1beta3` (1.32), and nothing here uses them.
- **Re-checked 2026-11-23:** no series with a `removed_release` set. Only Flux's own CRD versions (helm v2beta1, kustomize v1beta2, notification v1beta2, source v1beta1) plus `componentstatuses` and Cilium's `ciliumnodeconfigs` show up. Nothing is removed in 1.28.
  - A `flowcontrol v1beta2` hit may appear after 2026-11-23 03:35Z. It comes from a read-only probe in the readiness check, not from a workload.
- **pluto/kubent:** not installed on devbox. Nothing was installed for this plan; the metric plus the manifest scan above cover the same ground.
- **Objects that must change before a specific hop:**

| Before | Object(s) | Change |
|---|---|---|
| Flux 2.3 (after hop 1) | 35 HelmReleases | ✅ done 2026-11-23: `helm.toolkit.fluxcd.io/v2beta1` → `v2` in the repo, plus sources → `v1` |
| Flux 2.6/2.7 (after hops 4–5) | 3 `source v1beta1`, 21 `source v1beta2`, 1 `kustomize v1beta2`; cluster storedVersions | → `source.toolkit.fluxcd.io/v1` and `kustomize v1`; run `flux migrate` in-cluster. **Must be done before Flux 2.7.** |
| hop 6 (1.33) | `external-dns` chart 1.13.0 (app 0.13.5) | ≥ app 0.18 (EndpointSlices); the latest is chart 1.24.0 / app 0.24.0 |
| Cilium 1.19/1.20 bump | `CiliumL2AnnouncementPolicy` (v2alpha1, the only served version) and `CiliumLoadBalancerIPPool` (v2alpha1 in repo; v2 is already served) | Move the IP pool to `cilium.io/v2` now. Recheck the L2 policy's API in the Cilium 1.19/1.20 upgrade notes when bumping. Both objects are in the repo and in the Ansible template `custom-cilium-l2.yaml.j2`. |
| hop 4 (1.31) | **vega** (kubelet v1.27.3) | A 1.27 kubelet against a 1.31 API server is 4 minors of skew, outside the n-3 policy. **Remove vega from the cluster** before hop 4's server commit, or upgrade it idle-only (§7.2) |
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
- The kubelet skew policy (n-3 once the API server is ≥1.28; n-2 before that) would technically let agents skip minors. This plan **upgrades agents on every hop** anyway: it keeps nodes uniform, keeps each blast radius small, and is what the SUC agent Plan does naturally.
- If time gets tight, an acceptable compromise is to let agents trail the server by **at most one minor**, i.e. upgrade agents every second hop. Never run agents more than 2 minors behind, and never newer than the server.

### Add-on moves interleaved with hops

Each row is a separate PR or commit. Do them while the cluster sits at the stated k8s version, and let them settle before the next hop.

| When (cluster at) | Flux (via `flux.yaml` tag) | Other add-ons |
|---|---|---|
| 1.27 (before hop 1) | 2.0.1 → **2.2.3** ✅ done (`d2ab9e9`) | SUC v0.11.0 → v0.21.1 ✅ done (`3d97f22`, `673b2b7`). Still open, not blocking: external-snapshotter / source v1beta1 cleanup, Cilium IP pool → v2. |
| 1.28 (after hop 1) | → **2.3.0** ✅ done (`52aab2b`). Repo HelmReleases → `helm.toolkit.fluxcd.io/v2`, sources → v1 ✅ done (zero Helm upgrades). | stale k3s `coredns` HelmChart removed ✅; ingress-nginx ×2 + PDB ✅ (`33f0d04`); SUC agent `postCompleteDelay` ✅ (`ccddf12`) |
| 1.29 | → **2.4.x** (1.29–1.31) | |
| 1.30 (before hop 4) | → **2.5.x** (1.30–1.32) | metrics-server chart → app 0.7.x. **vega removed from the cluster** (or upgraded idle-only to 1.30) before hop 4, see §7.2. |
| 1.31 | → **2.6.x** (1.31–1.33). `flux migrate -v 2.6 -f .` on the repo, then `flux migrate` in-cluster (storedVersions → v1/v2) | Cilium 1.18 → **1.19** latest patch (1.31–1.34). cert-manager 1.17 → next minors one at a time (per cert-manager's support table). metrics-server → 0.8.x |
| 1.32 | → **2.7.x** (1.32–1.34). Removes `v1beta1` and helm `v2beta1`, so it fails if the migration was skipped. | cert-manager continue |
| 1.32 (before hop 6) | | **external-dns → chart 1.24.x / app ≥0.18** (required for 1.33) |
| 1.33 | → **2.8.x** (1.33–1.35) | Cilium 1.19 → **1.20.x** (1.33–1.36). cert-manager → **1.21.x** (1.33–1.36). |
| 1.34 | → **2.9.x** (1.34–1.36) | metrics-server → 0.9.x (1.34+). NFD → 0.17+ (CRDs separately). intel-gpu-plugin → 0.36.x. |
| 1.35 | → **2.10.x** (1.35–1.37) | ingress decision executed (§5.3) |
| 1.36 (done) | re-align `kubernetes/bootstrap/kustomization.yaml` to the running Flux version | kube-prometheus-stack 47 → current (separate project, see §5.6) |

---

## 3. Prerequisites (pre-flight, all at v1.27.3)

✅ = done, ☐ = open, ✗ = dropped. The details of every done step are in `docs/journal/2026-11-20-k3s-upgrade-prereqs.md`. No k3s version was changed and no Ansible playbook was run.

| # | Step | Status | Commit / evidence |
|---|---|---|---|
| P0 | 🔒 Approve the plan and a maintenance calendar | ◐ Prerequisites approved by Lee on 2026-11-20 18:31 UTC; maintenance windows not picked yet | – |
| P1 | Re-pin Ansible `k3s_release_version` to v1.27.3+k3s1 | ✅ 2026-11-20 | `20bdb52` |
| P2 | Off-node etcd snapshots plus restore drill | ✅ 2026-11-20: CronJob to titan (00:30/12:30 PT, sha256-verified) and a full `--cluster-reset` restore drill on an isolated container. **Passed.** | `5427837`; drill: journal §4 |
| P3 | SUC Plans: no `cordon` on the server plan, `drain` on the agent plan | ✅ 2026-11-20 | `002191f` |
| P3b | SUC controller v0.11.0 → v0.21.1 | ✅ 2026-11-23. CRB renamed (immutable roleRef), controller on a worker, `drain.timeout` changed to a string. Verified inert: 0 Jobs, hash unchanged, cordons unchanged. | `3d97f22`, `673b2b7`; journal `ce8eaa7` |
| P4 | Uncordon vega | ✗ **Dropped.** The 2026-11-21 load test hung vega in stage c (§7.2). vega stays cordoned (Lee, 2026-11-21 16:30). | journal `0f4bb20`, `0eae34a` |
| P5 | kube-vip `vip_interface: enp3s0` | ✅ 2026-11-20 19:00 UTC: template plus a hand edit on enterprise. VIP blip ~15 s. 0 restarts since (checked 2026-11-23 03:32Z, 2d8h). | `3307982` |
| P6 | Flux 2.0.1 → 2.2.3 | ✅ 2026-11-20. 44/44 ks, 34/34 hr Ready (`speedtest-exporter` suspended on purpose) | `d2ab9e9` |
| P7 | Health baseline | ✅ re-run 2026-11-23 as the hop-1 readiness check (§4.6) | – |
| P8 | (Lee) re-verify the nova/ziti SSH host keys; check cgroup v2 and the containerd template there | ☐ Not blocking for hop 1. It is the only way to roll back an agent by hand, and it is needed before hop 4 (containerd 2) | – |
| P9 | Pre-download the k3s binaries and `sha256sum-amd64.txt` for v1.28.15 … v1.36.6 | ☐ Target is now **enterprise** (`/root/k3s-binaries/`, 93 GB free), not vega. Only needed for the manual enterprise fallback | – |
| P10 | Announce the window | ☐ Needed per hop | – |
| P11 | **Exclude vega from the agent Plan** (`kubernetes.io/hostname NotIn [vega]`) | ☐ Part of hop 1, commit A0 (§4.6) | – |
| P12 | **Choose the agent mode**: cordon-only (recommended) or drain plus scale-down (§7.2) | ☐ 🔒 Lee, at the hop-1 gate | – |
| P13 | **vega: remove from the cluster** (recommended) or upgrade idle-only | ☐ 🔒 Lee, at the latest before hop 4 (§7.2) | – |

Rules learned during the prerequisites:
- **Plan durations must be strings** (`"900s"`), see §1.2 item 5.
- `kubectl --context home -n kube-system create job --from=cronjob/etcd-snapshot-offload <name>` copies an on-demand snapshot to titan.
- Leave both Plans' `version` alone outside an approved hop. The plan hash only covers `latestVersion`, `serviceAccountName` and secrets, so edits to `drain`, `cordon`, `nodeSelector`, `tolerations` or `postCompleteDelay` are inert.

### Hop-2 prerequisites (2026-11-23, after hop 1)

Go-ahead: Lee, 2026-11-23 18:09 UTC, items H1–H4 only. No k3s or Plan version change. Pre-change snapshot `pre-hop2-prereqs-20261123T1814Z-enterprise-1795457679` on titan, sha256 `cca496a3…` verified. Details: `docs/journal/2026-11-20-k3s-upgrade-prereqs.md`, last section.

| # | Step | Status | Commit |
|---|---|---|---|
| H1 | Stale k3s HelmChart `kube-system/coredns` (chart 1.24.1, same Helm release `kube-system/coredns` as Flux's HelmRelease) | ✅ removed without a helm-delete job: annotate `helmcharts.helm.cattle.io/unmanaged`, drop the finalizer, delete. CoreDNS untouched (rev 8). Not re-created: no manifest file, `disable: coredns` set. Don't run `cluster-installation.yaml` against the live cluster; its template would re-create it. | journal `03eaf9f` |
| H2 | ingress-nginx 2 replicas, PDB minAvailable 1, topologySpread over nova/ziti (`nodeTaintsPolicy: Honor`, `matchLabelKeys: [pod-template-hash]`) | ✅ one pod per worker; LB via Cilium L2, no hostPort/hostNetwork | `33f0d04` |
| H3 | Flux 2.2.3 → 2.3.0, repo → HelmRelease v2 / source v1 / kustomize v1 | ✅ zero Helm upgrades across all 34 HRs (history and secret revisions identical, 129 release secrets before and after) | `52aab2b`, `8b7e0c0`, `73c7055`, `bfdfb73`, `c1a027e`, `ebd2372`, `a2ba928` |
| H4 | SUC agent pause between nodes | ✅ `postCompleteDelay: "300s"`, inert (hash `f9eb9866` unchanged, 0 Jobs over 5 min); per-node gating moved into the hop procedure (§4.7) | `ccddf12` |

---

## 4. Per-hop procedure (repeat for hops 1–9)

Set `V` to the hop's version, e.g. `V=v1.28.15+k3s1`. All kubectl/flux commands use `--context home`.

### 4.1 Pre-hop gate (read-only, about 5 min)

```sh
kubectl --context home get nodes -o wide                     # all Ready, all on previous version
kubectl --context home get ks,hr -A | grep -v True           # expect nothing (or only known-bad, e.g. loki)
kubectl --context home get pods -A | grep -Ev 'Running|Completed'   # expect nothing new vs baseline
kubectl --context home get ds -A | awk '$3!=$5'              # DaemonSets fully available
kubectl --context home get nodes -o custom-columns=N:.metadata.name,U:.spec.unschedulable   # enterprise + vega true, nova + ziti empty
kubectl --context home -n kube-system get pod kube-vip-enterprise   # restarts still 0 (since 2026-11-20 19:00Z)
kubectl --context home -n system-upgrade get plans,jobs           # Plans Complete, no Jobs
kubectl --context home -n kube-system logs job/$(kubectl --context home -n kube-system get jobs -o name | grep etcd-snapshot-offload | tail -1 | cut -d/ -f2) | tail -5   # newest titan copy < 24 h
ssh enterprise 'free -m; uptime'                             # need ≥1.5 GiB "available", load < 4
```

### 4.2 Snapshot (enterprise, about 2 min)

```sh
ssh enterprise "sudo k3s etcd-snapshot save --name pre-${V//+/-}"
ssh enterprise 'sudo ls -la /var/lib/rancher/k3s/server/db/snapshots/ | tail -3'
kubectl --context home -n kube-system create job --from=cronjob/etcd-snapshot-offload pre-hop-${V%%+*}   # copy to titan; job name must be DNS-safe
kubectl --context home -n kube-system logs -f job/pre-hop-${V%%+*}   # expect "copied pre-…  <bytes> sha256=…" (sha256 checked against the source)
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
- SUC upgrades **nova and ziti** one at a time (`concurrency: 1`), then uncordons each. vega must already be excluded (§4.6 A0).
- From hop 2 on, commit B is split per node (B1 first agent only, B2 the second) and the agent Plan has `postCompleteDelay: "300s"`. See §4.7.
- Depending on the agent mode (§7.2):
  - **Cordon-only:** each node is cordoned, k3s restarts in place, and pods keep running (`KillMode=process`). Expect about 1 min of kubelet gap per node.
  - **Drain:** each node is drained. Commit B must be preceded by the scale-down commit (§7.2) and followed by its revert.
- The order is deterministic: sha256(node UID + agent plan UID + latestHash), computed 2026-11-23 for the current Plan object. It changes if the Plan is deleted and recreated.

  | Hop | Version | Agent order |
  |---|---|---|
  | 1 | v1.28.15+k3s1 | ziti → nova |
  | 2 | v1.29.15+k3s1 | ziti → nova |
  | 3 | v1.30.14+k3s2 | nova → ziti |
  | 4 | v1.31.14+k3s1 | nova → ziti |
  | 5 | v1.32.13+k3s1 | nova → ziti |
  | 6 | v1.33.13+k3s2 | nova → ziti |
  | 7 | v1.34.13+k3s1 | ziti → nova |
  | 8 | v1.35.10+k3s1 | ziti → nova |
  | 9 | v1.36.6+k3s1 | ziti → nova |

- Watch that nothing stays Pending for more than 5 min, especially PgBouncer/Postgres (PGO, primary on ziti), ingress-nginx, cert-manager, and Flux controllers.
- `frigate` (Coral) and `zwave-js-ui` (USB stick) are pinned to nova. In drain mode they are down from nova's drain until it returns, about 5–15 min. Jellyfin needs a GPU-labelled node, which both workers are.

### 4.4 Post-hop verification (about 10 min), before declaring the hop done

```sh
kubectl --context home get nodes                                   # enterprise, nova, ziti on $V, Ready; enterprise + vega still cordoned; nova/ziti schedulable; vega still v1.27.3 until removed
kubectl --context home get ks,hr -A | grep -v True                  # no new failures
kubectl --context home get pods -A | grep -Ev 'Running|Completed'
kubectl --context home get ds -A                                    # cilium, csi-nfs-node, netbird, node-exporter 4/4 (3/3 once vega is removed)
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

### 4.5 Manual fallback (enterprise only)

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

For agents, run `kubectl --context home drain <node> --ignore-daemonsets --delete-emptydir-data` first and `uncordon` afterwards. nova and ziti need P8 before this path works. vega is out of the campaign.

### 4.6 Hop 1 (v1.27.3 → v1.28.15+k3s1): readiness and exact procedure

#### Readiness check, 2026-11-23 03:30–03:36Z (read-only)

| Check | Result | OK? |
|---|---|---|
| Nodes | All 4 Ready, all v1.27.3+k3s1. enterprise and vega `unschedulable=true`; nova and ziti schedulable | ✅ |
| Flux | 44/44 Kustomizations and 34/34 HelmReleases Ready (`monitoring/speedtest-exporter` suspended on purpose) | ✅ |
| Pods / DaemonSets | No pod outside Running/Completed, no partially-ready pod, every DaemonSet fully available | ✅ |
| etcd snapshot on titan | Newest is `etcd-snapshot-enterprise-1795377601` (2026-11-22 20:00:01Z), 41,709,600 bytes. The offload job at 20:30Z verified its sha256 against the source. About 7.5 h old. Next scheduled copy 2026-11-23 08:30Z | ✅ |
| kube-vip | `kube-vip-enterprise` 0 restarts since 2026-11-20 19:00:38Z (2 d 8 h; the 7-day watch ends 2026-11-27) | ✅ |
| Removed APIs for 1.28 | None. k8s 1.28 removes no built-in API that this cluster uses; the metric has no series with a `removed_release` set | ✅ |
| enterprise memory | 2,031 MiB `available` (gate ≥1,536), load 1.2 (gate < 4), root fs 10% used | ✅ |
| SUC | v0.21.1, 1/1 on nova, Plans `Complete=True` at v1.27.3, 0 Jobs | ✅ |
| Flux / k8s support | Flux 2.2.3 supports 1.26–1.28 | ✅ |
| Artifacts | `rancher/k3s-upgrade:v1.28.15-k3s1`, `rancher/kubectl:v1.30.3` and the GitHub release v1.28.15+k3s1 all exist | ✅ |
| **Agent Plan excludes vega** | **No.** The selector still matches vega, so commit B would drain or cordon vega, upgrade it under load, and then uncordon it | ❌ fixed by A0 |
| **Agent mode** | **Undecided.** The Git state (drain) does not fit on two workers (§7.2) | ❌ Lee picks at the gate (P12) |
| Not blocking hop 1 | P8 (nova/ziti host keys), P9 (pre-download binaries, including v1.27.3 for rollback), maintenance window, announcement | ☐ |

**Verdict: NO-GO with the repo as it is today, GO for hop 1 once Lee approves it and commit A0 lands.** A0 is inert (the version is unchanged, so the hash is unchanged) and is the first step below. Cluster health is GO on every check. Re-run §4.1 at the start of the window.

#### Procedure (needs 🔒 Lee's go for hop 1 and the agent-mode choice)

1. **Pre-hop gate** (§4.1, about 5 min). Abort if any check fails.

2. **Snapshot** (about 2 min):
   ```sh
   ssh enterprise "sudo k3s etcd-snapshot save --name pre-v1.28.15-k3s1"
   kubectl --context home -n kube-system create job --from=cronjob/etcd-snapshot-offload pre-hop-v1.28.15
   kubectl --context home -n kube-system logs -f job/pre-hop-v1.28.15   # "copied pre-v1.28.15-k3s1-enterprise-<ts> <bytes> sha256=…"
   ```

3. **Commit A0: agent Plan guard (inert).** Edit `plans/agent.yaml`:
   ```yaml
   nodeSelector:
     matchExpressions:
       - {key: node-role.kubernetes.io/control-plane, operator: DoesNotExist}
       - {key: kubernetes.io/hostname, operator: NotIn, values: ["vega"]}
   ```
   - Cordon-only mode (recommended): also replace the whole `drain:` block with `cordon: true`.
   - Drain mode: keep `drain` (with `timeout: "900s"`) and prepare the scale-down commit from §7.2.
   - `version` stays `v1.27.3+k3s1`. Push, then run `flux --context home reconcile ks cluster-apps-system-upgrade-controller-plans -n flux-system --with-source`.
   - Verify: Plans `Complete=True`, `kubectl --context home -n system-upgrade get jobs` shows none, and no cordon changed.

4. **Commit A: server.**
   - Set `version: "v1.28.15+k3s1"` in `plans/server.yaml` and `k3s_release_version: "v1.28.15+k3s1"` in `ansible/inventory/group_vars/kubernetes/main.yaml`.
   - Push and reconcile the plans ks.
   - What SUC does:
     1. The controller (on nova) resolves `latestVersion v1.28.15-k3s1`, giving hash `f9eb9866…`. That is the same for both Plans, because the hash only covers the version and the service account.
     2. It selects enterprise, the only control-plane node, whose label no longer matches.
     3. It creates Job `apply-server-on-enterprise-with-f9eb…`. The pod is pinned to enterprise by hostname affinity and tolerates `node.kubernetes.io/unschedulable`, so it runs despite the cordon. It runs privileged with hostPID.
     4. The server Plan has no cordon or drain, so there are no init containers.
     5. The `rancher/k3s-upgrade:v1.28.15-k3s1` container (about 30 s to pull) puts the new binary at `/usr/local/bin/k3s` and kills the k3s server process. systemd restarts it on v1.28.15, and `KillMode=process` keeps the containers running.
     6. The API (VIP and direct) is down for about 1–3 min while etcd and the apiserver come back. etcd stays on 3.5.x, so there is no data migration.
     7. SUC labels enterprise with the new hash and leaves it **cordoned**.
   - Time: about 3–5 min.
   - Then run the server verification (§4.3): enterprise v1.28.15, Ready, SchedulingDisabled; `/readyz`; etcd snapshot list; `free -m`; kube-vip, cilium-operator and coredns pods. Check that Flux 2.2.3 is still reconciling.

5. **Commit B: agents.**
   - Set `version: "v1.28.15+k3s1"` in `plans/agent.yaml`. Push and reconcile.
   - SUC handles **ziti, then nova**, one at a time. For each node it creates Job `apply-agent-on-<node>-with-f9eb…` with these steps:
     1. `prepare` (`k3s-upgrade prepare server`) waits until the server Plan is done and the control plane is on v1.28.15. That is immediate here.
     2. Cordon-only mode: a `cordon` init container (`rancher/kubectl:v1.30.3`) cordons the node.
     3. Drain mode: a `drain` init container drains the node instead (DaemonSets ignored, emptyDir deleted, SUC's own pods excluded by `!upgrade.cattle.io/controller`, timeout 900 s).
     4. `upgrade` swaps the binary and restarts k3s on that node (about 30–60 s, with the pods kept).
     5. SUC labels the node and **uncordons** it.
   - Time:
     - Cordon-only: about 2–4 min per node, about 5–10 min in total. No app downtime beyond about a 1 min kubelet gap per node.
     - Drain: about 5–15 min per node, plus the scale-down commit before and its revert after (§7.2).
   - The SUC controller pod on nova keeps running through nova's turn, because it is excluded from the drain and k3s uses `KillMode=process`.
   - vega is not touched and stays v1.27.3, which is 1 minor of skew and allowed.

6. **Post-hop verification** (§4.4, about 10 min). Then run the 1.28 add-on step: Flux → 2.3.x and the HelmRelease migration to `helm.toolkit.fluxcd.io/v2` (§2), as its own commit. Journal the hop.

**Rollback for hop 1**
- **Server fails** (k3s won't start, or etcd/apiserver unhealthy after about 10 min). 🔒 Lee declares the hop failed (gate 6), then follow §6.4:
  1. Revert commit A in Git, and suspend the plans ks if the API is up.
  2. On enterprise, stop k3s and put back a v1.27.3+k3s1 binary. Get it from P9 if it is done; otherwise download it and check the sha256, or copy it out of the cached `rancher/k3s:v1.27.3-k3s1` image on the octo Docker host.
  3. Run `k3s server --cluster-reset --cluster-reset-restore-path=…/pre-v1.28.15-k3s1-enterprise-<ts>`, then start k3s.
  4. The agents are still on 1.27, so nothing else is needed.
- **An agent fails:**
  1. Revert commit B (or set the agent Plan back to `v1.27.3+k3s1`) so that SUC puts the node back on 1.27.3. It is unverified whether `k3s-upgrade` accepts a downgrade, so check its log.
  2. Manual rollback on nova or ziti needs P8 (SSH host keys).
  3. A cordoned node left behind by a failed job stays cordoned until the job succeeds or someone uncordons it (🔒).
- **Drain mode only:** revert the scale-down commit last.

### 4.7 Hop 2 (v1.28.15 → v1.29.15+k3s1): readiness and revised procedure

#### Readiness, 2026-11-23 ~19:05Z (read-only, after the hop-2 prerequisites)

| Check | Result | OK? |
|---|---|---|
| Nodes | enterprise, ziti, nova v1.28.15+k3s1; vega v1.27.3+k3s1 (excluded). enterprise and vega cordoned | ✅ |
| Flux / k8s support | Flux **2.3.0** supports 1.28–1.30. All repo manifests on HelmRelease v2 / source v1 | ✅ |
| Flux health | 44/44 Kustomizations, 34/34 HelmReleases Ready (`speedtest-exporter` suspended on purpose) | ✅ |
| Stale `coredns` HelmChart | removed; no `helm-install-coredns` Job will be re-created by the server hop | ✅ |
| Ingress | ingress-nginx 2/2, one pod on nova, one on ziti; PDB minAvailable 1 | ✅ |
| SUC | v0.21.1; Plans Complete at v1.28.15 (hash `f9eb9866`); agent `postCompleteDelay: "300s"`; 0 Jobs | ✅ |
| Removed APIs for 1.29 | 1.29 removes only `flowcontrol.apiserver.k8s.io/v1beta2`; nothing uses it. `apiserver_requested_deprecated_apis` has no series with `removed_release` set (only Flux CRD betas) | ✅ |
| k3s 1.29 flag removals | `rotate-keys`, `multi-cluster-cidr`: not in enterprise `config.yaml` | ✅ |
| Artifacts | GitHub release `v1.29.15+k3s1` (2025-03-25) and `rancher/k3s-upgrade:v1.29.15-k3s1` exist | ✅ |
| Plan hash / order | `v1.29.15-k3s1` → hash `59d1797a…`; agent order **ziti → nova** (recomputed with the live node and Plan UIDs) | ✅ |
| Add-ons | cert-manager 1.17 (1.29–1.33) ✅. Cilium 1.18 is officially 1.30+, but ran fine on 1.27/1.28; in range from hop 3. ingress-nginx 1.15.1 officially 1.31+, same situation | accepted |
| etcd snapshot | `pre-hop2-prereqs-20261123T1814Z` on titan, sha256 verified. Take a fresh `pre-hop2-*` at the window | ☐ at the window |
| Not blocking | P8 (nova/ziti host keys), P9 (binaries), P13 (vega removal, due before hop 4) | ☐ |

**Verdict: GO for hop 2 once Lee approves it.** Re-run §4.1 at the start of the window.

#### Procedure (needs 🔒 Lee's go for hop 2)

`V=v1.29.15+k3s1`. Agent mode stays **cordon-only**.

1. **Pre-hop gate** (§4.1). For kube-vip, compare the container `startedAt`, not `restartCount`; the static pod is recreated by every kubelet upgrade.
2. **Snapshot**: `ssh enterprise "sudo k3s etcd-snapshot save --name pre-hop2-<UTC ts>"`, offload Job to titan, check the sha256 on both sides.
3. **Commit A (server)**: `plans/server.yaml` `version: "v1.29.15+k3s1"` and the Ansible `k3s_release_version`. Push, reconcile `cluster-apps-system-upgrade-controller-plans`.
   - Expect: Job `apply-server-on-enterprise-with-59d1…`, API outage about 30 s (hop 1: 28 s), enterprise briefly NotReady, still SchedulingDisabled afterwards. kube-vip static pod recreated.
   - Server gate (§4.3). Also: no `helm-install-*` Job in kube-system, CoreDNS 2/2.
4. **Commit B1 (first agent, ziti only)**: in `plans/agent.yaml`, set `version: "v1.29.15+k3s1"` **and** add nova to the exclusion, i.e. `{key: kubernetes.io/hostname, operator: NotIn, values: ["vega", "nova"]}`. Push, reconcile.
   - SUC cordons ziti, restarts k3s on it (kubelet gap about 15–25 s), uncordons it on completion. The plan-hash label follows 300 s later, then the Plan shows Complete.
   - Verify ziti before going on: v1.29.15, Ready, schedulable; all pods on ziti Ready (Postgres primary / PgBouncer, `loki-chunks-cache-0`, the ziti ingress pod); ingress answers; DaemonSets full; no new restarts.
   - To stop here: just don't push B2. nova stays on 1.28.15 (1 minor of skew, allowed). **Do not** revert B1 after ziti has succeeded: SUC would then try to put ziti back to 1.28.15 (downgrade, unverified).
5. **Commit B2 (second agent, nova)**: remove `"nova"` from the `NotIn` list (back to `["vega"]`). nodeSelector is not in the hash, so ziti is not re-run. Push, reconcile.
   - SUC cordons nova (Frigate, Z-Wave, the SUC controller and the other ingress pod run there; all keep running), restarts k3s, uncordons it; Plan Complete 300 s later.
   - Ingress stays up through both agents, because the other replica is on the other node.
6. **Post-hop verification** (§4.4), `post-hop2-*` snapshot to titan, journal.
7. **Next add-on step at 1.29** (separate approval): Flux → 2.4.x (1.29–1.31), same `flux.yaml` tag method.

Time: A about 3 min, B1 about 2 min plus the 5 min delay and checks, B2 the same. About 30–40 min in all.

**Rollback**: as hop 1 (§4.6), with `pre-hop2-*` as the restore point. Server failure → revert A, restore etcd per §6.4. Agent failure → SUC leaves the node cordoned with a failed Job; investigate before uncordoning (🔒).

---

## 5. Add-on compatibility

Sources: upstream release notes and READMEs, gathered 2026-11-19. Items marked *unverified* need a recheck at execution time.

| Component | Deployed | Supports (deployed) | Needed for 1.36 | Action |
|---|---|---|---|---|
| **Flux** | 2.3.0 (was 2.0.1, then 2.2.3) | 1.28–1.30 | 2.9.x or 2.10.x | Step through 2.2 → 2.10 per §2. Migrate APIs (`flux migrate`) before 2.7. Any 2.x → 2.x jump is officially allowed, but each Flux minor only supports a 3-minor k8s window, so it has to track the hops. ✅ 2.2.3 before hop 1; ✅ 2.3.0 plus the HelmRelease v2 migration after hop 1. Next: 2.4.x at 1.29. |
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
| **system-upgrade-controller** | v0.21.1 (was v0.11.0) | uses only GA APIs; ships kubectl 1.30.3 for cordon/drain | v0.21.1 | ✅ Bumped 2026-11-23. Note the uncordon behaviour (§1.2) and string-only durations. |
| metrics-server | chart 3.10.0 (app 0.6.x) | 1.25+ | 0.8 (1.31+) / 0.9 (1.34+) | Bump at 1.30 and 1.34 |
| coredns | chart 1.49.0 (Flux HelmRelease) | unverified | 1.50.x | Optional. The stale k3s HelmChart for the same release was removed on 2026-11-23 (§3 H1). |
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

### 6.1 What existed on 2026-11-19 (before P2)

Since 2026-11-20, option A below is live (`5427837`): every k3s snapshot is copied to titan with a sha256, the token is copied too, and the restore drill passed. The rest of this subsection is the original state.


- k3s default scheduled snapshots: every 12 h (00:00/12:00), retention 5, **local only** at `enterprise:/var/lib/rancher/k3s/server/db/snapshots/`. There are five 38 MB snapshots from 2026-11-17 to 2026-11-19.
- One 111 MB manual `pre-drain-recovery-20260804-0901-enterprise-…` snapshot.
- No `etcd-s3` configuration and no off-node copy.
- A 2026-08-03 archive of `/etc/rancher/k3s` plus the server token exists on rigel (`/home/linked/enterprise-recovery/`). It is old but still useful for the token.
- Snapshots contain every Secret in plaintext: k3s secrets-encryption is not enabled in config.yaml. Treat every copy as sensitive, with a restricted share and mode 600.
- Titan: SSH to the NAS is rejected (by design per ENVIRONMENT.md), so copies have to arrive over NFS from inside the cluster or from a node.

### 6.2 Off-node copies (P2): option A chosen and done

| Option | How | Pros | Cons |
|---|---|---|---|
| **A. GitOps CronJob → Titan (recommended)** | CronJob in `kube-system`, `nodeName: enterprise` (bypasses the cordon without uncordoning), hostPath `/var/lib/rancher/k3s/server/db/snapshots` **read-only** plus `/var/lib/rancher/k3s/server/token` read-only, writing to a PVC on `nfs-titan-hdd` (restricted). Runs at 00:30/12:30 and copies new snapshots with sha256 files. Trigger on demand before each hop with `kubectl --context home -n kube-system create job --from=cronjob/etcd-snapshot-offload pre-hop-N`. | Titan, as requested. Through Git. No host config change. Tiny footprint on enterprise. | Needs a privileged-ish pod (hostPath root files). PVC content is readable by anyone with cluster access to that namespace. |
| B. k3s native S3 | Add `etcd-s3`, `etcd-s3-bucket`, `etcd-s3-folder`, `etcd-s3-region`, plus credentials to enterprise config (the creds belong in a root-only file, not this repo). Reuse the AWS account already used by `pvc-backup`/pgBackRest. Takes effect on the next k3s restart, i.e. hop 1. | Off-site, native retention, `k3s etcd-snapshot ls` shows both. | Not Titan. Host config change. Credentials on the node. |
| C. Manual, per hop (minimum) | `ssh enterprise 'sudo cat <snap>'` piped to a second, off-node location (not vega, which is out), plus sha256 | No cluster change | Not Titan. Manual. Must not land on devbox disk. |

Recommendation (unchanged): **A plus C for the pre-hop-6 snapshot.** Keep A afterwards as permanent DR. Also copy `/var/lib/rancher/k3s/server/token` once (encrypted at rest), because a restore on a rebuilt node needs it.

### 6.3 Restore drill (part of P2): done, passed

- 2026-11-20: Option 2 was run in Docker on the octo host, on an `--internal` network with no route to the LAN, server-only.
  - The restore of `offsite-prereq-20261120` came up with all 4 nodes, 44 ks, 34 hr, the PGO cluster and the SUC Plans.
  - Everything was destroyed afterwards.
  - Details and gotchas: journal §4.
- Repeat it before hop 6 (etcd 3.6) with that hop's pre-snapshot.


- Prove the snapshot is readable without touching the live cluster.
- **Option 1 (zero cluster impact):** on an off-cluster host (not vega), in a throwaway directory, run the matching k3s binary's bundled etcd tooling, `k3s etcd-snapshot` list/inspect against a copy. Or at minimum check `etcdutl snapshot status <file>` from an etcd release tarball, which validates hash, revision and key count.
- **Option 2 (fuller):** restore into a disposable single-node k3s VM or container with `--cluster-reset --cluster-reset-restore-path` and the copied token, then `kubectl get ns`. Do not join it to anything, and destroy it afterwards because it contains the cluster's secrets.

### 6.4 Rollback procedure for a failed server hop

Kubernetes does not support in-place downgrades. Rollback means restoring the pre-hop snapshot with the previous binary. 🔒 **APPROVAL #6:** Lee decides that the hop has failed and that we restore.

1. In Git, revert commit A (and B if made) so that Flux and SUC do not immediately re-upgrade. If the API is up, also `flux --context home suspend ks cluster-apps-system-upgrade-controller-plans -n flux-system`.
2. On enterprise:
   ```sh
   sudo systemctl stop k3s
   sudo install -m 0755 /usr/local/bin/k3s.prev /usr/local/bin/k3s     # or the pre-downloaded previous version (P9, /root/k3s-binaries/)
   sudo k3s server --config /etc/rancher/k3s/config.yaml \
        --cluster-reset --cluster-reset-restore-path=/var/lib/rancher/k3s/server/db/snapshots/pre-<V>-enterprise-<ts>
   # wait for "Managed etcd cluster membership has been reset, restart without --cluster-reset flag now"
   sudo systemctl start k3s
   ```
   - A restore may require the original token. k3s reads it from the existing data dir; on a rebuilt node, pass `--token` from the saved token file.
   - `k3s-killall.sh` is **not** needed and would kill every workload on the node.
3. If agents were already upgraded and are newer than the restored server, roll them back too (the kubelet must not be newer than the API server):
   - Use SUC with the reverted agent Plan, or the manual binary swap (nova and ziti need P8). vega is not upgraded, so it needs nothing.
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
  - The `etcd-snapshot-offload` CronJob pods (`nodeName: enterprise`, a few seconds twice a day)
  - The SUC controller is **no longer** on enterprise. It moved to a worker on 2026-11-23.
- Requests total 130m CPU and 70 Mi, yet actual memory is 119% of allocatable (unchanged on 2026-11-23: 6.7 GiB used, 2.0 GiB `available`). The control plane itself (k3s-server about 1.6 GiB RSS plus etcd) dominates, which allocatable doesn't account for.
- Today's live CPU was 23% (load 1.3–2.2). The 87% figure from earlier checks is consistent with periodic spikes; Prometheus history was unavailable for confirmation.
- **Can the server hop be done safely?** Yes, with conditions. The risk is a k3s restart plus apiserver storage migration spiking memory with no swap and about 2 GiB headroom, which could end in an OOM kill or slow start. August also showed CPU starvation causing leader-election loss loops.
- Mitigations, in order of value:
  1. **Keep enterprise cordoned** (hence P3: SUC must not uncordon it).
  2. Pre-hop gate: ≥1.5 GiB `available`, load < 4. Abort the window if not.
  3. Optional (Git): keep `loki-canary` and `vector-agent` off enterprise via node affinity. That saves roughly 150–250 MiB but loses enterprise logs. Probably not worth it; leave vector, drop loki-canary.
  4. ✅ Done (`3d97f22`): `system-upgrade-controller` runs on a worker. The SUC *jobs* still run on enterprise for the server Plan.
  5. **Hardware (best, cheapest real fix):** add RAM to enterprise (16 GiB). Lee to check whether the box supports it. Ideally do this before the campaign.

### 7.2 vega is unavailable; drain capacity with two workers

#### vega: out of the campaign

- **What happened.**
  - The supervised load test on 2026-11-21 reproduced the August hangs.
  - Stage a (CPU) and stage b (CPU plus 8 GB vm) passed. Stage c added disk I/O, and vega **hard-hung at about 05:56Z**, about 9 min in.
  - The last good node-exporter scrape was at 05:56:03Z. ssh and ping died together, and nothing reached the kernel log.
- **Neither safety net fired.**
  - The iTCO hardware watchdog was armed (`RuntimeWatchdogSec=30s`) and `kernel.panic=10` was set, yet the box stayed hung for about 10 h until Lee power-cycled it.
  - So a **platform or power-level fault** (PSU brick, VRM, CPU/PCH) is most likely, ahead of RAM, firmware or SSD.
  - Full diagnosis and next steps: journal sections "vega load test" and "vega post-freeze diagnosis".
- **Rule for this campaign:**
  - vega stays **cordoned** (Lee, 2026-11-21 16:30: "Leave vega cordoned for now").
  - vega is **not a drain target and not a spare node**.
  - vega is **excluded from the SUC agent Plan** (`kubernetes.io/hostname NotIn [vega]`, commit A0) until the fault is fixed and a load test passes.
  - The SUC controller Deployment already excludes it.
- **While cordoned, vega still runs the 9 DaemonSet pods**: cilium, cilium-envoy, csi-nfs-node, intel-gpu-plugin, nfd-worker, node-exporter, loki-canary, vector-agent and netbird.
  - It is still a cluster member with a v1.27.3 kubelet.
  - It holds no PVs and no other workloads.
- **Version skew.**
  - A kubelet may be at most 3 minors older than the API server (n-3, from API server 1.28; it was n-2 before).
  - vega's v1.27 kubelet is therefore within policy through **hop 3** (API server 1.30, skew 3).
  - It is **out of policy from hop 4** (1.31, skew 4).
  - Before hop 4's server commit, vega must be either:
    - **(a) upgraded idle-only**: a manual binary swap over SSH (vega's key is fine) or an SUC run limited to vega, to 1.30 or later. This must be repeated at least every third hop, on a box that can hang at any time. Each restart is low-load, but every time it is one more chance to wedge a node that runs Cilium for nothing.
    - **(b) removed from the cluster** (recommended).
- **Recommendation: (b) remove vega**, ideally in the hop-1 or hop-2 window and at the latest before hop 4. It contributes no capacity, only DaemonSet pods, and every hop it adds skew bookkeeping and a risk of a NotReady node mid-hop.
  - Steps (🔒 Lee, P13):
    1. `kubectl --context home drain vega --ignore-daemonsets --delete-emptydir-data`. Only DaemonSet pods are there, so this is a no-op.
    2. On vega, `sudo systemctl disable --now k3s`, so it cannot re-register. Leave the binaries and data dir.
    3. `kubectl --context home delete node vega`.
    4. Remove vega from the Ansible inventory, or comment it out, so no playbook touches it.
  - When the hardware is fixed and a load test passes, re-join it as a fresh agent at the cluster's version at that time.

#### Drain capacity with nova and ziti only (2026-11-23 03:30Z)

| | nova | ziti |
|---|---|---|
| Allocatable memory | 15,887 MiB | 15,888 MiB |
| Memory requests (all pods) | 6,024 MiB (37%) | 10,939 MiB (68%) |
| Node working set (`kubectl top node`) | 13,323 MiB (83%) | 7,203 MiB (45%) |
| Non-DaemonSet pods | 41: requests 5,954 MiB, working set 9,758 MiB | 14: requests 10,869 MiB, working set 5,111 MiB |
| Node overhead (node ws − non-DS pod ws: DaemonSets, k3s, kernel) | ≈3.5 GiB | ≈2.0 GiB |
| Pinned here | frigate (Coral, `hostname=nova`), zwave-js-ui (USB stick) | none. cilium-operator only avoids vega. |
| Biggest items | prometheus 1.6 GiB ws, frigate 1.7 GiB, immich-server 0.9 GiB, home-assistant 0.8 GiB, jellyfin 0.7 GiB, speakr 0.7 GiB, loki-0 0.6 GiB | **loki-chunks-cache-0: 9,830 MiB request**, 3.4 GiB ws. postgres primary 0.3 GiB |

CPU is not a constraint: requests are 2.9 and 0.8 of 8 cores. There are no PodDisruptionBudgets anywhere.

- **Drain nova onto ziti: does not fit.**
  - Movable requests are 5,854 MiB (frigate and zwave are pinned and stay down) against 4,949 MiB free on ziti, about 0.9 GiB short.
  - Working set would be about 7.2 + 7.9 = 15.1 GiB, which is 95% of ziti.
- **Drain ziti onto nova: does not fit.**
  - Movable requests are 10,869 MiB against 9,863 MiB free on nova. `loki-chunks-cache-0` alone is 9.6 GiB.
  - Working set would be about 13.3 + 5.1 = 18.4 GiB, which is 116% of nova.
- **The second drain of every hop is worse.** SUC uncordons the first node after its upgrade, but nothing moves back on its own. The second node's drain therefore has to put the **whole cluster's movable workload** on one 16 GiB node:
  - requests about 16.7 GiB, against 15.5 GiB allocatable;
  - working set about 15–17 GiB, against 15.5 GiB.

So with drains, something has to be scaled down for the whole agent phase of every hop. With cordon-only, nothing moves.

#### Agent mode (🔒 Lee decides at the hop-1 gate, P12)

**Option 1: cordon-only agents (recommended).**
- Replace the agent Plan's `drain` with `cordon: true` (commit A0). This is the shape k3s's own SUC example uses.
- The node is cordoned and k3s restarts with the new binary. Pods keep running (`KillMode=process`), and SUC uncordons the node.
- Downtime: about 0 for apps, with about 1 min where the kubelet on that node is not reporting.
- Risks:
  - If the new kubelet fails to come up, the node goes NotReady. After about 5 min its pods are evicted toward the other node, which can't hold them all. Mitigation: the hop is watched, and §4.6 has the rollback.
  - At **hop 4** (containerd 1.7 → 2.x), containers are expected to restart in place. For hop 4, either accept in-place restarts (cordon-only) or use option 2 for that hop only.

**Option 2: drain every hop, with a temporary scale-down.**
- A Git commit before commit B scales these to 0 (via HelmRelease values or `replicas`), and a revert after the agent phase.

  | Workload | Requests freed | ws freed | Needed? |
  |---|---|---|---|
  | Loki stack: `loki-0`, `loki-chunks-cache-0`, `loki-results-cache-0`, `loki-gateway`, `vector-aggregator` | 11,059 MiB | 4.2 GiB | **required**: the chunks cache alone blocks every ziti drain |
  | Immich: `immich-server`, `immich-machine-learning` (valkey can stay) | 2,048 MiB | 1.1 GiB | **required**: without it the second drain's node is at ~83–90% |
  | `speakr` | 512 MiB | 0.7 GiB | recommended |
  | radarr, sonarr, sabnzbd | 750 MiB | 0.7 GiB | recommended |

- With all four groups down, the node carrying everything during the second drain sits at about 11.8 GiB ws (74%) when nova carries it, or about 8.5 GiB (53%) when ziti carries it.
- **Accepted as down** while nova drains and restarts: frigate and zwave-js-ui, about 5–15 min, because they are pinned to nova.
- **Restarted twice per hop** (once per drain, 1–5 min each): home-assistant, esphome, mosquitto, Postgres primary and pgbouncer (every DB client blips), cert-manager, the Flux controllers, prometheus (WAL replay), grafana, jellyfin, recipes, syncthing, cloudflared, k8s-gateway and coredns. ingress-nginx is not interrupted, because it has 2 replicas.
- Logs stop for the whole agent phase (Loki down); metrics keep running.

**Recommendation:** use option 1 for hops 1–3 and 5–9. Decide hop 4 separately (option 2 at hop 4 only, or accept in-place restarts).

**Longer-term fix (separate change, not needed for hop 1):** `loki-chunks-cache` requests 9.6 GiB for a 3.4 GiB working set. Right-sizing it (e.g. `chunksCache.allocatedMemory` about 2 GiB) frees about 7.5 GiB of ziti's requests and most of the drain problem with it. A third worker, or RAM in enterprise, are the structural fixes.

### 7.3 Second control-plane node: **later, not during this campaign**

| Option | Pros | Cons |
|---|---|---|
| **A. Stay single-CP for the campaign; add HA afterwards (recommended)** | Simplest hops (one etcd member, one restore path). Snapshots give recovery. | Single point of failure continues for weeks |
| B. Add one server now (2 members) | Second copy of data | **Worse availability**: 2-member etcd needs both for quorum. Doubles the server-hop work. etcd 3.5 → 3.6 with mixed members is riskier. |
| C. Add two servers now (3 members) | Real HA during the campaign | Converting an agent to a server means a re-install (no in-place promotion). Ansible role history includes the 2026-08-07 self-promotion accident. Much more work and risk up front. |
| D. Later: move the control plane to a bigger box (add it as server, remove enterprise's member). vega was the candidate, but **not while it has an unexplained platform hang** | Control plane on more than 4 CPU / 7.6 GiB. Fixes the capacity issue permanently. | etcd member add/remove on a single-member cluster. Do it on a healthy, current cluster with snapshots, not mid-campaign. |

Recommendation: **A now**, then a follow-up project for **3 servers** (enterprise plus new or repaired hardware; vega only after its fault is fixed), or **D** if a third box isn't available. 🔒 Lee decides; this is outside the scope of the upgrade go-ahead.

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

Updated 2026-11-23 for two workers (nova, ziti), with vega out.

### Per hop

Measured at hop 1 (cordon-only): API outage about 28 s, about 13–26 s of kubelet gap per agent, 5.5 min from commit A to all nodes done. The estimates below are conservative. From hop 2, add 5 min per agent for `postCompleteDelay`.

| Event | Cordon-only agents (recommended) | Drain + scale-down agents |
|---|---|---|
| API unavailable (server k3s restart) | 1–3 min (hop 6: up to 5–10 min, etcd 3.6 data migration) | same |
| Agent k3s restart, per node (×2) | ~1 min kubelet gap; pods keep running; no app downtime | included below |
| Loki and Immich (and optionally speakr, *arr) | 0 | down for the whole agent phase: **~20–40 min** (scale-down → both drains → revert) |
| Singletons (HA, ESPHome, Mosquitto, Postgres/PgBouncer, cert-manager, Flux controllers, Prometheus, Grafana, Jellyfin…) | 0 | 1–5 min, **twice** (once per drain) |
| Frigate, Z-Wave JS (pinned to nova) | 0 (~1 min if the kubelet restart restarts them) | 5–15 min |
| Ingress | 0 (2 replicas, one per worker, PDB minAvailable 1, since 2026-11-23). Hop 1 still had 1 replica: a few seconds without a ready endpoint during nova's kubelet gap | about 0 |
| **Total per hop, wall-clock** | ~30–45 min including checks; user-visible ~1–3 min (API only) | ~60–90 min; user-visible as above |

### Whole campaign (9 hops)

| | Cordon-only | Drain |
|---|---|---|
| API | about 20–45 min total | about 20–45 min total |
| Apps | about 0, except hop 4 (containerd 2: in-place container restarts on each node, a few min per app) | Loki/Immich about 3–6 h total down; each singleton restarted about 18 times; Frigate/Z-Wave about 45–135 min |
| Add-on bumps | Cilium rolling restart: seconds per pod, about 2 times | same |

### Wall-clock

- Prerequisites: done (2026-11-20 to 2026-11-23).
- Hops: about 30–45 min each with cordon-only (60–90 min with drain), plus the interleaved add-on PRs.
- Suggested grouping:
  - Evening 1: hops 1–2 (plus the 1.28 Flux step)
  - Evening 2: hops 3–5. Remove vega before hop 4.
  - Evening 3: hop 6 alone (etcd 3.6), plus 7
  - Evening 4: hops 8–9, or stop at 8

---

## 10. Risks

| # | Risk | Likelihood / impact | Mitigation |
|---|---|---|---|
| 1 | **Single control-plane / etcd node.** A failed hop, OOM or disk fault means a total control-plane outage. | med / **critical** | ✅ P2 off-node snapshots to titan plus a passed restore drill; pre-hop snapshot every hop; rollback §6.4; RAM upgrade |
| 2 | **etcd 3.5 → 3.6 at hop 6.** Upstream now wants ≥3.5.32 first; k3s 1.32.13 ships 3.5.26. | low-med / critical | Extra snapshot with 2 off-node copies; hold point; read k3s 1.33.x release notes and issues at execution time; abort and restore if etcd fails to start |
| 3 | **SUC uncordons enterprise** (`cordon: true` plus handle_batch uncordon), re-creating the August scheduling storm on a 4-CPU/7.6 GiB node | low (fixed) / high | ✅ P3 (`002191f`): no `cordon` on the server Plan; verify the cordon after each hop |
| 4 | **Flux / k8s version lockstep.** Flux is now 2.2.3 (good through 1.28); the 2.7/2.8 API removals break reconciliation if the migration is skipped; the stale `bootstrap/` v2.9.6 being applied early would break Flux | med / high (GitOps stalls, including for rollback) | §2 table; matching CLI per step; never apply `bootstrap/` mid-campaign; `flux migrate` before 2.7 |
| 5 | **enterprise memory headroom** (119% of allocatable, about 2 GiB available, no swap) leading to OOM or leader-election loops during k3s start | med / high | Pre-hop gate; keep cordoned; ✅ SUC controller moved off; RAM upgrade |
| 6 | **ingress-nginx retired**: no supported release for 1.36 and no security fixes | certain / med | Stop at 1.35, or migrate ingress (§5.3) |
| 7 | **Drain capacity**: with only nova and ziti, neither drain fits on the other node, and the second drain of each hop needs the whole cluster on one node (§7.2). There are no PodDisruptionBudgets apart from ingress-nginx (since 2026-11-23), so singletons go down on every drain | certain with drain / med | Cordon-only agents (recommended), or drain plus the §7.2 scale-down; right-size `loki-chunks-cache`; `concurrency: 1` |
| 7b | **vega** (platform hang under load, kubelet 1.27): SUC drains, upgrades and then **uncordons** it if it is still selected; out of skew policy from hop 4 | high if not excluded / med | A0 excludes vega from the agent Plan; remove vega from the cluster before hop 4 (P13) |
| 7c | **SUC Plan field types**: int durations overflow in SUC ≥ v0.16.3, and the controller then stops listing Plans (seen on 2026-11-23) | low (fixed) / med (SUC inert, hop stalls) | `drain.timeout: "900s"`; strings for every duration; check the controller log after every Plan edit |
| 8 | **Add-ons already outside support** (Cilium 1.18 on 1.27–1.29, cert-manager 1.17 EOL) | low-med / high (CNI) | Cilium and cert-manager step-ups at the stated versions; one Cilium minor at a time with pre-flight |
| 9 | **kube-vip crash during a hop** confuses verification or extends the API outage | low (fixed) / med | ✅ P5: 0 restarts since 2026-11-20 |
| 10 | **nova/ziti unverifiable over SSH** (host keys changed): no manual fallback, rollback or cgroup check on the two busiest nodes | med / med | P8 by Lee; rely on SUC; agent rollback via SUC only |
| 11 | containerd 2.x at hop 4 with an unknown custom template on nova/ziti | low / med | P8 check; decide hop 4's agent mode (drain plus scale-down, or accept in-place restarts) |
| 12 | Ansible pin 1.29.1: an accidental playbook run skips a minor | low (fixed) / high | ✅ P1 (`20bdb52`); bump the pin with each server hop |
| 13 | Prometheus 2.45 / kube-state-metrics on very new k8s: metrics gaps | med / low | Accept; kube-prometheus-stack modernisation after the campaign |

---

## 11. Where Lee's approval is required

### ▶ Next gate: hop 2 (1.28.15 → 1.29.15)

Hop 1 is done (2026-11-23, Lee's go 17:13 UTC, cordon-only). The hop-2 prerequisites are done (§3). Ask for these together as "go for hop 2". The readiness check and the procedure are in §4.7.

| Decision | Options | Recommendation |
|---|---|---|
| Agent mode | cordon-only (as hop 1) | **cordon-only** |
| Commit A (server) | `server.yaml` version and the Ansible pin → v1.29.15+k3s1 | – |
| Commit B1 (ziti) | `agent.yaml` version → v1.29.15+k3s1 with nova excluded | after the server gate |
| Commit B2 (nova) | drop the nova exclusion | after ziti is verified; Lee can pre-approve B2 conditional on a clean ziti check, or batch A+B1+B2 as "hop 2 end-to-end" |
| Window | Date and time, announced (P10) | – |

### Gate status

| # | Gate | What is approved | Status |
|---|---|---|---|
| 1 | P0 | This plan, the target (1.36.6 vs the 1.35.10 stop point), and the maintenance windows | ◐ Prerequisites approved 2026-11-20. Target and windows still open. |
| 2 | P2 | etcd off-node mechanism and the restore drill | ✅ done (CronJob to titan, drill passed) |
| 3 | P4 | Uncordoning vega | ✗ dropped. vega stays cordoned and is excluded (§7.2). |
| 3b | P13 | Remove vega from the cluster (recommended) or upgrade it idle-only | ☐ open, **due before hop 4** |
| 4 | P5 | kube-vip static-pod change on enterprise, and optional IPv6 RA change | ✅ done (vip_interface). IPv6 RA change not made; Lee's call. |
| – | P3 / P6 / add-ons | Each Git PR to `main` touching SUC, Flux, Cilium, cert-manager, external-dns or ingress (infra tier: diff shown first) | ✅ P3, P3b (SUC v0.21.1), P6 done. ✅ Hop-2 prerequisites H1–H4 done (Lee 2026-11-23 18:09). Next add-on: Flux 2.4 at 1.29. |
| **per hop** | §4.3 / §4.6 / §4.7 | Go for hop N: server commit A, then agents commit B (from hop 2: B1/B2 per node). Lee can batch them as "go for hop N end-to-end" | ✅ hop 1 done 2026-11-23. **▶ hop 2 is next** |
| 5 | §5.3 | ingress-nginx: stop at 1.35, run unsupported on 1.36, or migrate first | ☐ needed before hop 9 |
| 6 | §6.4 | Declaring a hop failed and restoring etcd from snapshot | as needed |
| 7 | §7.3 | Any second control-plane / HA work (separate project) | later |

Without approval, workers may do only read-only checks, plus preparing PRs as drafts.

---

## 12. Out of scope / follow-ups

- Hop 10 to 1.37.x, once Cilium 1.21, cert-manager 1.22 and a PGO release with 1.37 ship.
- Control-plane HA (§7.3).
- kube-prometheus-stack 47 → current; Loki/Vector health; Flux notification Alerts (none exist).
- kube-vip v1.x; NetBird image pin; NFD restart counts.
- Right-size `loki-chunks-cache` (9.6 GiB request, 3.4 GiB used) so a single worker can absorb a drain (§7.2).
- vega hardware: brick, memtest, BIOS/microcode/kernel, iTCO test, netconsole (journal "Next steps"). Re-join it as a fresh agent only after a passing load test.
- Remove the stale `k3s-v1.29.1+k3s1` hard-link set left by the Ansible role once the role is re-pinned (cosmetic).
- Enable k3s secrets-encryption, so etcd snapshots don't hold plaintext Secrets.
