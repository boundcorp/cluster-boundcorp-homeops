# 2026-08-01 Power Outage and Node Storage Failures

Status: Active

Last updated: 2026-08-04 11:32 PDT

## Summary

A power outage on 2026-08-01 caused the home Kubernetes cluster to restart repeatedly and left many pods stuck terminating. The pod cleanup was a symptom of broader node failures:

- `enterprise`, the only control-plane/etcd node, developed severe local disk I/O stalls. These stalls make etcd unresponsive, cause the Kubernetes API and kube-vip to flap, and eventually leave the cluster unavailable.
- `ziti` no longer boots its local OS and falls through directly to network/PXE boot.
- `vega` boots reliably and has no evidence of local disk failure. Its Kubernetes and Cilium errors are downstream effects of the unavailable control plane.
- Two replacement SSDs were ordered on 2026-08-03. One is committed to `enterprise`; the second is tentatively intended for `nova`, with `ziti` still an alternative pending hardware/boot assessment.

## Current State

Snapshot updated 2026-08-04 at 09:04 PDT:

| Host or endpoint | Address | State | Notes |
| --- | --- | --- | --- |
| `enterprise` | `10.20.30.111` | Ready, cordoned | Running from the verified Patriot clone. Control plane, etcd, direct API, and kube-vip are stable after draining application workloads. No new storage errors observed. |
| `vega` | `10.20.30.112` | Ready | OS, SSH, and k3s agent are healthy. |
| `ziti` | `10.20.30.113` | Unreachable | Firmware falls through to PXE instead of booting locally. Keep its Kubernetes Node registration for possible recovery. |
| `nova` | `10.20.30.114` | Unreachable | Not currently in the active Ansible worker inventory. Tentative target for the second SSD. |
| kube-vip | `10.20.30.250` | Reachable | Stable after draining application workloads from `enterprise`. |

The cluster runs k3s `v1.27.3+k3s1`. `enterprise` is the only control-plane/etcd member, so its failure takes down cluster management and the API VIP.

## Confirmed Findings

### `enterprise`: severe system-disk I/O stalls

System disk:

- Device: `/dev/sda`
- Model: `PH6-CE120-G`
- Serial: `511190305031002367`
- Size: 120 GB nominal / 111.8 GiB
- Root: `/dev/sda2`, ext4
- EFI: `/dev/sda1`, FAT

Evidence captured from the host journal through a privileged diagnostic pod:

- etcd operations expected to complete within 100 ms took between 5 and 38 seconds.
- etcd WAL `fdatasync` calls took 1.1 to 2.27 seconds.
- API requests repeatedly failed with `etcdserver: request timed out` and `context deadline exceeded`.
- `systemd-journald` exceeded its three-minute watchdog and was killed with `SIGABRT`.
- The kernel reported `containerd-shim` blocked in uninterruptible `D` state for more than 120 seconds.
- The blocked-task stack was waiting in `wb_wait_for_completion`, `sync_filesystem`, and `ovl_sync_fs`, directly implicating filesystem writeback.
- The persistent system journal was renamed as corrupted or uncleanly shut down.
- The EFI FAT filesystem was reported as not properly unmounted.

No kernel OOM event or thermal shutdown was found. etcd's initial corruption check passed after reboot, but this does not make continued operation on the current disk safe.

The removed SSD was later read through a SATA-to-USB sled. SMART reported a passing attribute check, zero logged read or uncorrectable errors, zero interface CRC errors, 33 C temperature, and 57,759 power-on hours. The complete device was recovered with GNU ddrescue without a single read error or bad area. This means the failure may be intermittent SSD behavior or the host's SATA cable, port, power, or controller path; it does not invalidate the severe writeback stalls observed while the disk was installed in `enterprise`.

Conclusion: the `enterprise` system SSD or its SATA I/O path is failing or stalling badly enough to make etcd unusable. Replace the disk even if a later SMART test appears nominal.

### `vega`: disk appears healthy; control-plane dependency causes Kubernetes errors

System disk:

- Device: `/dev/sda`
- Model: `OEM Genuine 1TB`
- Serial: `DW190612100529B04`
- Size: 1 TB nominal / 931.5 GiB
- Root: `/dev/sda2`, ext4

The previous and current kernel journals contained no local I/O errors, ATA resets, hung tasks, or writeback stalls. The observed failures were Kubernetes API TLS handshake timeouts against the local k3s proxy at `127.0.0.1:6444`, followed by Cilium failures when configuration could not be read from the unavailable control plane.

SSH was separately verified to be healthy:

- `sshd` remained active and listened on IPv4 and IPv6 port 22.
- The service had zero restarts.
- The host input firewall policy was `ACCEPT` with no rule blocking port 22.
- A full SSH login and command execution succeeded while the Kubernetes VIP was unavailable.

Diagnostic correction: earlier reports that SSH port 22 was closed came from using zsh with `/dev/tcp`, which zsh does not support. Later checks used `nc` and a real SSH connection.

### `ziti`: local boot failure

`ziti` goes directly to network/PXE boot. This indicates that firmware does not find a usable local boot entry or disk. The exact cause remains unconfirmed:

- missing or failed system disk;
- loose SATA/NVMe connection;
- lost UEFI boot entry or changed boot order;
- damaged EFI/bootloader data.

The Kubernetes Node registration was intentionally preserved for later recovery.

## Actions Already Taken

- Observed up to 59 pods stuck terminating while `vega` and `ziti` were unavailable.
- Allowed `vega`'s kubelet to reconcile its pods naturally after it returned.
- Force-removed 24 pods that were already terminating on the presumed-lost `ziti` node.
- Did not delete the `ziti` Node object.
- Moved the USB workloads to `vega`; node-feature-discovery later detected:
  - `feature.node.kubernetes.io/usb-ff_10c4_ea60.present=true`
  - `feature.node.kubernetes.io/usb-ff_18d1_9302.present=true`
- Confirmed the direct `enterprise` API could briefly recover before etcd I/O latency caused another outage.
- Ordered two replacement SSDs on 2026-08-03.
- Removed the old `enterprise` SSD and identified it by model and serial through a SATA-to-USB sled.
- Created a restricted critical-data archive containing `/etc/rancher/k3s` and `/var/lib/rancher/k3s/server/token` at `/home/linked/enterprise-recovery/enterprise-k3s-critical-2026-08-03.tar`; its SHA-256 checksum verifies successfully.
- Confirmed that no etcd snapshots were present in the default `/var/lib/rancher/k3s/server/db/snapshots` directory.
- Created a complete ddrescue image at `/home/linked/enterprise-recovery/enterprise-PH6-CE120-G-511190305031002367-2026-08-03.img`. All 120,034,123,776 bytes were rescued with zero read errors and zero bad areas; the saved SHA-256 checksum verifies successfully. The source SSD was left unmounted after recovery.
- Ran `e2fsck -f -n` against the root partition inside the recovered image. It exited successfully with zero bad blocks and no filesystem corruption; only an optional extent-tree optimization was reported.
- Identified the new `enterprise` target as a Patriot P210 128GB, serial `P210HHBB241205001891`, capacity 128,035,676,160 bytes. Initial SMART attributes reported zero power-on hours, host writes, reallocated sectors, uncorrectable errors, and interface CRC errors.
- Wrote the verified full-disk image to the Patriot with ddrescue. All 120,034,123,776 image bytes were written with zero errors. A subsequent SHA-256 read-back of the same byte range exactly matched the master image (`0052baeb298bbc0fd3b8338023a89ce4f106f21cfdec4ea8c8811fb0bcc11b0a`).
- Flushed the replacement disk, re-read its partition table, confirmed the cloned FAT EFI and ext4 root partitions with their original UUIDs, and set the disk read-only for safe removal. Approximately 8 GB remains unallocated until the recovered system boots and etcd is validated.
- Installed and booted the Patriot clone in `enterprise`. The host, SSH, direct API, etcd readiness, and kube-vip initially returned; both `enterprise` and `vega` reported Ready. The root filesystem mounted read-write from the expected Patriot SSD.
- Observed no new ATA errors, kernel I/O errors, ext4 root errors, hung tasks, blocked writeback tasks, OOM kills, or etcd corruption. The temporary etcd startup corruption check passed.
- The recovered node immediately started dozens of existing workload containers. On four CPUs, load peaked above 50 with approximately 95% CPU pressure and significant memory pressure while I/O wait remained near zero. etcd reads and API operations slowed from hundreds of milliseconds to several seconds.
- K3s exited three times after its embedded controller-manager lost leader election under CPU starvation. Its systemd unit uses `KillMode=process`, leaving hundreds of container tasks running across restarts and perpetuating the recovery storm. The direct API and kube-vip consequently flap even though SSH remains reachable.
- Stopped active readiness polling at 19:31 PDT to avoid adding load. No workload eviction or other disruptive cluster mutation has yet been performed.

### 2026-08-04 controlled drain and stabilization

- Rebooted `enterprise`, caught the API as it became ready, and cordoned the node before the recovery storm could schedule additional work.
- Drained more than 50 non-DaemonSet workloads from `enterprise` with a 20-second grace period. The Kubernetes Node registration and required DaemonSets were preserved.
- Application and controller replacements scheduled primarily on `vega`. `enterprise` retained only Cilium, kube-vip, the NFS node plugin, node-feature-discovery worker, Intel GPU plugin, and NetBird during the initial drain.
- Load on `enterprise` fell from above 50 to approximately 1, memory availability recovered, k3s remained active with zero restarts after the reboot, and direct API plus kube-vip stayed continuously reachable during a five-minute readiness check. Readiness requests stabilized around 220-250 ms.
- CoreDNS and the system-upgrade controller could not schedule on `vega` because of node affinity. Briefly uncordoned `enterprise` until those two pods bound to it, then immediately cordoned the node again. Both pods became Ready.
- Verified DNS from the running ESPHome pod on `vega`; `github.com` resolved successfully. ESPHome, Home Assistant, Mosquitto, PostgreSQL, Flux controllers, NFS CSI controller, and ingress-nginx were Running on `vega`.
- Triggered a normal reconciliation of Flux's `home-kubernetes` GitRepository after CoreDNS recovered. It succeeded and stored revision `main@sha1:1cbaa3b8acad40926d28b774b30b661f97c1cc27`, confirming that the earlier DNS errors were stale recovery-time conditions.
- Created a fresh 117 MB etcd snapshot at `/var/lib/rancher/k3s/server/db/snapshots/pre-drain-recovery-20260804-0901-enterprise-1785859306`. The snapshot completed successfully in approximately two seconds.
- No new kernel storage errors, ATA resets/timeouts, ext4 root errors, hung tasks, or slow etcd WAL/fdatasync warnings were observed after the drain.
- At the 30-minute checkpoint after the 08:53 reboot, `enterprise` remained `Ready,SchedulingDisabled`; k3s was active with zero restarts, `/readyz` passed in approximately 220 ms, load averaged 0.50, and both the direct API and kube-vip were reachable. Current-boot searches found no disk, etcd timeout, slow WAL/fdatasync, leader-election, OOM, or hung-task faults.
- Cluster workload summary at that checkpoint was 69 Running, 6 Completed, 18 Error, 3 UnexpectedAdmissionError, 1 Terminating, and 1 ImagePullBackOff. The Error pods were old failed backup jobs rather than active CrashLoops.
- Three old Jellyfin pods remained in `UnexpectedAdmissionError` because Kubernetes reported no healthy `gpu.intel.com/i915` devices on `vega`. One older Jellyfin pod remained terminating.
- Follow-up confirmed that `vega`'s integrated Intel UHD 630 is healthy: PCI device `8086:3e92` is bound to `i915`, `/dev/dri/card0` and `/dev/dri/renderD128` exist, firmware loaded, and the current boot contains no GPU hang/reset errors. The Intel device plugin is Running and advertises four allocatable shares because its DaemonSet uses `-shared-dev-num 4`. Current pod `jellyfin-85b5d677c7-wbtbj` is Running on `vega` with one `gpu.intel.com/i915` share allocated. The unhealthy messages belong only to stale pods rejected during the outage/recovery window.
- `networking/k8s-gateway-f8f78b4b6-6xd5p` remained in `ImagePullBackOff` for `quay.io/oriedge/k8s_gateway:v0.3.4`.
- At the 2 hour 37 minute post-reboot checkpoint, `enterprise` remained Ready and cordoned with k3s active and zero restarts. Direct API, kube-vip, and `/readyz` were healthy; readiness latency was approximately 253 ms and load averaged 1.28. Current-boot logs still contained no storage, etcd timeout, slow WAL/fdatasync, leader-election, OOM, or hung-task faults.
- Kubernetes node conditions reported `MemoryPressure=False`, `DiskPressure=False`, `PIDPressure=False`, `NetworkUnavailable=False`, and `Ready=True`. Linux memory-pressure averages were zero and approximately 3.1 GiB remained available, despite Metrics Server's higher working-set percentage.
- All key workloads remained Running without restarts since the drain: ESPHome, Home Assistant, Mosquitto, Jellyfin with Intel GPU allocation, PostgreSQL, Flux source controller, ingress-nginx, CoreDNS, and kube-vip. Flux remained Ready at `main@sha1:1cbaa3b8acad40926d28b774b30b661f97c1cc27`.

### 2026-08-06 power outage and `vega` reboot

- After another power outage, `enterprise` returned healthy on the replacement Patriot SSD. SSH, the direct API, kube-vip, etcd readiness, and corruption checks passed; `enterprise` remained intentionally cordoned.
- `vega` was initially `NotReady`, then returned after a user-initiated reboot at 15:22 PDT. Its actual systemd unit is `k3s.service`, which was active and running `/usr/local/bin/k3s agent`; the earlier inactive `k3s-agent.service` observation was a unit-name mismatch rather than an agent failure.
- Monitored `vega` every approximately 16 seconds for ten minutes after it returned. All 30 samples passed ICMP, SSH, active-k3s, and Kubernetes `Ready` checks with zero missed probes or state changes.
- `vega`'s current boot showed no ATA resets/timeouts, kernel I/O or ext4 errors, OOM kills, hung tasks, NIC resets, watchdog faults, or thermal-critical/throttling messages. Ethernet counters showed zero receive/transmit errors, drops, or carrier faults.
- SMART for `vega`'s 1 TB OEM Genuine SATA disk passed, with zero reallocated sectors, zero pending sectors, and zero offline-uncorrectable sectors. The disk has 56,671 power-on hours and one lifetime UDMA CRC error, so it remains worth observing but has no present evidence of media failure.
- The CPU package briefly reached 79 C while approximately 56 workloads restarted, then settled near 51-55 C. The root disk was 42 C. Kubernetes reported no memory, disk, or PID pressure.
- The previous `vega` boot ran from 10:41 to 13:40 PDT and its journal ended without a clean shutdown sequence. No kernel, storage, network, OOM, watchdog, or thermal fault preceded the end; this is consistent with an external power interruption or hard reset, not evidence that the host crashed on its own.
- At the end of the watch, `vega` hosted 56 Running pods. The remaining `k8s-gateway` `ImagePullBackOff` and two terminating Jellyfin pods were pre-existing workload cleanup/image-registry issues rather than host-health failures.

## Replacement Plan

### SSD 1: `enterprise`

This replacement is required.

1. Preserve the verified full-disk image and critical K3s archive; do not repair or modify the old SSD or master image.
2. Write the verified image to the new SSD and retain the old SSD as an untouched fallback.
3. Install the new SSD in `enterprise`, preferably with a replacement SATA cable or a different known-good SATA port/power connector.
4. Boot using the existing `enterprise` hostname and `10.20.30.111` address, then validate the single-node etcd datastore before making upgrades or configuration changes.
5. If the new SSD is larger, expand partition 2 and its ext4 filesystem only after the cloned system has booted and etcd has been validated.
6. Confirm kube-vip `10.20.30.250` remains reachable and `/readyz` stays healthy under sustained write load.
7. Run an offline SMART extended test on the removed disk for additional evidence; do not return it to control-plane service.

### SSD 2: `nova` or `ziti`

Current preference: `nova`, not yet final.

Before choosing:

- Check whether `ziti` detects its current disk in UEFI/BIOS.
- Try its local Debian/UEFI entry and verify boot order before declaring the disk failed.
- Inspect/reseat the local disk if firmware does not detect it.
- Decide whether recovering the existing `ziti` hardware or rebuilding `nova` provides the more reliable second worker.
- Update Ansible inventory only after the target hostname, address, and role are decided.

## Recovery Validation

After replacing `enterprise` storage:

- All intended nodes remain `Ready` for at least 30 minutes.
- The direct API and kube-vip remain continuously healthy.
- etcd logs contain no slow `fdatasync`, request timeout, or long apply warnings.
- Cilium is ready on every active node without restart loops.
- NFS CSI drivers register and persistent workloads mount successfully.
- Frigate, Z-Wave, and GPU workloads schedule on `vega` with the expected hardware labels.
- Ingress address `10.20.30.200` and internal application URLs respond.
- No stale terminating pods remain.
- Rotate the long-lived `home` kubeconfig service-account token that appeared in diagnostic output during the incident.

## Remaining Risks

- The cluster has a single control-plane/etcd node and therefore no control-plane fault tolerance.
- Repeated hard resets may have damaged EFI filesystems or persistent journals on more than one host, even where no disk failure is currently evident.
- k3s `v1.27.3+k3s1` is old and should be upgraded in a separate, controlled maintenance window after storage recovery.
- The second SSD destination remains undecided.
