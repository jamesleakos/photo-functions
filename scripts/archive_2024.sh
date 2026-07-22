#!/usr/bin/env bash
set -euo pipefail

project_dir="/Users/jamesleakos/Documents/Development/Python/photo-functions"
cd "$project_dir"

printf '2024 archive started: %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
printf 'Phase 1: resume verified camera upload\n'
.venv/bin/photo-manager backup --workers 8

printf 'Phase 2: archive iPhone Favorites in disk-safe size-capped batches\n'
printf 'Using size-capped batches with immediate cleanup and a 100 GiB free-space reserve\n'
.venv/bin/photo-manager iphone-year 2024 \
  --minimum-free-gb 100 \
  --batch-gb 1 \
  --max-batch-items 25

printf 'Final catalog statistics\n'
.venv/bin/photo-manager stats
printf '2024 archive completed: %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
