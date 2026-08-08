# 2026-08-07 ziti/nova Recovery and vega Stabilization

Status: Active

Last updated: 2026-08-07

Continues [2026-08-01 Power Outage and Node Storage Failures](./2026-08-01-power-outage-and-node-storage-failures.md).

## Summary

Follow-on work after the power-outage incident. vega was crashing under load and
was stabilized. The two spare SSDs from that incident were re-assigned after a
hands-on triage of the failed nodes' drives via USB sleds on the workstation:

- **ziti's NVMe is dead** (reads 0 B across reseat + full USB power-cycle) — this is
  why ziti falls through to PXE. Its OS drive is gone.
- **nova's NVMe is healthy** (reads perfectly, ~15 GB used) — its boot failure is
  almost certainly boot-order/GRUB, not dead hardware.

Revised drive plan: recover **ziti** with a fresh Debian **trixie** install on its
existing **PNY 500 GB SATA SSD** (currently a decommissioned Ceph OSD); keep nova's
NVMe as-is and likely just repair its boot. The 256 GB Patriot is being used as the
netinst installer medium.

## vega stabilization

- vega had been hard-crashing after ~10–30 min of uptime (abrupt journal cutoff, no
  clean shutdown, no auto-reboot) while it was the **only schedulable node**
  (`enterprise` cordoned, `ziti`/`nova` down) and additionally running the
  **Satisfactory** dedicated server (Unreal Engine, req 2 CPU / 4 Gi, limit 4 CPU / 8 Gi).
- Fix (GitOps): set `replicas: 0` on `games/satisfactory` (commit `8f03c53`). Verified
  `0/0`, no pods.
- Monitored 2 h / 61 samples: zero crashes, continuous uptime, temps 51–75 °C, load
  ~2.5, memory well within 32 GB. Root-cause correlation confirmed.

## enterprise backup cleanup

- The enterprise recovery is complete and stable on the Patriot 128 GB clone through
  two outages. Deleted the master ddrescue image
  (`enterprise-PH6-CE120-G-…-2026-08-03.img`, ~28 GB on disk) to reclaim space.
- **Kept** `enterprise-k3s-critical-2026-08-03.tar` (10 KB — `/etc/rancher/k3s` +
  k3s server token) as cheap insurance / source of the cluster join token.

## Drive triage (USB sleds on workstation)

Sleds: an SATA-to-USB sled and an RTL9210 USB-NVMe sled. The RTL9210 was proven good
(read nova's NVMe fine).

| Drive | Model | Interface | Finding |
| --- | --- | --- | --- |
| nova OS | KingFast 1 TB | NVMe (M.2) | **Healthy.** All partitions enumerate (root sdb5 ~22 G/64%, empty 875 G ex-NFS export sdb7). ~15 GB real data. |
| ziti OS | (unknown) | NVMe (M.2) | **Dead.** 0 B across reseat + USB power-cycle in a known-good sled → explains PXE fallthrough. |
| ziti data | PNY CS900 500 GB | SATA | Raw `ceph_bluestore` OSD (decommissioned). Repurposing as ziti's new OS disk. |
| spare | Patriot P210 256 GB | SATA | Was enterprise's fallback line; now the trixie netinst installer medium. |

nova's 875 GB partition is the old `nfs-nova-nvme` export (data already on Titan), so
it's dead weight — a future migration only needs to carry ~15 GB.

## Node OS: ziti recovery plan

1. **Base OS on ziti** (on-target): boot Debian **13 (trixie)** netinst USB (flashed to
   the Patriot 256 GB), install onto the **PNY 500 GB** (wipes the Ceph OSD). User
   `setup`, openssh-server, hostname `ziti`, address `10.20.30.113`. Fix ziti firmware
   boot order (stop PXE fallthrough).
2. **Prepare, scoped:** `ansible-playbook … cluster-prepare.yaml --limit ziti`.
3. **k3s join:** via the (fixed) Ansible flow, pinned to the **cluster's current
   version** — not the stock config version (see landmines).
4. **Inventory cleanup:** set ziti `rook_devices: []` (PNY is now OS, no Ceph).
5. **Verify:** ziti `Ready`; watch for stability like vega.

## Ansible landmines (must avoid)

- **Version skew.** `group_vars/kubernetes/main.yaml` pins `k3s_release_version:
  v1.29.1+k3s1`, but the running cluster is **v1.27.3+k3s1**. Running the stock install
  would try to upgrade nodes to 1.29.
- **Cluster-wide plays.** Both `cluster-prepare` and `cluster-installation` target
  `hosts: all` with `any_errors_fatal: true`; prepare has a **Reboot** handler. Unscoped
  runs would reboot/reconfigure the fragile `enterprise` + `vega`. Always `--limit` and
  supply the join token so worker adds don't need the control node in the play.

## 2026-08-07 ziti install + k3s join attempts

- Fresh Debian 13 trixie installed on ziti's PNY 500 GB. Came up on DHCP `10.20.30.10`
  (DNS registered `ziti` via the ISP resolver). Reached by IP; `ssh ziti` fails because
  `~/.ssh/config` maps `ziti -> .113` (the not-yet-set static).
- SSH gotchas this session: the node SSH key is a **GPG auth subkey on a hardware token**
  served by `gpg-agent` — it stopped serving mid-session (`agent refused operation`),
  killing SSH to all nodes. Workaround: `ssh-copy-id`'d the on-disk passphraseless
  `~/.ssh/id_ed25519` onto fresh nodes and use `-i` with `IdentitiesOnly=yes`.
- Fresh installs need two prep steps: `ssh-copy-id` the key, and grant `setup`
  passwordless sudo (as root: `/etc/sudoers.d/010-setup-nopasswd`) — the Ansible flow
  runs `become` without a password.
- **SOPS age key path in CLAUDE.md is stale.** Real path is `~/.secrets/infra/age-key.txt`
  (from `.envrc`), not `~/.config/sops/age/keys.txt`.
- Set ziti static `10.20.30.113/24` via `nmcli` (NetworkManager is active on trixie),
  rebooted, confirmed at `.113`. Ran `cluster-prepare.yaml --limit ziti` — succeeded
  (took the Trixie branch), swap off.
- **k3s join failure #1:** `cluster-installation.yaml --limit ziti` — with no control
  node in the play, the xanmanning.k3s role **self-promoted ziti to primary control
  node** and did `cluster-init`, creating a *separate* single-node cluster with a
  kube-vip static pod contending for the shared VIP `.250`. Real cluster was unharmed
  (enterprise kept the VIP). Fixed with `k3s-uninstall.sh`. Lesson: never run the install
  playbook `--limit <worker>` alone.
- **k3s join failure #2:** manual agent join
  (`curl … INSTALL_K3S_VERSION=v1.27.3+k3s1 K3S_URL=https://10.20.30.250:6443 K3S_TOKEN=… agent`)
  installed the agent, but ziti's **networking broke** — reverted from static `.113` to
  DHCP `.10`, then went fully unreachable. Suspected: **NetworkManager fighting Cilium's
  datapath**. The prepare playbook's Cilium fix (`ManageForeignRoutingPolicyRules=no`,
  `ManageForeignRoutes=no`) only touches `systemd-networkd.conf`, which is **inactive** on
  trixie (NM is active), so NM was never told to leave `cilium_*`/`lxc*` interfaces and
  foreign routes alone. Secondary suspect: old (v1.27-era) Cilium on trixie's ~6.12 kernel.
- **Decision:** switch ziti/nova from NetworkManager to **systemd-networkd** (matching
  vega/enterprise) before re-attempting the join. Test-join ziti first, then nova.
- ziti currently offline (needs console recovery: boot, then
  `systemctl disable --now k3s-agent && /usr/local/bin/k3s-agent-uninstall.sh` to stop
  the break-on-boot cycle). nova is up on DHCP `10.20.30.42` (trixie), key not yet copied.
- Stale `ziti` Node object + `ziti.node-password.k3s` secret were deleted from the real
  cluster to allow a clean rejoin. The ansible run also clobbered repo-root `kubeconfig`
  (kubeconfig fetch task); working kubectl uses `~/.kube/clusters/home` and is unaffected.

## 2026-08-07 resolution — both workers recovered

**Actual root cause of the node instability: ACPI sleep.** Both trixie netinst
runs pulled in the **full GNOME desktop**, which brought NetworkManager *and*
power management that suspended the boxes on idle — they kept dropping off the
network. The earlier "the k3s join breaks Cilium networking" conclusion was wrong;
ziti runs Cilium fine on kernel 6.12 (even on NetworkManager). The join always
worked — the box was just suspending. Masking sleep fixed the flapping.

Final state — cluster back to **3 healthy workers** (vega, ziti, nova) + cordoned
control plane. ziti and nova are now identical: headless (multi-user, desktop
purged), sleep/suspend masked, **systemd-networkd** static (`.113`/`.114`),
NetworkManager off, k3s agent `v1.27.3+k3s1` (manual pinned join via the VIP and
the kept cluster token), Ready.

New/changed Ansible tooling:
- `cluster-node-harden.yaml` + `task ansible:harden` — purge desktop, mask sleep,
  multi-user. **Protects sudo/ssh/python/network-manager via `apt-mark manual`**
  before autoremove (an early version orphan-removed `sudo`, breaking escalation —
  fixed). No reboot handler, safe on live nodes.
- `cluster-prepare.yaml` — trixie-gated **systemd-networkd** block (static from
  `node_static_ipv4`/`ansible_host`, disables NM). Bookworm nodes untouched.
- `inventory/hosts.yaml` — **nova** added (`.114`, `rook_devices: []`).

Gotchas worth keeping:
- k3s join for a single worker: use the **manual agent install** pinned to the
  cluster version, never `cluster-installation.yaml --limit <worker>` (it
  self-promotes to a control node and `cluster-init`s a rogue cluster).
- Node hostname reuse: delete the stale `Node` + `<node>.node-password.k3s` secret
  before a fresh box rejoins under the same name.
- `curl … | sh -s - agent`: do **not** add `</dev/null` (it starves `sh` of the
  piped script); redirect the installer's *output* to a remote file instead so the
  SSH call returns.

## Follow-ups

- **nova:** run SMART + read-only fsck on the KingFast; if healthy (expected), repair
  boot (GRUB/boot-order) rather than replace. The incoming 128 GB NVMe likely becomes a
  spare.
- **k3s upgrade to latest:** deliberate, controlled, cluster-wide — **after** ziti/nova
  are back and the cluster has worker redundancy again. Bump `k3s_release_version` then.
- Longer term: the cluster still has a single control-plane/etcd node (no HA).
