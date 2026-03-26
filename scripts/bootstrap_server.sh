#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${1:-/opt/spotify-soundeo-sync}"

mkdir -p "$PROJECT_DIR"
cd "$PROJECT_DIR"

python3 -m venv .venv
. .venv/bin/activate
pip install -e .

mkdir -p data logs artifacts/screenshots artifacts/html artifacts/reports playwright/.auth

if [[ ! -f .env ]]; then
  cp .env.example .env
fi

echo "Bootstrap complete for $PROJECT_DIR"
echo "Next steps:"
echo "  1. Fill .env with Spotify and Soundeo credentials"
echo "  2. Run: .venv/bin/python -m app dry-run"
echo "  3. Install timer files from deploy/systemd if needed"

