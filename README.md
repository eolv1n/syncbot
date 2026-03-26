# spotify-soundeo-sync

Production-oriented scaffold for syncing Spotify liked tracks into Soundeo actions.

## What is ready

- CLI modes: `initial-sync`, `daily-sync`, `retry-waitlist`, `dry-run`
- SQLite state storage and JSON run reports
- Track normalization and fuzzy matching
- Runtime directories for logs and artifacts
- Deployment examples for `systemd`, `cron`, and Docker
- Docker-first path for local checks and server install

## What still needs project-specific setup

- Spotify OAuth credentials and token flow
- Real Soundeo selectors, login flow, and action handlers
- Optional Telegram notifications

## Quick start

### Docker-first

```bash
cp .env.example .env
mkdir -p data logs artifacts/screenshots artifacts/html artifacts/reports playwright/.auth
docker compose build
docker compose run --rm app python -m app show-config --as-paths
docker compose run --rm app python -m unittest discover -s tests -v
docker compose run --rm app python -m app dry-run
```

### Native Python

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
PYTHONPATH=src python3 -m app show-config --as-paths
PYTHONPATH=src python3 -m app dry-run
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

## Repository layout

```text
src/app/
tests/
data/
logs/
artifacts/screenshots/
artifacts/html/
artifacts/reports/
deploy/
```

## CLI

```bash
python -m app initial-sync
python -m app daily-sync
python -m app retry-waitlist
python -m app dry-run
```

## Spotify setup

1. Create an app in Spotify Developer Dashboard.
2. Add redirect URI from `.env`, by default `http://127.0.0.1:8899/callback`.
3. Request `user-library-read`.
4. Put credentials into `.env`.
5. For the current scaffold, provide a valid `SPOTIFY_ACCESS_TOKEN` manually or extend the client with refresh-token flow.

## Server install

### Recommended: Docker

```bash
git clone <your-repo> /opt/spotify-soundeo-sync
cd /opt/spotify-soundeo-sync
cp .env.example .env
mkdir -p data logs artifacts/screenshots artifacts/html artifacts/reports playwright/.auth
docker compose build
docker compose run --rm app python -m app dry-run
```

Daily run as a long-lived container:

```bash
docker compose --profile scheduler up -d scheduler
```

Manual runs:

```bash
docker compose run --rm app python -m app initial-sync
docker compose run --rm app python -m app daily-sync
docker compose run --rm app python -m app retry-waitlist
docker compose run --rm app python -m app dry-run
```

### Alternative: native Python

Example target path:

```text
/opt/spotify-soundeo-sync
```

Example deploy steps:

```bash
git clone <your-repo> /opt/spotify-soundeo-sync
cd /opt/spotify-soundeo-sync
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
playwright install chromium
cp .env.example .env
mkdir -p data logs artifacts/screenshots artifacts/html artifacts/reports
python -m app dry-run
```

## systemd example

Service file: `deploy/systemd/spotify-soundeo-sync.service`

Timer file: `deploy/systemd/spotify-soundeo-sync.timer`

```bash
sudo cp deploy/systemd/spotify-soundeo-sync.service /etc/systemd/system/
sudo cp deploy/systemd/spotify-soundeo-sync.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now spotify-soundeo-sync.timer
```

## Cron example

```bash
15 3 * * * cd /opt/spotify-soundeo-sync && ./.venv/bin/python -m app daily-sync >> logs/cron.log 2>&1
```

## Docker

```bash
docker compose build
docker compose run --rm app python -m app dry-run
```

## Notes on Soundeo automation

`src/app/integrations/soundeo.py` intentionally contains a safe scaffold. Before active use, replace placeholder search and click logic with:

- stable selectors for login, search, star and like controls
- downloaded-page parsing
- fallback hotkeys only where DOM automation is not stable
- error screenshots and HTML dumps from real pages

If you already have a prepared Python environment on the server, you can run directly with `PYTHONPATH=src python3 -m app ...` and skip editable install for the first smoke test.
