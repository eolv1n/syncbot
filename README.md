# spotify-soundeo-sync

Production-oriented scaffold for syncing Spotify liked tracks into Soundeo actions.

## What is ready

- CLI modes: `sync-downloads-cache`, `full-sync`, `daily-sync`, `retry-waitlist`, `dry-run`
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
docker compose run --rm --service-ports app python -m app spotify-auth --no-browser
docker compose run --rm app python -m app show-config --as-paths
docker compose run --rm app python -m unittest discover -s tests -v
docker compose run --rm app python -m app dry-run
```

The Docker image installs the `automation` extra, so Soundeo browser automation is available after `docker compose build`.

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
python -m app sync-downloads-cache
python -m app full-sync
python -m app daily-sync
python -m app retry-waitlist
python -m app dry-run
```

`daily-sync` and `dry-run` only process fresh tracks. On the very first run without a saved cursor they use the recent window from `SPOTIFY_RECENT_DAYS_ON_FIRST_SYNC`. Use `full-sync` for a full historical pass.

Recommended flow:
- `sync-downloads-cache` builds a local cache of all Soundeo downloaded tracks
- `daily-sync` checks fresh Spotify likes against that cache first
- if already downloaded, it skips
- if found and downloadable on Soundeo, it stars
- if found but only vote-able, it votes
- if not found, it stores a local waitlist entry

## Spotify setup

1. Create an app in Spotify Developer Dashboard.
2. Add redirect URI from `.env`, by default `http://127.0.0.1:8899/callback`.
3. Request `user-library-read`.
4. Put `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` into `.env`.
5. Run auth once:

```bash
docker compose run --rm --service-ports app python -m app spotify-auth --no-browser
```

6. Open the printed URL in your browser, approve access, then wait until the terminal says that the token was saved to `data/spotify_tokens.json`.
7. After that, regular commands like `dry-run` and `daily-sync` will reuse the cached token and refresh it automatically.

Fallback if the callback port is busy or blocked:

```bash
docker compose run --rm app python -m app spotify-auth-url
docker compose run --rm app python -m app spotify-auth-exchange --code '<CODE_FROM_REDIRECT_URL>'
```

After approval Spotify will redirect the browser to something like:

```text
http://127.0.0.1:8899/callback?code=...&state=...
```

If the page does not open correctly, copy the `code` value from the browser address bar and pass it to `spotify-auth-exchange`.

## Server install

### Recommended: Docker

```bash
git clone <your-repo> /opt/spotify-soundeo-sync
cd /opt/spotify-soundeo-sync
cp .env.example .env
mkdir -p data logs artifacts/screenshots artifacts/html artifacts/reports playwright/.auth
docker compose build
docker compose run --rm --service-ports app python -m app spotify-auth --no-browser
docker compose run --rm app python -m app dry-run
```

Daily run as a long-lived container:

```bash
docker compose --profile scheduler up -d scheduler
```

Manual runs:

```bash
docker compose run --rm app python -m app initial-sync
docker compose run --rm app python -m app sync-downloads-cache
docker compose run --rm app python -m app full-sync
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
