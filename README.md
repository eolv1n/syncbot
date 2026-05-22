# spotify-soundeo-sync

Production-oriented sync tool for mirroring Spotify liked tracks into Soundeo actions.

## What is ready

- CLI modes: `sync-downloads-cache`, `refresh-spotify-metadata`, `sync-track-ids`, `full-sync`, `fresh-sync`, `daily-sync`, `retry-waitlist`, `dry-run`
- Waitlist maintenance modes: `waitlist-report`, `mark-old-waitlist-manual-review`
- SQLite state storage and JSON run reports
- Spotify OAuth with local token cache
- Soundeo login, search, favorites, votes, and downloads-page parsing
- Track normalization and guarded matching
- Runtime directories for logs and artifacts
- Deployment examples for `systemd`, `cron`, and Docker
- Docker-first path for local checks and server install

## What still needs project-specific setup

- Spotify OAuth credentials
- Soundeo account credentials
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
python -m app sync-track-ids <SPOTIFY_TRACK_ID> [SPOTIFY_TRACK_ID...]
python -m app full-sync
python -m app fresh-sync
python -m app daily-sync
python -m app retry-waitlist
python -m app dry-run
python -m app waitlist-report
python -m app refresh-spotify-metadata
python -m app mark-old-waitlist-manual-review --older-than-days 365
```

`fresh-sync`, `daily-sync`, and `dry-run` process fresh tracks. On the very first run without a saved cursor they use the recent window from `SPOTIFY_RECENT_DAYS_ON_FIRST_SYNC`. Use `full-sync` for a full historical pass.

Recommended flow:
- `sync-downloads-cache` builds a local cache of all Soundeo downloaded tracks
- `refresh-spotify-metadata` refreshes local Spotify track metadata, including release dates, without opening Soundeo or applying actions
- `sync-track-ids` checks only explicit Spotify liked track ids and does not move the daily cursor
- `fresh-sync` checks only fresh Spotify likes since the last daily cursor and does not retry waitlist
- `daily-sync` retries due waitlist entries first, then checks fresh Spotify likes against the cache
- `manual_review` waitlist entries are excluded from automatic retry and are intended for human review
- if already downloaded, it skips
- if found and downloadable on Soundeo, it stars
- if found but only vote-able, it votes
- if vote is blocked by account limits or premium restrictions, it keeps the track on waitlist for later retry instead of treating it as a hard failure
- if not found, it stores a local waitlist entry

Old waitlist maintenance:

```bash
python -m app waitlist-report --older-than-days 365 --status active
python -m app mark-old-waitlist-manual-review --older-than-days 365
python -m app mark-old-waitlist-manual-review --older-than-days 365 --apply
```

The apply command does not delete tracks. It marks active old waitlist rows as `manual_review`, so normal `daily-sync` stops retrying them every run while preserving them for a selective review UI or manual investigation.

Suggested first real run:
- `sync-downloads-cache` once to cache the whole Soundeo downloads history
- `daily-sync` for normal day-to-day usage
- `full-sync` only as a separate long-running historical backfill

Important matching rules:
- Spotify source is only `Liked Songs` / saved tracks
- artist overlap is required
- main track title must also match closely
- `original`, `extended`, and `edit` are treated as compatible base versions of the same track
- named remixes and named mixes are not allowed to collapse into base versions or unrelated remixes
- Spotify album release year is used as a supporting match hint when Soundeo exposes a year in candidate text
- when both Spotify and Soundeo expose release years, candidates outside the configured tolerance are rejected before scoring
- Soundeo release name/date from matched rows is stored in `soundeo_matches` and surfaced in waitlist reports for manual review
- release year/date is never appended to Soundeo search queries; it is only used after candidates are collected
- if a candidate is weak or ambiguous, the track should go to waitlist rather than to favorites

Important download protection:
- a track is treated as already downloaded if either its normalized key or the matched Soundeo `track_id` exists in `downloads_cache`
- downloaded-key normalization folds common punctuation and diacritics such as `Cafe`/`Café` and straight/curly quotes
- because of that, running `sync-downloads-cache` before `full-sync` is strongly recommended

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

One-shot run through Docker:

```bash
docker compose run --rm app python -m app fresh-sync
docker compose run --rm app python -m app daily-sync
```

If you changed Python code locally, rebuild the image before relying on new behavior:

```bash
docker compose build
```

Manual runs:

```bash
docker compose run --rm app python -m app initial-sync
docker compose run --rm app python -m app sync-downloads-cache
docker compose run --rm app python -m app sync-track-ids <SPOTIFY_TRACK_ID> [SPOTIFY_TRACK_ID...]
docker compose run --rm app python -m app full-sync
docker compose run --rm app python -m app fresh-sync
docker compose run --rm app python -m app daily-sync
docker compose run --rm app python -m app retry-waitlist
docker compose run --rm app python -m app dry-run
docker compose run --rm app python -m app refresh-spotify-metadata
```

For large Soundeo history imports you can speed up the cache build:

```bash
docker compose run --rm -e RATE_LIMIT_SECONDS=0.2 app python -m app sync-downloads-cache
```

The terminal will print pagination progress while the downloads cache is being collected.

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

The provided unit runs a one-shot Docker task and exits after completion. The timer schedule is Tuesday and Friday at `19:00`.

## Cron example

```bash
0 19 * * 2,5 cd /opt/spotify-soundeo-sync && docker compose run --rm app python -m app daily-sync >> logs/cron.log 2>&1
```

## Docker

```bash
docker compose build
docker compose run --rm app python -m app dry-run
```

## Notes on Soundeo automation

`src/app/integrations/soundeo.py` contains the current Soundeo automation logic. It already handles the main workflow, but you should still expect occasional site-specific maintenance if Soundeo changes layout or behavior.

Current behavior:
- login is performed from the main page modal
- search uses the global Soundeo search field
- favorites are used for tracks you want to download later
- votes are used for tracks that exist but are not yet downloadable
- vote-limit or premium-blocked tracks are returned to waitlist for retry on later runs
- downloads cache is parsed from `https://soundeo.com/account/downloads`
- error screenshots and HTML dumps are written into `artifacts/`
- `problem_tracks` in reports are written in a human-readable `Artist - Title [spotify_track_id]` form

If you already have a prepared Python environment on the server, you can run directly with `PYTHONPATH=src python3 -m app ...` and skip editable install for the first smoke test.
