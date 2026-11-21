# k3s upgrade prerequisites (2026-11-20)

Status: done except P4 (vega uncordon), which was stopped on purpose. The SUC controller bump is deferred.

Plan: `docs/k3s-upgrade-plan.md` (draft PR #3). Go-ahead: Lee, 2026-11-20 18:31 UTC, prerequisites only. **No k3s version change was made and no Ansible playbook was run.** enterprise stayed cordoned the whole time.

## Steps

| # | Step | Status | Commit |
|---|---|---|---|
| 1 | Re-pin Ansible `k3s_release_version` to v1.27.3+k3s1 | done | `20bdb52` |
| 2 | SUC Plans: drop `cordon` from server plan, add `drain` to agent plan | done; controller bump deferred | `002191f` |
| 3 | Off-node etcd snapshots to titan (CronJob) | done | `5427837` |
| 4 | Restore drill, off-cluster | done, passed | – |
| 5 | kube-vip `vip_interface: enp3s0` | done | `3307982` (template) + hand edit on enterprise |
| 6 | Flux 2.0.1 → 2.2.3 | done | `d2ab9e9` |
| 7 | Uncordon vega | **stopped, not uncordoned** | – |

### 2. SUC Plans

- SUC v0.11.0 hashes only `latestVersion`, `serviceAccountName`, the include-in-digest annotation and the secrets (`pkg/upgrade/plan/plan.go` `DigestStatus`). Changing `cordon` or `drain` therefore leaves the hash unchanged.
  - Every node already carries `plan.upgrade.cattle.io/<plan>=e1d892a3…`, so this change created no jobs. Verified after the push: same hash, no Jobs, no cordon changes.
  - The Plans stay inert until a `version` bump (hop 1).
- `drain.timeout` is an integer in nanoseconds (900000000000). The v0.11.0 CRD rejects `900s`.
- **Deferred: SUC v0.11.0 → v0.21.1.** It is not needed for safety: v0.21.1 has the same hash, node selection and uncordon logic. It is also riskier than the plan assumed:
  1. The v0.21.1 manifests change ClusterRoleBinding `system-upgrade`'s `roleRef` from `cluster-admin` to `system-upgrade-controller`. `roleRef` is immutable, so the Flux apply fails and the ks goes NotReady.
     - Fix: delete the CRB or rename it via a patch.
  2. The v0.21.1 Deployment requires a `control-plane` node and adds a hostname podAntiAffinity. The only control-plane node, enterprise, is cordoned, so the new pod stays Pending and the rollout stalls (the ks has `wait: true`).
     - Fix: patch the affinity, e.g. move SUC to a worker per plan §7.1, and use `strategy: Recreate`.
  - Do it as its own reviewed change before hop 1.

### 3. etcd snapshot offload

- **Source**, mounted read-only on enterprise:
  - `/var/lib/rancher/k3s/server/db/snapshots/*`
  - `/var/lib/rancher/k3s/server/token`
- **Target**: titan `10.20.30.99:/volume2/k8s-hdd/etcd-snapshots/`, root:root 0700, files 0600.
  - Mounted through static PV `etcd-snapshot-offload-titan` (reclaim Retain), PVC in `kube-system`, `subPath: etcd-snapshots`.
  - titan exports without root squash.
- **CronJob** `kube-system/etcd-snapshot-offload`:
  - Runs at `30 0,12 * * *` America/Los_Angeles, after k3s's local 00:00/12:00 snapshots.
  - `nodeName: enterprise`, so it bypasses the cordon without uncordoning.
  - Verifies sha256 against the source and writes `<snap>.sha256` alongside each copy.
  - Retention: 14 scheduled and 20 other snapshots.
  - Its Flux ks has substitution disabled.
- **On-demand use** (e.g. before each hop, after `k3s etcd-snapshot save`):
  ```
  kubectl --context home -n kube-system create job --from=cronjob/etcd-snapshot-offload <name>
  ```
- **First run** (`etcd-snapshot-offload-manual-20261120`): copied 7 snapshots plus the token.
  - Fresh snapshot: `offsite-prereq-20261120-enterprise-1795200089`, 40,276,000 bytes.
  - sha256 `47c481758ec0f86f5af132ae58717ebd84ea5677a8ea8277711b75575efa7398`, identical on enterprise and titan.

### 4. Restore drill

- **Where**: Docker on the octo host, reached from devbox. Image `rancher/k3s:v1.27.3-k3s1`.
- **Isolation**:
  - The container was on a `--internal` Docker network: its only route was 172.20.0.0/16, and both 10.20.30.250 and 1.1.1.1 returned "Network unreachable".
  - Server only (`--disable-agent`), so no restored workload could start.
- **Snapshot transfer**: piped `ssh enterprise sudo cat` → `docker exec -i`. It was never written to devbox disk. The sha256 matched titan.
- **Restore**:
  - `--cluster-reset --cluster-reset-restore-path` worked. It needs the server token in `<data-dir>/server/token`.
  - etcd reported "restored snapshot", current-rev 677216137.
- **Proof**, read from the restored API:
  - 4 nodes (enterprise, nova, vega, ziti) with their cordon state
  - 19 namespaces
  - 44 Flux Kustomizations and 34 HelmReleases
  - 49 Deployments, 29 PVs and 195 Secrets
  - the `etcd-snapshot-offload` CronJob created minutes before the snapshot
  - SUC Plans at v1.27.3
  - PGO `postgres`
- **Destroyed**: the containers, the network and all 8 anonymous volumes (`docker rm -f -v`, `docker volume rm`). Nothing remains.
- **Gotchas**:
  - `--node-ip 127.0.0.1` makes etcd bind 127.0.0.1:2380 twice and die. Use a real container IP.
  - In the k3s image, call `kubectl` directly; `k3s kubectl` fails there.

### 5. kube-vip

- **Before**: 424 restarts, the last on 2026-11-01. The home kubeconfig uses the VIP `https://10.20.30.250:6443`. The fallback `ssh enterprise sudo k3s kubectl` was verified first, and kube-vip's own kubeconfig points at 127.0.0.1.
- **Change**:
  - Backed up the live manifest to `/root/kube-vip-static-pod.yaml.bak-20261120`.
  - Installed the new file at 19:00:19 UTC.
- **Result**:
  - The VIP was unavailable from 19:00:21 to 19:00:36 (about 15 s). The direct IP answered throughout.
  - kube-vip re-took the lease at 19:00:38 and no longer auto-detects the interface.
  - Side effect: `cilium-operator` on ziti lost its lease during the blip, exited and restarted once at 19:00:37 (3 restarts now).
- **After**: 0 restarts, checked through 19:20 (20 min).
  - One devbox VIP probe at 19:14:38 timed out. There was no cluster-side trace: no kube-vip lease change, no restart, and no errors from vega's k3s or from Cilium. It was most likely the devbox → NetBird path.
- **Next**: keep watching for 7 days, as the plan says. The optional IPv6 RA change was not made; that is Lee's call.

### 6. Flux

- **Controllers now**: helm v0.37.4, kustomize v1.2.2, source v1.2.4, notification v1.2.4, image-reflector v0.31.2, image-automation v0.37.1. All Ready.
- **Status**: all 44 Kustomizations and 34 HelmReleases are Ready.
  - 33 HelmReleases were reconciled and adopted by helm-controller v0.37.4, which wrote v2beta2 status history.
  - `monitoring/speedtest-exporter` was already suspended.
  - Zero new Helm release secrets, i.e. no upgrades were triggered.
- **CRDs**: the HelmRelease CRD now serves v2beta1 and v2beta2; storedVersions are `[v2beta1, v2beta2]`.
- **Not done here**: no `flux migrate`, and `bootstrap/` (v2.9.6) was not touched. The devbox flux CLI (2.9.6) still can't read OCIRepository; use `kubectl`.

### 7. vega: not uncordoned

The cordon was added 2026-08-30T04:42:57Z, 11 minutes after vega's last pre-outage boot ended (2026-08-29 21:31:46 PDT).

Boot history from `journalctl --list-boots`:
- Boots ending 2026-08-23 02:17 and 2026-08-29 21:31 PDT ended abruptly: no shutdown markers and no kernel MCE, EDAC, I/O, lockup or OOM trace before the cutoff.
- After the second hang, vega stayed down until 2026-10-22 17:30 PDT.
- In early August it hard-crashed after 10–30 min under load. `docs/journals/2026-08-07-*` ties that to load (Satisfactory); the RAM is non-ECC.
- Root cause was never found (`docs/journal/2026-08-30-vega-notready-and-node-storage.md` is still Open). Commit 3f412e1 keeps cilium-operator off vega.

Current state is clean:
- 28 days up, load ~0.1, 0 kernel or journal errors in 24 h, Package 51 °C.
- SMART PASSED, 0 reallocated/pending/uncorrectable sectors, 1 UDMA CRC error.

But since 2026-10-22 vega has been cordoned and idle. Its uptime has never exercised the load-related hang. Under the "anything suspicious → do not uncordon" rule, it stays cordoned.

Options for Lee:
- (a) A supervised load test (e.g. `stress-ng` for 1–2 h with temperature logging), then uncordon plus a 24 h soak.
- (b) Uncordon with a 24 h soak while someone watches.
- (c) The plan's no-drain variant (§7.2).

Note: without vega, nova's drain does not fit on ziti alone. P3's agent `drain` plus SUC's uncordon-after-job would also uncordon vega at hop 1. Decide (a), (b) or (c) before hop 1.

## Hand-run changes (everything outside Git)

1. `ssh enterprise sudo k3s etcd-snapshot save --name offsite-prereq-20261120` (18:41 UTC). Created `offsite-prereq-20261120-enterprise-1795200089` in enterprise's local snapshot dir and kept it there, as with k3s's own snapshots.
2. `kubectl --context home -n kube-system create job --from=cronjob/etcd-snapshot-offload etcd-snapshot-offload-manual-20261120`
3. `flux --context home reconcile ...` for cluster, cluster-apps, the system-upgrade plans ks, the etcd-snapshot-offload ks and the flux ks.
4. On enterprise:
   - `sudo cp -a .../pod-manifests/kube-vip-static-pod.yaml /root/kube-vip-static-pod.yaml.bak-20261120`
   - Staged `/root/kube-vip-static-pod.yaml.new-20261120`.
   - `sudo install -m 0644 /root/kube-vip-static-pod.yaml.new-20261120 /var/lib/rancher/k3s/agent/pod-manifests/kube-vip-static-pod.yaml`
   - Rollback: install the `.bak` file back.
5. Drill on the octo Docker host:
   - Created and deleted containers `etcd-restore-drill-20261120` (twice) and network `etcd-restore-drill-net`.
   - Removed 8 anonymous volumes.
   - Pulled the image `rancher/k3s:v1.27.3-k3s1`; it is still cached and holds no data.

Not run: no Ansible, no cordon or uncordon, no `kubectl apply/edit/patch/delete`, and no k3s restart.

## Final health (19:20 UTC)

- All 4 nodes Ready. enterprise and vega are SchedulingDisabled; nova and ziti are schedulable.
- API `/readyz` ok.
- 44/44 Kustomizations and 34/34 HelmReleases Ready.
- No unhealthy pods and no DaemonSet shortfall.
- No SUC Jobs.
- Restart changes since baseline: kube-vip (new pod, 0), and cilium-operator +1 at the VIP blip.

## Checks at hop 1

- SUC Plans: server plan without cordon, agent plan with drain.
- Take a pre-hop snapshot, run the offload job, and check its sha256 on titan.
- Decide on vega first.
- Decide on the SUC bump.

## vega un-cordon and 30-minute watch (21:01 UTC)

Lee asked to uncordon vega (cordoned since 2026-08-30 after unexplained hangs on Aug 23 and Aug 29). Only the un-cordon was done: no k3s change, no Ansible, no drain. enterprise stays cordoned.

- Baseline (21:01): Ready, uptime 28d, load 0.10, 4.4Gi/31Gi used, root 11% used, package temp 42C, no warning/error kernel entries since the Oct 22 boot (read via `sudo -n dmesg`/`journalctl`; the `setup` user is not in adm or systemd-journal). Only the 9 DaemonSet pods ran there.
- `kubectl --context home uncordon vega` at 2026-11-20T21:01:55Z; `spec.unschedulable` empty, enterprise still `true`.
- Checks every 5 min (21:02, :07, :12, :17, :22, :27, plus a final one at 21:32): vega Ready every time, load 0.04-0.24, memory used 4.5Gi (kubectl top 32%, CPU 0-1%), package temp 40-42C, no kernel warnings, no hung task/MCE/NMI/I/O error matches, no pod restarts.
- No new pods were scheduled onto vega in the 30 minutes (still the same 9 DaemonSet pods); nothing was forced or moved there.
- Not re-cordoned. The plan's 24h soak runs from 21:01:55Z; this entry only covers the first 30 minutes. An idle node tells us little about the load-triggered hangs, so keep watching.

## vega load test (2026-11-21 05:10-05:57 UTC): FAILED, vega hung in stage c

Lee asked for a load test of vega. It hung during stage c, the same way as Aug 23 and Aug 29: no warning in the kernel log, and the host simply stopped answering. Result: vega is re-cordoned and needs a hands-on power-cycle by Lee.

What ran: test pods only, in the temporary namespace `vega-loadtest`, pinned with nodeSelector `kubernetes.io/hostname: vega`, no toleration. Image `alexeiled/stress-ng:0.12.05` (stress-ng 0.12.05), memory limits per pod, emptyDir `sizeLimit: 10Gi`, no NFS or PVC. vega is an Intel i7-8700T, 6 cores / 12 threads, 31Gi RAM (allocatable 30632536Ki), 12 CPU. A watcher on devbox logged Ready, `kubectl top`, load, memory, temperatures and new warn+ kernel lines (via ssh with a 10s timeout) every 60s.

| Stage | Window (UTC) | stress-ng args | Peak load1 | Memory (free used / top) | Peak pkg temp | Kernel |
|---|---|---|---|---|---|---|
| idle baseline | 05:10 | - | 0.11 | 4.5Gi / 32% | 41C | boot-time (Oct 22) audio/FAT noise only |
| a | 05:11-05:27 (completed) | `--cpu 6 --cpu-method matrixprod --timeout 900s` | 6.6 | 4.6Gi / 32% | 78C | clean |
| b | 05:31:44-05:47 (completed) | `--cpu 12 --vm 2 --vm-bytes 8G --vm-keep --timeout 900s` | 16.9 | 12.8Gi / 60% (64% briefly) | 79C | clean |
| c | 05:47:39-~05:56 (HUNG) | b + `--hdd 2 --hdd-bytes 1G --temp-path /scratch` | 20.6 | 13.8Gi / 60% | 79C (one 90.0C reading in the b to c handover) | none captured |

- First stage b run used `--vm-bytes 4G` (stress-ng 0.12 splits it across workers, so only 4G was allocated, memory 46%); it was deleted after 3 minutes and re-run with 8G. Stage d (30 min, 75-80% memory, `--matrix`, `--vm`, `--hdd`, `--iomix`) was never started.
- Last good sample 05:55:28Z: Ready, load 19.5, 12.8Gi used, 77C. Kubelet's last heartbeat 05:53:37Z. At 05:56:33Z ssh to vega timed out; `kubectl top` lost its metrics; at 05:57:02Z node `NotReady` (kubelet stopped posting status), ssh "No route to host", ping 100% loss. Memory, temperature and load were all inside limits at the last sample: the hang was not caused by memory pressure or heat (max 79C vs 94C high / 100C crit).
- No MCE, hung-task, lockup, NMI, I/O or EDAC lines in the last captured kernel log; the only post-boot warn line was a harmless stress-ng "oom_adj is deprecated". Kernel log from the hang itself could not be read (host down); check `sudo dmesg`/`journalctl -b -1` after the power-cycle.
- Actions: `kubectl --context home cordon vega` at 05:57:13Z, `kubectl --context home delete ns vega-loadtest` at 05:57:13Z (namespace stuck Terminating while vega is down; pods clear when it returns). Not touched: k3s, Ansible, enterprise (still cordoned), real workloads. No recovery attempt.
- Verdict: FAILED at stage c (CPU 100% + 60% memory + disk I/O, ~9 minutes in). The August hangs are reproducible under sustained combined CPU and disk I/O load; the cause is still unknown (suspect the storage path or platform firmware/power; stages a and b alone were fine).
- Advice for upgrade step 1: do not use vega as a schedulable worker for the upgrade, and the 24h soak (ends 2026-11-21 21:02Z) is no longer meaningful for it, since vega is cordoned and down. Evidence: the local watcher log and `vega-loadtest-evidence.txt` in the worktree (not committed).
