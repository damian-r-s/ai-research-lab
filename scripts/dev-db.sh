#!/usr/bin/env bash
# Local development PostgreSQL cluster (user space, unix socket + 127.0.0.1 only).
# Usage: scripts/dev-db.sh init|start|stop|status|psql
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
LOG="$LAB_ROOT/var/log/postgres.log"
mkdir -p "$LAB_PGHOST" "$(dirname "$LOG")"

case "${1:-}" in
  init)
    if [[ -f "$LAB_PGDATA/PG_VERSION" ]]; then echo "Cluster already initialised at $LAB_PGDATA"; exit 0; fi
    initdb -D "$LAB_PGDATA" --username=lab_admin --auth-local=trust --auth-host=scram-sha-256 \
           --encoding=UTF8 --locale=C.UTF-8 >/dev/null
    cat >> "$LAB_PGDATA/postgresql.conf" <<CONF
listen_addresses = '127.0.0.1'
port = $LAB_PGPORT
unix_socket_directories = '$LAB_PGHOST'
timezone = 'UTC'
CONF
    echo "Initialised cluster at $LAB_PGDATA (superuser: lab_admin, local socket trust only)"
    ;;
  start)
    pg_ctl -D "$LAB_PGDATA" -l "$LOG" -w start
    psql -h "$LAB_PGHOST" -p "$LAB_PGPORT" -U lab_admin -d postgres -tAc \
      "SELECT 1 FROM pg_database WHERE datname='lab'" | grep -q 1 || \
      createdb -h "$LAB_PGHOST" -p "$LAB_PGPORT" -U lab_admin lab
    # Least-privilege roles: lab_owner owns the schemas and runs migrations,
    # lab_app is what the running application uses (DML only, no DDL),
    # lab_graph is the LangGraph checkpointer, confined to schema langgraph (DECISIONS C-6).
    psql -q -h "$LAB_PGHOST" -p "$LAB_PGPORT" -U lab_admin -d lab -v ON_ERROR_STOP=1 <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='lab_owner') THEN CREATE ROLE lab_owner LOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='lab_app')   THEN CREATE ROLE lab_app LOGIN;   END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='lab_graph') THEN CREATE ROLE lab_graph LOGIN; END IF;
END \$\$;
GRANT CREATE ON DATABASE lab TO lab_owner;
GRANT CONNECT ON DATABASE lab TO lab_app;
GRANT CONNECT ON DATABASE lab TO lab_graph;
SQL
    ;;
  stop)   pg_ctl -D "$LAB_PGDATA" -m fast stop ;;
  status) pg_ctl -D "$LAB_PGDATA" status ;;
  psql)   shift; exec psql -h "$LAB_PGHOST" -p "$LAB_PGPORT" -U lab_admin -d lab "$@" ;;
  *) echo "usage: $0 init|start|stop|status|psql" >&2; exit 2 ;;
esac
