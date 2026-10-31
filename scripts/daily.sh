#!/usr/bin/env bash
# Advance a long-running island by one day: start it if it doesn't exist yet,
# otherwise resume from the last checkpoint. Meant to run once a day (cron, launchd
# or the GitHub Actions workflow in .github/workflows/island-daily.yml).
#
# Settings, all optional, via environment variables:
#   RUN_DIR   where the run lives            (default: runs/live)
#   SCENARIO  scenario for a new run         (default: scenarios/island_blank.yaml)
#   SEED      seed for a new run             (default: 0)
#   BACKEND   backend name from config       (default: the config's default)
#   DAYS      island days to add per call    (default: 1)
set -euo pipefail
cd "$(dirname "$0")/.."

RUN_DIR="${RUN_DIR:-runs/live}"
SCENARIO="${SCENARIO:-scenarios/island_blank.yaml}"
SEED="${SEED:-0}"
DAYS="${DAYS:-1}"
BACKEND_ARG=()
if [ -n "${BACKEND:-}" ]; then BACKEND_ARG=(--backend "$BACKEND"); fi

if [ -f "$RUN_DIR/checkpoint.pkl" ]; then
  llmabm resume "$RUN_DIR" --days "$DAYS" "${BACKEND_ARG[@]}"
else
  llmabm run "$SCENARIO" --run-dir "$RUN_DIR" --seed "$SEED" --days "$DAYS" "${BACKEND_ARG[@]}"
fi
