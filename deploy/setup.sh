#!/usr/bin/env bash
# Prepares a fresh Ubuntu 24.04 Vultr Cloud Compute instance. Run as root from the repository root:
#   DOMAIN=203.0.113.10.sslip.io bash deploy/setup.sh
set -euo pipefail

: "${DOMAIN:?Set DOMAIN to the public hostname, for example <ip>.sslip.io}"
APP_DIR=/opt/crossexam
DATA_DIR=/var/lib/crossexam

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q docker.io python3-venv python3-pip caddy curl gnupg ufw

# gVisor gives each document sandbox its own user-space kernel.
curl -fsSL https://gvisor.dev/archive.key | gpg --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main" \
  > /etc/apt/sources.list.d/gvisor.list
apt-get update -q
apt-get install -y -q runsc
runsc install
systemctl restart docker

id crossexam >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin crossexam
usermod -aG docker crossexam
mkdir -p "$APP_DIR" "$DATA_DIR"
cp -r app bench sandbox web requirements.txt "$APP_DIR"/
chown -R crossexam:crossexam "$APP_DIR" "$DATA_DIR"

python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
docker build -q -t crossexam-parser:latest sandbox

if [ ! -f /etc/crossexam.env ]; then
  cat > /etc/crossexam.env <<EOF
CROSSEXAM_DATA=$DATA_DIR
SANDBOX_MODE=gvisor
VULTR_INFERENCE_KEY=
EOF
  chmod 600 /etc/crossexam.env
fi

cp deploy/crossexam.service /etc/systemd/system/crossexam.service
sed "s/__DOMAIN__/$DOMAIN/" deploy/Caddyfile > /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable --now crossexam
systemctl restart caddy

ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "Deployed. Add the inference key to /etc/crossexam.env, then: systemctl restart crossexam"
