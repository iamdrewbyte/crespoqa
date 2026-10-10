#!/usr/bin/env bash
# Start CrespoQA for local testing: local Qwen (if installed) + web app on http://localhost:8000
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.local/ollama/bin:$PATH"

if command -v ollama >/dev/null 2>&1; then
  if ! curl -fs -m 2 http://127.0.0.1:11434/api/version >/dev/null; then
    echo "Starting Ollama..."
    nohup ollama serve > /tmp/ollama.log 2>&1 &
    for _ in $(seq 1 20); do curl -fs -m 1 http://127.0.0.1:11434/api/version >/dev/null && break; sleep 1; done
  fi
  echo "Ollama models: $(ollama list | tail -n +2 | awk '{print $1}' | tr '\n' ' ')"
else
  echo "Ollama not installed: AI review will be unavailable (the QA clean-up works without it)."
fi

AUTH="$(grep -E '^CRESPOQA_AUTH=' .env 2>/dev/null | cut -d= -f2 | tr A-Z a-z || true)"
if [ "$AUTH" = "on" ] && [ "$(.venv/bin/python -m crespoqa user list | wc -l)" -eq 0 ]; then
  echo
  echo "No accounts yet. Create one first:  .venv/bin/python -m crespoqa user add <name> --role editor"
  exit 1
fi
echo "Open http://localhost:8000 in your Windows browser. Ctrl+C to stop."
exec .venv/bin/python -m crespoqa.api.app
