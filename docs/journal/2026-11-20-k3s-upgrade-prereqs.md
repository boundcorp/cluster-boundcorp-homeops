# k3s upgrade prerequisites (2026-11-20)

Status: done except P4 (vega uncordon), which was stopped on purpose; vega is now out of the campaign (hung under load 2026-11-21). The SUC controller bump was done on 2026-11-23 (`3d97f22`, `673b2b7`, see the last section).

Plan: `docs/k3s-upgrade-plan.md` (draft PR #3). Go-ahead: Lee, 2026-11-20 18:31 UTC, prerequisites only. **No k3s version change was made and no Ansible playbook was run.** enterprise stayed cordoned the whole time.

## Steps

| # | Step | Status | Commit |
|---|---|---|---|
| 1 | Re-pin Ansible `k3s_release_version` to v1.27.3+k3s1 | done | `20bdb52` |
| 2 | SUC Plans: drop `cordon` from server plan, add `drain` to agent plan | done; controller bump v0.11.0 → v0.21.1 done 2026-11-23 | `002191f`, `3d97f22`, `673b2b7` |
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

## vega post-freeze diagnosis (2026-11-21 16:08-16:30 UTC)

Lee power-cycled vega; it booted at 2026-11-21 16:07:38Z (08:07 PST). This pass was read-only: journal, SMART, sysfs, dmidecode, plus Prometheus and Loki through the API proxy. No reboot, stress, package, firmware or cordon change. The only change is this entry.

### Node state (16:08Z)

- vega Ready since 16:07:49Z, still cordoned (`unschedulable=true`, `node.kubernetes.io/unschedulable:NoSchedule`). enterprise still cordoned. nova and ziti were not touched.
- Only the 9 DaemonSet pods run on vega (cilium, cilium-envoy, csi-nfs-node, intel-gpu-plugin, nfd-worker, node-exporter, loki-canary, vector-agent, netbird), each with +1 restart from the power-cycle.
- The `vega-loadtest` namespace is gone (NotFound). It finished deleting once vega's kubelet came back, so no finalizers had to be removed.

### When it froze: 05:56:03-05:56:33Z

- The persistent journal works (`/var/log/journal/<machine-id>`; `--list-boots` goes back to Feb 2026).
- Boot -1 (2026-10-22 17:30 PDT to 2026-11-20 21:55:44 PST) ends at **05:55:44.012Z** with `systemd[1]: Removed slice user-1000.slice`. That is routine teardown after the watcher's 05:55:33 ssh session ran `dmesg`. k3s last logged at 05:47:41 (stage c pod start). Nothing after that is unusual.
- Prometheus: the last good node-exporter scrape was at **05:56:03Z** and the 05:56:33 scrape failed (`up=0`). So the host died within that 30 s window, 35 s after the last good watcher sample (05:55:28Z). The journal's last ~20-50 s never reached disk.
- At 05:56:03 the box looked healthy:
  - IO PSI `stalled` was flat at 111.08 s (no I/O stall building), memory PSI 0.03 s.
  - Dirty 348 MB, writeback 17 MB.
  - sda ~105 MB/s writes, io_time 1.0 (disk saturated but completing I/O).
  - Package and core throttle counters 0 for the whole boot; temp 77-79 °C. The one transient was 92 °C at 05:48:30, during the b-to-c handover.
  - Root fs 775 GB free.
- Loki only has pod logs (vector ships no journald or kernel stream). The last vega line was at 05:55:25.
- Boot -1 has **no** kernel warnings after boot. There are no lockup, hung-task, RCU-stall, MCE, EDAC, ATA, I/O or ext4 lines; the only warning is stress-ng's `oom_adj is deprecated` at 05:12:16.
- The earlier hangs show the same pattern:
  - Boot -2 (Aug 25 to Aug 29) ends at 2026-08-30T04:31:46.263Z mid-stream. k3s was logging every 2 s (an orphaned-pod volume error) and simply stopped.
  - Boot -3 (Aug 22 to Aug 23) ends at 2026-08-23T09:17:01Z with the hourly cron. It was quiet before that, so the exact hang time is unknown.
  - Neither boot has kernel warnings or any lockup line.
- No crash artifacts:
  - efi-pstore is registered as the pstore backend, so a kernel panic would have left a dump. `/sys/fs/pstore` and `/var/lib/systemd/pstore` are both empty, which means no panic dump was ever written.
  - No `/var/crash` and no kdump installed.
  - `last -x` shows each of these boots with no shutdown record. The SSD counts 229 power-off retracts (unsafe power-offs) out of 258 power cycles.

### Key finding: the hardware watchdog was armed and did not reset the box

- `/etc/systemd/system.conf.d/watchdog.conf` (dated 2025-11-19) sets `RuntimeWatchdogSec=30s` on `/dev/watchdog0`, which is `iTCO_wdt` (Intel PCH TCO v6, nowayout=0).
- Boots -1, -2 and -3 all logged `Using hardware watchdog 'iTCO_wdt'` and `Watchdog running with a timeout of 30s`.
- Also set: `kernel.panic=10`, `nmi_watchdog=1`, `softlockup_panic=0`, `hardlockup_panic=0`.
- Yet vega stayed hung for ~10 h until the manual power-cycle, and the same happened on Aug 23 and Aug 29.
- A software hang (kernel deadlock, panic, livelock) stops systemd feeding the TCO timer, and the PCH should reset the board about 30-60 s later. A panic would also self-reboot after 10 s.
- Either the platform was wedged below the OS (power delivery, VRM or CPU/PCH hard hang), or the iTCO reset path does not work on this board. The latter has never been tested; `bootstatus` is 0.

### Hardware checks

| Check | Result |
|---|---|
| Machine | Lenovo ThinkCentre M720q Tiny (10T7001QUS), board 312D |
| BIOS | **M1UKT39A, 2019-05-10**: original, never updated |
| CPU / microcode | i7-8700T (sig 0x906ea), microcode **0xf2** (2023-01-12) from intel-microcode 3.20230512.1; candidate 3.20251111.1~deb12u1 is available but not installed |
| Kernel | 6.1.0-10-amd64 (6.1.37, Jul 2023), the only kernel besides -9. Cmdline already has `intel_idle.max_cstate=1 pcie_aspm=off nvme_core.default_ps_max_latency_us=0`, so the C-state/ASPM workarounds were tried earlier and did not help. |
| RAM | 2x16 GB DDR4-2666 SO-DIMM, mfr 85F7, P/N J642GU42J9266NF; **non-ECC** (`EDAC ie31200: No ECC support`). No ras-mc-ctl/rasdaemon/mcelog installed; no MCE lines in any boot. |
| Power limits | RAPL PL1 35 W, **PL2 92 W**. The Tiny runs on an external Lenovo brick; its wattage can't be read from software (SMBIOS type 39 is "To Be Filled By O.E.M."). |
| Disk | single SATA SSD `OEM Genuine 1TB` (fw R0817B0, not in smartctl db, DRAM-less no-name) on Intel Cannon Lake AHCI at 6 Gb/s. **It holds everything**: `/` = sda2 ext4, and `/var/lib/kubelet` (emptyDir) and `/var/lib/rancher` are plain directories on `/`. mq-deadline, queue depth 32, no swap. |
| SMART | PASSED. Reallocated 0, pending 0, offline-uncorrectable 0, program/erase fails 0. UDMA CRC **1**, unchanged since Aug. SATA phy ICRC/R_ERR 0, 2 COMRESETs. Error log empty, no self-tests logged. 57,857 POH (56,671 in Aug), 3% endurance used, 36 °C. Hardware_ECC_Recovered 3,482,895 (vendor-specific, no baseline). |
| ATA/I/O in kernel log | none in any retained boot (no `ata1` exception/reset, no I/O or ext4 errors) |
| Thermal now | package 41 °C idle, throttle counters 0 |
| IPMI / NVMe | none (consumer Tiny, no BMC; no NVMe device) |

`smartctl -t short` was not run: reading the attributes was enough, and the disk shows no fault.

### Diagnosis, most likely first

1. **Platform/power-level hard hang (external PSU brick, board VRM, or CPU/PCH)**. Most likely.
   - For:
     - Death was instant, inside a 30 s window. There was no lead-up in PSI, dirty pages, temperature or throttling.
     - Nothing reached the kernel log, and neither panic=10 nor the NMI hard-lockup detector produced a reboot or a line.
     - The hardware TCO watchdog failed to reset it three times. Only a wedged platform (or a dead reset path) explains that.
     - It is reproducible only under sustained high combined load: Satisfactory in Aug, stage c now.
     - The brick and board are ~6.6 years powered on, and PL2 92 W is above what a 65 W brick can feed with SSD, RAM and board load on top.
   - Against: stage b ran the same 100% CPU for 15 min without failing. Stage c only added SSD/SATA and page-cache traffic, a few watts more.
   - Confirm:
     - Read the brick's label (65 W vs 90 W).
     - Rerun stage c on a known-good 90 W+ Lenovo brick, or with PL2 capped to 35-45 W.
     - Test the iTCO reset so we know whether a broken reset path or a wedged platform is why it never rebooted.
2. **RAM (non-ECC, generic modules)**.
   - For: no ECC means silent corruption is possible, and load-triggered.
   - Against: bad RAM usually produces oopses or panics, and panic=10 would reboot. Stage b's 8 GB `--vm` passed.
   - Confirm: memtest86+ for at least 4 passes, then one DIMM at a time.
3. **Firmware/microcode/kernel**.
   - For: the BIOS is from 2019 and Lenovo shipped many later M720q releases; microcode and kernel are 2-3 years stale.
   - Against: a pure kernel hang should have tripped the TCO reset.
   - Confirm: update BIOS, microcode and kernel, then rerun stage c.
4. **SSD/SATA path**. Unlikely.
   - For: the freeze began only once disk I/O was added, and the disk is a no-name DRAM-less SSD.
   - Against:
     - SMART is clean, with no errors since Aug, and there are no ATA resets in any boot.
     - IO PSI was flat right before the end.
     - A stalled SSD leaves ping working and logs hung tasks after 120 s; here ping died immediately.
   - Confirm: rerun stage c with `--hdd` writing to NFS or tmpfs instead of sda. If that survives, revisit.
5. **Thermal**: ruled out. Throttle count was 0 for the whole run, max 79 °C sustained against 94 °C high.

### Next steps

| # | Step | Needs |
|---|---|---|
| 1 | Keep vega cordoned and out of the upgrade plan. Treat it as non-schedulable hardware until a cause is confirmed. | nothing |
| 2 | Read the power brick's wattage label and check it for heat or damage; look at the BIOS event log if the setup menu has one. | physical access |
| 3 | Install a netconsole target (e.g. to enterprise or rigel) and set `kernel.softlockup_panic=1`, `hardlockup_panic=1` and `hung_task_panic=1`. Next time we either get the dying kernel's last words over the network or a reboot. | Lee's go-ahead (config change) |
| 4 | Test the iTCO watchdog once: stop feeding it, e.g. `sysctl kernel.panic=0` plus `echo c > /proc/sysrq-trigger`, and check that the board resets within ~60 s. If it doesn't reset, the watchdog is useless as a safety net. | Lee's go-ahead (deliberate crash and reboot) |
| 5 | memtest86+, at least 4 passes. | physical access + downtime |
| 6 | Update the BIOS to the latest M720q release, then intel-microcode 3.20251111 and the current bookworm kernel. | Lee's go-ahead (firmware/packages; BIOS flash wants physical presence) |
| 7 | After 2-6, rerun stage c in steps: (a) known-good 90 W+ brick, (b) PL2 capped, (c) `--hdd` off sda. Each takes ~15 min, under the same watcher. | Lee's go-ahead |
| 8 | If it still hangs on a good brick with good RAM and current firmware, retire or RMA the board/CPU. Moving the SSD is not enough evidence on its own. | Lee |

## SUC controller v0.11.0 → v0.21.1 (2026-11-23 03:19-03:31 UTC)

Go-ahead: Lee's 2026-11-20 prerequisites approval (this was the deferred part of P3). No k3s version change, no Plan version change, no cordon or drain. Commits on main: `3d97f22` (bump) and `673b2b7` (drain timeout fix).

### What changed (`kubernetes/apps/system-upgrade/system-upgrade-controller/app/kustomization.yaml`)

- Upstream manifests and CRD at v0.21.1, image `rancher/system-upgrade-controller:v0.21.1`.
- **ClusterRoleBinding renamed** `system-upgrade` → `system-upgrade-controller` with a JSON6902 patch. v0.21.1 points the binding at the new ClusterRole `system-upgrade-controller` instead of `cluster-admin`, and `roleRef` is immutable. Under the new name Flux creates the binding, then prunes the old one (the ks has `prune: true`). The `system-upgrade` SA lost `cluster-admin` and now has the upstream least-privilege ClusterRoles `system-upgrade-controller` and `system-upgrade-controller-drainer` (pods/eviction, nodes get/patch), plus the namespaced Role (jobs, secrets).
- **Deployment runs on a worker.** The upstream nodeAffinity (`control-plane Exists`) is replaced with `control-plane DoesNotExist`, `hostname NotIn [vega]`, `os In [linux]`, and the tolerations with `[]`. The upstream hostname podAntiAffinity is kept.
- **Strategy stays RollingUpdate** with `maxSurge: 0`, `maxUnavailable: 1`. Upstream's `Recreate` failed the server-side dry-run (`spec.strategy.rollingUpdate: Forbidden: may not be specified when strategy type is 'Recreate'`), because the live object has a defaulted `rollingUpdate` block. With one replica this behaves the same as Recreate.

### Checks before the push

- **Plan hash.** `DigestStatus` and `MungeVersion` are identical in v0.11.0 and v0.21.1: sha224 of `latestVersion` + `serviceAccountName` (no secrets, no include-in-digest annotation). Recomputed locally, the hash is `e1d892a3…`, which every node already carries, so no node is selected and no Job is created.
- **Server-side dry-run** (`--server-side --field-manager=kustomize-controller --dry-run=server`) of the full render passed after the strategy fix. A negative control applying the unrenamed `system-upgrade` CRB with the new roleRef failed with `cannot change roleRef`, as expected.
- The render with kustomize v5.3.0 (the version in kustomize-controller v1.2.2) was byte-identical to kubectl's.

### Problem found after the push: drain.timeout int overflow (fixed in `673b2b7`)

- After `3d97f22` the new controller started on nova and took the lease, but could not list Plans: `json: cannot unmarshal number 900000000000 into Go struct field DrainSpec.items.spec.drain.timeout of type int32`.
- Cause: SUC ≥ v0.16.3 types `drain.timeout` as `IntOrString`, whose int is an int32. 900 s in nanoseconds does not fit. The v0.11.0 CRD only accepted an int (see §2 above), so the old value was valid until now.
- Effect: fully inert (no Plans visible, so no Jobs), but the controller did nothing.
- Fix: `timeout: "900s"` (the v0.21.1 CRD accepts int-or-string, and a string goes straight to `kubectl drain --timeout`). `drain` is not part of the hash. The coordinator approved fixing forward instead of rolling back, since the state was inert. The server Plan has no duration fields.
- **Rule from now on: Plan durations must be strings.** An int is only usable below ~2.1 s.
- At 03:28:15 the controller started its Plan controller and logged `Jobs complete for version v1.27.3-k3s1. Hash: e1d892a3…` for both Plans. Since then there have been 0 errors.

### Verification (03:20-03:31, plus a 5 s watcher on jobs and cordons from 03:20 to 03:45)

- `system-upgrade-controller` 1/1 Running on **nova**, image v0.21.1, 0 restarts.
- Both ks (`cluster-apps-system-upgrade-controller`, `-plans`) Ready at `673b2b7`.
- Plans `server` and `agent`: version v1.27.3+k3s1, `Validated=True`, `LatestResolved=True`, `Complete=True`, `applying` empty, hash `e1d892a3…`.
- **0 Jobs and 0 upgrade pods** in `system-upgrade` at every check. The only events are the controller's own rollout and the two `Complete` events.
- Cordons unchanged: enterprise and vega `unschedulable=true`, nova and ziti schedulable. All 4 nodes v1.27.3+k3s1.
- `clusterrolebinding/system-upgrade` is gone (pruned); `system-upgrade-controller` and `system-upgrade-drainer` exist.

### Notes for hop 1

- SUC's agent order is deterministic: nodes are sorted by sha256(node UID + plan UID + latestHash). With vega excluded, hop 1 (v1.28.15+k3s1) runs **ziti, then nova**. The order for every hop is in the plan (§4).
- The agent Plan's nodeSelector still matches vega (no control-plane label). Before any agent version bump, vega must be excluded (`kubernetes.io/hostname NotIn [vega]` in the agent Plan), otherwise SUC would drain and then **uncordon** vega. SUC v0.21.1 also skips any node labelled `plan.upgrade.cattle.io/agent=disabled`, but that is a kubectl label outside Git, so prefer the nodeSelector.
- The upstream ConfigMap sets the drain/cordon kubectl image to `rancher/kubectl:v1.30.3`. Against a 1.28 API server that is +2 minors of client skew. Drain uses only GA APIs, so this is fine, but consider pinning it to the hop's minor if a drain misbehaves.

### Rollback

A plain `git revert 673b2b7 3d97f22` is **not** enough. The v0.11.0 Deployment requires `node-role.kubernetes.io/master` and has no `node.kubernetes.io/unschedulable` toleration. The old pod only ran on enterprise because it was scheduled 8 minutes before the 2026-08-04 cordon, so after a revert the new pod would stay Pending and the ks (`wait: true`) would go NotReady.

Rollback is a single commit that:
1. Restores the v0.11.0 kustomization (the revert brings back the `system-upgrade` CRB with `cluster-admin`, and Flux prunes the two new CRBs).
2. Keeps a worker nodeAffinity patch on the v0.11.0 Deployment.
3. Restores the int `drain.timeout: 900000000000`, because the v0.11.0 Go type (`time.Duration`) cannot decode `"900s"`.

Server-side dry-run it before pushing. Nothing else was touched by hand, so no manual restore is needed.
