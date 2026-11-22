#!/usr/bin/env bash
# Create the lab's database credentials as a Kubernetes Secret (run ONCE per environment, on the
# server). Passwords are generated here, written only to a private temporary directory that is
# removed afterwards, and never printed, committed or passed on a command line.
#
#   scripts/server/create-secrets.sh <staging|prod>
set -euo pipefail
case "${1:-}" in
  staging) ns=ai-lab-staging ;;
  prod) ns=ai-lab ;;
  *) echo "usage: $0 <staging|prod>" >&2; exit 2 ;;
esac
if kubectl -n "$ns" get secret lab-secrets >/dev/null 2>&1; then
  echo "lab-secrets already exists in $ns; refusing to overwrite (the database uses these passwords)" >&2
  exit 1
fi
umask 077
dir="$(mktemp -d)"
trap 'rm -rf "$dir"' EXIT
pw() { head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 40; }
pw > "$dir/superuser-password"
pw > "$dir/owner-password"
pw > "$dir/app-password"
printf 'postgresql+psycopg://lab_app:%s@postgres:5432/lab' "$(cat "$dir/app-password")" > "$dir/database__url"
printf 'postgresql+psycopg://lab_owner:%s@postgres:5432/lab' "$(cat "$dir/owner-password")" > "$dir/database__migration_url"
kubectl -n "$ns" create secret generic lab-secrets \
  --from-file=superuser-password="$dir/superuser-password" \
  --from-file=owner-password="$dir/owner-password" \
  --from-file=app-password="$dir/app-password" \
  --from-file=database__url="$dir/database__url" \
  --from-file=database__migration_url="$dir/database__migration_url" >/dev/null
kubectl -n "$ns" label secret lab-secrets app.kubernetes.io/part-of=ai-research-lab >/dev/null
echo "created secret lab-secrets in $ns (5 keys; values not shown)"
