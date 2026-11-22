#!/usr/bin/env bash
# Restore the newest backup into a scratch database and verify it (criterion 1.18):
# the dump's checksum, the audit hash chain, and that no event older than the recorded chain head
# is missing. The scratch database is always dropped afterwards. Production data is not modified.
#
#   scripts/server/restore-test.sh <staging|prod>
set -euo pipefail
case "${1:-}" in
  staging) ns=ai-lab-staging ;;
  prod) ns=ai-lab ;;
  *) echo "usage: $0 <staging|prod>" >&2; exit 2 ;;
esac
pg() { kubectl -n "$ns" exec statefulset/postgres -c postgres -- "$@"; }
backup_pod() {  # a short-lived pod with the backups volume mounted, to read the files
  kubectl -n "$ns" get pods -l app.kubernetes.io/name=postgres-backup -o name | head -1
}

echo "running a fresh backup first"
job="restore-test-$(date -u +%Y%m%d%H%M%S)"
kubectl -n "$ns" create job "$job" --from=cronjob/postgres-backup >/dev/null
kubectl -n "$ns" wait --for=condition=complete "job/$job" --timeout=600s
latest="$(kubectl -n "$ns" logs "job/$job" | sed -n 's/^backup written: \(\/backups\/[^ ]*\.dump\).*/\1/p')"
head_seq="$(kubectl -n "$ns" logs "job/$job" | sed -n 's/.*audit head seq \([0-9]*\).*/\1/p')"
[[ -n "$latest" && -n "$head_seq" ]] || { echo "could not read backup result" >&2; exit 1; }
echo "newest backup: $latest (audit head seq $head_seq)"

# Copy the dump into the postgres pod via a temporary reader pod that mounts the backups claim.
reader="restore-reader-$$"
cleanup() {
  kubectl -n "$ns" delete pod "$reader" --ignore-not-found --wait=false >/dev/null 2>&1 || true
  pg psql -q -U lab_admin -d postgres -c "DROP DATABASE IF EXISTS lab_restore_test WITH (FORCE)" >/dev/null 2>&1 || true
  pg rm -f /tmp/restore.dump >/dev/null 2>&1 || true
}
trap cleanup EXIT
kubectl -n "$ns" run "$reader" --restart=Never --image=docker.io/pgvector/pgvector:0.8.7-pg17-trixie \
  --labels=app.kubernetes.io/name=postgres-backup \
  --overrides='{"spec":{"securityContext":{"runAsUser":999,"runAsNonRoot":true},"automountServiceAccountToken":false,"containers":[{"name":"r","image":"docker.io/pgvector/pgvector:0.8.7-pg17-trixie","command":["sleep","600"],"volumeMounts":[{"name":"b","mountPath":"/backups","readOnly":true}],"resources":{"requests":{"cpu":"10m","memory":"16Mi"},"limits":{"cpu":"200m","memory":"64Mi"}},"securityContext":{"allowPrivilegeEscalation":false,"readOnlyRootFilesystem":true,"capabilities":{"drop":["ALL"]}}}],"volumes":[{"name":"b","persistentVolumeClaim":{"claimName":"backups","readOnly":true}}]}}' >/dev/null
kubectl -n "$ns" wait --for=condition=ready "pod/$reader" --timeout=120s >/dev/null
kubectl -n "$ns" exec "$reader" -- sh -c "cd /backups && sha256sum -c $(basename "$latest").sha256"
kubectl -n "$ns" exec "$reader" -- cat "$latest" | pg sh -c 'cat > /tmp/restore.dump'

echo "restoring into lab_restore_test"
pg psql -q -U lab_admin -d postgres -c "DROP DATABASE IF EXISTS lab_restore_test WITH (FORCE)"
pg psql -q -U lab_admin -d postgres -c "CREATE DATABASE lab_restore_test OWNER lab_owner"
pg psql -q -U lab_admin -d postgres -c "GRANT CONNECT ON DATABASE lab_restore_test TO lab_app"
pg pg_restore -U lab_admin -d lab_restore_test --exit-on-error /tmp/restore.dump

restored_max="$(pg psql -U lab_admin -d lab_restore_test -Atc "SELECT coalesce(max(seq), 0) FROM lab.events")"
[[ "$restored_max" -ge "$head_seq" ]] \
  || { echo "FAILED: restored audit log ends at seq $restored_max, before the recorded head $head_seq" >&2; exit 1; }
echo "audit log restored up to seq $restored_max (recorded head $head_seq): no missing tail"

echo "verifying the hash chain of the restored copy"
kubectl -n "$ns" exec deploy/api -c api -- sh -c \
  'LAB_DATABASE__URL="$(sed "s#/lab\$#/lab_restore_test#" /run/secrets/lab/database__url)" python -m scripts.verify_events'
echo "RESTORE TEST PASSED"
