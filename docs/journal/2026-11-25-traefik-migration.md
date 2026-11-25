# Traefik migration (phase 1 a–b) — 2026-11-25

Status: **Phase 1 a and b COMPLETE. Step c (remove ingress-nginx) and hop 9 are ON HOLD for Lee.**

Traefik serves all 18 Git-managed Ingresses and owns the ingress VIP `10.20.30.200` and the Cloudflare tunnel. ingress-nginx is still running on `10.20.30.207`. Only two Ingresses are still on class `nginx`, and neither is in Git. Removing ingress-nginx would make both unreachable, so the coordinator is holding the removal until Lee approves (details below).

Brief `traefik-hop9-1125-v1` (Lee, 21:13 UTC: "Migrate to Traefik, then do hop 9"), task `task_cfe4f250e786`. It follows the [hop 9 hold journal](2026-11-25-k3s-hop9-hold.md). The home cluster stays at v1.35.10+k3s1, with no k3s or add-on version changes.

## Mechanism (verified before switching)

- **The LB IP:** Cilium LB-IPAM assigns IPs from the `io.cilium/lb-ipam-ips` Service annotation (pool `10.20.30.0/24`), and the CiliumL2AnnouncementPolicy announces them. No kube-vip is involved for Services.
- **LAN clients:** they reach `*.home.boundcorp.net` through a public, unproxied wildcard A record `*.home.boundcorp.net → 10.20.30.200`. The router at `10.20.30.1` answers `.200` even for names that don't exist. k8s_gateway (`.210`) isn't on the LAN path.
- **Public hosts:** `*.boundcorp.net`, plus `flux-webhook.home`, come through Cloudflare to cloudflared, then to an in-cluster Service. cloudflared sends SNI `ingress.home.boundcorp.net` and the real `Host` header.
- **Consequence:** moving an Ingress before the VIP moved would have broken it on the LAN. So Traefik took over `.200` and the tunnel **first**, using a priority-1 fallback IngressRoute that proxies any host it doesn't route yet to ingress-nginx. Ingresses were then moved over without any DNS changes.
- **Free IPs:** `.206` (Traefik's temporary IP) and `.207` (ingress-nginx's new IP) were confirmed free before use, with ARP `INCOMPLETE` from enterprise and no Service or Git reference.

## Traefik deployment

- Chart **43.0.0** (Traefik **v3.8.0**), the current stable chart, from `traefik.github.io/charts`.
- The v3.8 migration notes were reviewed. Their changes are about Gateway API and new CRD fields, and Gateway API isn't used here. The chart's CRDs (`traefik.io`, `hub.traefik.io`) are installed with `CreateReplace`. It does not include the Gateway API CRDs.
- Two replicas spread across workers, PDB `minAvailable: 1`.
- IngressClass `traefik`, non-default for now.
- ServiceMonitor enabled, which adds 2 targets (52 → 54).

ingress-nginx equivalents:

| ingress-nginx | Traefik |
|---|---|
| default cert `boundcorp-net-production-tls` | `TLSStore default` with the same secret |
| TLS 1.2/1.3 | `TLSOption default` with `minVersion: VersionTLS12`. TLS 1.1 is refused (verified). |
| HSTS 31449600, includeSubDomains | entrypoint-wide `networking-hsts` Headers middleware (verified on responses) |
| ssl-redirect | `web → websecure` redirect for every host. This returns **301** where nginx returned 308. It also redirects the two non-TLS Ingresses (adguard, filebrowser), which nginx served over plain HTTP. |
| serves every Ingress on 443 | `websecure.http.tls` on, so non-TLS Ingresses get the default cert |
| client body/header timeouts 120 s, keep-alive 120 s | `websecure` readTimeout `0` (Traefik v3's 60 s total-request default would break large uploads), idleTimeout 180 s |
| `proxy-body-size 100M` (global) | no limit. Traefik only limits body size through a buffering middleware, and none is used. **This is looser than before.** |
| Brotli | not ported. A global compress middleware risks websocket and streaming responses, and the gain on the LAN is small. |
| websockets by default | same (verified: hass, frigate, zwave 101; esphome and jellyfin unchanged) |

Per-Ingress translations (no rewrite-target was ported; its only user is `devbox/hatchet-ingress`, which is left unchanged):
- **immich:** dropped `proxy-body-size: 0` and `proxy-read/send-timeout: 600`.
  - Traefik has no body limit and no backend response timeout by default.
  - Its router `respondingtimeouts` cap the *whole* request, whereas nginx's 600 s was idle time between reads. A literal 600 s would therefore be stricter than before, so readtimeout and writetimeout are pinned to `"0"`.
  - The immich chart still adds its own inert `nginx.ingress.kubernetes.io/proxy-body-size` annotation.
- **speakr:** dropped `proxy-body-size: 0` and pinned router readtimeout `"0"`.
- **Behaviour change:** Traefik streams request bodies, while nginx buffered them. An unauthenticated 120 MB POST to immich now gets its 401 after about 64 KB instead of after the full upload. There is no 413 on either path. speakr closes the connection early on large unauthenticated bodies through both controllers (direct to nginx at `.207` too).

## Execution (all on main, each reconciled and gated)

Pre-phase snapshot: `pre-traefik-20261125T2121Z-enterprise-1795641716`, 59,510,816 bytes, SHA-256 `998d322a75f48477c265ab697219b311769d5e99fe863556cde45c65367ab404`. It matched the titan offload copy. The existing retention pruned two old titan copies; the enterprise sources remain.

| Stage | Commit | Result |
|---|---|---|
| 1a Traefik on `.206` + fallback | `36f5883` | Render and server dry-run passed (34 objects). All 20 hosts through `.206` matched the nginx baseline, including tunnel-style SNI and websockets. Gate PASS 21:27:49 with 45 ks, 35 HR and 54/54 targets. |
| 1b-0a cloudflared → Traefik | `3d01d17` | Public hosts and the public websocket returned the same codes, and Traefik metrics show tunnel traffic. cloudflared rev 2→3. Gate PASS. |
| 1b-0b VIP swap (nginx `.207`, Traefik `.200`) | `a9e8a4d` | IPs swapped by 21:30:16. **116 probe samples, 21:29:47–21:35:44, LAN and public, had zero failures.** Revisions ingress-nginx 3→4 and traefik 1→2. Gate and extra checks PASS. |
| Batch 1: adguard, syncthing, loki, prometheus, alertmanager | `c76d379` | Hosts unchanged. Revisions syncthing 3→4, kube-prometheus-stack 10→11, loki 4→5. PASS. |
| hubble-ui (Cilium release) | `40ffbd2` | Cilium 23→24. **The agent DaemonSet rolled** (see Incidents). Cilium status, DNS and hosts passed, and the probe stayed clean. PASS. |
| Batch 2: radarr, sonarr, sabnzbd, esphome, zwave-js-ui, frigate | `420989e` | Websocket codes unchanged. Each app revision went up by one. PASS. |
| Batch 3: immich, speakr, flux-webhook | `71baf39` | Annotations translated as above. 120 MB POST: no 413. flux-webhook gives the same public responses. immich 6→7, speakr 6→7. PASS. |
| Batch 4a: grafana, home-assistant | `7f81353` | HA already trusts `10.0.0.0/8` as proxies, and its log has no proxy warnings. grafana `/api/health` 200 publicly, HA websocket 101 on LAN and public. grafana 2→3, HA 11→12. PASS. |
| Batch 4b: jellyfin (last) | `c9c8ff2` | `/web/` 200, `/System/Info/Public` 200, Range request 206, websocket code unchanged. jellyfin 5→6. Gate and extra checks PASS 21:43:47. |

Every gate checked:
- 44+1 Kustomizations and 34+1 HelmReleases Ready
- 54/54 targets
- 29/29 PVCs, 9/9 DaemonSets
- the same writable Postgres primary `postgres-postgres-6bb6-0` (`false/off`)
- API, etcd and SUC idle
- Helm revisions changed only on the release being edited

Hosts were checked over HTTPS with certificates verified (`ssl_verify_result=0`):
- by IP through the LAN VIP
- tunnel-style (SNI `ingress.home`, real `Host`)
- through public Cloudflare DNS

**Clean watch of the dual-controller state: 21:46:23–21:58:40 UTC (12 min 17 s).**
- 5/5 full gates passed.
- 26 watcher samples with zero alerts, no restart deltas and no bad events.
- All five host sweeps matched the baseline: LAN, tunnel-style and public, with websockets 101 on LAN and public.

`adguard.home` returned **502 before any change**. Its ExternalName backend `net.internal.boundcorp.net:3000` (the router) doesn't answer on port 3000, so this is pre-existing and not related to the migration.

## Incidents and deviations

- **Cilium agent roll on the hubble-ui move.** Changing only `hubble.ui.ingress.className` caused Helm to compute a new `cilium.io/cilium-configmap-checksum`, even though `cilium-config` data did not change (its last managed write is 18:37:24). `rollOutCiliumPods` then rolled all 4 agents, one at a time, between 21:34:26 and 21:35:07. There was no traffic impact. Any future edit to Cilium values, including the 1.20.5 stage, should be expected to roll the agents.
- **Local render printed generated TLS material.** A `helm template` diff of the Cilium chart printed Hubble CA/server key material to the worker terminal. That material was generated at random by the offline render; it was never applied and isn't the cluster's. The saved `helm get manifest` (which holds the live Hubble secrets) was deleted unread, and later diffs left out Secret objects. No live certificate, key or query string was printed or committed.

## Held: step c (remove ingress-nginx)

Two Ingresses are still on class `nginx`. Neither is in Git, and both are left untouched:

- **`devbox/hatchet-ingress`** (`hatchet.devbox.local`, `rewrite-target: /`). It is unmanaged, and the brief says to leave it unchanged.
- **`media/filebrowser`** (`filebrowser.home`, plain HTTP). Commit `87a6f54` removed this app from Git because of unpatchable CVE-2026-54089 and kept only its config PVC. The parent `cluster-apps` Kustomization has `prune: false`, so its live HelmRelease (`app-template` 2.0.3, labelled `kustomize.toolkit.fluxcd.io/name: cluster-apps`) and its Ingress were never removed and are still serving. It can't be moved through GitOps without adding the CVE app back.

Removing ingress-nginx would make both unreachable. The coordinator is asking Lee to approve that. **No Vaultwarden Ingress or route exists.** Nothing was deleted.

The prepared plan for step c, once approved:

1. Traefik: drop the fallback IngressRoute/ServersTransport, make `traefik` the default class, and move the inert external-dns hostname annotation over.
2. Take a fresh verified snapshot. Then empty `ingress-nginx/app` (the HelmRelease is pruned and helm-controller uninstalls it) and repoint the grafana and kube-prometheus-stack `dependsOn` from ingress-nginx to traefik. Keep `cluster-apps-ingress-nginx-certificates`, because it owns the wildcard certificate Traefik uses.
   - The `cluster-apps-ingress-nginx` Kustomization stays as a no-op. With the parent's `prune: false`, removing it from Git would leave an orphan pointing at a missing path.
3. Remove the `ingress-nginx` HelmRepository, then recheck every host. After that, targets go back to 52.

## Not done

- No ingress-nginx removal, no change to the default IngressClass, no hop 9, and no Cilium, cert-manager or Flux upgrade.
- No change to the filebrowser or hatchet Ingresses or their apps.
- No DB, credential, Octo/devbox or Vaultwarden action.

Evidence: `/home/dev/orca/artifacts/traefik-hop9-1125-task_cfe4f250e786/`. It holds:
- health and gate JSON
- `hosts-*.json` (status codes only)
- `vip-swap-probe.log`
- the snapshot logs
- the Traefik render and dry-run
- the scripts
