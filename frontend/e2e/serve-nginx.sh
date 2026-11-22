#!/usr/bin/env bash
# Runs the PRODUCTION nginx template locally (same file the container uses), serving dist/
# and proxying /api to the e2e backend. Started by Playwright; stops with it.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
run="$here/.run"
mkdir -p "$run/logs" "$run/var/log/nginx"  # the latter: nginx's compiled-in default, used before the config loads
export LAB_LISTEN_PORT="${LAB_LISTEN_PORT:-4790}"
export LAB_STATIC_ROOT="$(cd "$here/../dist" && pwd)"
export LAB_API_UPSTREAM="${LAB_API_UPSTREAM:-http://127.0.0.1:8790}"
# envsubst limited to LAB_* variables, like NGINX_ENVSUBST_FILTER=^LAB_ in the image.
python3 - "$here/../nginx.conf.template" "$run/server.conf" <<'PY'
import os, re, sys
src = open(sys.argv[1]).read()
out = re.sub(r"\$\{(LAB_[A-Z_]+)\}", lambda m: os.environ[m.group(1)], src)
open(sys.argv[2], "w").write(out)
PY
cat > "$run/nginx.conf" <<CONF
worker_processes 1;
pid $run/nginx.pid;
error_log $run/logs/error.log warn;
events { worker_connections 256; }
http {
    include $(dirname "$(command -v nginx)")/../etc/nginx/mime.types;
    access_log $run/logs/access.log;
    client_body_temp_path $run/client_body;
    proxy_temp_path $run/proxy;
    fastcgi_temp_path $run/fastcgi;
    uwsgi_temp_path $run/uwsgi;
    scgi_temp_path $run/scgi;
    include $run/server.conf;
}
CONF
nginx -t -p "$run" -c "$run/nginx.conf"
exec nginx -p "$run" -c "$run/nginx.conf" -g "daemon off;"
