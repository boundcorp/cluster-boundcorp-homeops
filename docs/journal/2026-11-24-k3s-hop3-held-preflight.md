# k3s hop 3: preflight held on ingress placement (2026-11-24)

Status: **HELD. Hop 3 was not started.** No etcd snapshot, Plan or Ansible version commit, Flux reconcile, k3s restart, pod deletion, cordon change, or host change was made. The cluster is as it was after the ziti `/srv` migration (`aca24ca`). This note is on branch `leewardbound/k3s-hop3-1124` only, not main.

Authorization at dispatch: Lee's "continue" on 2026-11-24 at 02:13 UTC, read by the coordinator as the go-ahead for hop 3 (v1.29.15+k3s1 → v1.30.14+k3s2, cordon-only agents, server → ziti → nova). The hold came from a plan-vs-reality mismatch found in preflight. The coordinator escalated it to Lee; there was no answer by about 05:00 UTC, so the coordinator closed the dispatch as held. A new supervised worker resumes after Lee's decision.

## Decisions recorded at dispatch (carry forward)

1. Flux stays on 2.3.0 for hop 3 (supports 1.28–1.30); 2.4 is deferred.
2. ingress-nginx runs under the plan's support exception (controller 1.15.1 tested on 1.31–1.35): 2 replicas, PDB minAvailable 1. **See the hold below: the premise that the two replicas sit on different workers is false today.**
3. Postgres has a single primary on nova and no standby. Lee accepts a transient Patroni self-demotion of about 10–20 s during the server's API outage, as in hop 2. **nova is upgraded last.** Anything more than a self-recovering blip is a stop condition.
4. vega stays on v1.27.3+k3s1, cordoned and excluded from SUC. The upstream [version-skew policy](https://kubernetes.io/releases/version-skew-policy/) says: "`kubelet` may be up to three minor versions older than `kube-apiserver` (`kubelet` < 1.25 may only be up to two minor versions older…)". kube-proxy has the same rule. So 1.27 under a 1.30 API server is supported, and the policy warns that nodes three minors behind "must be upgraded before the control plane can be upgraded". **vega must be removed before hop 4.**
5. ziti storage gate met: imagefs on `/srv`. Require at least 2 GB free on ziti nodefs (`/var`) before and during the hop.

**Agent order deviation from the plan.** Draft plan §4.8 (PR #3) lists hop 3 as B1 nova, then B2 ziti; that order was based on the SUC hash and on the primary then being on ziti. The primary now runs on nova, so the coordinator set **B1 ziti only** (`NotIn [vega, nova]`), then **B2 nova only** (back to `NotIn [vega]`). This uses the same per-node exclusion as hop 2.

## Preflight, 02:15–02:22 UTC (read-only, all passed)

- `~/ENVIRONMENT.md` was read. `envcheck` (`/home/dev/bin/envcheck`): home and octo readyz ready; SSH enterprise, vega, rigel and macaroni reachable.
- Nodes: 4/4 Ready. enterprise, ziti and nova are on v1.29.15+k3s1 (containerd 1.7.26-k3s1); vega is on v1.27.3+k3s1. enterprise and vega are cordoned. No Memory, Disk or PID pressure on any node.
- Flux: 44/44 Kustomizations and 34/34 HelmReleases Ready; `speedtest-exporter` is suspended on purpose. 52/52 Prometheus targets up. 9/9 DaemonSets full; 29/29 PVCs Bound.
- Pods: no unhealthy active pods. The three terminal hop-2 records are retained (jellyfin `s2wht` and `zzfzf`, grafana `rpvwd`).
- Helm revision baseline: cert-manager 2533 (unchanged since the earlier baselines), coredns 8, jellyfin 5, ingress-nginx 3, cilium 22, kube-prometheus-stack 10. The full map is in the evidence directory.
- CoreDNS 2/2, one replica on ziti and one on nova. Ingress 2/2, PDB disruptionsAllowed 1, **both replicas on nova** (see below).
- Postgres: primary `postgres-postgres-6bb6-0` on nova, UID `e5480b37-7547-4e63-8566-ca709218f0a4`, 5/5, 0 restarts. Patroni Leader, running, TL 330, no other members. SQL `pg_is_in_recovery()=false`, `transaction_read_only=off`, both directly and through PgBouncer (single pod `xg6ch` on nova).
- SUC: both Plans Complete at v1.29.15+k3s1, 0 Jobs. Controller `l9d96` runs on nova.
- ziti stats: nodefs 2,789,994,496 B available of 12,459,843,584; imagefs 414,433,820,672 B available of 445,098,295,296. nova imagefs 805 GB available.
- Enterprise: 3,005 MiB available, load 1.57, root 11% used. etcd health true; readyz ok; VIP and direct livez 200. kube-vip mirror UID `dac857b1…`, restartCount 1, started 19:53:34Z (unchanged since hop 2).
- Route: devbox NetBird reports rigel Connected with Networks 10.20.30.0/24.
- API compatibility: no built-in API removals in 1.30; `SecurityContextDeny` is enabled neither in the repo nor in enterprise's config. Live `apiserver_requested_deprecated_apis` shows only `cilium.io/v2alpha1 ciliumnodeconfigs` and `helm.toolkit.fluxcd.io/v2beta1 helmreleases`, both with empty `removed_release`. Discovery still serves `flowcontrol` v1beta3, which is removed in 1.32 and is not relevant to 1.30.
- Artifacts: GitHub release `v1.30.14+k3s2` (sha256sum asset HTTP 200) and Docker Hub `rancher/k3s-upgrade:v1.30.14-k3s2` exist.

## Why it is held: both ingress replicas on nova

Plan §4.7 and §4.8 assume one ingress-nginx replica per worker, so that one keeps serving through each agent's kubelet restart. Since the ziti migration moved `ingress-nginx-controller-79cfc496bb-btd99` to nova, nova holds:
- **both** ingress replicas (`btd99`, `fb75k`);
- the only PgBouncer;
- the Postgres primary;
- the SUC controller;
- one CoreDNS replica.

Cordon-only keeps containers running through nova's restart, and ingress stayed at HTTP 200 through the node restarts in hops 1 and 2. Even so, the nova step would have no cross-node ingress redundancy. The coordinator did not accept that risk on Lee's behalf and asked Lee to approve restoring redundancy first.

### Proposed single-pod move (awaiting Lee)

Read-only analysis at 02:27 UTC:
- **The move needs no config edit.** The Deployment has 2 replicas, no affinity, nodeSelector `kubernetes.io/os=linux` and no tolerations. Its topologySpreadConstraint is maxSkew 1 on `kubernetes.io/hostname`, `whenUnsatisfiable: DoNotSchedule`, `nodeTaintsPolicy: Honor` (so cordoned enterprise and vega don't count), with `matchLabelKeys: [pod-template-hash]`.
- **The replacement can only go to ziti.** After one deletion, nova has 1 and ziti has 0, so placing the replacement on nova would give skew 2. The old terminal pod `6599c48cc-476sp` on ziti has a different template hash and does not count.
- **ziti has room and the image.** Its requests are CPU 760m of 8 and memory 67%; the pod needs 100m/90Mi. Image digest `sha256:594ceea7…` is already cached on ziti.
- **The PDB allows it:** minAvailable 1, currentHealthy 2, disruptionsAllowed 1.

Procedure:
1. **Pre-gate:** ingress 2/2 Ready, PDB disruptionsAllowed 1, HTTP 200 from hass, esphome, photos, sync, grafana `/login` and jellyfin `/health`.
2. **Action:** `kubectl --context home -n networking delete pod ingress-nginx-controller-79cfc496bb-fb75k`, with no `--force` and the default grace period (the last graceful drain took about 4 min). `btd99` would do equally well.
3. **Gates:**
   - the replacement is scheduled on ziti and Ready within 2 min, with an "already present" image event;
   - the Deployment is 2/2 available and the PDB shows currentHealthy 2;
   - all six hosts return 200 in samples about every 10 s through the whole termination;
   - no HelmRelease revision changes.
4. **Abort:** if the replacement is Pending for more than 2 min or lands anywhere but ziti, or any non-200 lasts more than 30 s, stop, report, and leave the other pod alone.

Also relevant for Lee: the Cilium L2-announcement lease for the ingress LoadBalancer IP 10.20.30.200 is held by **nova** (`externalTrafficPolicy: Cluster`). Moving one replica gives ziti a backend, but ARP for the VIP is still announced by nova's Cilium agent. That agent keeps running through a cordon-only kubelet restart, and the lease can move to another node if it lapses.

## When resuming

After Lee's decision, re-run every preflight check above. Then:
1. take the fresh pre-hop snapshot to titan and check that the sha256 matches on both sides;
2. start the watcher, which covers nodes and cordons, API/VIP/direct, etcd, SQL, DNS, ingress, ziti nodefs/imagefs, pressure and eviction events, restart deltas and SUC, plus a 2 s API probe. The scripts are in the evidence directory;
3. run the hop as A (server), B1 (ziti), B2 (nova), using the dispatch's stop conditions.

## Durable consent: ziti rollback-copy cleanup (not executed)

Lee approved on 2026-11-24 at 02:15 UTC, relayed by the coordinator as Ergo instruction `ziti-cleanup-consent-20261124` v1: "Also remove the old ziti copy after 24h". Scope:
- Delete **only** `/var/lib/rancher/k3s/agent/containerd.pre-srv-20261124` on ziti.
- **Not before 2026-11-25 00:47 UTC**: at least 24 h stable after the migration's kubelet return at 00:47:21 UTC.
- Before deleting, verify that:
  - the live containerd root is the `/srv` bind (`/dev/sda5[/k3s/agent/containerd]`);
  - the old tree is on `/var` (`/dev/sda3`) and is neither mounted nor referenced by any process;
  - the cluster is stable;
  - the removal scope is exactly that one path.
- The coordinator schedules it as a one-shot follow-up after hop 3; it was not done in this worker. Expected effect: about 7.4 GiB freed, raising ziti nodefs available from about 2.8 GB to about 10 GB.

## Still blocking hop 4 (unchanged)

- **vega removal** (P13), due before hop 4 because of kubelet skew.
- **Flux 2.4** (1.29–1.31): Flux 2.3.0 does not support 1.31.
- Add-ons are unchanged:
  - ingress-nginx is under its support exception;
  - kube-prometheus-stack 47 and its operator are best effort;
  - cert-manager 1.17 is EOL but supports up to 1.33;
  - Cilium 1.18 is tested on 1.30–1.33.

Evidence (metadata only, outside Git): `/home/dev/orca/artifacts/k3s-hop3-20261124-task_ee612458bc93/`. It holds `pre-health.json` (full baseline, including Helm revisions and restart counts), node and API discovery output, deprecated-API metrics, and `scripts/` (`watch3.py`, `apiprobe.sh`, `health.py`, `bouncer-sql.sh`, `events.sh`).
