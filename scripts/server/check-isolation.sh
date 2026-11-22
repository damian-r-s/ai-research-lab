#!/usr/bin/env bash
# Prove the lab's network isolation from INSIDE the api pod (criterion 1.17).
# Expected: the lab's own database and DNS work; the other project, the host LAN and the internet
# are unreachable (Phase 1 has no internet egress at all).
#
#   scripts/server/check-isolation.sh <staging|prod>
set -euo pipefail
case "${1:-}" in
  staging) ns=ai-lab-staging ;;
  prod) ns=ai-lab ;;
  *) echo "usage: $0 <staging|prod>" >&2; exit 2 ;;
esac
node_ip="$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')"
kubectl -n "$ns" exec deploy/api -c api -- python - "$node_ip" <<'PY'
import socket, sys
node_ip = sys.argv[1]
checks = [
    # (description, host, port, expected_reachable)
    ("own database (positive control)", "postgres", 5432, True),
    ("other project's API Service", "api.default.svc.cluster.local", 8000, False),
    ("other project's PostgreSQL Service", "postgres.default.svc.cluster.local", 5432, False),
    ("other project's PostgreSQL NodePort on the host", node_ip, 30432, False),
    ("host Ollama", node_ip, 11434, False),
    ("k3s API server on the host", node_ip, 6443, False),
    ("internet (1.1.1.1:443)", "1.1.1.1", 443, False),
]
failed = 0
for desc, host, port, expected in checks:
    try:
        with socket.create_connection((host, port), timeout=3):
            reachable = True
    except OSError:
        reachable = False
    ok = reachable == expected
    failed += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {desc}: {'reachable' if reachable else 'blocked'}")
print("ISOLATION CHECK PASSED" if not failed else f"ISOLATION CHECK FAILED ({failed})")
sys.exit(1 if failed else 0)
PY
