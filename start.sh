#!/usr/bin/env bash
# One-command start: builds the app, starts the AI server, downloads the model on first run (~4.7 GB), starts everything.
set -euo pipefail
cd "$(dirname "$0")"

command -v docker >/dev/null || { echo "Docker is not installed. Install Docker Engine or Docker Desktop first."; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "Docker Compose v2 is required ('docker compose')."; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker is installed but not running (or you lack permission)."; exit 1; }

if [ ! -f .env ]; then
  cp .env.docker.example .env
  echo "Created .env from .env.docker.example (edit it to change port, login or model)."
fi

AUTH="$(grep -E '^CRESPOQA_AUTH=' .env | tail -1 | cut -d= -f2 | tr A-Z a-z || true)"
PUB="$(grep -E '^CRESPOQA_PUBLISH=' .env | tail -1 | cut -d= -f2 || true)"
if [ "${PUB:-127.0.0.1}" != "127.0.0.1" ] && [ "${AUTH:-off}" != "on" ]; then
  echo "WARNING: reachable from your network with login OFF. Set CRESPOQA_AUTH=on and an admin user in .env."
fi

echo "Building and starting (the first run downloads the AI model, about 4.7 GB; later runs start in seconds)..."
docker compose up -d --build

PORT="$(grep -E '^CRESPOQA_PORT=' .env | tail -1 | cut -d= -f2 || true)"; PORT="${PORT:-8000}"
echo -n "Waiting for the app"
for _ in $(seq 1 600); do
  if curl -fsS "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; then echo; echo "Ready: http://localhost:${PORT}"; exit 0; fi
  echo -n "."; sleep 3
done
echo; echo "Not ready yet. Model download progress:  docker compose logs -f model-pull"
exit 1
