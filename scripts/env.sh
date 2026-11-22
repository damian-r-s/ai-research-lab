#!/usr/bin/env bash
# Source this file: `source scripts/env.sh`. Puts the user-space toolchain (micromamba env
# "ai-lab": Python 3.13, PostgreSQL 17 + pgvector, Node 22) on PATH. No root required.
LAB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAB_CONDA_ENV="${LAB_CONDA_ENV:-$HOME/micromamba/envs/ai-lab}"
if [[ ! -x "$LAB_CONDA_ENV/bin/python" ]]; then
  echo "Missing toolchain at $LAB_CONDA_ENV. Create it with:" >&2
  echo "  micromamba create -n ai-lab -c conda-forge python=3.13 'postgresql>=17,<18' pgvector 'nodejs>=22,<23'" >&2
  return 1 2>/dev/null || exit 1
fi
export LAB_ROOT LAB_CONDA_ENV
export PATH="$LAB_ROOT/.venv/bin:$LAB_CONDA_ENV/bin:$PATH"
export LAB_ENV="${LAB_ENV:-dev}"
export LAB_PGDATA="${LAB_PGDATA:-$LAB_ROOT/var/pgdata}"
export LAB_PGHOST="${LAB_PGHOST:-$LAB_ROOT/var/run}"
export LAB_PGPORT="${LAB_PGPORT:-55432}"
# Dev database URLs (local socket, trust auth → no password involved).
export LAB_DATABASE__URL="${LAB_DATABASE__URL:-postgresql+psycopg://lab_app@/lab?host=$LAB_PGHOST&port=$LAB_PGPORT}"
export LAB_DATABASE__MIGRATION_URL="${LAB_DATABASE__MIGRATION_URL:-postgresql+psycopg://lab_owner@/lab?host=$LAB_PGHOST&port=$LAB_PGPORT}"
export LAB_DATABASE__GRAPH_URL="${LAB_DATABASE__GRAPH_URL:-postgresql+psycopg://lab_graph@/lab?host=$LAB_PGHOST&port=$LAB_PGPORT}"
# Superuser connection used ONLY by the test suite to create and drop throwaway databases.
export LAB_TEST_ADMIN_URL="${LAB_TEST_ADMIN_URL:-postgresql://lab_admin@/postgres?host=$LAB_PGHOST&port=$LAB_PGPORT}"
