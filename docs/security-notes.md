# Security notes

Ad hoc notes on findings/decisions from the Oct 2026 security review that don't
belong in the SRE audit journals (operational health) but are worth keeping
somewhere durable.

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
