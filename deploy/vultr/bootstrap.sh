#!/usr/bin/env bash
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run as root.' >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a
apt-get update
apt-get -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold upgrade -y
apt-get install -y ca-certificates curl git python3.12 python3.12-venv caddy unattended-upgrades

curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh
id refundops >/dev/null 2>&1 || useradd --system --user-group \
    --home-dir /var/lib/refundops --shell /usr/sbin/nologin refundops
usermod --lock refundops
install -d -o refundops -g refundops -m 0750 /opt/refundops /var/lib/refundops
install -d -o refundops -g refundops -m 0700 /var/lib/refundops/tmp
install -d -o root -g refundops -m 0750 /etc/refundops

# Bootstrap browser system libraries before the project virtualenv exists.
/usr/local/bin/uv tool run --python /usr/bin/python3.12 --from playwright playwright install-deps chromium

for host in portal.localhost tracking.localhost inbox.localhost store.localhost; do
    if ! getent hosts "$host" >/dev/null; then
        printf '127.0.0.1 %s\n' "$host" >> /etc/hosts
    fi
done

# OpenSSH takes the first value, so put our drop-in before cloud-init's file.
install -d -m 0755 /etc/ssh/sshd_config.d
printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin prohibit-password\n' \
    > /etc/ssh/sshd_config.d/00-refundops.conf
/usr/sbin/sshd -t
systemctl reload ssh
printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' \
    > /etc/apt/apt.conf.d/20auto-upgrades
systemctl enable --now caddy
echo 'Bootstrap complete. Reboot before deployment if /var/run/reboot-required exists.'
