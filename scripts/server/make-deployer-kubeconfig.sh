#!/usr/bin/env bash
# Write the kubeconfig for the lab's runner user from the lab-deployer ServiceAccount token
# (k8s/cluster/deployer.yaml). Run by the server owner, after applying deployer.yaml.
#
#   scripts/server/make-deployer-kubeconfig.sh [runner-user]     # default user: ailab-ci
set -euo pipefail
user="${1:-ailab-ci}"
home="$(getent passwd "$user" | cut -d: -f6)"
[[ -n "$home" ]] || { echo "user $user does not exist" >&2; exit 1; }
umask 077
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
token="$(kubectl -n ai-lab get secret lab-deployer-token -o jsonpath='{.data.token}' | base64 -d)"
ca="$(kubectl -n ai-lab get secret lab-deployer-token -o jsonpath='{.data.ca\.crt}')"
[[ -n "$token" && -n "$ca" ]] || { echo "token not populated yet; wait a few seconds and retry" >&2; exit 1; }
# Written with a heredoc, so the token never appears on a command line (process listings).
cat > "$tmp" <<KUBECONFIG
apiVersion: v1
kind: Config
clusters:
  - name: k3s
    cluster:
      server: https://127.0.0.1:6443
      certificate-authority-data: ${ca}
users:
  - name: lab-deployer
    user:
      token: ${token}
contexts:
  - name: lab
    context: { cluster: k3s, user: lab-deployer, namespace: ai-lab-staging }
current-context: lab
KUBECONFIG
sudo install -d -o "$user" -g "$user" -m 0700 "$home/.kube"
sudo install -o "$user" -g "$user" -m 0600 "$tmp" "$home/.kube/lab-deployer.conf"
echo "wrote $home/.kube/lab-deployer.conf (owner $user, mode 0600)"
