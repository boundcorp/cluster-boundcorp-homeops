# Post-hop 9 cleanup — 2026-11-25

Lee approved both cleanups at 23:36 UTC. The home context was accessed using the documented explicit kubeconfig.

- `kubernetes/bootstrap/kustomization.yaml` already specified Flux `v2.9.6` in main at `1b1e741` (the same reference has been in Git since `87a6f54`). No configuration edit or bootstrap apply was needed. The Flux OCI source also specifies `v2.9.6`. An offline kustomize render of the upstream v2.9.6 install directory, checked out at `b9c17924adb533d3617ec7de97a7b0f4acc07b39`, produced 43 objects; all seven controller images matched the running deployments. Since the bootstrap file was unchanged, there was no resource delta from a config edit.
- Deleted only `networking/ingress-nginx-admission` on home. Before deletion, ingress-nginx's controller, workloads, Service, HelmRelease, IngressClass and admission webhook were absent; live webhook and workload specifications contained no reference to the Secret. The Secret was inspected by metadata only. It was absent immediately after deletion and on the subsequent check at 23:40:12 UTC. Traefik remains Ready and its IngressClass is default.
- Post-delete health at 23:39:58 UTC: four nodes Ready at `v1.36.6+k3s1`, only enterprise cordoned; 45/45 Flux Kustomizations and 34/34 HelmReleases Ready; 52/52 Prometheus targets up; 29/29 PVCs Bound; same primary `postgres-postgres-6bb6-0` writable (`false/off`) and PgBouncer Ready. All seven Flux controller images match v2.9.6. All Helm revisions match the hop 9 final baseline, with no delta. Hatchet and filebrowser orphan objects were not touched.

No bootstrap reinstallation, reconciliation, restart, credential change, or other deletion was performed. Secret values were not inspected or exported.
