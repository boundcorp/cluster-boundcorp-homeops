# ziti: containerd root moved from `/var` to `/srv` (2026-11-24)

Status: **COMPLETE, no rollback.** ziti's containerd root now lives on the 445 GB `/srv` partition (`/dev/sda5`), bind-mounted back at `/var/lib/rancher/k3s/agent/containerd`, with a fail-closed k3s-agent drop-in. Kubelet now reports imagefs **445,098,295,296 B capacity / 414.4 GB available** instead of the shared 12.46 GB `/var`. ziti was NotReady for **3 min 24 s**. The Postgres primary was untouched (UID unchanged, SQL `false/off` throughout). The old tree is retained as `containerd.pre-srv-20261124` on `/var`; its cleanup (plan §10) is **not** approved. Hop 3 is not authorized.

Authorization: Lee, 2026-11-24 00:21 UTC, "Merge #6 and run the migration now; rigel stays up", covering plan §11 items 1–4. Procedure: draft PR #5, `docs/ziti-srv-migration-plan.md` at `b75c1af`, §6, §7, §9 and §11. Ansible ownership: PR #6, merged as `e68195a`. Starting main: `84f55fa`.

## Timeline (UTC)

| UTC | Step |
|---|---|
| 00:22 | `~/ENVIRONMENT.md` read; `envcheck` run (home/octo readyz ready; its enterprise SSH probe reported "unavailable or not trusted", but the documented route `ssh enterprise` with strict host-key checking and `sudo -n` worked, as did `ssh ziti`) |
| 00:26–00:31 | Preflight §7.1 (below) |
| 00:31:23 | PR #6 marked ready and squash-merged → `e68195a` |
| 00:31:43 | Pre-change etcd snapshot saved on enterprise; offload Job `pre-ziti-srv-20261124t0031z` 00:31:49–00:31:55 |
| 00:32:16 | `kubectl cordon ziti` |
| 00:32:17–00:42:55 | §6.2 pods deleted one at a time, no `--force` (table below) |
| 00:43:21 | `systemctl stop k3s-agent` (rc 0) |
| 00:43:21–00:43:24 | `k3s-killall.sh` (rc 0) |
| 00:43:32–00:43:43 | Gate Q passed. No containerd survived, so no `kill -TERM` was needed |
| 00:43:57 | Node event NodeNotReady |
| 00:43:51–00:44:55 | `cp -a --preserve=xattr -x --sparse=auto` (rc 0, 64 s) |
| 00:45:18–00:46:17 | Manifest check on source and target (match) |
| 00:46:43–00:46:44 | Rename to `containerd.pre-srv-20261124`, empty 0700 mountpoint, units installed, `daemon-reload`, `enable --now` mount |
| 00:46:54–00:47:02 | §7.5 mountpoint verification passed (including P12's exact-source test) |
| 00:47:12–00:47:21 | `systemctl start k3s-agent`; kubelet started; **NodeReady 00:47:21** |
| 00:47:21–00:55:27 | §7.6 validation steps 1–7 (startup GC warning handled as a documented exception, below) |
| 00:56:25 | `kubectl uncordon ziti` (enterprise and vega remain cordoned) |
| 00:56:35–01:06:44 | ≥10-minute stable watch: 28 samples, 0 anomalies (final health 01:06:44) |
| 01:07:28 | Post-change etcd snapshot saved; offload Job `post-ziti-srv-20261124t0107z` 01:07:32–01:07:37 |

Outage figures: kubelet down **00:43:21–00:47:21 (4 min 0 s)**; node NotReady **00:43:57–00:47:21 (3 min 24 s)**; ziti cordoned **00:32:16–00:56:25 (24 min 9 s)**. These are well inside the plan's estimate of 10–20 min NotReady.

## Preflight (§7.1), 00:26–00:31 UTC

| Gate | Result |
|---|---|
| P1 | `ssh ziti sudo -n true` passed with strict host-key checking, against the Lee-verified ED25519 key `SHA256:j1WeX8uwp/JuQWaBKvUO9M441G6peJUpznh2VQLQrds` |
| P2 | Devbox NetBird sidecar: `rigel.netbird.cloud` Connected P2P, Networks `10.20.30.0/24`; no other peer advertises networks. Rechecked about every minute by the watcher; it was true in every sample |
| P3 | PR #6 merged. **The nova `--check --diff` part is DEFERRED, not passed**: nova's SSH host key is unverified, so no nova SSH or Ansible ran |
| P4 | 4/4 nodes Ready (enterprise/ziti/nova v1.29.15+k3s1, vega v1.27.3+k3s1); enterprise and vega cordoned; 44/44 ks, 34/34 hr; 52/52 targets; no unhealthy active pods (3 terminal Failed records from hop 2 retained); 9/9 DaemonSets full; 29/29 PVCs Bound; readyz ok, VIP and direct livez 200, etcd `/health` true; 0 SUC Jobs, both Plans Complete at v1.29.15+k3s1 |
| P5 | ziti had no pressure. Baseline `stats/summary`: nodefs, imagefs and containerFs all capacity 12,459,843,584; nodefs available 2,770,714,624; imagefs used 5,729,607,680 |
| P6 | (a) Snapshot below, sha256 equal on enterprise and titan. (b) pgBackRest stanza `db` status ok; newest backup incremental `20261122-100008F_20261123-100008I`, stopped 2026-11-23 10:01:07Z (about 14.4 h old, under 24 h); full 2026-11-22 10:18:47Z. (c) No credentials or PVC contents went into evidence |
| P7 | Primary `postgres-postgres-6bb6-0` 5/5 on nova, UID `e5480b37-7547-4e63-8566-ca709218f0a4`; SQL `false/off`; Patroni lists it as Leader, running, TL 330 |
| P8 | nova allocatable 8 CPU / 16,268,448 Ki, requests 2,930m / 6,231,076 Ki; moved pods request 250m / 318 Mi in total. No pressure |
| P9 | `/srv` = `/dev/sda5` ext4 rw; contains only `lost+found`; 422,411,227,136 B available; `/srv/k3s` absent |
| P10 | python3 3.13.5, cp (GNU coreutils 9.7), tar, findmnt, mountpoint, lsof and systemd-escape all present in `/usr/bin`. No packages installed |
| P11 | `/usr/local/bin/k3s-killall.sh` (2026-08-07, sha256 `6990bdde…9068bb`) still unmounts only `/run/k3s`, `/var/lib/kubelet/pods`, `/var/lib/kubelet/plugins` and `/run/netns/cni-`; nothing under `/var/lib/rancher/k3s` |
| P12 | Verified after the switch (below) |
| P13 | Bounded read-only watcher (3 h maximum, about 22 s per sample): node Ready, cordon and pressure; API readyz and VIP; primary UID and SQL; DNS (`getent` in the primary pod); ingress hass, esphome, photos, sync, grafana `/login` and jellyfin `/health`; strict SSH to ziti; rigel route. Warning events were checked separately |
| P14 | Lee's "run the migration now" go-ahead set the window; Lee was reachable through the coordinator. No separate broadcast was sent, because the dispatch allows only the coordinator channel |

Before the switch ziti ran 1 k3s-agent, 1 containerd and 22 shims; there were no drop-ins and no mount unit.

## etcd snapshots

| Snapshot | Size (bytes) | SHA-256 (enterprise = titan) |
|---|---|---|
| `pre-ziti-srv-20261124T0031Z-enterprise-1795480303` | 37,797,920 | `1ad720add63437e19880df29798247d6eb708a8968a5d84abb8dbcb07cae297f` |
| `post-ziti-srv-20261124T0107Z-enterprise-1795482448` | 37,797,920 | `69a97c9fcca02b2a5c5f1fd8e951c0e09af1085d6a350425a83b7cec0daa4050` |

Method as in hops 1 and 2: `ssh enterprise sudo -n k3s etcd-snapshot save --name …`, then `kubectl create job --from=cronjob/etcd-snapshot-offload …`. The Job re-reads the titan copy and compares hashes; the enterprise hash was read independently with `sudo sha256sum`. The source is mode 0600 root:root and the titan target is root:root 0700. No snapshot contents or token values were read or printed.

## Workload moves (§6.2, one at a time, no `--force`)

| Pod (old → new) | Delete | Replacement Ready on nova | Notes |
|---|---|---|---|
| PgBouncer `ph2f6` → `xg6ch` | 00:32:17 | 00:32:26 | SQL through PgBouncer (birdweather role, TLS): `false/off` |
| mosquitto `vn9fs` → `hbgkr` | 00:33:04 | 00:33:10 | HA and Frigate logged a disconnect at 00:33:05; 4 established MQTT connections on the new pod at 00:33:43 |
| CoreDNS `hrqgb` → `4t4rf` | 00:33:47 | Pending (topology spread; only nova was schedulable) | Accepted per §6.2. The nova replica served; DNS resolved internal and external names. After the uncordon the replica scheduled onto ziti |
| ingress-nginx `gqnrn` → `btd99` | 00:34:24 | 00:34:45 | The old pod drained gracefully until about 00:38:37. Ingress HTTP stayed 200 throughout |
| pgo `sbtlq` → `ls69l` | 00:41:35 | 00:41:56 | |
| cilium-operator `8phtb` → `9rtgd` | 00:42:04 | 00:42:11 | |
| csi-nfs-controller `kkv2p` → `7sndp` | 00:42:21 | 00:42:51 | |

sonarr, radarr, sabnzbd, filebrowser, `loki-chunks-cache-0` and all DaemonSet pods stayed on ziti and were down for the window (Lee did not opt in to moving the *arr apps). The primary was never touched.

## Host steps on ziti (`ssh ziti`, strict host-key checking, `sudo -n`)

Gate Q, 00:43:32–00:43:43: k3s-agent inactive; 0 containerd, shim or k3s processes (checked by exact process name); 0 mounts under `/run/k3s/`; 0 `rancher/k3s/agent/containerd` mount references; 0 `/var/lib/kubelet/pods/` and 0 plugin mounts; 0 NFS mounts; `lsof +D` on the containerd root returned 0 lines.

Copy: `/srv/k3s`, `/srv/k3s/agent` and `/srv/k3s/agent/containerd` were created 0700 root:root, and the target was verified empty. `cp -a --preserve=xattr -x --sparse=auto /var/lib/rancher/k3s/agent/containerd/. /srv/k3s/agent/containerd/` exited 0 after 64 s. `du -sxB1`: source 7,892,434,944, target 7,892,094,976; the 340 KB difference is directory-block allocation, and the manifest is the gate.

Manifest: the plan's §7.4 script, installed byte-for-byte (sha256 `491b71e3…aae8bb`) as `/root/ctrd-manifest.py`.

| Tree | Entries | Digest |
|---|---|---|
| `/var/lib/rancher/k3s/agent/containerd` | 84,559 | `fe8e2aafda3516c1348bc8745144ee3ffece256d39d58be5f1629be6dea96e26` |
| `/srv/k3s/agent/containerd` | 84,559 | `fe8e2aafda3516c1348bc8745144ee3ffece256d39d58be5f1629be6dea96e26` |

The count differs from the plan's "about 74,161" for a reason that is now explained. `find -xdev` gives 73,721 entries, of which 10,839 are directories. The script counts every directory twice, once in its parent's listing and once as the walk root, except the top directory: 73,721 + 10,838 = 84,559. Files with more than one hard link number 4,876, exactly as the plan says. Top-directory mtimes matched, so no rerun excluding the top entry was needed.

Switch-over: the source was renamed to `containerd.pre-srv-20261124` (same filesystem; `findmnt -T` shows `/dev/sda3 /var`), and a new empty 0700 root:root mountpoint was created. The two systemd files were rendered from merged PR #6 (`e68195a`) with Ansible's template settings, locally and with no host run. They are byte-identical to the prep renders, and the same render of nova's files matches nova's live bytes. They were installed 0644 root:root:

| File | SHA-256 |
|---|---|
| `/etc/systemd/system/var-lib-rancher-k3s-agent-containerd.mount` | `408ad97612b2490f1217527f7d5b3848af0e74f181c354e18d97aaaa07816ec1` |
| `/etc/systemd/system/k3s-agent.service.d/runtime-storage.conf` (directory 0755) | `ab8d75a8cff41fc79b9f3909f41e652d90d6e5e0e2f31d63f2991b9aab67ed5f` |

§7.5 verification:
- `mountpoint`: yes.
- `findmnt`: `/dev/sda5[/k3s/agent/containerd]` ext4 rw,relatime.
- `stat -f` blocks 108,666,576 (equal to `/srv`; `/var` has 3,041,954).
- `meta.db` present.
- k3s-agent Requires/After reference the mount (2 hits).
- The unit is enabled through `local-fs.target.wants`.
- P12: the drop-in's exact-source test, run by hand, returned PASS. The parsed `ExecStartPre` argv matches the drop-in.

## Validation (§7.6)

1. **Runtime.** containerd running; `crictl info` ok; `crictl imagefsinfo` mountpoint is `/var/lib/rancher/k3s/agent/containerd/io.containerd.snapshotter.v1.overlayfs`. All 29 previous images are present. The only pull was NetBird `:latest`: a new upstream digest, about 5.6 s, anticipated by the plan. Its previous digest stays cached, untagged, so the image list now has 30 entries. Every other container start logged "already present on machine".
2. **Kubelet stats.** At 00:48:02 and 00:55:24, nodefs capacity was 12,459,843,584 (still `/var`) with available 2,796,871,680 → 2,796,339,200. imageFs and containerFs capacity was **445,098,295,296** with available **414,433,972,224**. This is the expected split, and nodefs stays above 2 GB.
3. **Node.** ziti was Ready at v1.29.15+k3s1 with containerd 1.7.26-k3s1. Memory, disk and PID pressure were all False, and the only taint was the cordon until 00:56:25. Every DaemonSet pod on ziti came back Ready: cilium, cilium-envoy, csi-nfs-node, intel-gpu-plugin, nfd-worker, node-exporter, loki-canary, vector-agent and netbird.
4. **Networking.** `cilium status`: OK, 63/63 controllers healthy, cluster health 4/4 reachable. DNS resolved `kubernetes.default.svc.cluster.local` → 10.43.0.1 and `github.com` from sonarr on ziti and from the primary pod on nova.
5. **NFS.** csi-nfs-node on ziti is Ready. Brief `FailedMount` events at 00:47:24–26 happened before the CSI node plugin re-registered. sonarr, radarr, sabnzbd and filebrowser then mounted their NFS PVCs on ziti and are Running.
6. **Apps.** 44/44 ks and 34/34 hr, with **every HR revision unchanged**. 51/51 targets: the 52→51 drop was the `coredns` job, which had 1 target while the second replica was Pending. After the uncordon the CoreDNS replica scheduled onto ziti (Ready by 00:58, image cached), so CoreDNS is 2/2 and targets are back to **52/52**. Ingress returned 200 for hass, esphome, photos and sync, grafana `/login` and jellyfin `/health` in every watcher sample. MQTT clients reconnected.
7. **Postgres.** Primary UID `e5480b37-7547-4e63-8566-ca709218f0a4` is unchanged and still on nova, 5/5. SQL returned `false/off` in every sample (about 22 s apart) across the window. PgBouncer is Ready on nova. Birdweather polls succeeded at 00:45:59 and 00:51:02.
8. **Uncordon** at 00:56:25. Moved pods stay on nova, and nothing was rebalanced.
9. **Watch.** The watch ran from 00:56:35 to 01:06:44: 28 samples, about 22 s apart, with **zero anomalies**.
   - SQL `false/off` every time, with the same primary UID.
   - DNS, VIP and readyz OK; all six ingress checks 200.
   - ziti Ready and schedulable, with no pressure; enterprise and vega cordoned throughout.
   - Strict SSH to ziti OK; rigel route true in all 9 route samples.
   - Warning events since the uncordon: none. The only ziti event was the CoreDNS start from the cached image.
   - `k3s-agent` journal since initialization: 0 GC-failure, invalid-capacity or eviction lines.
   - Restart deltas since the post-start check: none.

   Final health at 01:06:44:
   - 4/4 nodes Ready; 52/52 targets; 9/9 DaemonSets full; 29/29 PVCs Bound.
   - No unhealthy active pods.
   - readyz, VIP and direct livez OK; etcd healthy.
   - SUC: 0 Jobs, both Plans Complete.
   - 34/34 hr with revisions unchanged.

   ks read 43/44 in that one sample: `cluster-apps-nfs-provisioner` was caught in its normal 30-minute reconcile. It became Ready at 01:06:46 ("Health check passed in 43.8 ms"), giving 44/44. This is the same kind of in-progress sample seen in hop 2, with no intervention. Stats at 01:07:07: nodefs available 2,795,438,080 B of 12,459,843,584 B; imagefs available 414,433,853,440 B of 445,098,295,296 B.
10. **Post snapshot.** See the table above.

Restart deltas are only the ziti-resident pods, each restarted once by the node outage: the DaemonSet pods, sonarr, radarr, sabnzbd, filebrowser and loki-chunks-cache. Nothing restarted on any other node.

### Documented exception: the startup-only image-GC warning

§7.6 step 1 says the kubelet journal must contain no `invalid capacity 0 on image filesystem`. At kubelet start, 00:47:21, kubelet logged exactly one line: `"Image garbage collection failed once. Stats initialization may not have completed yet" err="invalid capacity 0 on image filesystem"`, together with a matching `InvalidDiskCapacity` node event. ziti was held cordoned, and the question went to the coordinator with this evidence:
- The identical line appeared at **both** pre-migration kubelet starts on ziti, on 2026-11-23 at 17:29:43Z and 20:13:11Z, so it is a startup transient that predates this change.
- The next GC cycle, at 00:52:21, was clean: there was no `failed multiple times in a row` line and no `ImageGCFailed` event. Both earlier starts did log that line 5 minutes later, because `/var` was genuinely short.
- Stats showed the correct 445 GB imagefs.

The coordinator ruled it a **documented PASS exception**, not a rollback trigger. The stop conditions remained in force for any recurrence after stats initialized, pressure or evictions, wrong accounting, or DB, ingress or DNS failures. None occurred.

**Runbook amendment, recommended for PR #5 and future runbooks.** §7.6 step 1 should distinguish the single kubelet-startup `failed once… Stats initialization may not have completed yet` line from a persistent failure. Pass if imagefs capacity in `stats/summary` is the new filesystem and the next 5-minute GC cycle logs no `failed multiple times` or `ImageGCFailed`. Fail on any `invalid capacity 0` after stats initialize, or on any repeated GC failure.

## Deviations from the plan

- The §7.6.1 startup GC warning was treated as a documented exception, as described above, by coordinator decision.
- P3's nova `--check --diff` is deferred, because nova's host key is unverified.
- The plan expected about 74,161 manifest entries; the script reported 84,559 for the reason explained above. Source and target were identical.
- The ingress pod's graceful termination took about 4 minutes. The replacement was Ready after 20 s, so ingress was not affected.
- The CoreDNS second replica stayed Pending until the uncordon, which the plan expected.
- P14: no separate broadcast was sent (coordinator channel only).
- The plan writes the switch-over step as `install … <from Git>`. The files were streamed over SSH, installed with `install -m 0644 -o root -g root`, and verified by sha256 against the local render.
- `envcheck` falsely reported enterprise SSH as unavailable, while the documented route worked.

Nothing was installed, nothing was copied outside `agent/containerd`, and no credential file or `/etc/rancher` was read or changed. Nothing ran on nova, enterprise (apart from the approved snapshot) or vega. No Ansible host run, no GC or eviction tuning, no Flux change, no deletion of old data, no rebalancing and no hop 3.

## Left in place (intentionally)

- `/var/lib/rancher/k3s/agent/containerd.pre-srv-20261124` on `/var`: about 7.4 GiB, the R1 rollback source. Deleting it is plan §10 and needs **separate approval after ≥24 h stable**; it would give `/var` about 10 GB free. Until then, nodefs available is about 2.80 GB.
- `/root/ctrd-manifest.py`, removed in the §10 cleanup.
- `/tmp/killall-20261124.log` on ziti (k3s-killall trace; mount paths only).
- Terminal Completed and ContainerStatusUnknown pod records from hop 2.
- Moved pods on nova (pgbouncer, mosquitto, ingress replica, pgo, cilium-operator, csi-nfs-controller).

## What remains

1. Approval for the §10 cleanup after ≥24 h stable (earliest about 2026-11-25 00:56 UTC). Recheck `findmnt` before any `rm`.
2. Verifying nova's SSH host key, then running PR #6's `--check --diff --limit nova` (expect zero changes) and `--check --diff --limit ziti` (expect zero changes now that the mounts exist).
3. Amending §7.6 step 1 as described above (PR #5 stays a draft).
4. Hop 3 is still not authorized. The ziti imagefs headroom gate (≥5 GB available) is now met for imagefs: 414 GB.

Evidence (metadata only, outside Git): `/home/dev/orca/artifacts/ziti-srv-migration-20261124/`.

## Approved rollback-copy cleanup — 2026-11-25

**COMPLETE.** Lee's Nov 24 02:15 UTC approval, “Also remove the old ziti copy after 24h”, was dispatched for exactly `/var/lib/rancher/k3s/agent/containerd.pre-srv-20261124` on ziti. This supersedes the pending-approval statements above for that directory only. The migration runtime started Nov 24 00:47 UTC; cleanup ran Nov 25 **00:53:44–00:53:45 UTC**, over 24 hours later. The planned hop 3 and hop 4 restarts were expected interruptions, reviewed against their completed journals rather than treated as storage instability. No further upgrade or cleanup was authorized.

Read environment map, migration journal `aca24ca`, hop 4 journal `e1f9c4d`, hop 3 recovery evidence, and draft PR #5 §10. Fetched origin/main before work and before the journal commit; baseline `e1f9c4d`. Used explicit home kubeconfig/context and strict SSH to ziti with the existing trusted key; no known_hosts change. `envcheck` needed its documented `/home/dev/bin/envcheck` path and explicit cluster configs; then home and octo readyz both passed. No service was stopped or restarted.

Pre-cleanup checks:

- Four nodes Ready at v1.31.14+k3s1 / containerd 2.1.5-k3s1.32, only enterprise cordoned, all pressure False. API readyz ok, VIP/direct livez 200, etcd healthy. Both upgrade Plans Complete, zero upgrade Jobs.
- 44/44 Kustomizations, 34/34 HelmReleases, 52/52 targets, 9/9 DaemonSets full, 29/29 PVCs Bound, no unhealthy active pods; four historical Failed records retained. Same Postgres primary UID `e5480b37-7547-4e63-8566-ca709218f0a4`, 5/5 on nova; SQL `false/off`, PgBouncer Ready.
- Past-24h Prometheus storage minima: `/var` available **2,310,950,912 B**, `/srv` available **411,365,986,304 B**; ziti DiskPressure true maximum **0**, 2,879 filesystem samples. K3s journal showed only the documented single startup capacity warning at each approved hop start (16:58:32Z and 20:17:31Z), no repeated GC failure or storage-pressure eviction; kernel review found no matching disk/filesystem errors.
- Live original containerd path was an exact mount with SOURCE **`/dev/sda5[/k3s/agent/containerd]`**. k3s-agent active, CRI RuntimeReady/NetworkReady true, fail-closed mount dependency present.
- Old path was an actual directory (device **2051**, inode **141721**) on **`/dev/sda3`**, mounted at `/var`; all path ancestors checked against symlinks. No mount at/below old path, including process mount namespaces; no runtime/systemd/fstab reference, process command/maps/fd/cwd/root/exe reference, or `lsof +D` result. Reference contents and secrets were not printed.

Immediately before removal, the guarded script repeated directory identity, ancestor, filesystem, exact live-bind and all-process mount checks. It then ran exactly one removal: `rm -rf --one-file-system -- /var/lib/rancher/k3s/agent/containerd.pre-srv-20261124` (no glob or symlink traversal).

| Measurement | Bytes |
|---|---:|
| Old tree allocated (`du -sxB1`) | 7,892,434,944 |
| `/var` available immediately before | 2,349,060,096 |
| `/var` available immediately after | 10,241,437,696 |
| Observed available-space increase | **7,892,377,600** |

The 57,344-byte difference between tree allocation and observed gain includes concurrent filesystem activity/accounting. Verified old path absent, original live bind unchanged, k3s-agent active and CRI ready; `/srv` available remained **412,285,452,288 B**. Fresh post-cleanup health passed every preflight cluster/SQL/Flux/target/PVC/DaemonSet gate above; **zero workload restart deltas**, Helm revisions unchanged, primary UID unchanged. Ordinary `/var` writes continued after the measurement.

Only the approved old tree was removed. `/root/ctrd-manifest.py`, the old temporary trace, live `/srv`, PVCs, credentials and all other host files were left in place. Draft PR #5 remains a historical draft; no plan change was required to execute the narrower dispatched scope. Rollback-copy retention is now ended: rollback would require image re-pulls as described in §10, with the original `/var` capacity risk. No hop 5 or other maintenance was performed.

Sanitized evidence and exact guarded removal script: `/home/dev/orca/artifacts/ziti-old-copy-cleanup-20261125/` (`pre-health.json`, `post-health.json`, `storage-prometheus.json`, `storage-journal.txt`, `deletion.jsonl`, `cleanup.py`, `envcheck.txt`). Task `task_86113024e483`, dispatch `ctx_ef31c123f0e8`.
