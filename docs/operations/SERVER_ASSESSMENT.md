# Server Assessment — `alpha`

**Status:** Complete (snapshot and follow-up checks) · **Collected:** 2026-11-22 13:15 UTC
**Method:** read-only inspection script run by the owner on the server. No changes were made.

## 1. Verified Facts

### Hardware and OS
| Item | Value |
|---|---|
| Machine | **Dell Pro Max Tower T2 FCT2250, bare metal** (`systemd-detect-virt` = `none`); BIOS 1.18.1 (2026-08-04) |
| CPU | Intel Core Ultra 5 245K, 14 cores (6P + 8E), no SMT |
| RAM | 30 GiB usable (32 GB), **25 GiB available** at sample time; 8 GiB swap, unused |
| GPU | **No discrete GPU.** Intel Arrow Lake integrated graphics only; no NVIDIA driver or tooling |
| Disk | 1 TB NVMe (SK hynix) with LVM. **Root LV is only 98 GB, with 48 GB free; 850.8 GB is unallocated in the volume group** (§5) |
| OS | Ubuntu 26.04.1 LTS, kernel 7.0.0-31 |
| Uptime | 63 days. It already runs 24/7 |

### Kubernetes
| Item | Value |
|---|---|
| Distribution | **k3s v1.36.4+k3s1**, installed directly on the host (single node `alpha`, control plane), containerd 2.3.4 |
| Bundled components | Traefik 3.7.8 (LoadBalancer via klipper-lb on ports 80/443), CoreDNS, local-path provisioner (default StorageClass), metrics-server |
| metrics-server | **Not ready** (0/1, 9 restarts), so `kubectl top` does not work |
| Namespaces | only `default` plus the system namespaces |
| NetworkPolicies | none exist; k3s's embedded controller is enabled (default configuration, §5), so they will be enforced |
| Ingresses | none. Traefik is running but unused |
| Scheduled vs. capacity | requests 650m CPU (4%) and 1996 MiB memory (6%); limits 4138 MiB (13%) |

### The existing project (`agentic-trading-intelligence-platform`, namespace `default`)
| Item | Value |
|---|---|
| Workloads | Deployments `api`, `finbert`, `frontend`; StatefulSet `postgres` (postgres:16, 5 Gi PVC); CronJobs `auto-analysis` (3-hourly), `evaluation-worker` (hourly), `metrics-engine` (daily) |
| Exposure | NodePorts 30000 (frontend), 30080 (api), **30432 (PostgreSQL)** |
| Images | `localhost/agentic-trading-platform:local` (5.61 GB) and `…-frontend:local`, built with **podman** and imported into k3s containerd |
| CI/CD | A self-hosted GitHub Actions runner is **installed and running** as `actions.runner.damian-r-s-agentic-trading-intelligence-platform.alpha.service`, registered to that repository only. **There are no workflow files**: `origin/main` has no `.github/` (checked 2026-11-22 after `git fetch`). So the runner is idle and deployments are still manual |

### Other host services
| Service | Observation |
|---|---|
| Ollama 0.34.0 (systemd, host) | only `llama3.2:3b` present, none loaded; **listens on all interfaces (`*:11434`) without authentication** |
| Tailscale | running; node `alpha` 100.79.17.59; the development laptop is on the same tailnet |
| Firewall | `ufw` inactive |
| Listening on all interfaces | SSH (22), k3s API (6443), kubelet (10250), Ollama (11434), plus k3s NodePorts and Traefik via iptables |

## 2. Corrections to Earlier Assumptions

The earlier assumptions came from the sibling repository's documentation, not from the server.

| Earlier assumption | Reality |
|---|---|
| Linux VM under VMware Player | **Bare-metal Dell Pro Max Tower T2.** This is the machine described in the spec |
| "No CI/CD" | Partly right: the self-hosted runner **is installed**, but no workflows use it yet |
| "The colleague's VM" | **the server is the owner's own machine** (confirmed) |
| GPU could not be passed to a VM | no VM is involved; there is simply **no discrete GPU yet**. The tower has PCIe slots, so the GPU upgrade in spec §11 applies to this machine (compatibility checks from spec §11 still required) |

## 3. Observations Affecting the Existing Project (informational; nothing was changed)

1. **The node IP appears stale.** k3s registered the node as `192.168.1.160`, and Traefik's
   external IP and the other project's `OLLAMA_BASE_URL` both use `.160`. The host's interfaces
   now have **`.150` (ethernet) and `.158` (Wi-Fi)**, and no `.160`.
   The last three `auto-analysis` runs **failed** (75 min, 4 h 15 min and 7 h 15 min ago), while
   earlier runs succeeded. An unreachable Ollama at `.160` is a plausible cause, **not confirmed**.
   A DHCP reservation or a static IP for the server would prevent this.
2. `metrics-server` is not ready, which is why `kubectl top` fails.
3. The `api` pod has 96 restarts (the last 30 days ago).
4. PostgreSQL (30432) and Ollama (11434) are reachable from the whole LAN without a firewall.
5. Dangling podman images of 5.61 GB each use a significant part of the 48 GB free on root.

## 4. Preliminary Capacity Budget for the Lab

This is based on one snapshot (`kubectl top` is unavailable). Quotas are enforced by k3s and will be
adjusted from the lab's own resource monitoring once it runs.

| Resource | Node total | Reserved for OS, k3s, other project, host Ollama | Proposed `ai-lab` quota (limits) | `ai-lab-staging` |
|---|---|---|---|---|
| CPU | 14 cores | ~4 cores | 8 cores | 2 cores |
| Memory | 30 GiB | ~10 GiB (other project's limits of 4.1 GiB, host Ollama ~3 GiB while active, OS and k3s) | 16 GiB | 4 GiB |
| Disk | 48 GB free on root, 850 GB unallocated in LVM | — | dedicated LV, e.g. 300 GB (D-17) | from the same LV |

Inference is **CPU-only**. RAM allows mid-sized quantised models (roughly 7–14B parameters),
but generation will be slow. Phase 3 measures real throughput before roles are assigned to
models.

## 5. Follow-up Checks (2026-11-22)

| Question | Result |
|---|---|
| Ownership | **The server is the owner's own machine** (confirmed by the owner) |
| LVM free space | volume group `ubuntu-vg` is 950.8 GB, with **850.8 GB unallocated**; only `ubuntu-lv` (100 GB, root) exists |
| k3s configuration | started as plain `k3s server` with **no flags and no `/etc/rancher/k3s/config.yaml`**. So k3s defaults apply: the embedded NetworkPolicy controller is **enabled**, Traefik is enabled, flannel is the network, and the node IP was auto-detected at install time (hence the stale `.160`) |
| GitHub runner | runs as user **`damian`** from `/home/damian/deployments/agentic-trading-intelligence-platform/…`. That user has a cluster-admin kubeconfig (`~/.kube/config`), so any workflow of that repository effectively has full cluster access |
| Ollama | `OLLAMA_HOST=0.0.0.0` (all interfaces); everything else at defaults; runs as user `ollama` |
| Disk use | `/var/lib/rancher` 14 GB (containerd images and PVCs), rootless podman storage 5.9 GB |
| Kubeconfigs | `/etc/rancher/k3s/k3s.yaml` (root, 0600) and `~/.kube/config` (`damian`, 0600), both cluster-admin |
| 24 h load sample | **dropped** by owner decision; the snapshot shows ample headroom, and the lab's own resource monitoring (Phase 1) records real usage over time |

## 6. Conclusions for the Lab

1. **Storage:** create a dedicated logical volume for lab data from the 850 GB of free space
   (D-17), and do not grow the root filesystem with lab data.
2. **Isolation:** NetworkPolicies will be enforced, so namespace isolation from `default` is real.
3. **Ingress:** Traefik is available. Dashboard access will be restricted to the tailnet (U-9).
4. **CI/CD:** the lab's runner must not repeat the cluster-admin pattern. It gets its own user and
   a namespace-scoped kubeconfig (D-07b).
5. **Capacity:** the budget in §4 is used as the initial quota and revisited from the lab's own monitoring data.
