# Vega NotReady and node ephemeral-storage incident

Status: Open

Created: 2026-08-30
Source: weekly SRE review (`docs/sre-audit/journals/2026-08-30.md`)

## Problem

`vega` is currently unreachable and `NotReady`. At the review snapshot, `Ready=Unknown` had persisted for approximately 15 hours 38 minutes. The kubelet proxy returned HTTP 503 and an approved SSH attempt timed out. Active unreachable taints include `NoSchedule` and `NoExecute`; the node is also cordoned.

This leaves Cilium, CSI NFS, and NetBird at 3/4 available and removes one worker from the effective scheduling pool. The stale node-local DaemonSet pods cannot be treated as healthy while kubelet communication is absent.

## Related node-storage incident

A separate but correlated incident affected `nova` and `ziti` on 2026-08-30:

- `nova`: 31 retained pod evictions; approximately 2.76 GiB free at collection; 582 `FreeDiskSpaceFailed` events since 2026-08-24.
- `ziti`: 40 retained pod evictions; approximately 2.56 GiB free at collection.
- Evicted workloads included Immich, PgBouncer, PGO, Frigate, ESPHome, Home Assistant, Speakr, cert-manager, Flux notification, Prometheus, Hubble UI, and other singleton services.
- Prometheus remains unavailable after eviction.

The immediate eviction mechanism is node ephemeral-storage pressure. The underlying disk consumer and the relationship to the vega failure are not established.

## Required investigation

1. Restore management reachability to vega using console or separately authorized node operations.
2. Preserve evidence before reboot/cleanup where possible: k3s/kubelet/containerd logs, kernel/network logs, filesystem usage, deleted-open-file state, disk health, and power/network state.
3. Determine whether the vega failure is host power, network, k3s/kubelet, filesystem, or hardware related.
4. Verify the cordon/taints against intended scheduling policy; do not clear them automatically.
5. Verify vega Cilium, CSI NFS, NetBird, Intel GPU, and node-feature-discovery health after recovery.
6. Separately identify and remediate durable ephemeral-storage consumption on nova and ziti. Record before/after capacity and image-GC behavior.
7. Confirm no new workload evictions, restore CSI/Cilium/NetBird to 4/4, and verify Prometheus recovery.

## Constraints

- This review applied no changes; `$AUTO_FIXES` is `None`.
- Any reboot, cleanup, taint/cordon change, package operation, workload restart, or GitOps change requires explicit operational authorization.
- Titan and node credentials/identity material must not be recorded here.

## Evidence references

- `docs/sre-audit/journals/2026-08-30.md`
- `docs/sre-audit/README.md`, especially `$TARGET`, `$EXTRA_INSTRUCTIONS`, and `$AUTO_FIXES`
- Kubernetes node, pod DisruptionTarget, DaemonSet, and `FreeDiskSpaceFailed` evidence collected during the 2026-08-30 review
