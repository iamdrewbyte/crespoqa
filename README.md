# CrespoQA

Cleans up Word (.docx) security reports and reviews them with a local AI. The AI only flags; it never edits.

## Run it (Docker)

```
./start.sh
```

Then open http://localhost:8000. The first start downloads the AI model (about 9 GB in total, once).

Settings live in `.env` (created from `.env.docker.example` on first run): port, login, and AI model.
For a team instance reachable on your network, set `CRESPOQA_PUBLISH=0.0.0.0`, `CRESPOQA_AUTH=on`,
and `CRESPOQA_ADMIN_USER` / `CRESPOQA_ADMIN_PASSWORD` (10+ characters).

Stop: `docker compose down`   Update: `git pull && ./start.sh`   Logs: `docker compose logs -f app`
