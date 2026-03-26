#!/usr/bin/env bash
set -euo pipefail

PYTHONPATH=src python3 -m compileall src tests
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m app dry-run
