#!/usr/bin/env bash
# Copy CrespoQA from this machine to the Ubuntu server and install it there.
#
#   deploy/transfer.sh user@server                      code + installer
#   deploy/transfer.sh user@server --with-templates     also your templates and their versions (never accounts)
#   deploy/transfer.sh user@server --with-ai            also install Ollama and pull the same model on the server
#   deploy/transfer.sh user@server --dry-run            build the package and show what would be sent; sends nothing
#
# Needs: ssh access to the server and sudo rights there. Reports and your .env are never sent.
set -euo pipefail

TARGET="${1:-}"; shift || true
[ -n "$TARGET" ] || { sed -n '2,10p' "$0"; exit 1; }
WITH_TEMPLATES=0; WITH_AI=0; DRY=0
for a in "$@"; do case "$a" in
  --with-templates) WITH_TEMPLATES=1;; --with-ai) WITH_AI=1;; --dry-run) DRY=1;;
  *) echo "Unknown option: $a"; exit 1;; esac; done

cd "$(dirname "$0")/.."
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
PKG="$TMP/crespoqa.tgz"

echo "1/4 Building the package (no .venv, no .env, no reports, no accounts)..."
find . \( -name .venv -o -name __pycache__ -o -name data -o -name logs -o -name qa_output -o -name .pytest_cache \
          -o -name 'out_*' \) -prune -o -type f \
     ! -name '.env' ! -name '*.pyc' ! -name '*.db' ! -name 'inspect_output*' ! -name 'crespoqa.tgz' \
     ! -regex '\./[^/]*\.docx' -print \
  | tar -czf "$PKG" -T -
echo "    package: $(du -h "$PKG" | cut -f1), $(tar -tzf "$PKG" | wc -l) files"

TEMPLATES_DB=""
if [ "$WITH_TEMPLATES" = 1 ]; then
  [ -f data/crespoqa.db ] || { echo "No data/crespoqa.db here: nothing to copy."; exit 1; }
  TEMPLATES_DB="$TMP/templates.db"
  python3 - "$TEMPLATES_DB" <<'PY'
import shutil, sqlite3, sys
shutil.copyfile("data/crespoqa.db", sys.argv[1])
db = sqlite3.connect(sys.argv[1])
db.execute("DELETE FROM users")            # accounts (and password hashes) never leave this machine
db.commit(); db.execute("VACUUM"); 
n = db.execute("SELECT COUNT(*) FROM templates").fetchone()[0]
v = db.execute("SELECT COUNT(*) FROM template_versions").fetchone()[0]
print(f"    templates: {n} template(s), {v} version(s); accounts removed")
PY
fi

MODEL="$(grep -E '^AI_MODEL=' .env 2>/dev/null | cut -d= -f2 || true)"
if [ "$DRY" = 1 ]; then
  echo "DRY RUN: nothing sent. Top-level contents:"; tar -tzf "$PKG" | sed 's|^\./||' | awk -F/ 'NF>1{print $1"/"$2} NF==1{print $1}' | sort -u | head -30
  [ "$WITH_AI" = 1 ] && echo "Would install Ollama on the server and pull: ${MODEL:-<AI_MODEL not set in .env>}"
  exit 0
fi

echo "2/4 Sending to $TARGET ..."
scp -q "$PKG" "$TARGET:/tmp/crespoqa.tgz"
[ -n "$TEMPLATES_DB" ] && scp -q "$TEMPLATES_DB" "$TARGET:/tmp/crespoqa_templates.db"

echo "3/4 Installing on the server (you may be asked for the sudo password) ..."
ssh -t "$TARGET" "set -e
  rm -rf /tmp/crespoqa_src && mkdir -p /tmp/crespoqa_src && tar -xzf /tmp/crespoqa.tgz -C /tmp/crespoqa_src
  cd /tmp/crespoqa_src && sudo bash deploy/install.sh
  if [ -f /tmp/crespoqa_templates.db ]; then
    sudo systemctl stop crespoqa
    sudo install -o crespoqa -g crespoqa -m 640 /tmp/crespoqa_templates.db /var/lib/crespoqa/crespoqa.db
    sudo systemctl start crespoqa && echo 'templates installed'
  fi
  rm -f /tmp/crespoqa.tgz /tmp/crespoqa_templates.db"

if [ "$WITH_AI" = 1 ]; then
  [ -n "$MODEL" ] || { echo "AI_MODEL is not set in .env: cannot know which model to pull."; exit 1; }
  echo "4/4 Installing Ollama and the model ($MODEL) on the server ..."
  ssh -t "$TARGET" "set -e
    command -v ollama >/dev/null || curl -fsSL https://ollama.com/install.sh | sh
    ollama pull '$MODEL'
    sudo sed -i 's|^AI_ENABLED=.*|AI_ENABLED=true|; s|^AI_MODEL=.*|AI_MODEL=$MODEL|' /etc/crespoqa/crespoqa.env
    sudo systemctl restart crespoqa && echo 'AI review enabled'"
else
  echo "4/4 AI not installed (add --with-ai when you want it)."
fi

cat <<'MSG'

Done. Remaining by hand on the server:
  1. Create the first account:
       sudo -u crespoqa env $(grep -v '^#' /etc/crespoqa/crespoqa.env | xargs) \
           /opt/crespoqa/.venv/bin/python -m crespoqa user add <name> --role editor
  2. Put nginx + HTTPS in front (deploy/nginx-crespoqa.conf), then browse to the server.
  3. Check:  curl http://127.0.0.1:8000/healthz     and     sudo systemctl status crespoqa
MSG
