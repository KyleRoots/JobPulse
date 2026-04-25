#!/bin/bash
# Post-merge setup hook for Scout Genius.
#
# Runs automatically after a background task agent merges work into main.
# Keeps the running environment in sync by:
#   1. Installing any new/updated Python dependencies.
#   2. Applying any new Alembic migrations.
#
# Idempotent and non-interactive. Safe to re-run.

set -euo pipefail

echo "[post-merge] Installing Python dependencies..."
pip install --quiet --disable-pip-version-check -r requirements.txt

echo "[post-merge] Applying Alembic migrations to head..."
alembic upgrade head

echo "[post-merge] Done."
