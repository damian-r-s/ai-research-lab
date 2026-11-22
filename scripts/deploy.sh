#!/usr/bin/env bash
# Deploy the lab to the k3s server (same build path as the sibling project: podman → k3s import).
#
#   scripts/deploy.sh render <staging|prod> <tag>      print the manifests (no cluster access)
#   scripts/deploy.sh deploy <staging|prod> [git-ref]  build, import, apply, verify (on the server)
#
# Safety rules:
# - Builds from `git archive <sha>`: exactly the committed code, never local edits.
# - prod only accepts a commit that was deployed AND verified on staging first.
# - prod takes a database backup before applying anything (migrations may run).
# - Touches only namespaces ai-lab / ai-lab-staging and images named localhost/ai-lab-*.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE="${XDG_STATE_HOME:-$HOME/.local/state}/ai-lab"
KEEP_IMAGES=3
IMAGES=(ai-lab-api ai-lab-frontend)

die() { echo "error: $*" >&2; exit 1; }
log() { echo "[$(date -u +%H:%M:%SZ)] $*"; }

namespace_for() {
  case "$1" in
    staging) echo ai-lab-staging ;;
    prod) echo ai-lab ;;
    *) die "environment must be 'staging' or 'prod', got '$1'" ;;
  esac
}

render() {  # render <env> <tag>
  local env="$1" tag="$2" dir
  namespace_for "$env" >/dev/null
  [[ "$tag" =~ ^[0-9a-f]{7,40}$ ]] || die "tag must be a git SHA, got '$tag'"
  dir="$(mktemp -d "$ROOT/k8s/.render-XXXXXX")"
  trap 'rm -rf "$dir"' RETURN
  cat > "$dir/kustomization.yaml" <<EOF
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ../overlays/$env
images:
  - { name: localhost/ai-lab-api, newTag: "$tag" }
  - { name: localhost/ai-lab-frontend, newTag: "$tag" }
EOF
  kubectl kustomize "$dir"
}

preflight() {  # preflight <env>
  local ns="$1"
  for tool in git kubectl podman k3s; do command -v "$tool" >/dev/null || die "$tool not found"; done
  kubectl get namespace "$ns" >/dev/null 2>&1 \
    || die "namespace $ns does not exist; follow docs/operations/SERVER_SETUP.md first"
  kubectl -n "$ns" get secret lab-secrets >/dev/null 2>&1 \
    || die "secret lab-secrets missing in $ns; run scripts/server/create-secrets.sh first"
  kubectl get storageclass ai-lab-local >/dev/null 2>&1 \
    || die "StorageClass ai-lab-local missing; apply k8s/cluster/storage.yaml first"
}

build_and_import() {  # build_and_import <sha>
  local sha="$1" src
  src="$(mktemp -d)"
  trap 'rm -rf "$src"' RETURN
  git -C "$ROOT" archive --format=tar "$sha" | tar -x -C "$src"
  log "building localhost/ai-lab-api:$sha"
  podman build --quiet -t "localhost/ai-lab-api:$sha" "$src" >/dev/null
  log "building localhost/ai-lab-frontend:$sha"
  podman build --quiet -t "localhost/ai-lab-frontend:$sha" "$src/frontend" >/dev/null
  for image in "${IMAGES[@]}"; do
    log "importing localhost/$image:$sha into k3s"
    podman save "localhost/$image:$sha" | sudo k3s ctr -n k8s.io images import - >/dev/null
  done
}

predeploy_backup() {  # predeploy_backup <ns> <sha>
  local ns="$1" job="predeploy-${2:0:12}"
  if ! kubectl -n "$ns" get statefulset postgres >/dev/null 2>&1; then
    log "first deployment in $ns: no database to back up yet"
    return
  fi
  log "pre-deploy backup ($job)"
  kubectl -n "$ns" delete job "$job" --ignore-not-found >/dev/null
  kubectl -n "$ns" create job "$job" --from=cronjob/postgres-backup >/dev/null
  kubectl -n "$ns" wait --for=condition=complete "job/$job" --timeout=600s \
    || die "pre-deploy backup failed; nothing was changed (see: kubectl -n $ns logs job/$job)"
}

verify() {  # verify <ns>
  local ns="$1"
  log "waiting for rollout"
  kubectl -n "$ns" rollout status statefulset/postgres --timeout=300s
  kubectl -n "$ns" rollout status deployment/api --timeout=300s
  kubectl -n "$ns" rollout status deployment/frontend --timeout=300s
  log "checking readiness from inside the api pod"
  kubectl -n "$ns" exec deploy/api -c api -- python -c "
import json, urllib.request
body = json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/health/ready', timeout=10))
assert body['status'] == 'ready', body
print('ready:', body)"
}

prune_images() {  # keep the newest KEEP_IMAGES tags of each lab image
  for image in "${IMAGES[@]}"; do
    # Newest first by creation time; only full-SHA tags (never touches anything else).
    podman images --format '{{.CreatedAt}}|{{.Tag}}' "localhost/$image" | sort -r | cut -d'|' -f2 \
      | grep -E '^[0-9a-f]{40}$' | tail -n +"$((KEEP_IMAGES + 1))" | while read -r old; do
        log "pruning localhost/$image:$old"
        podman rmi "localhost/$image:$old" >/dev/null || true
        sudo k3s ctr -n k8s.io images rm "localhost/$image:$old" >/dev/null 2>&1 || true
      done
  done
}

deploy() {  # deploy <env> [git-ref]
  local env="$1" ref="${2:-origin/main}" ns sha
  ns="$(namespace_for "$env")"
  mkdir -p "$STATE/verified-on-staging" "$STATE/logs"
  exec > >(tee -a "$STATE/logs/deploy-$(date -u +%Y%m%dT%H%M%SZ)-$env.log") 2>&1
  git -C "$ROOT" fetch --quiet origin
  sha="$(git -C "$ROOT" rev-parse --verify "$ref^{commit}")"
  log "deploying $sha to $env ($ns)"
  preflight "$ns"
  if [[ "$env" == prod && ! -f "$STATE/verified-on-staging/$sha" ]]; then
    die "$sha has not been verified on staging; run: scripts/deploy.sh deploy staging $sha"
  fi
  build_and_import "$sha"
  [[ "$env" == prod ]] && predeploy_backup "$ns" "$sha"
  log "applying manifests"
  render "$env" "$sha" | kubectl apply -f -
  verify "$ns"
  [[ "$env" == staging ]] && touch "$STATE/verified-on-staging/$sha"
  prune_images
  log "done: $sha is live in $env"
}

case "${1:-}" in
  render) [[ $# -eq 3 ]] || die "usage: $0 render <staging|prod> <tag>"; render "$2" "$3" ;;
  deploy) [[ $# -ge 2 && $# -le 3 ]] || die "usage: $0 deploy <staging|prod> [git-ref]"; deploy "$2" "${3:-}" ;;
  *) die "usage: $0 render <env> <tag> | deploy <env> [git-ref]" ;;
esac
