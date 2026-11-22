# Server Setup and First Staging Deployment — `alpha`

**Audience:** the server owner. **Status:** ready for review; nothing has been run yet.

Every command below runs **on the server**, in order. Steps marked **(server change)** modify the
host or the cluster and need your explicit decision. No step touches the `default` namespace, the
other project's images, its ports or the host Ollama. Steps 1 and 9 prove that.

Run steps 0–8 for `staging` now. Production (step 10) follows only after staging passes.

---

## 0. Get the code onto the server
The repository is private, so the server needs read access to GitHub. If `git clone` fails with
"Permission denied", add a read-only **deploy key**: create it with `ssh-keygen -t ed25519 -f
~/.ssh/ai-research-lab`, then add the `.pub` file in GitHub → repository → Settings → Deploy keys.
```bash
mkdir -p ~/deployments
git clone git@github.com:damian-r-s/ai-research-lab.git ~/deployments/ai-research-lab
cd ~/deployments/ai-research-lab
```

## 1. Inventory of everything that is not the lab (read-only)
```bash
scripts/server/inventory.sh > ~/ai-lab-inventory-before.txt
```

## 2. Dedicated storage volume (server change, DECISIONS D-17)
Creates a 300 GB logical volume from the 850 GB unallocated in `ubuntu-vg`. It can be grown online
later with `lvextend -r`.
```bash
sudo vgs ubuntu-vg                                   # check: VFree ≈ 850g
sudo lvcreate -L 300G -n ai-lab ubuntu-vg
sudo mkfs.ext4 -L ai-lab /dev/ubuntu-vg/ai-lab
sudo mkdir -p /srv/ai-lab
echo '/dev/ubuntu-vg/ai-lab /srv/ai-lab ext4 defaults,noatime 0 2' | sudo tee -a /etc/fstab
sudo systemctl daemon-reload && sudo mount /srv/ai-lab
for env in staging prod; do sudo mkdir -p /srv/ai-lab/$env/postgres /srv/ai-lab/$env/backups; done
sudo chown -R 999:999 /srv/ai-lab/staging /srv/ai-lab/prod   # UID of postgres in its image
sudo chmod 700 /srv/ai-lab/*/postgres
df -h /srv/ai-lab                                    # check: ~295G available
```

## 3. Namespaces, storage class and volumes (server change, cluster-scoped, once)
```bash
kubectl apply -f k8s/cluster/namespaces.yaml -f k8s/cluster/storage.yaml
kubectl get ns ai-lab ai-lab-staging && kubectl get pv -l app.kubernetes.io/part-of=ai-research-lab
```
Expected: two namespaces and four volumes in `Available` state (each pre-bound to one lab claim).

## 4. Database credentials for staging (once)
Generated on the server and stored only in a Kubernetes Secret. They are never printed or written
to the repository.
```bash
scripts/server/create-secrets.sh staging
```

## 5. Build and deploy to staging
Builds both images from the exact commit, imports them into k3s (asks for your sudo password),
applies the manifests, waits for the rollout and checks readiness from inside the API pod.
```bash
scripts/deploy.sh deploy staging
```
Expected: the last line is `done: <sha> is live in staging`. Logs are kept in `~/.local/state/ai-lab/logs/`.

## 6. Create your admin account (once per environment)
```bash
kubectl -n ai-lab-staging exec -it deploy/api -c api -- \
  python -m scripts.create_user --username damian --role admin
```

## 7. Dashboard access over Tailscale (server change)
1. In the Tailscale admin console (DNS page), make sure **MagicDNS** and **HTTPS certificates** are
   enabled.
2. Publish staging on the tailnet only (not the LAN). It proxies to the pinned ClusterIP of the
   staging dashboard:
   ```bash
   sudo tailscale serve --bg --https=8443 http://10.43.100.11:8080
   tailscale serve status
   ```
3. From the laptop open `https://alpha.<your-tailnet>.ts.net:8443` and sign in.

## 8. Verification (Phase 1 criteria 1.11, 1.12, 1.16–1.19)
| Check | Command | Pass when |
|---|---|---|
| Pods ready (1.16) | `kubectl -n ai-lab-staging get pods` | postgres, api and frontend are `Running` and ready |
| Quota respected (1.16) | `kubectl -n ai-lab-staging describe resourcequota lab-quota` | usage below the hard limits |
| Status on the server (1.11, 1.12) | dashboard → Lab status | GPU shows "No GPU in use" with a reason and no warning; database reachable; live updates connected |
| Real client IP behind the proxies | `kubectl -n ai-lab-staging exec statefulset/postgres -c postgres -- psql -U lab_admin -d lab -c "SELECT host(ip), attempted_at FROM lab.login_attempts ORDER BY attempted_at DESC LIMIT 3"` | your login shows your laptop's **tailnet address (100.x)**, not a 10.42.x address |
| Isolation (1.17) | `scripts/server/check-isolation.sh staging` | `ISOLATION CHECK PASSED` |
| Other project untouched (1.17) | `scripts/server/inventory.sh > ~/ai-lab-inventory-after.txt && diff ~/ai-lab-inventory-before.txt ~/ai-lab-inventory-after.txt` | no output |
| Backup and restore (1.18) | `scripts/server/restore-test.sh staging` | `RESTORE TEST PASSED` |
| Pod restarts (1.19) | `kubectl -n ai-lab-staging delete pod -l app.kubernetes.io/name=api` and then `... -l app.kubernetes.io/name=postgres` | both come back by themselves; you can still sign in |
| Server reboot (1.19) | reboot at a time you choose, then open the dashboard | the lab is back without manual steps; the audit log shows a new `api started` event |

Send me the output of the isolation check, the inventory diff, the restore test and anything that
did not pass. I'll record the results in IMPLEMENTATION_PLAN.md.

**One check may legitimately fail:** whether pods can reach services that listen on the **host
itself** (the host Ollama, port 30432, the k3s API). Some network-policy implementations always
allow pod-to-node traffic. If the isolation check reports them as reachable, that is a real finding:
the fix is a host firewall rule, which needs your decision.

## 9. Production (after staging has passed)
```bash
scripts/server/create-secrets.sh prod
scripts/deploy.sh deploy prod                       # only accepts a commit verified on staging
kubectl -n ai-lab exec -it deploy/api -c api -- python -m scripts.create_user --username damian --role admin
sudo tailscale serve --bg --https=443 http://10.43.100.10:8080
scripts/server/check-isolation.sh prod
```

## Rollback
Redeploy the previous commit, whose images are kept: `scripts/deploy.sh deploy staging <previous-sha>`.
Every production deploy takes a backup first (`kubectl -n ai-lab get jobs | grep predeploy`).
