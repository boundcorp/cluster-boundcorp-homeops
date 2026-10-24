# Security notes

Ad hoc notes on findings/decisions from the Oct 2026 security review that don't
belong in the SRE audit journals (operational health) but are worth keeping
somewhere durable.

## AdGuard Home is NOT running in this cluster — correction to the Oct 2026 report

The original security review flagged `networking/adguard`'s HelmRelease
(chart `adguard-home` 5.5.2, appVersion v0.107.7) as critically vulnerable
(CVE-2026-32136, CVE-2026-41448 — unauthenticated admin takeover). That
finding was **based on dead code**: `kubernetes/apps/networking/adguard/app/kustomization.yaml`
never actually listed `helm-release.yaml` or `config-pvc.yaml` as resources,
so nothing from that HelmRelease was ever applied to the cluster. Confirmed
live: no HelmRelease, Deployment, or PVC named `adguard` exists; only an
`ExternalName` Service pointing at `net.internal.boundcorp.net`, which
resolves to `10.20.30.1` — your router/gateway. **The real AdGuard Home
instance runs on your router, outside Kubernetes and outside this repo
entirely.** The dead manifest files were deleted during remediation
(2026-10-23); the ExternalName Service + Ingress (the actual proxy-through
config) were left as-is.

**Action needed, outside this repo:** check whatever AdGuard Home build is
actually running at `10.20.30.1` and update it directly (router UI / SSH /
whatever manages that device) — I have no visibility into its version or
update mechanism from here.

## Zigbee2MQTT was also not actually running — correction

Same class of issue: `home/kustomization.yaml` has `#- ./zigbee2mqtt`
commented out, so the Zigbee2MQTT HelmRelease (flagged for CVE-2026-71279)
was never deployed either. Confirmed live: no matching Deployment/Pod
anywhere in the cluster. Migrated the manifest off the dead `k8s-at-home-charts`
source to `bjw-s` app-template anyway (image bumped 1.31.2 → 2.14.2) since
it was a zero-risk edit with nothing running to break — but it's still
commented out in `home/kustomization.yaml`. Re-enable only after reading
Zigbee2MQTT's 1.x→2.x `configuration.yaml` migration notes and backing up
the `zigbee2mqtt-config-v2` PVC; that's a config-schema concern independent
of this repo.

## `homeops-admin` cluster-admin ServiceAccount

- **Found:** 2026-10-23, during the security review.
- **What it is:** `ServiceAccount/homeops-admin` and `ClusterRoleBinding/homeops-admin`
  in `kube-system`, bound to `cluster-admin`. Created 2026-07-29 via a direct
  `kubectl apply` (visible in `last-applied-configuration`) — **not** created
  through this repo/Flux, so it has no commit history and no GitOps record.
  Its token `Secret` (`homeops-admin-token`) is a long-lived, static bearer
  credential with full cluster-admin scope.
- **Purpose:** unknown as of this review — asked directly, answer was "not
  sure / forgot." Nothing in this repo, `.taskfiles/`, or Ansible references
  it, so if something depends on it, that dependency is outside this
  repository.
- **Risk:** a static, undocumented, full-admin credential outside version
  control is the kind of thing that outlives its reason for existing. If it's
  unused, it's pure risk with no upside; if it's load-bearing for something
  (a script, a cron job, a laptop's kubeconfig), deleting it blind would break
  that silently.
- **Plan:** don't delete outright. Mint a replacement token/identity for
  whatever still needs admin access, leave this Secret in place but flagged,
  and give it a few weeks of being otherwise unused before removing it. Since
  Prometheus/Alertmanager were down for most of this review (see the kube-prometheus-stack
  upgrade work), there's no metrics-based way to detect its use yet — watch
  for it manually, or check API server audit logs if audit logging is ever
  enabled.
- **Target removal date:** no earlier than 2026-11-20 (4 weeks out), and only
  after confirming nothing broke.

## No ingress-layer authentication

As of the Oct 2026 review, no Ingress in this repo carries a forward-auth /
Cloudflare Access / oauth2-proxy annotation. Every exposed app relies solely
on its own login screen. The following have **no native authentication at
all** and should get a Cloudflare Access policy (Zero Trust dashboard,
outside this repo) as a priority, since that's the one gate available without
deploying anything new into the cluster:

- `prometheus.home.boundcorp.net`
- `alert-manager.home.boundcorp.net`
- `hubble.home.boundcorp.net`
- `adguard.home.boundcorp.net` (admin UI)

(The Home Assistant code-server add-on, which ran with `--auth none`, was
removed outright during this review rather than gated — see the HelmRelease
history for `home/home-assistant`.)

The Cloudflare Tunnel's own ingress config (`networking/cloudflared/app/configs/config.yml`)
routes both `*.boundcorp.net` and `*.home.boundcorp.net` to the same internal
ingress controller — whether the `*.home.*` hostnames are actually reachable
from the public internet depends entirely on the Cloudflare Zero Trust
dashboard's configured Public Hostname list, which lives outside this repo
and wasn't verified end-to-end during this review.
