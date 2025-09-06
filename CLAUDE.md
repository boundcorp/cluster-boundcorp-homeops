# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

This is a GitOps repository for managing a Kubernetes cluster using Flux CD. The cluster runs on k3s and uses a variety of cloud-native tools for networking, storage, monitoring, and application deployment.

## CRITICAL: GitOps Workflow

**THIS IS A GITOPS REPOSITORY - All changes MUST go through Git!**

### The Golden Rule
- **NEVER** run `kubectl apply`, `helm install`, or `helm upgrade` directly
- **NEVER** manually edit resources in the cluster
- **ALWAYS** make changes by editing files in this repository, committing, and pushing
- Wait 30-60 seconds for Flux to automatically reconcile and apply your changes

### Why This Matters
1. Manual changes will be **overwritten** by Flux on the next reconciliation
2. The Git repository is the single source of truth
3. All changes are tracked, versioned, and reversible through Git
4. Manual changes break the GitOps workflow and can cause configuration drift

### Correct Workflow for Changes
```bash
# 1. Make your changes to the YAML files
vim kubernetes/apps/example/deployment.yaml

# 2. Commit and push the changes
git add -A
git commit -m "feat: update example deployment"
git push

# 3. Wait for Flux to reconcile (usually 30-60 seconds)
# Or force immediate reconciliation:
task cluster:reconcile
```

### EXCEPTIONS - When Manual Commands Might Be Needed
If you absolutely need to run manual commands, **ALWAYS verify with the user first** by:
1. Explaining exactly what command you want to run
2. Why it's necessary to bypass GitOps
3. What the impact will be
4. How to restore GitOps control afterward

Valid exceptions might include:
- Emergency debugging with `kubectl logs` or `kubectl describe` (read-only)
- Testing a configuration before committing (must be followed by proper GitOps commit)
- Initial cluster bootstrap operations
- Disaster recovery scenarios

**If you're about to run kubectl apply or helm install, STOP and reconsider!**

## Common Commands

### Cluster Management
```bash
# View cluster status and resources
task cluster:nodes          # List all nodes
task cluster:pods           # List all pods across namespaces
task cluster:resources      # Gather all common resources (comprehensive status)
task cluster:helmreleases   # List all Helm releases
task cluster:kustomizations # List all Flux kustomizations

# Flux GitOps operations
task cluster:verify         # Verify Flux prerequisites
task cluster:install        # Install Flux into the cluster
task cluster:reconcile      # Force Flux to pull latest changes from Git
task cluster:hr-restart     # Restart failed Helm releases

# Check Flux sync status
flux get sources git -A
flux get sources oci -A
flux get ks -A
flux get hr -A
```

### Ansible Operations
```bash
# Node management
task ansible:deps          # Install Ansible dependencies
task ansible:list          # List all hosts
task ansible:ping          # Test connectivity to all nodes
task ansible:prepare       # Prepare nodes for k3s
task ansible:install       # Install k3s on nodes
task ansible:nuke          # Destroy k3s cluster
task ansible:rollout-update # Perform OS updates with rolling restart
```

### Working with Secrets
```bash
# Decrypt secrets for viewing (DO NOT commit decrypted files)
sops --decrypt kubernetes/flux/vars/cluster-secrets.sops.yaml

# Edit encrypted secrets
sops kubernetes/flux/vars/cluster-secrets.sops.yaml

# Encrypt a new secret file
sops --encrypt --in-place new-secret.sops.yaml
```

### Development Workflow
```bash
# Initialize configuration (first time setup)
task init
task configure

# After making changes to manifests
git add -A
git commit -m "feat: description of changes"
git push

# Force immediate reconciliation
task cluster:reconcile
```

## Architecture

### Directory Structure
- `/kubernetes/` - All Kubernetes manifests managed by Flux
  - `/bootstrap/` - Flux bootstrap configuration (not managed by Flux)
  - `/flux/` - Flux configuration and repositories
    - `/config/` - Main cluster kustomization
    - `/repositories/` - Helm and Git repository definitions
    - `/vars/` - Cluster settings and secrets
  - `/apps/` - Application deployments organized by namespace
    - Each app typically contains: `kustomization.yaml`, `helmrelease.yaml`, and optional configs

- `/ansible/` - Ansible playbooks for cluster provisioning
  - `/inventory/` - Host definitions
  - `/playbooks/` - Cluster lifecycle management playbooks

- `/.taskfiles/` - Task definitions for common operations

### Key Technologies

**Core Infrastructure:**
- **k3s** - Lightweight Kubernetes distribution
- **Flux CD** - GitOps operator managing deployments from this Git repository
- **Cilium** - CNI providing networking and security policies
- **cert-manager** - Automated TLS certificate management
- **SOPS + Age** - Secret encryption in Git

**Storage:**
- **local-path-provisioner** - Default storage class
- **NFS storage classes** - For persistent volumes (nfs-nova-nvme)

**Networking:**
- **ingress-nginx** - Ingress controller
- **external-dns** - Manages DNS records in Cloudflare
- **Cloudflare Tunnel** - Secure exposure of services to internet
- **k8s_gateway** - Internal DNS resolution

**Database:**
- **Crunchy Postgres Operator** - PostgreSQL clusters with pgvector support
- Database namespace contains shared PostgreSQL instance
- Automatic backups to S3 (AWS eu-north-1)

**Monitoring Stack:**
- **kube-prometheus-stack** - Prometheus, AlertManager, and exporters
- **Grafana** - Metrics visualization
- **Loki + Vector** - Log aggregation and shipping

## Important Patterns

### Adding New Applications
1. Create namespace directory under `/kubernetes/apps/`
2. Create `namespace.yaml`, `kustomization.yaml`, and app manifests
3. Add the namespace to `/kubernetes/apps/kustomization.yaml`
4. Secrets should use SOPS encryption (`.sops.yaml` extension)

### Database Access
- Shared Crunchy Postgres cluster in `database` namespace
- Connection secrets auto-generated as `postgres-pguser-<username>`
- pgvector extension enabled for AI/ML workloads
- Use pgBouncer service for connection pooling

### Flux Kustomization Hierarchy
- Cluster level: `/kubernetes/flux/config/cluster.yaml`
- Apps level: `/kubernetes/apps/kustomization.yaml`
- Individual apps: `/kubernetes/apps/<namespace>/kustomization.yaml`

### Secret Management
- All secrets must be encrypted with SOPS before committing
- Age key location: `~/.config/sops/age/keys.txt`
- Encrypted files use `.sops.yaml` extension
- Path regex in `.sops.yaml` determines encryption rules

## Troubleshooting

### Debug Application Issues
```bash
# Check Flux sync status
flux get ks -A
flux get hr -A

# View pod status
kubectl get pods -n <namespace>
kubectl describe pod <pod-name> -n <namespace>
kubectl logs <pod-name> -n <namespace> -f

# Check events
kubectl get events -n <namespace> --sort-by='.metadata.creationTimestamp'

# Restart failed Helm releases
task cluster:hr-restart
```

### Force Reconciliation
```bash
# Specific kustomization
flux reconcile ks <name> -n <namespace> --with-source

# Specific Helm release
flux reconcile hr <name> -n <namespace>

# Everything
task cluster:reconcile
```

## Environment Variables
- `KUBECONFIG` - Set to `./kubeconfig` via direnv
- `SOPS_AGE_KEY_FILE` - Age key for SOPS decryption

## Network Configuration
- Cluster network: 10.20.30.0/24
- LoadBalancer IPs managed by Cilium L2 announcements
- External DNS: postgres.boundcorp.net points to cluster
- Internal services use *.boundcorp.net domain
