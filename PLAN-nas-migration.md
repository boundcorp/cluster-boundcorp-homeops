# NAS Migration Plan: Consolidation to Titan

**Status:** Phase 4 FULLY COMPLETE - All NVMe workloads including Crunchy Postgres migrated to Titan
**Started:** 2026-02-04
**Last Updated:** 2026-02-08

---

## Executive Summary

Migrate all NFS storage from distributed nodes (nova, iota, vega) to a single Asustor 12-bay NAS codenamed **Titan**. Titan will provide two storage pools:
- **NVMe pool** - fast storage for configs, databases, active workloads
- **HDD pool** - bulk storage for media, backups

Long-term goal: potentially run k3s on Titan itself to consolidate the entire homelab to a single reliable box.

---

## Current State

### Storage Classes

| Storage Class | YAML Says | Actual PV Server | Share Path | Status |
|--------------|-----------|------------------|------------|--------|
| `nfs-nova-nvme` (default) | 10.20.30.114 (nova) | **10.20.30.112 (VEGA)** | `/share/k8s` | **MISLABELED** - 25 PVCs |
| `nfs-iota-hdd-slush` | 10.20.30.115 | 10.20.30.115 | `/mnt/user/slush/k8s` | Correct - 2 PVCs |

**IMPORTANT:** The storage class YAML file says nova (10.20.30.114) but the actual PVs point to vega (10.20.30.112). The NVMe is physically on VEGA, not nova.

### PVC Inventory

#### On `nfs-nova-nvme` (NVMe tier) - 24 PVCs

| Namespace | PVC Name | Size | App | Priority |
|-----------|----------|------|-----|----------|
| **database** | postgres-postgres-lpqc-pgdata | 20Gi | Crunchy Postgres | **CRITICAL** |
| **home** | home-assistant-config-v1 | 5Gi | Home Assistant | HIGH |
| **home** | frigate-media-v1 | 1Ti | Frigate recordings | HIGH |
| **home** | frigate-config-v1 | 5Gi | Frigate | HIGH |
| **home** | zwave-js-ui-config-v1 | 1Gi | Z-Wave | HIGH |
| **home** | mosquitto-config-v1 | 100Mi | MQTT | HIGH |
| **home** | esphome-config-v1 | 5Gi | ESPHome | MEDIUM |
| **home** | wyoming-whisper-data | 1Gi | Voice assistant | MEDIUM |
| **home** | recipes-media | 10Gi | Recipes | MEDIUM |
| **home** | recipes-static | 10Gi | Recipes | MEDIUM |
| ~~**home**~~ | ~~recipes-data-recipes-postgresql-0~~ | ~~2Gi~~ | ~~Old recipes postgres~~ | **DELETED** |
| ~~**media**~~ | ~~mediamega-data~~ | ~~1Ti~~ | ~~Media library~~ | ❌ **DELETED** (was duplicate of slushmedia) |
| ~~**media**~~ | ~~config-plex-0~~ | ~~50Gi~~ | ~~Plex~~ | ❌ **DELETED** (unused) |
| ~~**media**~~ | ~~jellyfin-config~~ | ~~50Gi~~ | ~~Jellyfin~~ | ✅ **MIGRATED** |
| ~~**media**~~ | ~~radarr-config~~ | ~~1Gi~~ | ~~Radarr~~ | ✅ **MIGRATED** |
| ~~**media**~~ | ~~sonarr-config~~ | ~~1Gi~~ | ~~Sonarr~~ | ✅ **MIGRATED** |
| ~~**media**~~ | ~~sabnzbd-config~~ | ~~1Gi~~ | ~~SABnzbd~~ | ✅ **MIGRATED** |
| **monitoring** | grafana | 10Gi | Grafana | MEDIUM |
| **monitoring** | prometheus-...stack-0 | 25Gi | Prometheus | MEDIUM |
| **monitoring** | alertmanager-...stack-0 | 8Gi | AlertManager | LOW |
| **monitoring** | alertmanager-...stack-1 | 8Gi | AlertManager | LOW |
| **monitoring** | alertmanager-...stack-2 | 8Gi | AlertManager | LOW |
| **backups** | syncthing-config | 10Gi | Syncthing | MEDIUM |
| **boundcorp-dev** | buildcache-pvc | 50Gi | Build cache | LOW |
| **devbox** | devbox-home-pvc | 80Gi | Dev environment | LOW |

**Approximate total claimed: ~1.4 TB**

#### On `nfs-iota-hdd-slush` (HDD tier) - 2 PVCs

| Namespace | PVC Name | Size | App | Priority |
|-----------|----------|------|-----|----------|
| **backups** | leeward-backups | 2Ti | Personal backups | HIGH |
| **media** | slushmedia-data | 10Ti | Bulk media | MEDIUM |

**Approximate total claimed: ~12 TB**

### Non-K8s Data on NFS

#### On Vega `/share/` (alongside k8s/)

| Path | Size | Description | Action |
|------|------|-------------|--------|
| `/share/Archive/Nextcloud.july26-2024.tar.gz` | 23.4G | Old Nextcloud backup | Review/delete? |
| `/share/Archive/rebar/` | 180.8G | Old project? | Review/delete? |
| `/share/Archive/rigel-backup/` | 97.5G | Old server backup | Review/delete? |
| `/share/Archive/rigel-backup.tar.gz` | 77G | Duplicate of above | Review/delete? |
| `/share/Archive/Homeops.aug18-2024.tar.gz` | 157M | Old homeops backup | Can delete (stale) |
| `/share/Archive/vaultwarden/` | 12M | Old vaultwarden backup | Can delete (stale) |
| `/share/Game/Satisfactory/` | ? | Game server data | Migrate to Titan |
| `/share/backup-k8s-pvcs.sh` | - | **ACTIVE** backup script | Migrate to Titan |
| `/share/backup.log` | 300K | Backup log | Migrate with script |

#### On Iota `/mnt/user/slush/`

| Path | Description | Action |
|------|-------------|--------|
| `/mnt/user/slush/k8s/` | K8s PVCs | Migrate to Titan HDD |
| `/mnt/user/slush/vega-backup/` | Mirror of vega /share | Redundant backup - good! |

---

## Existing Backup Systems

### 1. PVC Backup Script (ACTIVE)

**Location:** `/share/backup-k8s-pvcs.sh` on vega
**Schedule:** Daily at midnight (cron on vega?)
**Destination:** `s3://boundcorp-backups-eu-north-1/homeops-backup-nfs/`
**Last run:** Jan 18, 2026

**What it backs up:**
- home-assistant-config (1.1G)
- esphome-config (1.6G)
- frigate-config (37M) - NOT media
- zwave-js-ui-config (212M)
- mosquitto-config (4K)
- recipes-media (2.8M), recipes-static (196M)
- wyoming-whisper-data (41M)
- jellyfin-config (1.7G)
- plex-config (4K - seems wrong?)
- radarr-config (175M), sonarr-config (122M), sabnzbd-config (28M)

**What it EXCLUDES:**
- backups-* (leeward-backups, syncthing)
- monitoring-* (prometheus, grafana, alertmanager)
- database-* (Crunchy - has its own backup)
- mediamega (too large)
- frigate-media (too large)

### 2. Crunchy pgbackrest (ACTIVE)

**Schedule:** Full Sunday 02:00, Incremental Mon-Sat/2 02:00
**Destination:** `s3://boundcorp-backups-eu-north-1/backups/cluster-boundcorp-homeops/crunchy-pgo`
**Retention:** 30 days

### 3. Iota vega-backup Mirror

**Location:** `/mnt/user/slush/vega-backup/`
**Description:** Appears to be a rsync mirror of vega /share from July 2025
**Status:** Stale (last updated July 13, 2025)

---

## Target State: Titan

### Titan Storage Pools

| Pool | Type | Intended Use | New Storage Class |
|------|------|--------------|-------------------|
| **nvme** | NVMe | Configs, databases, active data | `nfs-titan-nvme` |
| **hdd** | HDD (RAID?) | Media, backups, bulk storage | `nfs-titan-hdd` |

### Migration Mapping

| Current Storage Class | Target Storage Class | Notes |
|----------------------|---------------------|-------|
| `nfs-nova-nvme` | `nfs-titan-nvme` | Most workloads |
| `nfs-iota-hdd-slush` | `nfs-titan-hdd` | Bulk storage |
| `nfs-vega-nvme` | `nfs-titan-nvme` | If any exist |

### Workloads to Consider Moving to HDD

Some PVCs currently on NVMe might be better suited for HDD:
- `frigate-media-v1` (1Ti) - recordings don't need NVMe speed
- `mediamega-data` (1Ti) - media library
- Alertmanager PVCs - logs/alerts don't need NVMe

### Actual Disk Usage (measured 2026-02-04)

**Vega NFS Pool:** 3.6T total, 2.1T used (61%), actual k8s PVC usage ~1.26T

| PVC | Claimed | Actual | App | Notes |
|-----|---------|--------|-----|-------|
| mediamega-data | 1Ti | **1.2T** | Media library | Biggest consumer |
| frigate-media | 1Ti | 21G | Frigate recordings | Way under quota |
| devbox-home-pvc | 80Gi | 5.6G | Dev environment | |
| prometheus | 25Gi | 4.5G | Prometheus | |
| buildcache-pvc | 50Gi | 3.7G | Build cache | |
| jellyfin-config | 50Gi | 1.8G | Jellyfin | |
| home-assistant-config | 5Gi | 1.1G | Home Assistant | |
| esphome-config | 5Gi | 425M | ESPHome | |
| zwave-js-ui-config | 1Gi | 215M | Z-Wave JS UI | |
| recipes-static | 10Gi | 196M | Tandoor Recipes | |
| radarr-config | 1Gi | 178M | Radarr | |
| sonarr-config | 1Gi | 124M | Sonarr | |
| wyoming-whisper-data | 1Gi | 41M | Voice assistant | |
| sabnzbd-config | 1Gi | 29M | SABnzbd | |
| frigate-config | 5Gi | 7.7M | Frigate | |
| recipes-media | 10Gi | 2.8M | Tandoor Recipes | |
| grafana | 10Gi | 2.7M | Grafana | |
| postgres-pgdata | 20Gi | 640K | Crunchy Postgres | Data in tablespace? |
| syncthing-config | 10Gi | 20K | Syncthing | |
| alertmanager x3 | 8Gi ea | 8K ea | AlertManager | |
| config-plex-0 | 50Gi | **4K** | Plex | **EMPTY - can delete** |
| mosquitto-config | 100Mi | 4K | Mosquitto MQTT | |

**Local Backups Created:** `.backups/home-pvcs-20260204/` (1.3G total)

---

## Phase Checklist

### Phase 0: Pre-Migration Verification ✅ COMPLETE
- [x] ~~Verify Vaultwarden backups work (haha cluster)~~ - Manual backup taken to `.backups/vaultwarden-20260204/` (352 passwords verified). Volsync configured but blocked by Calico networking issue on haha cluster (see `cluster-boundcorp-ha/PLAN-fix-calico.md`)
- [x] ~~Test Crunchy Postgres restore~~ - pgbackrest verified working (last full backup Jan 18, recent failures due to homeops cluster instability, not backup config)
- [x] ~~Delete orphaned `recipes-data-recipes-postgresql-0` PVC~~ - Deleted (was 2Gi, 479 days old)
- [x] ~~Document current actual disk usage~~ - See "Actual Disk Usage" section below
- [x] ~~Verify all apps are healthy~~ - All critical home apps running. Cleaned up stale Flux resources. Minor issues: filebrowser (no PVC), vector-aggregator (missing loki dep)
- [x] **Local backups created** - `.backups/home-pvcs-20260204/` contains home-assistant, zwave-js-ui, esphome, frigate-config, recipes-media, recipes-static, mosquitto

### Phase 1: Titan Setup ✅ COMPLETE
- [x] Rack and power Titan NAS - AS6810T online at 10.20.30.99
- [x] Configure NVMe pool - WD_BLACK SN7100X 4TB in Single mode (RAID 1 migration available when 2nd NVMe added)
- [ ] Configure HDD pool (RAID level TBD) - 10 HDD bays empty, ready for drives
- [x] Set up NFS exports for k8s - `/volume1/k8s` exported to 10.20.30.0/24 (rw, no_root_squash)
- [x] Assign static IP to Titan - **10.20.30.99**
- [x] Test NFS mounts from a k8s node - **PASSED** via test pod with NFS volume mount
- [x] Create new storage classes in manifests - `nfs-titan-nvme` already existed and deployed
- [x] **Data pre-staged on Titan** - 12G of k8s PVCs copied (excludes mediamega, frigate-media, devbox)

**Titan NFS Details:**
- NFS mount: `10.20.30.99:/volume1/k8s`
- Storage: 3.63 TB NVMe (Btrfs), 3.6T available
- RAID: Single (can migrate to RAID 1 online after adding 2nd NVMe)
- Storage class: `nfs-titan-nvme` (deployed, not default yet)

**Pre-Migration Backups (2026-02-04):**
- `~/atlas/homeops-backup-20260204/` - 370G total on local SSD
  - Archive (vega, excluding Game): 354G
  - k8s-pvcs (excluding mediamega/frigate-media/devbox): 13G
  - vaultwarden: 3.2M
- `~/titan-ssd/k8s/` - 12G k8s PVCs staged on Titan
- `.backups/home-pvcs-20260204/` - 1.3G critical home configs

### Phase 2: Backup Everything ✅ MOSTLY COMPLETE
- [ ] **Crunchy Postgres**: pg_dumpall + document restore procedure (pgbackrest working, but manual dump recommended)
- [x] **Home Assistant**: backed up to atlas + titan (1.1G)
- [x] **Frigate**: config backed up (8.4M), media excluded (replaceable)
- [x] **Jellyfin**: config backed up to atlas + titan (1.7G)
- [ ] **Grafana**: export dashboards (2.7M, can recreate)
- [x] **All PVCs**: rsync to Titan as staging area (12G, excludes mediamega/frigate-media/devbox)
- [x] **Archive backup**: 354G copied to atlas SSD (excludes Game folder)
- [x] Verify backup integrity - data verified accessible from k8s pod

### Phase 3: Storage Class Migration ✅ READY
- [x] Deploy new storage classes - `nfs-titan-nvme` deployed and verified working
- [ ] `nfs-titan-hdd` - waiting for HDD pool configuration
- [ ] Test with a non-critical PVC first (buildcache or alertmanager)
- [ ] Document PVC migration procedure

**Current Storage Classes:**
```
nfs-nova-nvme (default)   -> 10.20.30.112 (vega)
nfs-titan-nvme            -> 10.20.30.99 (titan) ← NEW
nfs-iota-hdd-slush        -> 10.20.30.115 (iota)
```

### Phase 4: Workload Migration (by priority)

**Home Namespace ✅ COMPLETE (2026-02-05)**
- [x] Home Assistant - migrated, required auth file copy from vega
- [x] Z-Wave JS UI - migrated
- [x] Mosquitto MQTT - migrated (empty config, works)
- [x] ESPHome - migrated
- [x] Frigate config - migrated
- [x] Recipes - migrated (both media + static)
- [x] Wyoming Whisper - migrated (first test case)
- [ ] Frigate media (1Ti) - **NOT MIGRATED** - large, needs direct copy from vega

**Database Namespace ✅ COMPLETE (2026-02-08)**
- [x] Crunchy Postgres - migrated to Titan via S3 pgbackrest restore (backup 20260208-100006F)
  - Deleted old PostgresCluster CR + PVC, recreated with dataSource pointing to S3
  - Fixed pg_hba auth method: md5 → scram-sha-256 (required for new SCRAM password hashes)
  - Synced PGO-generated secrets to app namespaces (home, media) where copies existed
  - Fixed sonarr cached config.xml with stale credentials
  - Removed deprecated DB users: devboxdaylight, devboxzeroindex, devboxergo, devboxcmux, zward
  - Updated recipes to v2.5.0

**Media Namespace ✅ COMPLETE (2026-02-05)**
- [x] Jellyfin config - migrated to Titan
- [x] Sonarr config - migrated to Titan
- [x] Radarr config - migrated to Titan
- [x] SABnzbd config - migrated to Titan
- [x] Plex - **DELETED** (was empty/unused)
- [x] mediamega-data (2Ti) - **COMPLETE** - 1.2TB copied from vega to titan

**Monitoring Namespace ✅ COMPLETE (2026-02-06)**
- [x] Grafana - migrated to Titan
- [x] Prometheus - storage class updated (scaled to 0, old PVC deleted)
- [x] Alertmanager x3 - storage class updated (scaled to 0, old PVCs deleted)

**Other ✅ COMPLETE (2026-02-06)**
- [x] Syncthing config - migrated to Titan
- [x] Build cache - deleted (no workloads, can recreate)
- [x] Devbox - deleted (no workloads, can recreate)
- [x] Frigate media - fresh start on Titan (no data migration needed)

### Phase 5: Iota HDD Migration to Titan
**Goal:** Move all 3 Unraid drives (2 storage + 1 parity) to Titan at once

**Current Iota HDD usage:**
- slushmedia-data: 2.4TB (Jellyfin media library - ACTIVE)
- leeward-backups: 25GB (personal backups - ACTIVE)
- vega-backup: 1.8TB (stale mirror from July 2025 - DELETE)
- **Total after cleanup: ~2.5TB** (fits on Titan NVMe 2.8TB free)

**Steps:**
- [x] Delete stale vega-backup from iota (1.8TB) - DONE, freed 1.7TB
- [x] Create temporary PVCs on Titan NVMe for iota data - DONE
  - temp-slushmedia-data (3Ti) → pvc-bc2f9f21-6c5f-4771-9e45-d11ef43d414a
  - temp-leeward-backups (50Gi) → pvc-add9e47a-7d21-4939-bda4-be94501d7d50
- [x] Copy slushmedia-data (2.4TB) to Titan NVMe - DONE (54MB/s avg)
- [x] Copy leeward-backups (25GB) to Titan NVMe - DONE

**Phase 5a: Vega NVMe Decommission → Titan RAID 1**
- [ ] Power down vega
- [ ] Pull NVMe drive from vega
- [ ] Power vega back on
- [ ] Verify all k8s pods running OK (all NVMe data already on Titan)
- [ ] Install NVMe in Titan NAS
- [ ] Migrate Titan NVMe pool to RAID 1 (online migration, no data loss)

**Phase 5b: Switch media apps to Titan temporary storage**
- [ ] Update Jellyfin helmrelease: `slushmedia-data` → `temp-slushmedia-data`
- [ ] Update Sonarr helmrelease: `slushmedia-data` → `temp-slushmedia-data`
- [ ] Update Radarr helmrelease: `slushmedia-data` → `temp-slushmedia-data`
- [ ] Update SABnzbd helmrelease: `slushmedia-data` → `temp-slushmedia-data`
- [ ] Update Filebrowser: `slushmedia-data` → `temp-slushmedia-data`
- [ ] Update leeward-backups references
- [ ] Verify all apps working on temporary Titan storage
- [ ] Commit and push GitOps changes

**Phase 5c: Iota HDD drives → Titan**
- [ ] Power down iota (Unraid)
- [ ] Pull all 3 HDD drives (2 storage + 1 parity)
- [ ] Install HDDs in Titan NAS
- [ ] Configure Titan HDD pool (RAID level TBD)
- [ ] Create `nfs-titan-hdd` storage class
- [ ] Create permanent slushmedia-data + leeward-backups PVCs on nfs-titan-hdd
- [ ] Copy data from NVMe temp → HDD permanent
- [ ] Update app manifests to use permanent HDD PVCs
- [ ] Delete temporary NVMe PVCs

### Phase 6: Final Cleanup
- [ ] Verify all workloads healthy on Titan storage (NVMe + HDD)
- [ ] Remove old storage classes from manifests (nfs-nova-nvme, nfs-iota-hdd-slush)
- [ ] Decommission vega NFS (NVMe removed, no longer serving storage)
- [ ] Decommission iota (Unraid no longer needed, drives in Titan)
- [ ] Update backup script (migrate from vega to Titan)
- [ ] Update documentation

### Phase 7: Future Consideration
- [ ] Evaluate running k3s on Titan directly
- [ ] Single-box homelab architecture

---

## Someday List

Items deferred to keep scope manageable:

- [ ] **Recipes → haha cluster migration** - Move Tandoor Recipes to the HA VPS cluster instead of homeops. Requires solving PostgreSQL on haha (managed DB, self-hosted, or SQLite).
- [ ] **Move frigate-media to HDD tier** - 1TB of recordings don't need NVMe speed
- [ ] **Move mediamega to HDD tier** - 1TB media library, HDD is fine for streaming
- [ ] **Review Archive folder** - ~380GB of old data on vega, clean up or migrate
- [ ] **Fix storage class naming** - Rename `nfs-nova-nvme` to `nfs-vega-nvme` for accuracy (or just migrate to titan)

---

## PVC Migration Procedure (TESTED & WORKING)

**Key Learning:** PV specs are IMMUTABLE - you cannot patch the NFS path. Must delete and recreate PVCs.

### For Static PVC Files (e.g., home-assistant-config-v1)

```bash
# 1. Scale down workload
kubectl scale deployment/<app> -n <namespace> --replicas=0
# or: kubectl scale statefulset/<app> -n <namespace> --replicas=0

# 2. Protect old PV data (prevent deletion on PVC delete)
OLD_PV=$(kubectl get pvc <pvc-name> -n <namespace> -o jsonpath='{.spec.volumeName}')
kubectl patch pv $OLD_PV -p '{"spec":{"persistentVolumeReclaimPolicy":"Retain"}}'

# 3. Delete old PVC
kubectl delete pvc <pvc-name> -n <namespace>

# 4. Update manifest: change storageClassName from nfs-nova-nvme to nfs-titan-nvme
# Edit: kubernetes/apps/<namespace>/<app>/config-pvc.yaml

# 5. Commit and push
git add . && git commit -m "feat: migrate <app> storage to Titan" && git push

# 6. Flux creates new PVC with new PV on Titan
flux reconcile ks cluster-apps -n flux-system

# 7. Copy data from old vega dir to new titan dir
# Create helper pod with both NFS mounts, then rsync

# 8. CRITICAL: Copy auth/permission-restricted files directly from vega
# (Staged backups may miss files with restricted permissions!)

# 9. Fix permissions if needed (run as root)
chown 568:568 /data/.storage/auth*  # for HA

# 10. Scale back up
kubectl scale deployment/<app> -n <namespace> --replicas=1

# 11. Restart to clear stale NFS file handles
kubectl rollout restart deployment/<app> -n <namespace>
```

### For HelmRelease Persistence (e.g., jellyfin)

Same as above, but edit the HelmRelease persistence.storageClass instead of a PVC file.

### Helper Pod for Data Copy

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: data-mover
  namespace: home
spec:
  containers:
  - name: mover
    image: alpine
    command: ["sh", "-c", "apk add rsync && sleep 3600"]
    volumeMounts:
    - name: vega
      mountPath: /vega
    - name: titan
      mountPath: /titan
  volumes:
  - name: vega
    nfs:
      server: 10.20.30.112
      path: /share/k8s/<old-pvc-id>
  - name: titan
    nfs:
      server: 10.20.30.99
      path: /volume1/k8s/<new-pvc-id>
```

Then: `kubectl exec data-mover -- rsync -av /vega/ /titan/`

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Data loss during migration | HIGH | Multiple backups, verify before delete |
| Extended downtime | MEDIUM | Migrate non-critical first, do critical during low-usage |
| Titan hardware failure | HIGH | Proper RAID config, off-site backups |
| Network issues to Titan | MEDIUM | Test thoroughly, have rollback plan |
| Postgres corruption | CRITICAL | pg_dump before migration, test restore |

---

## Backup Gap Analysis

### What HAS Scheduled Backups ✅

| System | Backup Method | Destination | Schedule | Status |
|--------|--------------|-------------|----------|--------|
| Crunchy Postgres | pgbackrest | S3 eu-north-1 | Full Sun, Incr M-Sa/2 | **VERIFY RESTORE** |
| home-assistant-config | PVC backup script | S3 eu-north-1 | Daily midnight | ✅ Working |
| esphome-config | PVC backup script | S3 eu-north-1 | Daily midnight | ✅ Working |
| frigate-config | PVC backup script | S3 eu-north-1 | Daily midnight | ✅ Working |
| zwave-js-ui-config | PVC backup script | S3 eu-north-1 | Daily midnight | ✅ Working |
| mosquitto-config | PVC backup script | S3 eu-north-1 | Daily midnight | ✅ Working |
| wyoming-whisper-data | PVC backup script | S3 eu-north-1 | Daily midnight | ✅ Working |
| recipes-media/static | PVC backup script | S3 eu-north-1 | Daily midnight | ✅ Working |
| jellyfin-config | PVC backup script | S3 eu-north-1 | Daily midnight | ✅ Working |
| radarr/sonarr/sabnzbd | PVC backup script | S3 eu-north-1 | Daily midnight | ✅ Working |

### What has Backup CONFIG but NO Active Schedule ⚠️

| System | Issue | Action Needed |
|--------|-------|---------------|
| Vaultwarden (haha) | Has volsync secrets + restore manifest, but NO ReplicationSource | **CREATE scheduled backup** |
| homeops namespaces | Have `volsync.backube/privileged-movers: true` labels but volsync not deployed | Deploy volsync or remove labels |

### What has NO Backup (GAP!) ❌

| PVC | Namespace | Risk | Notes |
|-----|-----------|------|-------|
| ~~config-plex-0~~ | media | N/A | Plex no longer used - can delete |
| grafana | monitoring | MEDIUM | Excluded by script, dashboards exportable |
| prometheus | monitoring | LOW | Excluded, metrics ephemeral |
| alertmanager x3 | monitoring | LOW | Excluded, not critical |
| syncthing-config | backups | MEDIUM | Excluded, ironic since it's a sync tool |
| leeward-backups | backups | **???** | 2TB - This IS your backups, needs offsite! |
| slushmedia-data | media | LOW | 10TB bulk media |
| mediamega-data | media | MEDIUM | 1TB media library, excluded (too large) |
| frigate-media-v1 | media | LOW | 1TB recordings, excluded (replaceable) |
| buildcache-pvc | boundcorp-dev | LOW | Ephemeral |
| devbox-home-pvc | devbox | MEDIUM | Dev environment, excluded |

### Backup Script Issues Found

1. **Plex config showing 4K** - either broken or config is minimal
2. **Old symlinks point to missing PVCs** - `backups-leeward-backups` and `backups-syncthing-config` symlinks point to old PVC IDs that don't exist in current k8s directory

### Priority Backup Actions

1. **Verify Crunchy backups work** - test restore to a temp DB
2. **Create Vaultwarden scheduled backup** on haha cluster
3. **Back up Home Assistant** - use HA's built-in backup feature
4. **Back up Plex/Jellyfin** - export configs before migration
5. **Document what's acceptable to lose** vs what must be preserved

---

## Node Status & NFS Confusion

### Current Node Status

| Node | IP | Status | Role |
|------|-----|--------|------|
| enterprise | 10.20.30.111 | Ready | control-plane |
| nova | 10.20.30.114 | **NotReady** | worker |
| vega | 10.20.30.112 | Ready | worker |
| ziti | 10.20.30.113 | Ready | worker |

### Storage Class Mystery

The storage class `nfs-nova-nvme` points to `10.20.30.114` (nova), but:
- Nova is **NotReady** as a k8s node
- User suspects the NVMe might actually be mounted from vega
- Need to SSH to nodes to verify actual NFS mount sources

**TODO:** Verify which physical machine has the NVMe drives mounted and serving NFS

---

## Orphaned Resources

| Resource | Namespace | Size | Action |
|----------|-----------|------|--------|
| ~~recipes-data-recipes-postgresql-0~~ | ~~home~~ | ~~2Gi~~ | **DELETED 2026-02-04** |

---

## Open Questions

1. ~~**Titan IP address**~~ - **RESOLVED**: 10.20.30.99
2. **RAID configuration** - NVMe is Single (RAID 1 available when 2nd drive added). HDD pool TBD.
3. **Downtime tolerance** - What's acceptable downtime per app?
4. **k3s on Titan** - Is this a goal for this migration or future phase?
5. **What backs up leeward-backups?** - 2TB of backups needs offsite copy
6. ~~**Plex config backup**~~ - **RESOLVED**: Plex PVC is empty (4K), not in use - can delete
7. **Archive cleanup** - Backed up to atlas (354G), can delete from vega after migration verified

### Resolved Questions

- ~~**NFS source mystery**~~ - **SOLVED**: NVMe is on VEGA (10.20.30.112), storage class YAML is mislabeled
- ~~**Titan IP address**~~ - **SOLVED**: 10.20.30.99
- ~~**Plex config**~~ - **SOLVED**: Empty PVC, Plex not in use

---

## Decision Log

### 2026-02-04
- NAS codename: **Titan**
- Scope clarified: This is a full storage consolidation, not just Recipes migration
- Recipes → haha moved to Someday list to keep scope manageable
- Identified 27 PVCs total across 2 storage classes (~13.4 TB claimed) - now 26 after cleanup
- **CONFIRMED: NVMe is on VEGA (10.20.30.112)**, not nova. Storage class YAML is wrong!
- **Found existing backup script!** Daily cron backing up most PVCs to S3
- **Vaultwarden backup gap found**: Has config but no active ReplicationSource in haha
- **Orphaned PVC found**: `recipes-data-recipes-postgresql-0` (2Gi) - old recipes postgres, now uses Crunchy
- **Plex backup may be broken**: Shows only 4K in backup log
- **Found Archive data**: ~380GB of old backups on vega that should be reviewed
- **Found vega-backup mirror on iota**: Stale copy from July 2025
- Strategy: Verify backups work → Fix gaps → Add drives to Titan one at a time

### 2026-02-04 (Phase 0 Complete)
- **Vaultwarden backup**: Manual backup taken to `.backups/vaultwarden-20260204/` (352 passwords, verified fresh data from Feb 3)
- **Volsync configured on haha**: ReplicationSource created, controller fixed (upgraded to 0.9.1, fixed RBAC, installed VolumeSnapshot CRDs), but blocked by Calico networking issue
- **Calico issue discovered**: Route conflicts on node `lke114750-170486-583f0fd30000` preventing new pods. Plan created at `cluster-boundcorp-ha/PLAN-fix-calico.md`
- **Crunchy pgbackrest verified**: Backups working (last successful Jan 18), recent failures due to homeops cluster instability not config issues
- **Orphaned PVC deleted**: `recipes-data-recipes-postgresql-0` removed (was 2Gi, 479 days old, recipes now uses Crunchy)
- **PVC count updated**: 24 PVCs on nfs-nova-nvme (was 25)

### 2026-02-05 (Phase 1 Complete)
- **Titan online**: AS6810T at 10.20.30.99, NFS configured and working
- **Full backup to Atlas SSD**: 370G total (Archive 354G, k8s-pvcs 13G, vaultwarden 3.2M)
- **Data staged on Titan**: 12G of k8s PVCs copied (excludes mediamega 1.2T, frigate-media 21G, devbox 5.6G)
- **NFS mount tested from k8s**: Test pod successfully mounted `10.20.30.99:/volume1/k8s` and verified all 21 PVC directories
- **Storage class deployed**: `nfs-titan-nvme` already existed in manifests and is deployed in cluster
- **Data verification**: All critical app data confirmed present on Titan (home-assistant 1.1G, jellyfin 1.7G, zwave 213M, prometheus 4.5G, etc.)
- **Ready for Phase 4**: Workload migration can begin

### 2026-02-05 (Phase 4 Started)
- **wyoming-whisper migrated**: First successful migration to Titan using dynamic PVC approach
- **Migration procedure refined**: Update storageClass → commit/push → move data on Titan → restart pod

### 2026-02-05 (Media Namespace Migration)
- **Plex deleted**: Empty/unused app removed from cluster and git repo
- **Jellyfin migrated**: Config now on nfs-titan-nvme (required Helm release secret reset due to state mismatch)
- **Sonarr migrated**: Config now on nfs-titan-nvme
- **Radarr migrated**: Config now on nfs-titan-nvme
- **SABnzbd migrated**: Config now on nfs-titan-nvme
- **mediamega-data PVC created**: Upgraded from 1Ti to 2Ti on nfs-titan-nvme
- **mediamega-data copy COMPLETE**: rsync finished - 1.2TB copied from vega (pvc-b089aa67) to titan (pvc-0c7859c9)
- **All media apps running**: sonarr, radarr, sabnzbd, jellyfin all healthy on Titan storage
- **Media namespace FULLY MIGRATED**: All apps and data now on Titan NAS

### 2026-02-06 (Phase 4 Complete)
- **Monitoring namespace migrated**: Grafana on Titan, Prometheus/Alertmanager storage class updated (scaled to 0)
- **Syncthing migrated**: 15.8MB config copied from vega to titan
- **Frigate media**: Fresh 1Ti PVC on Titan (no data migration needed)
- **Devbox PVC deleted**: No workloads using it, can recreate when needed
- **Buildcache PVC deleted**: No workloads using it, can recreate when needed
- **PHASE 4 COMPLETE**: All NVMe workloads migrated to Titan NAS
- **nfs-nova-nvme is now EMPTY**: All workloads migrated to Titan

**Final PVC count on Titan: 17 PVCs**
- home: 10 (HA, zwave, mosquitto, esphome, frigate-config, frigate-media, recipes x2, whisper)
- media: 4 (jellyfin, sonarr, radarr, sabnzbd) - mediamega deleted, slushmedia stays on iota
- monitoring: 1 (grafana)
- backups: 1 (syncthing)
- database: 1 (crunchy postgres)

### 2026-02-06 (Phase 5 Cleanup Started)
- **mediamega-data analysis**: Discovered mediamega (1.2TB on Titan) was orphaned - no pods used it!
  - Jellyfin/Sonarr/Radarr/SABnzbd all mount `slushmedia-data` (on Iota HDD)
  - TV: All 24 shows on mediamega were duplicates of slushmedia (plus slushmedia has 25 more)
  - Movies: 19/20 duplicates, only "Star Trek Section 31" was unique (not worth keeping)
- **mediamega-data DELETED**: Removed orphaned PVC, freed ~500GB on Titan NVMe
- **Titan NVMe status**: 936GB used, 2.8TB free (26% utilization)
- **Iota HDD (slushmedia)**: 2.4TB media library - the REAL Jellyfin library, stays on Iota for now
- **Stale vega-backup DELETED from iota**: 1.8TB freed (was July 2025 mirror, redundant)
- **Iota → Titan copy COMPLETE**: slushmedia (2.4TB) + leeward-backups (25GB) copied to temp PVCs on Titan NVMe
- **Titan NVMe status post-copy**: ~2.6TB used, ~1.1TB free
- **Plan**: Pull vega NVMe → add to Titan for RAID 1, then switch apps to temp storage, then move Unraid HDDs to Titan

### 2026-02-08 (Crunchy Postgres Migrated)
- **Crunchy Postgres migrated to Titan**: Restored from S3 pgbackrest backup (20260208-100006F)
  - Old NFS mount on vega was stale (NVMe removed from fstab), postgres was in CrashLoopBackOff
  - Deleted PostgresCluster CR + old PVC, Flux recreated with dataSource for S3 restore
  - Fixed dataSource anchor: `*minio` → `*s3`
  - WAL replay took ~10 minutes, full restore ~15 minutes
- **pg_hba auth fix**: Changed `md5` → `scram-sha-256` (PGO generates SCRAM passwords, md5 method caused auth failures)
- **Secret sync issue discovered**: PGO copies secrets to app namespaces via crunchy-userinit, but recreating the cluster generated NEW secrets in database namespace while stale copies remained in home/media namespaces
- **Sonarr config.xml caching**: Sonarr caches DB connection in persistent config.xml, had to manually update after password change
- **Deprecated DB users removed**: devboxdaylight, devboxzeroindex, devboxergo, devboxcmux, zward (no running apps)
- **Recipes updated**: 2.2.0 → 2.5.0
- **nfs-nova-nvme is now COMPLETELY EMPTY**: Ready for decommission
- **PVC count on Titan: 17** (was 16)

---

## PVC to PV Mapping Reference (Pre-Migration Snapshot)

**Generated: 2026-02-05**

This mapping is needed for data migration - the old PV name tells us which directory on Titan contains the staged data.

### nfs-nova-nvme PVCs (to migrate to Titan)

| Namespace | PVC Name | Old PV (vega source) | New PV (titan) | Size | Status |
|-----------|----------|---------------------|----------------|------|--------|
| home | wyoming-whisper-data | pvc-29256e06-1b59-43ed-a2f6-6f2b4e106708 | pvc-e3ca061a-947f-454b-8d55-ba43187d35eb | 1Gi | ✅ MIGRATED |
| home | home-assistant-config-v1 | pvc-f446ee1f-0ebe-4557-95a0-bd0e3a53094b | pvc-ce686504-1c91-4bca-b2be-8c9f5d623368 | 5Gi | ✅ MIGRATED |
| home | zwave-js-ui-config-v1 | pvc-07fabc71-04bc-4f87-90a7-618935a988d1 | pvc-10cef59c-af18-4043-9cb9-2aa45a2a42b7 | 1Gi | ✅ MIGRATED |
| home | mosquitto-config-v1 | pvc-f22da4ad-1859-4964-952d-1c38e4804939 | pvc-50d5e76a-7eef-4a72-9373-70a3ca473e01 | 100Mi | ✅ MIGRATED |
| home | esphome-config-v1 | pvc-fe27df28-3cfc-49ca-9b64-0b74ab22537d | pvc-f64dd2e4-face-4d5d-8c41-9e248cf5a7d5 | 5Gi | ✅ MIGRATED |
| home | frigate-config-v1 | pvc-5ff6d94e-0479-4f06-996f-0243b578e03b | pvc-5ad89a0f-e69d-4b86-9ccd-2b2bcec25639 | 5Gi | ✅ MIGRATED |
| home | frigate-media-v1 | pvc-ba4662a2-fdb0-490a-aa07-6527e74e7570 | - | 1Ti | ⏸️ NOT MIGRATED (large) |
| home | recipes-media | pvc-8b12a160-4f28-4fc7-84af-40051ea4d2de | pvc-bb28fcde-3f78-4eef-8e40-1c28cdab28f5 | 10Gi | ✅ MIGRATED |
| home | recipes-static | pvc-8354ee50-2596-4b48-b200-d1c805b2e81f | pvc-5564002d-fabd-491d-9b74-1dfb0ea21c3a | 10Gi | ✅ MIGRATED |
| media | jellyfin-config | pvc-006cea7c-75f2-420b-bf2c-4365aedc37b7 | (dynamic on Titan) | 50Gi | ✅ MIGRATED |
| media | radarr-config | pvc-5d79bb99-47ae-4a03-9745-c43bdaf16bdd | (dynamic on Titan) | 1Gi | ✅ MIGRATED |
| media | sonarr-config | pvc-fe11d12b-fc39-4a8e-bea1-f172fb2e9f94 | (dynamic on Titan) | 1Gi | ✅ MIGRATED |
| media | sabnzbd-config | pvc-bd3ad6f1-c687-438f-b928-33f469ff2b1f | (dynamic on Titan) | 1Gi | ✅ MIGRATED |
| ~~media~~ | ~~config-plex-0~~ | ~~pvc-3e2c80f2-32bd-42da-bde3-cfe294655586~~ | - | ~~50Gi~~ | ❌ DELETED |
| ~~media~~ | ~~mediamega-data~~ | ~~pvc-b089aa67-bbf8-48e9-999d-859e0da2ba43~~ | ~~pvc-0c7859c9-6c4b-402e-92df-cb069bf6250c~~ | ~~2Ti~~ | ❌ DELETED (duplicate of slushmedia) |
| monitoring | grafana | pvc-cf24cd5d-a0a1-4f85-a875-12ac2955ce3b | 10Gi | pending |
| monitoring | prometheus-...stack-0 | pvc-0441506b-d5d1-4ba1-9f7b-8d07033d9900 | 25Gi | pending |
| monitoring | alertmanager-...stack-0 | pvc-017db479-1cf9-4491-98e4-f44cdcdb971d | 8Gi | pending |
| monitoring | alertmanager-...stack-1 | pvc-a7f8316f-af4d-45af-ae46-e61b1760f6c0 | 8Gi | pending |
| monitoring | alertmanager-...stack-2 | pvc-b7b3403a-91eb-424d-b50b-343577f07722 | 8Gi | pending |
| backups | syncthing-config | pvc-665fdbec-bbe5-46b4-9d2e-7f4604fcb376 | 10Gi | pending |
| boundcorp-dev | buildcache-pvc | pvc-f02a0f63-dbcc-4b22-9ca2-437409f22a13 | 50Gi | pending |
| ~~database~~ | ~~postgres-postgres-lpqc-pgdata~~ | ~~pvc-6c5c84e7-1b52-47bf-94bf-236b518cade8~~ | ~~20Gi~~ | ✅ **MIGRATED** (S3 restore to Titan) |
| devbox | devbox-home-pvc | pvc-0ea9bcfb-1cf4-4931-86ac-579a7808eece | 80Gi | pending (NOT staged) |

### nfs-iota-hdd-slush PVCs (migrate to nfs-titan-hdd later)

| Namespace | PVC Name | Old PV | Size |
|-----------|----------|--------|------|
| backups | leeward-backups | pvc-405b0d15-4402-45a7-b147-d161f010deab | 2Ti |
| media | slushmedia-data | pvc-09b2d146-4522-4d3f-8f1d-82293fc45e15 | 10Ti |

### Manifest Files to Update

**PVC files (direct storageClass change):**
- `kubernetes/apps/home/home-assistant/app/config-pvc.yaml`
- `kubernetes/apps/home/zwave-js-ui/app/config-pvc.yaml`
- `kubernetes/apps/home/mosquitto/app/config-pvc.yaml`
- `kubernetes/apps/home/esphome/config-pvc.yaml`
- `kubernetes/apps/home/frigate/app/config-pvc.yaml`
- `kubernetes/apps/home/frigate/app/media-pvc.yaml`
- `kubernetes/apps/home/recipes/app/data-pvc.yaml`
- `kubernetes/apps/backups/syncthing/pvc.yaml`
- `kubernetes/apps/media/mediamega-pvc.yaml`

**HelmRelease files (persistence.storageClass change):**
- `kubernetes/apps/media/jellyfin/app/helmrelease.yaml`
- `kubernetes/apps/media/sonarr/app/helmrelease.yaml`
- `kubernetes/apps/media/radarr/app/helmrelease.yaml`
- `kubernetes/apps/media/sabnzbd/app/helmrelease.yaml`
- `kubernetes/apps/media/plex/app/helmrelease.yaml`
- `kubernetes/apps/monitoring/grafana/app/helmrelease.yaml`
- `kubernetes/apps/monitoring/loki/app/helm-release.yaml`
- `kubernetes/apps/networking/adguard/app/helm-release.yaml`

**Operator-managed (special handling):**
- `kubernetes/apps/database/crunchy/cluster.yaml` - Crunchy Postgres
- `kubernetes/apps/monitoring/kube-prometheus-stack/app/helmrelease.yaml` - Prometheus/Alertmanager
