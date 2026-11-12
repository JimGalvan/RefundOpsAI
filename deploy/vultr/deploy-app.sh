#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run as root.' >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a
HOST=${1:?usage: deploy-app.sh <public-host>}
[[ $# -eq 1 && ${#HOST} -le 253 && "$HOST" =~ ^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?$ ]] || {
    echo 'Expected one DNS hostname, without a scheme or port.' >&2; exit 1;
}
test -s /root/refundops-src.tar
test -s /etc/refundops/refundops.env

# Fixed paths only. Keep one previous source tree for manual recovery.
rm -rf /opt/refundops.stage
install -d -o refundops -g refundops -m 0750 /opt/refundops.stage
tar -xf /root/refundops-src.tar -C /opt/refundops.stage \
    --exclude='.env' --exclude='.venv' --exclude='data' --exclude='staticfiles'
test -f /opt/refundops.stage/manage.py
test -f /opt/refundops.stage/uv.lock
test -f /opt/refundops.stage/deploy/vultr/production_settings.py
chown -R refundops:refundops /opt/refundops.stage
systemctl stop refundops.service 2>/dev/null || true
rm -rf /opt/refundops.previous
mv /opt/refundops /opt/refundops.previous
mv /opt/refundops.stage /opt/refundops
cd /opt/refundops
runuser -u refundops -- env HOME=/var/lib/refundops \
    /usr/local/bin/uv sync --frozen --no-dev --python /usr/bin/python3.12
runuser -u refundops -- env HOME=/var/lib/refundops \
    PLAYWRIGHT_BROWSERS_PATH=/var/lib/refundops/browsers \
    .venv/bin/playwright install chromium
# Use the same pinned Playwright version for any newly required system libraries.
.venv/bin/playwright install-deps chromium

# EnvironmentFile uses systemd syntax: never source it as shell code.
for action in migrate collectstatic seed_demo_sites; do
    args=()
    if [[ "$action" != seed_demo_sites ]]; then args+=(--noinput); fi
    systemd-run --quiet --wait --pipe --collect \
        --property=User=refundops --property=Group=refundops \
        --property=WorkingDirectory=/opt/refundops \
        --property=EnvironmentFile=/etc/refundops/refundops.env \
        --setenv=DJANGO_SETTINGS_MODULE=deploy.vultr.production_settings \
        --setenv=HOME=/var/lib/refundops \
        /opt/refundops/.venv/bin/python manage.py "$action" "${args[@]}"
done

# The running process cannot modify its source or dependencies.
chown -R root:refundops /opt/refundops
chmod -R u=rwX,g=rX,o= /opt/refundops
install -o root -g root -m 0644 deploy/vultr/refundops.service /etc/systemd/system/refundops.service
sed "s/PUBLIC_HOST/$HOST/g" deploy/vultr/Caddyfile > /etc/caddy/refundops.Caddyfile.new
caddy validate --config /etc/caddy/refundops.Caddyfile.new --adapter caddyfile
install -o root -g root -m 0644 /etc/caddy/refundops.Caddyfile.new /etc/caddy/Caddyfile
rm /etc/caddy/refundops.Caddyfile.new
systemctl daemon-reload
systemctl enable refundops.service
systemctl restart refundops.service
systemctl reload caddy
for ((attempt=1; attempt<=60; attempt++)); do
    if curl --fail --silent --max-time 2 -H "Host: $HOST" http://127.0.0.1:8000/health/ >/dev/null; then
        echo "Application healthy. Check HTTPS at https://$HOST/health/"
        exit 0
    fi
    sleep 2
done
echo 'Health check failed. Inspect systemctl status refundops and journalctl locally on the VM.' >&2
exit 1
