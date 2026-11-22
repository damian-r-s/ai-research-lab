# k3s deployment

Kustomize layout (rendered with `kubectl kustomize`, which is built into kubectl):

| Path | What | Applied by |
|---|---|---|
| `cluster/` | the two namespaces, the `ai-lab-local` StorageClass and the static volumes on `/srv/ai-lab` | the server owner, **once** |
| `base/` | PostgreSQL, API (with `migrate` init container), dashboard, nightly backup, network policies, quota | `scripts/deploy.sh` |
| `overlays/staging`, `overlays/prod` | namespace, sizes, quota and dashboard ClusterIP per environment | `scripts/deploy.sh` |

Never apply `base/` or an overlay by hand. `scripts/deploy.sh` sets the image tags to the exact git
commit, which the manifests leave as `UNSET` on purpose. Preview what would be applied:

```bash
scripts/deploy.sh render staging <git-sha>
```

The full procedure is in [docs/operations/SERVER_SETUP.md](../docs/operations/SERVER_SETUP.md).
Policy checks on the rendered manifests run with the test suite (`test/test_k8s_manifests.py`):
lab namespace only, no NodePorts, non-root, pinned images, default-deny networking, quota fits.
