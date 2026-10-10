#!/usr/bin/env bash
# Idempotent installer for Ubuntu 22.04/24.04. Run as root from the project folder:  sudo bash deploy/install.sh
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo"; exit 1; }
SRC="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/crespoqa

apt-get update -y
apt-get install -y python3 python3-venv python3-pip rsync poppler-utils fontconfig fonts-liberation fonts-crosextra-carlito
# LibreOffice computes the contents page numbers (needs the metric-compatible fonts above, or numbers drift):
apt-get install -y --no-install-recommends libreoffice-writer
install -m 644 "$SRC/deploy/fonts.conf" /etc/fonts/local.conf
fc-cache -f

id crespoqa >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin crespoqa
mkdir -p "$APP" /var/lib/crespoqa /var/log/crespoqa /etc/crespoqa
rsync -a --delete --exclude '.venv' --exclude '.env' --exclude 'data' --exclude 'logs' --exclude '__pycache__' \
      --exclude 'out_test*' --exclude '*.docx' "$SRC"/ "$APP"/
python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install --quiet --upgrade pip
"$APP/.venv/bin/pip" install --quiet -r "$APP/requirements.txt"

if [ ! -f /etc/crespoqa/crespoqa.env ]; then
cat > /etc/crespoqa/crespoqa.env <<'ENV'
CRESPOQA_HOST=127.0.0.1
CRESPOQA_PORT=8000
CRESPOQA_DB=/var/lib/crespoqa/crespoqa.db
CRESPOQA_TEMP_DIR=/var/lib/crespoqa/tmp
CRESPOQA_LOG_DIR=/var/log/crespoqa
CRESPOQA_AUTH=on
CRESPOQA_COOKIE_SECURE=true
CRESPOQA_WORKERS=1
CRESPOQA_TTL_MINUTES=30
CRESPOQA_MAX_FILE_MB=50
# --- optional local AI review ---
AI_ENABLED=false
AI_BASE_URL=http://localhost:11434/v1
AI_MODEL=
AI_TIMEOUT_S=300
ENV
chmod 640 /etc/crespoqa/crespoqa.env
chgrp crespoqa /etc/crespoqa/crespoqa.env
echo "Created /etc/crespoqa/crespoqa.env: edit AI_* if you installed Ollama."
fi

chown -R crespoqa:crespoqa /var/lib/crespoqa /var/log/crespoqa
chown -R root:crespoqa "$APP"; chmod -R g+rX,o-rwx "$APP"
install -m 644 "$APP/deploy/crespoqa.service" /etc/systemd/system/crespoqa.service
systemctl daemon-reload
systemctl enable crespoqa
systemctl restart crespoqa   # also on updates, so new code is actually running
sleep 2
curl -fsS http://127.0.0.1:8000/healthz && echo " <- healthy"
echo
echo "Next: create the first editor account:"
echo "  sudo -u crespoqa env \$(grep -v '^#' /etc/crespoqa/crespoqa.env | xargs) $APP/.venv/bin/python -m crespoqa user add <name> --role editor"
echo "Then configure nginx with deploy/nginx-crespoqa.conf (HTTPS)."
